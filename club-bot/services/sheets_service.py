"""
Google Sheets 同期サービス（改訂版）

gspread + サービスアカウント認証。全行置換を基本とし、検索ロボットのみ append。
レート制限（1分60req）に配慮しシートごとにウェイトを挟む。
Sheets 無効時（credentials または spreadsheet_id 未設定）は no-op。
（改訂版: 設定再読み込みメソッドを追加）
"""
from __future__ import annotations

import asyncio
import time
import random
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

LAYER_HEADER = ["層塗り番号", "作業者", "開始時刻", "終了時刻", "作業時間(分)"]
SCHEDULE_HEADER = ["開催日時", "参加", "不参加", "未定", "未回答"]


class SheetsError(Exception):
    pass


class RateLimiter:
    """レートリミッター（1分あたり max_per_minute 回）"""

    def __init__(self, max_per_minute: int = 60):
        self.max_per_minute = max_per_minute
        self.tokens = max_per_minute
        self.updated_at = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.updated_at
            # 1分経過でトークンをリセット
            if elapsed >= 60:
                self.tokens = self.max_per_minute
                self.updated_at = now
            # トークンがあれば消費
            if self.tokens > 0:
                self.tokens -= 1
                return
            # トークンがなければ待機
            wait_time = 60 - elapsed
            await asyncio.sleep(wait_time)
            self.tokens = self.max_per_minute - 1
            self.updated_at = time.monotonic()


class SheetsService:
    def __init__(self):
        self.reload_config()
        self._client = None
        self._syncing = False  # 同期フラグ（改訂版 11.7.3 二重書き込み防止）
        self._rate_limiter = RateLimiter(max_per_minute=50)

    def reload_config(self) -> None:
        """config から設定を再読み込みする"""
        self.enabled = config.sheets_enabled() and gspread is not None
        self._client = None  # クライアントをリセット

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
        await self._rate_limiter.acquire()
        max_retries = 5
        for attempt in range(max_retries):
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait = (2 ** attempt) + random.uniform(0, 1)
                log.warning("Sheets API [33mretry %d/%d after %.1fs: %s",
                           attempt + 1, max_retries, wait, e)
                await asyncio.sleep(wait)

    async def get_sheet(self, sheet_name: str) -> Any:
        """シートを取得する"""
        try:
            client = self._ensure_client()
            spreadsheet = client.open_by_key(config.spreadsheet_id)
            return spreadsheet.worksheet(sheet_name)
        except Exception as e:
            log.error("シート取得失敗: %s", e)
            raise SheetsError(f"シート取得失敗: {e}")

    async def replace_all(self, sheet_name: str, header: list[str], rows: list[list[Any]]) -> int:
        """
        シートを全行置換する（ヘッダー行含む）
        """
        if not self.enabled:
            return 0
        try:
            sheet = await self.get_sheet(sheet_name)
            # 既存データをクリア
            await self._run(sheet.clear)
            # ヘッダーを書き込み
            await self._run(sheet.append_row, header)
            # データを書き込み
            for row in rows:
                await self._run(sheet.append_row, row)
            return len(rows)
        except Exception as e:
            log.error("シート全行置換失敗 (%s): %s", sheet_name, e)
            raise SheetsError(f"シート全行置換失敗: {e}")

    async def append_row(self, sheet_name: str, row: list[Any]) -> None:
        """シートに1行追加する"""
        if not self.enabled:
            return
        try:
            sheet = await self.get_sheet(sheet_name)
            await self._run(sheet.append_row, row)
        except Exception as e:
            log.error("行追加失敗 (%s): %s", sheet_name, e)
            raise SheetsError(f"行追加失敗: {e}")

    async def get_all_values(self, sheet_name: str) -> list[list[Any]]:
        """シートの全ての値を取得する"""
        if not self.enabled:
            return []
        try:
            sheet = await self.get_sheet(sheet_name)
            return await self._run(sheet.get_all_values)
        except Exception as e:
            log.error("値取得失敗 (%s): %s", sheet_name, e)
            raise SheetsError(f"値取得失敗: {e}")

    async def update_cell(self, sheet_name: str, cell: str, value: Any) -> None:
        """セルを更新する"""
        if not self.enabled:
            return
        try:
            sheet = await self.get_sheet(sheet_name)
            await self._run(sheet.update, cell, value)
        except Exception as e:
            log.error("セル更新失敗 (%s, %s): %s", sheet_name, cell, e)
            raise SheetsError(f"セル更新失敗: {e}")

    async def batch_update(self, sheet_name: str, data: dict[str, Any]) -> None:
        """バッチ更新する"""
        if not self.enabled:
            return
        try:
            sheet = await self.get_sheet(sheet_name)
            await self._run(sheet.batch_update, [({"range": k, "values": [v]} if isinstance(v, list) else {"range": k, "values": [[v]]} ) for k, v in data.items()])
        except Exception as e:
            log.error("バッチ更新失敗 (%s): %s", sheet_name, e)
            raise SheetsError(f"バッチ更新失敗: {e}")
