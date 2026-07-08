"""
Google Sheets 同期サービス（仕様 11.7, 11.8.6）。

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

    # ---------- 同期中フラグ ----------
    def begin_sync(self) -> bool:
        if self._syncing:
            return False
        self._syncing = True
        return True

    def end_sync(self) -> None:
        self._syncing = False
