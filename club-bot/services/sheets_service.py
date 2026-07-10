"""
Google Sheets 同期サービス（仕様 11.7, 11.8.6, 11.2）。

gspread + サービスアカウント認証。全行置換を基本とし、監査ログのみ append。
レート制限（1分60req）に配慮しシートごとにウェイトを挟む。
Sheets 無効時（credentials または spreadsheet_id 未設定）は no-op。
"""
from __future__ import annotations

import asyncio
from typing import Any

from config import config
from utils.logger import get_logger

log = get_logger("sheets")

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None  # type: ignore
    Credentials = None  # type: ignore

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LAYER_HEADER = ["層番号", "作業者", "開始時刻", "終了時刻", "作業時間(分)"]
SCHEDULE_HEADER = ["候補日時", "参加", "未定", "不参加", "未回答"]


class SheetsError(Exception):
    pass


class SheetsService:
    def __init__(self):
        self.enabled = config.sheets_enabled() and gspread is not None
        self._client = None
        self._syncing = False  # 同期中フラグ（仕様 11.7.3 二重書き込み防止）

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.enabled:
            raise SheetsError("Google Sheets が無効です（credentials または SPREADSHEET_ID 未設定）")
        creds = Credentials.from_service_account_file(
            config.google_credentials_path, scopes=SCOPES)
        self._client = gspread.authorize(creds)
        return self._client

    async def _run(self, fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.error("Sheets API 失敗: %s", e)
            raise SheetsError(str(e)) from e

    def _open_book(self, spreadsheet_id: str):
        client = self._ensure_client()
        return client.open_by_key(spreadsheet_id)

    def _get_or_create_ws(self, book, name: str, header: list[str] | None = None,
                          rows: int = 1000, cols: int = 26):
        try:
            ws = book.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = book.add_worksheet(title=name, rows=rows, cols=cols)
            if header:
                ws.append_row(header, value_input_option="USER_ENTERED")
        return ws

    # ---------- 全行置換 ----------
    def _replace_all_sync(self, spreadsheet_id: str, sheet_name: str,
                          header: list[str], rows: list[list[Any]]):
        book = self._open_book(spreadsheet_id)
        ws = self._get_or_create_ws(book, sheet_name, header)
        ws.clear()
        values = [header] + rows
        ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
        return len(rows)

    async def replace_all(self, sheet_name: str, header: list[str],
                          rows: list[list[Any]]) -> int:
        """シート全体を上書きする。書き込み行数を返す。"""
        if not self.enabled:
            return 0
        count = await self._run(self._replace_all_sync,
                                config.spreadsheet_id, sheet_name, header, rows)
        await asyncio.sleep(1.2)  # レート制限配慮
        return count

    # ---------- append（監査ログ）----------
    def _append_sync(self, spreadsheet_id: str, sheet_name: str,
                     header: list[str], row: list[Any]):
        book = self._open_book(spreadsheet_id)
        ws = self._get_or_create_ws(book, sheet_name, header)
        ws.append_row(row, value_input_option="USER_ENTERED")

    async def append_row(self, sheet_name: str, header: list[str], row: list[Any]) -> None:
        if not self.enabled:
            return
        await self._run(self._append_sync, config.spreadsheet_id, sheet_name, header, row)
        await asyncio.sleep(0.5)

    # ---------- 桁巻き追記（仕様 11.8.6）----------
    def _append_layer_sync(self, keta: str, row: list[Any]):
        spreadsheet_id = config.effective_layer_spreadsheet_id
        if not spreadsheet_id:
            raise SheetsError("LAYER_SPREADSHEET_ID / SPREADSHEET_ID が未設定です")
        book = self._open_book(spreadsheet_id)
        try:
            sheet = book.worksheet(keta)
        except gspread.WorksheetNotFound:
            sheet = book.add_worksheet(title=keta, rows=1000, cols=6)
            sheet.append_row(LAYER_HEADER, value_input_option="USER_ENTERED")
        sheet.append_row(row, value_input_option="USER_ENTERED")

    async def append_layer_row(self, keta: str, row: list[Any]) -> None:
        """桁名に対応するシートへ1行追記。無ければヘッダー付きで自動作成。"""
        if not self.enabled:
            raise SheetsError("Google Sheets が無効です")
        await self._run(self._append_layer_sync, keta, row)

    # ---------- スケジュール専用シート（仕様 11.2）----------
    def _resolve_sheet_title(self, book, base_title: str) -> str:
        """既存シート名と重複しない名前を返す。重複時は (1),(2)... と付加する。"""
        existing = {ws.title for ws in book.worksheets()}
        if base_title not in existing:
            return base_title
        i = 1
        while f"{base_title}({i})" in existing:
            i += 1
        return f"{base_title}({i})"

    def _create_schedule_sheet_sync(self, title: str, options: list[dict], votes_map: dict):
        spreadsheet_id = config.schedule_spreadsheet_id
        if not spreadsheet_id:
            raise SheetsError("SCHEDULE_SPREADSHEET_ID が未設定です")

        book = self._open_book(spreadsheet_id)
        sheet_title = self._resolve_sheet_title(book, title)

        ws = book.add_worksheet(title=sheet_title, rows=500, cols=10)
        ws.append_row(SCHEDULE_HEADER, value_input_option="USER_ENTERED")

        for opt in options:
            label = opt["label"]
            v = votes_map.get(opt["option_id"],
                              {"ok": [], "maybe": [], "ng": [], "unanswered": []})
            row = [
                label,
                "\n".join(v.get("ok", [])),
                "\n".join(v.get("maybe", [])),
                "\n".join(v.get("ng", [])),
                "\n".join(v.get("unanswered", [])),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")

        return sheet_title

    async def create_schedule_sheet(self, title: str, options: list[dict],
                                    votes_map: dict) -> str:
        """スケジュール専用 SS にシートを作成し、確定したシート名を返す。"""
        if not config.schedule_sheets_enabled():
            return title
        sheet_title = await self._run(
            self._create_schedule_sheet_sync, title, options, votes_map)
        await asyncio.sleep(1.2)
        return sheet_title

    def _update_schedule_sheet_sync(self, sheet_title: str, options: list[dict], votes_map: dict):
        """既存シートをタイトルで特定して出欠を上書きする（締切時用）。"""
        spreadsheet_id = config.schedule_spreadsheet_id
        if not spreadsheet_id:
            raise SheetsError("SCHEDULE_SPREADSHEET_ID が未設定です")
        book = self._open_book(spreadsheet_id)
        try:
            ws = book.worksheet(sheet_title)
        except gspread.WorksheetNotFound:
            ws = book.add_worksheet(title=sheet_title, rows=500, cols=10)

        ws.clear()
        ws.append_row(SCHEDULE_HEADER, value_input_option="USER_ENTERED")
        for opt in options:
            label = opt["label"]
            v = votes_map.get(opt["option_id"],
                              {"ok": [], "maybe": [], "ng": [], "unanswered": []})
            row = [
                label,
                "\n".join(v.get("ok", [])),
                "\n".join(v.get("maybe", [])),
                "\n".join(v.get("ng", [])),
                "\n".join(v.get("unanswered", [])),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")

    async def update_schedule_sheet(self, sheet_title: str, options: list[dict],
                                    votes_map: dict) -> None:
        """既存シートを最終出欠で上書きする（締切時）。"""
        if not config.schedule_sheets_enabled():
            return
        await self._run(self._update_schedule_sheet_sync, sheet_title, options, votes_map)
        await asyncio.sleep(1.2)

    # ---------- 同期中フラグ（既存機能・削除しないこと）----------
    def begin_sync(self) -> bool:
        if self._syncing:
            return False
        self._syncing = True
        return True

    def end_sync(self) -> None:
        self._syncing = False