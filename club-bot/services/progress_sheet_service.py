"""機体進捗管理シート（Google Sheets 正本）の読み書きサービス。

シート構成（1ブック3シート）:
- 進捗管理     : 機体〜サブタスクを隣接リストで持つメインシート
- Todoist対応表 : Todoist プロジェクト名 → 紐付け先ノード ID の対応表
- ダッシュボード : 全体進捗・階層別サマリーの数式を設置（値はシート側で自動計算）

設計方針:
- 「グリッド（2次元配列）⇔ ノード」の変換と書き戻し範囲の組み立ては
  純粋関数として実装し、gspread なしでテストできるようにする
- gspread の呼び出しは ProgressSheetClient に集約する
  （client を注入するとネットワークなしでテストできる。
   services/sheets_service.py の SheetsExporter と同じパターン）
- 呼び出し側（cog・同期ジョブ）は asyncio.to_thread() で実行する
- bot が毎回書き込むのは「深さ・集計進捗率・更新日時」のみ。
  進捗バー（SPARKLINE）は空セルにだけ数式を設置し、以降は
  集計進捗率の更新に自動追従させる。条件付き書式・ダッシュボードの
  数式・グラフは初期セットアップで1回だけ設置する
"""
from __future__ import annotations

import os
from typing import Any

from services.progress_tree import ProgressNode, ProgressTree, parse_progress

SETTINGS_KEY = "PROGRESS_SPREADSHEET_ID"

PROGRESS_SHEET = "進捗管理"
MAPPING_SHEET = "Todoist対応表"
DASHBOARD_SHEET = "ダッシュボード"
SETTINGS_SHEET = "設定"
SPAR_MAPPING_SHEET = "桁巻き対応表"

# 進捗管理シートの列（0始まりインデックス）
COL_ID = 0            # A
COL_PARENT_ID = 1     # B
COL_ORDER = 2         # C
COL_DEPTH = 3         # D: bot が自動計算
COL_NAME = 4          # E
COL_ASSIGNEE = 5      # F
COL_STATUS = 6        # G
COL_MANUAL = 7        # H: 進捗率(手入力)
COL_AGGREGATED = 8    # I: bot が自動計算
COL_BAR = 9           # J: SPARKLINE 数式（1回だけ設置）
COL_SOURCE = 10       # K: manual / todoist
COL_TODOIST_ID = 11   # L
COL_UPDATED_AT = 12   # M

PROGRESS_HEADER = [
    "ID", "親ID", "表示順", "深さ", "名前", "担当者", "状態",
    "進捗率(手入力)", "集計進捗率", "進捗バー", "ソース",
    "TodoistタスクID", "更新日時",
]
MAPPING_HEADER = ["Todoistプロジェクト名", "紐付け先ノードID", "通知チャンネルID"]
SETTINGS_HEADER = ["キー", "値", "メモ"]
SPAR_MAPPING_HEADER = ["桁巻きファイル内の識別子", "紐付け先ノードID"]

# 「設定」タブの管理者設定キー（.env ではなくシートで管理する。
# キーを増やすときはここに定数を足し、SETTINGS_INITIAL_ROWS に行を足すだけでよい）
SHEET_KEY_DEFAULT_CHANNEL = "デフォルト通知チャンネルID"
SHEET_KEY_SPAR_BOOK = "桁巻きスプレッドシートID"

SETTINGS_INITIAL_ROWS = [
    [SHEET_KEY_DEFAULT_CHANNEL, "",
     "タスク通知の共通送信先。対応表の通知チャンネルIDが空の場合に使用"],
    [SHEET_KEY_SPAR_BOOK, "",
     "桁巻きデータを管理する別スプレッドシートの ID（任意）"],
]

SOURCE_MANUAL = "manual"
SOURCE_TODOIST = "todoist"
SOURCE_SPAR_WINDING = "spar_winding"

# Todoist 由来ノードの ID プレフィックス
TODOIST_ID_PREFIX = "td_"


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
# 純粋関数: グリッド ⇔ ノード
# ---------------------------------------------------------------------
def grid_to_nodes(grid: list[list]) -> list[ProgressNode]:
    """進捗管理シートのグリッド（ヘッダー行含む）をノード一覧へ変換する。

    row_index にはシート上の行番号（1始まり。データは2行目から）を保持し、
    書き戻し時の位置決めに使う。ID が空の行はスキップする
    （罫線用の空行を許容するため。エラーにはしない）。
    """
    nodes: list[ProgressNode] = []
    for i, row in enumerate(grid[1:], start=2):
        node_id = _cell(row, COL_ID)
        if not node_id:
            continue
        source = _cell(row, COL_SOURCE) or SOURCE_MANUAL
        nodes.append(ProgressNode(
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
        ))
    return nodes


