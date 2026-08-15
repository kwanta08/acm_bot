"""旧・中央スプレッドシート（機体進捗）の**読み取り専用**アダプタ。

/progress の正本は DB の progress_nodes へ移行済み（migrations/009・
スキーマ v10）。本モジュールは移行前から運用していたサークルのシートを
scripts/migrate_progress_sheet_to_db.py が読み込むためだけに残している。

- **bot 本体はこのモジュールを import しない**（gspread / google-auth と
  GOOGLE_CREDENTIALS_PATH は /progress の動作に不要）。
  再混入は tests/test_progress_no_sheets.py が検出する
- 書き込み系（集計の書き戻し・SPARKLINE 設置・ダッシュボード数式・
  シート初期化）は DB 移行に伴いすべて削除した
- 「グリッド（2次元配列）⇔ ノード」の変換は純粋関数として実装し、
  gspread なしでテストできるようにしている

旧シート構成（読み取り対象）:
- 進捗管理     : 機体〜サブタスクを隣接リストで持つメインシート
- Todoist対応表 : Todoist プロジェクト名 → 紐付け先ノード ID の対応表
- 設定         : キー・バリュー形式の管理者設定
- 桁巻き対応表  : 桁巻きファイル内の識別子 → 紐付け先ノード ID
"""

from __future__ import annotations

import os
from typing import Any

from services.progress_tree import (
    SOURCE_MANUAL,
    ProgressNode,
    parse_progress,
)

# 旧・進捗シート ID を保持していた settings キー（移行済みギルドの判定用。
# 新規の読み書きは行わない）
SETTINGS_KEY = "PROGRESS_SPREADSHEET_ID"

PROGRESS_SHEET = "進捗管理"
MAPPING_SHEET = "Todoist対応表"
SETTINGS_SHEET = "設定"
SPAR_MAPPING_SHEET = "桁巻き対応表"

# 進捗管理シートの列（0始まりインデックス）
COL_ID = 0  # A
COL_PARENT_ID = 1  # B
COL_ORDER = 2  # C
COL_DEPTH = 3  # D: シート側の計算結果（DB へは取り込まない）
COL_NAME = 4  # E
COL_ASSIGNEE = 5  # F
COL_STATUS = 6  # G
COL_MANUAL = 7  # H: 進捗率(手入力)
COL_AGGREGATED = 8  # I: シート側の計算結果（DB へは取り込まない）
COL_BAR = 9  # J: SPARKLINE 数式
COL_SOURCE = 10  # K: manual / todoist / spar_winding
COL_TODOIST_ID = 11  # L
COL_UPDATED_AT = 12  # M

PROGRESS_HEADER = [
    "ID",
    "親ID",
    "表示順",
    "深さ",
    "名前",
    "担当者",
    "状態",
    "進捗率(手入力)",
    "集計進捗率",
    "進捗バー",
    "ソース",
    "TodoistタスクID",
    "更新日時",
]
MAPPING_HEADER = ["Todoistプロジェクト名", "紐付け先ノードID", "通知チャンネルID", "登録ギルドID"]
SETTINGS_HEADER = ["キー", "値", "メモ"]
SPAR_MAPPING_HEADER = ["桁巻きファイル内の識別子", "紐付け先ノードID"]

# 「設定」タブの管理者設定キー（移行時に settings テーブルへ移す）
SHEET_KEY_DEFAULT_CHANNEL = "デフォルト通知チャンネルID"
SHEET_KEY_SPAR_BOOK = "桁巻きスプレッドシートID"


class ProgressSheetUnavailable(Exception):
    """gspread 未導入・認証情報未設定などで Sheets 連携を実行できない。"""


def _cell(row: list, index: int) -> str:
    """ragged な行でも安全にセル値を文字列で返す。"""
    if index < len(row) and row[index] is not None:
        return str(row[index]).strip()
    return ""


