"""
桁巻き積層作業の開始/終了ロジック（仕様 11.8）。

SQLite に進行中セッションを永続化し、Sheets の桁ごとシートへ追記する。
"""
from __future__ import annotations

from datetime import datetime

from repositories.layer_session_repository import LayerSessionRepository
from services.sheets_service import SheetsService
from utils.parser import fmt_sheet, from_iso, now, to_iso


class LayerTrackingService:
    def __init__(self, session_repo: LayerSessionRepository, sheets: SheetsService):
        self.session_repo = session_repo
        self.sheets = sheets

    async def has_active(self, user_id: str) -> bool:
        return await self.session_repo.get_by_user(user_id) is not None

    async def start(self, user_id: str, keta: str, layer_num: int) -> datetime:
        """開始を記録。開始時刻を返す。"""
        started = now()
        await self.session_repo.start(user_id, keta, layer_num, to_iso(started))
        return started

    async def end(self, user_id: str, display_name: str) -> dict:
        """
        進行中セッションを終了し Sheets へ追記する。
        戻り値: {keta, layer_num, minutes, started, ended}
        呼び出し側で事前にセッション存在を確認すること。
        """
        session = await self.session_repo.get_by_user(user_id)
        if session is None:
            raise ValueError("進行中セッションがありません。")

        started = from_iso(session["started_at"])
        ended = now()
        minutes = int((ended - started).total_seconds() // 60)  # 端数切り捨て（仕様 11.8.4）

        row = [
            session["layer_num"],          # A 層番号
            display_name,                  # B 作業者
            fmt_sheet(started),            # C 開始時刻
            fmt_sheet(ended),              # D 終了時刻
            minutes,                       # E 作業時間(分)
        ]
        # Sheets 書き込み（失敗時は例外を上位へ。セッションは保持したまま）
        await self.sheets.append_layer_row(session["keta"], row)

        # 書き込み成功後にセッション削除（仕様 11.8.5）
        await self.session_repo.end(user_id)
        return {
            "keta": session["keta"],
            "layer_num": session["layer_num"],
            "minutes": minutes,
            "started": started,
            "ended": ended,
        }

    async def list_active(self) -> list[dict]:
        sessions = await self.session_repo.list_all()
        current = now()
        out = []
        for s in sessions:
            started = from_iso(s["started_at"])
            elapsed = int((current - started).total_seconds() // 60)
            out.append({
                "user_id": s["user_id"],
                "keta": s["keta"],
                "layer_num": s["layer_num"],
                "started": started,
                "elapsed_min": elapsed,
            })
        return out