def sparkline_formula(row_index: int) -> str:
    """進捗バー列に設置する SPARKLINE 数式（集計進捗率セルを参照）。"""
    return (f'=SPARKLINE(I{row_index}, {{"charttype","bar";'
            f'"max",1;"color1","#4a86e8"}})')


def build_writeback_ranges(grid: list[list], tree: ProgressTree,
                           now_text: str) -> list[dict[str, Any]]:
    """書き戻し用の batch_update 範囲（gspread values 形式）を組み立てる。

    - 深さ（D）・集計進捗率（I）・更新日時（M）: ツリーに採用された行へ書き込む。
      エラーでスキップされた行は既存値を保持する（列単位の連続範囲で
      書き込むため、既存グリッドの値をそのまま埋め戻す）
    - 進捗バー（J）: 空セルにのみ SPARKLINE 数式を設置する
    """
    n_rows = len(grid) - 1
    if n_rows <= 0:
        return []

    depth_col: list[list[Any]] = []
    agg_col: list[list[Any]] = []
    updated_col: list[list[Any]] = []
    bar_updates: list[dict[str, Any]] = []

    by_row: dict[int, ProgressNode] = {
        node.row_index: node for node in tree.by_id.values()
        if node.row_index is not None}

    for i, row in enumerate(grid[1:], start=2):
        node = by_row.get(i)
        if node is None:
            # スキップ行・空行: 既存値を保持
            depth_col.append([_cell(row, COL_DEPTH)])
            agg_col.append([_cell(row, COL_AGGREGATED)])
            updated_col.append([_cell(row, COL_UPDATED_AT)])
            continue
        depth_col.append([node.depth if node.depth is not None else ""])
        agg_col.append([round(node.aggregated, 4)
                        if node.aggregated is not None else ""])
        updated_col.append([now_text])
        if not _cell(row, COL_BAR):
            bar_updates.append({
                "range": f"'{PROGRESS_SHEET}'!J{i}",
                "values": [[sparkline_formula(i)]],
            })

    last = n_rows + 1
    ranges = [
        {"range": f"'{PROGRESS_SHEET}'!D2:D{last}", "values": depth_col},
        {"range": f"'{PROGRESS_SHEET}'!I2:I{last}", "values": agg_col},
        {"range": f"'{PROGRESS_SHEET}'!M2:M{last}", "values": updated_col},
    ]
    ranges.extend(bar_updates)
    return ranges


def parse_mapping_grid(grid: list[list]) -> list[dict[str, str]]:
    """Todoist対応表のグリッドを
    [{project_name, node_id, notify_channel_id}] へ変換する。

    プロジェクト名・ノード ID のどちらかが空の行はスキップする。
    通知チャンネルID 列（3列目）は任意（旧2列シートも読める）。
    空欄は「設定」タブのデフォルト通知チャンネルへのフォールバックを意味する。
    """
    out: list[dict[str, str]] = []
    for row in grid[1:]:
        project = _cell(row, 0)
        node_id = _cell(row, 1)
        if project and node_id:
            out.append({"project_name": project, "node_id": node_id,
                        "notify_channel_id": _cell(row, 2)})
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
# ダッシュボード・初期セットアップの定義（純粋データ）
# ---------------------------------------------------------------------
def dashboard_cells() -> list[dict[str, Any]]:
    """ダッシュボードシートに設置する数式・ラベル（batch_update 範囲）。

    数式は進捗管理シートの範囲を参照するため、以降のデータ更新に
    自動追従する（bot が毎回書き直す必要はない）。
    グラフ（縦棒・円）は初回にシート UI から範囲を指定して作成する
    （データ範囲参照のため作成後は自動更新される）。
    """
    q = f"'{PROGRESS_SHEET}'"
    return [
        {"range": f"'{DASHBOARD_SHEET}'!A1:B2", "values": [
            ["機体全体の進捗", ""],
            [f"=IFERROR(AVERAGE(FILTER({q}!I2:I, {q}!D2:D=0)), 0)",
             f'=SPARKLINE(A2, {{"charttype","bar";"max",1;"color1","#4a86e8"}})'],
        ]},
        {"range": f"'{DASHBOARD_SHEET}'!A4:B4", "values": [
            ["パーツ別進捗（深さ=1）", ""],
        ]},
        {"range": f"'{DASHBOARD_SHEET}'!A5", "values": [
            [f"=IFERROR(SORT(FILTER({{{q}!E2:E, {q}!I2:I}}, "
             f"{q}!D2:D=1), 2, TRUE), \"（データなし）\")"],
        ]},
        {"range": f"'{DASHBOARD_SHEET}'!D4:E7", "values": [
            ["状態別内訳", ""],
            ["未着手", f'=COUNTIF({q}!G2:G, "未着手")'],
            ["製作中", f'=COUNTIF({q}!G2:G, "製作中")'],
            ["完了", f'=COUNTIF({q}!G2:G, "完了")'],
        ]},
        {"range": f"'{DASHBOARD_SHEET}'!D9", "values": [
            [("グラフの作成（初回のみ）: パーツ別進捗は A5 以降の範囲、"
              "状態別内訳は D5:E7 を選択し、挿入 > グラフ から"
              "縦棒グラフ / 円グラフを作成してください。"
              "範囲参照のため以降は自動更新されます。")],
        ]},
    ]


