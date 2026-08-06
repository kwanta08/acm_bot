"""Google Sheets エクスポート連携（ギルド別）。

DB（正本）のタスク・メンバー一覧を、ギルドごとに設定された
スプレッドシートへエクスポートする任意連携。

- スプレッドシート ID はギルド別 settings の `SPREADSHEET_ID` から取得する
- 未設定ギルドは自動スキップ（sync_guild が None を返す。例外にはしない）
- gspread / google-auth はこの連携を使うときだけ import する
  （未導入・認証情報なしの環境では SheetsUnavailable を送出し、
   呼び出し側が案内メッセージに変換する）

なお旧来の「Sheets を正本とする常時同期」は廃止されたまま。
本モジュールは DB → Sheets への一方向エクスポートのみを行う。
"""
from __future__ import annotations

import os
from typing import Any

from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from repositories.task_repository import TaskRepository
from services import team_service
from utils.db import Database
from utils.logger import get_logger

log = get_logger("sheets_service")

SETTINGS_KEY = "SPREADSHEET_ID"

TASKS_SHEET = "tasks"
MEMBERS_SHEET = "members"

TASKS_HEADER = [
    "local_task_id", "todoist_task_id", "title", "assignee_id", "team",
    "due_date", "priority", "status", "created_by", "created_at", "completed_at",
]
MEMBERS_HEADER = [
    "user_id", "display_name", "primary_team", "secondary_teams",
    "is_leader", "skills", "joined_at",
]


class SheetsUnavailable(Exception):
    """gspread 未導入・認証情報未設定などで Sheets 連携を実行できない。"""


async def get_spreadsheet_id(db: Database, guild_id: int) -> str | None:
    """ギルド別 settings からスプレッドシート ID を取得する。未設定なら None。"""
    value = await SettingsRepository(db).get(guild_id, SETTINGS_KEY)
    value = (value or "").strip()
    return value or None


def build_tasks_rows(tasks: list[dict[str, Any]],
                     team_names: dict[str, str]) -> list[list[str]]:
    """tasks シートに書き込む行（ヘッダー除く）を組み立てる。"""
    rows: list[list[str]] = []
    for t in tasks:
        team_key = t.get("team_key") or ""
        rows.append([
            str(t.get("local_task_id", "")),
            str(t.get("todoist_task_id") or ""),
            str(t.get("title", "")),
            str(t.get("assignee_id") or ""),
            team_names.get(team_key, team_key),
            str(t.get("due_date") or ""),
            str(t.get("priority") or ""),
            str(t.get("status", "")),
            str(t.get("created_by", "")),
            str(t.get("created_at", "")),
            str(t.get("completed_at") or ""),
        ])
    return rows


def build_members_rows(members: list[dict[str, Any]],
                       team_names: dict[str, str]) -> list[list[str]]:
    """members シートに書き込む行（ヘッダー除く）を組み立てる。"""
    rows: list[list[str]] = []
    for m in members:
        primary = m.get("primary_team") or ""
        secondary = [team_names.get(k, k) for k in m.get("secondary_teams") or []]
        rows.append([
            str(m.get("user_id", "")),
            str(m.get("display_name", "")),
            team_names.get(primary, primary),
            ", ".join(secondary),
            "1" if m.get("is_leader") else "0",
            ", ".join(m.get("skills") or []),
            str(m.get("joined_at", "")),
        ])
    return rows


class SheetsExporter:
    """
    gspread を使った書き込み側の薄いラッパー。

    client を注入すると gspread なしでテストできる
    （client は open_by_key(spreadsheet_id) -> book を持つオブジェクト。
     book.worksheet(title) / book.add_worksheet(title, rows, cols) と
     worksheet.clear() / worksheet.update(values) を使う）。
    """

    def __init__(self, credentials_path: str | None = None, client: Any = None):
        self._credentials_path = credentials_path
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        creds_path = (self._credentials_path or "").strip()
        if not creds_path or not os.path.exists(creds_path):
            raise SheetsUnavailable(
                "GOOGLE_CREDENTIALS_PATH（サービスアカウント JSON）が未設定です。")
        try:
            import gspread  # noqa: PLC0415
            from google.oauth2.service_account import Credentials  # noqa: PLC0415
        except ImportError as e:
            raise SheetsUnavailable(
                "gspread / google-auth が見つかりません。"
                "`pip install -r requirements.txt` を実行してください。") from e
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        self._client = gspread.authorize(
            Credentials.from_service_account_file(creds_path, scopes=scopes))
        return self._client

    def write_book(self, spreadsheet_id: str,
                   sheets: dict[str, list[list[str]]]) -> None:
        """
        ブックの各シートを header+rows で上書きする。
        シートが無ければ作成する。API エラーは呼び出し側で捕捉する。
        """
        book = self._get_client().open_by_key(spreadsheet_id)
        for title, values in sheets.items():
            try:
                ws = book.worksheet(title)
            except Exception:  # noqa: BLE001  (gspread.WorksheetNotFound 等)
                ws = book.add_worksheet(
                    title=title,
                    rows=max(len(values), 1),
                    cols=max(len(values[0]), 1) if values else 1)
            ws.clear()
            if values:
                ws.update(values)


async def sync_guild(db: Database, guild_id: int,
                     exporter: SheetsExporter) -> dict[str, Any] | None:
    """
    1ギルド分のタスク・メンバー一覧を Sheets へエクスポートする。

    スプレッドシート ID 未設定のギルドは自動スキップし None を返す
    （例外にはしない）。設定済みなら件数を含む dict を返す。
    """
    spreadsheet_id = await get_spreadsheet_id(db, guild_id)
    if spreadsheet_id is None:
        return None

    tasks = await TaskRepository(db).list_all_for_export(guild_id)
    members = await MemberRepository(db).list_members(guild_id)
    team_names = await team_service.team_name_map(db, guild_id)

    exporter.write_book(spreadsheet_id, {
        TASKS_SHEET: [TASKS_HEADER] + build_tasks_rows(tasks, team_names),
        MEMBERS_SHEET: [MEMBERS_HEADER] + build_members_rows(members, team_names),
    })
    log.info("Sheets エクスポート完了 (guild=%s): tasks=%d, members=%d",
             guild_id, len(tasks), len(members))
    return {"spreadsheet_id": spreadsheet_id,
            "tasks": len(tasks), "members": len(members)}
