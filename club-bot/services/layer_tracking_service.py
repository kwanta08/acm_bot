"""
桁巻き積層作業の開始/終了ロジック（仕様 11.8）。

進行中セッションと完了記録は SQLite（正本）に永続化する。
Google Sheets 連携は廃止され、記録の参照は DB（NocoDB）から行う。
"""
from __future__ import annotations

from datetime import datetime

from repositories.layer_session_repository import LayerSessionRepository
from utils.parser import from_iso, now, to_iso


class LayerTrackingService:
    def __init__(self, session_repo: LayerSessionRepository):
        self.session_repo = session_repo

    async def has_active(self, user_id: str) -> bool:
        return await self.session_repo.get_by_user(user_id) is not None

    async def start(self, user_id: str, keta: str, layer_num: str) -> datetime:
        """開始を記録。開始時刻を返す。"""
        started = now()
        await self.session_repo.start(user_id, keta, layer_num, to_iso(started))
        return started

    async def end(self, user_id: str, display_name: str) -> dict:
        """
        進行中セッションを終了し、DBへ記録を保存する。
        戻り値: {keta, layer_num, minutes, started, ended}
        呼び出し側で事前にセッション存在を確認すること。
        """
        session = await self.session_repo.get_by_user(user_id)
        if session is None:
            raise ValueError("進行中セッションがありません。")

        started = from_iso(session["started_at"])
        ended = now()
        minutes = int((ended - started).total_seconds() // 60)  # 端数切り捨て（仕様 11.8.4）

        # 記録をDBへ保存し、進行中セッションを削除（仕様 11.8.5）
        record_id = await self.session_repo.add_record(
            user_id, session["keta"], session["layer_num"],
            session["started_at"], to_iso(ended), minutes)
        await self.session_repo.end(user_id)
        # 外部同期先（Sheets）は廃止されたため、保存時点で同期済み扱いにする
        await self.session_repo.mark_synced(record_id)

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