def conditional_format_request(sheet_id: int) -> dict[str, Any]:
    """集計進捗率列（I）に赤→黄→緑のカラースケールを設定するリクエスト。

    Sheets API の batchUpdate（AddConditionalFormatRuleRequest）形式。
    初期セットアップで1回だけ実行する。
    """
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,          # 2行目以降
                    "startColumnIndex": COL_AGGREGATED,
                    "endColumnIndex": COL_AGGREGATED + 1,
                }],
                "gradientRule": {
                    "minpoint": {"type": "NUMBER", "value": "0",
                                 "color": {"red": 0.918, "green": 0.6,
                                           "blue": 0.6}},
                    "midpoint": {"type": "NUMBER", "value": "0.5",
                                 "color": {"red": 1.0, "green": 0.898,
                                           "blue": 0.6}},
                    "maxpoint": {"type": "NUMBER", "value": "1",
                                 "color": {"red": 0.576, "green": 0.769,
                                           "blue": 0.49}},
                },
            },
            "index": 0,
        },
    }


# ---------------------------------------------------------------------
# gspread ラッパー
# ---------------------------------------------------------------------
class ProgressSheetClient:
    """gspread 呼び出しの薄いラッパー（全メソッド同期。to_thread で呼ぶ）。

    client を注入すると gspread なしでテストできる
    （client は open_by_key(spreadsheet_id) -> book を持つオブジェクト）。
    """

    def __init__(self, credentials_path: str | None = None, client: Any = None):
        self._credentials_path = credentials_path
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        creds_path = (self._credentials_path
                      or os.getenv("GOOGLE_CREDENTIALS_PATH", "")).strip()
        if not creds_path or not os.path.exists(creds_path):
            raise ProgressSheetUnavailable(
                "GOOGLE_CREDENTIALS_PATH（サービスアカウント JSON）が未設定です。")
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as e:
            raise ProgressSheetUnavailable(
                "gspread / google-auth が見つかりません。"
                "`pip install -r requirements.txt` を実行してください。") from e
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        self._client = gspread.authorize(
            Credentials.from_service_account_file(creds_path, scopes=scopes))
        return self._client

    def _open(self, spreadsheet_id: str) -> Any:
        return self._get_client().open_by_key(spreadsheet_id)

    def _worksheet(self, book: Any, title: str, *, create: bool = False,
                   rows: int = 200, cols: int = 15) -> Any:
        try:
            return book.worksheet(title), False
        except Exception:  # noqa: BLE001  (gspread.WorksheetNotFound 等)
            if not create:
                raise
            return book.add_worksheet(title=title, rows=rows, cols=cols), True

    # ---------- 読み込み ----------
    def read_progress_grid(self, spreadsheet_id: str) -> list[list]:
        ws, _ = self._worksheet(self._open(spreadsheet_id), PROGRESS_SHEET)
        return ws.get_all_values()

    def read_mapping_grid(self, spreadsheet_id: str) -> list[list]:
        ws, _ = self._worksheet(self._open(spreadsheet_id), MAPPING_SHEET)
        return ws.get_all_values()

    def read_settings_grid(self, spreadsheet_id: str) -> list[list]:
        ws, _ = self._worksheet(self._open(spreadsheet_id), SETTINGS_SHEET)
        return ws.get_all_values()

    def read_spar_mapping_grid(self, spreadsheet_id: str) -> list[list]:
        ws, _ = self._worksheet(self._open(spreadsheet_id),
                                SPAR_MAPPING_SHEET)
        return ws.get_all_values()

    def read_grid(self, spreadsheet_id: str, sheet_title: str) -> list[list]:
        """任意ブック・任意シートのグリッドを読む（桁巻きブック用）。"""
        ws, _ = self._worksheet(self._open(spreadsheet_id), sheet_title)
        return ws.get_all_values()

    def list_sheet_titles(self, spreadsheet_id: str) -> list[str]:
        """ブック内のシート名一覧（桁巻きブックの桁別シート探索用）。"""
        return [ws.title for ws in self._open(spreadsheet_id).worksheets()]

    # ---------- 書き込み ----------
    def apply_value_ranges(self, spreadsheet_id: str,
                           ranges: list[dict[str, Any]]) -> None:
        """values_batch_update で複数範囲を一括更新する。

        USER_ENTERED で書き込むため、SPARKLINE 等の数式は数式として
        評価される。
        """
        if not ranges:
            return
        book = self._open(spreadsheet_id)
        book.values_batch_update(body={
            "valueInputOption": "USER_ENTERED",
            "data": ranges,
        })

    def append_progress_rows(self, spreadsheet_id: str,
                             rows: list[list[Any]]) -> None:
        """進捗管理シートの末尾に行を追加する（Todoist 同期の新規行用）。"""
        if not rows:
            return
        ws, _ = self._worksheet(self._open(spreadsheet_id), PROGRESS_SHEET)
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    def append_mapping_row(self, spreadsheet_id: str, project_name: str,
                           node_id: str, notify_channel_id: str = "") -> None:
        """Todoist対応表に1行追加する（/progress setup 用）。"""
        ws, _ = self._worksheet(self._open(spreadsheet_id), MAPPING_SHEET)
        ws.append_rows([[project_name, node_id, notify_channel_id]],
                       value_input_option="USER_ENTERED")

    # ---------- 初期セットアップ ----------
    def setup_book(self, spreadsheet_id: str) -> dict[str, bool]:
        """3シートの作成・ヘッダー・条件付き書式・ダッシュボード数式を設置する。

        冪等: 既存シートは作り直さない。条件付き書式は進捗管理シートを
        新規作成した場合のみ追加する（繰り返し実行で重複させない）。
        戻り値は {シート名: 新規作成したか}。
        """
        book = self._open(spreadsheet_id)
        created: dict[str, bool] = {}

        ws_progress, is_new = self._worksheet(
            book, PROGRESS_SHEET, create=True, rows=500, cols=15)
        created[PROGRESS_SHEET] = is_new
        if is_new:
            ws_progress.update([PROGRESS_HEADER])
            book.batch_update(
                {"requests": [conditional_format_request(ws_progress.id)]})

        ws_mapping, is_new = self._worksheet(
            book, MAPPING_SHEET, create=True, rows=50, cols=5)
        created[MAPPING_SHEET] = is_new
        if is_new:
            ws_mapping.update([MAPPING_HEADER])

        ws_settings, is_new = self._worksheet(
            book, SETTINGS_SHEET, create=True, rows=50, cols=5)
        created[SETTINGS_SHEET] = is_new
        if is_new:
            ws_settings.update([SETTINGS_HEADER, *SETTINGS_INITIAL_ROWS])

        ws_spar, is_new = self._worksheet(
            book, SPAR_MAPPING_SHEET, create=True, rows=50, cols=5)
        created[SPAR_MAPPING_SHEET] = is_new
        if is_new:
            ws_spar.update([SPAR_MAPPING_HEADER])

        _, is_new = self._worksheet(
            book, DASHBOARD_SHEET, create=True, rows=50, cols=10)
        created[DASHBOARD_SHEET] = is_new
        if is_new:
            book.values_batch_update(body={
                "valueInputOption": "USER_ENTERED",
                "data": dashboard_cells(),
            })
        return created