def _parse_order(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------
# 純粋関数: グリッド → ノード
# ---------------------------------------------------------------------
def grid_to_nodes(grid: list[list]) -> list[ProgressNode]:
    """進捗管理シートのグリッド（ヘッダー行含む）をノード一覧へ変換する。

    row_index にはシート上の行番号（1始まり。データは2行目から）を保持する
    （移行時のエラー報告用）。ID が空の行はスキップする
    （罫線用の空行を許容するため。エラーにはしない）。
    """
    nodes: list[ProgressNode] = []
    for i, row in enumerate(grid[1:], start=2):
        node_id = _cell(row, COL_ID)
        if not node_id:
            continue
        source = _cell(row, COL_SOURCE) or SOURCE_MANUAL
        nodes.append(
            ProgressNode(
                node_id=node_id,
                parent_id=_cell(row, COL_PARENT_ID) or None,
                order=_parse_order(_cell(row, COL_ORDER)),
                name=_cell(row, COL_NAME),
                assignee=_cell(row, COL_ASSIGNEE),
                status=_cell(row, COL_STATUS),
                manual_progress=parse_progress(_cell(row, COL_MANUAL)),
                source=source,
                todoist_task_id=_cell(row, COL_TODOIST_ID),
                row_index=i,
            )
        )
    return nodes


def parse_mapping_grid(grid: list[list]) -> list[dict[str, str]]:
    """Todoist対応表のグリッドを
    [{project_name, node_id, notify_channel_id, guild_id}] へ変換する。

    プロジェクト名・ノード ID のどちらかが空の行はスキップする。
    通知チャンネルID（3列目）・登録ギルドID（4列目）は任意
    （旧2〜3列シートも読める）。登録ギルドID は、1枚のシートを複数サーバーで
    共有していた場合にどのサーバー由来かを判別するために使う。
    """
    out: list[dict[str, str]] = []
    for row in grid[1:]:
        project = _cell(row, 0)
        node_id = _cell(row, 1)
        if project and node_id:
            out.append(
                {
                    "project_name": project,
                    "node_id": node_id,
                    "notify_channel_id": _cell(row, 2),
                    "guild_id": _cell(row, 3),
                }
            )
    return out


def parse_settings_grid(grid: list[list]) -> dict[str, str]:
    """「設定」タブ（キー・バリュー形式）を dict へ変換する。

    キーが空の行はスキップ。同一キーは後勝ち。値は前後空白を除去する。
    """
    out: dict[str, str] = {}
    for row in grid[1:]:
        key = _cell(row, 0)
        if key:
            out[key] = _cell(row, 1)
    return out


def parse_spar_mapping_grid(grid: list[list]) -> list[dict[str, str]]:
    """桁巻き対応表を [{spar_key, node_id}] へ変換する。

    spar_key は桁巻きスプレッドシート内でその桁を一意に指す識別子
    （桁別シートのシート名）。どちらかが空の行はスキップする。
    """
    out: list[dict[str, str]] = []
    for row in grid[1:]:
        spar_key = _cell(row, 0)
        node_id = _cell(row, 1)
        if spar_key and node_id:
            out.append({"spar_key": spar_key, "node_id": node_id})
    return out


# ---------------------------------------------------------------------
# gspread ラッパー（読み取り専用）
# ---------------------------------------------------------------------
class ProgressSheetClient:
    """gspread 呼び出しの薄いラッパー（全メソッド同期・読み取り専用）。

    client を注入すると gspread なしでテストできる
    （client は open_by_key(spreadsheet_id) -> book を持つオブジェクト）。
    移行スクリプトからのみ利用する。
    """

    def __init__(self, credentials_path: str | None = None, client: Any = None):
        self._credentials_path = credentials_path
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        creds_path = (self._credentials_path or os.getenv("GOOGLE_CREDENTIALS_PATH", "")).strip()
        if not creds_path or not os.path.exists(creds_path):
            raise ProgressSheetUnavailable(
                "GOOGLE_CREDENTIALS_PATH（サービスアカウント JSON）が未設定です。"
            )
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as e:
            raise ProgressSheetUnavailable(
                "gspread / google-auth が見つかりません。"
                "移行時のみ `pip install gspread google-auth` が必要です。"
            ) from e
        # 読み取りのみのため readonly スコープで十分
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        self._client = gspread.authorize(
            Credentials.from_service_account_file(creds_path, scopes=scopes)
        )
        return self._client

    def _open(self, spreadsheet_id: str) -> Any:
        return self._get_client().open_by_key(spreadsheet_id)

    def _worksheet(self, book: Any, title: str) -> Any:
        return book.worksheet(title)

    # ---------- 読み込み ----------
    def read_progress_grid(self, spreadsheet_id: str) -> list[list]:
        return self._worksheet(self._open(spreadsheet_id), PROGRESS_SHEET).get_all_values()

    def read_mapping_grid(self, spreadsheet_id: str) -> list[list]:
        return self._worksheet(self._open(spreadsheet_id), MAPPING_SHEET).get_all_values()

    def read_settings_grid(self, spreadsheet_id: str) -> list[list]:
        return self._worksheet(self._open(spreadsheet_id), SETTINGS_SHEET).get_all_values()

    def read_spar_mapping_grid(self, spreadsheet_id: str) -> list[list]:
        return self._worksheet(self._open(spreadsheet_id), SPAR_MAPPING_SHEET).get_all_values()

    def read_grid(self, spreadsheet_id: str, sheet_title: str) -> list[list]:
        """任意ブック・任意シートのグリッドを読む（桁巻きブック用）。"""
        return self._worksheet(self._open(spreadsheet_id), sheet_title).get_all_values()

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        """ブック内のシート名一覧（桁巻きブックの桁別シート探索用）。"""
        return [ws.title for ws in self._open(spreadsheet_id).worksheets()]
