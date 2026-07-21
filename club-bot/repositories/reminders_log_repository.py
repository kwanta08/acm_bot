"""reminders_log テーブル（通知ログ）の CRUD。

これまで Cog（cogs/reminders.py, cogs/reports.py）が bot.db に直接
発行していた SQL を Repository 層に集約する（設計書 R7）。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class RemindersLogRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def add(self, guild_id: int, reminder_type: str, target_id: str,
                  target_user_id: str | None, sent_channel_id: str | None,
                  status: str, error_message: str | None = None) -> int:
        """通知履歴を記録する。戻り値は reminder_id。"""
        cur = await self.db.execute(
            """
            INSERT INTO reminders_log
                (guild_id, reminder_type, target_id, target_user_id, sent_channel_id,
                 sent_at, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, reminder_type, target_id, target_user_id, sent_channel_id,
             to_iso(now()), status, error_message),
        )
        return cur.lastrowid

    async def list_recent(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """指定ギルドの直近ログを新しい順に返す。"""
        rows = await self.db.fetchall(
            "SELECT * FROM reminders_log WHERE guild_id = ?"
            " ORDER BY reminder_id DESC LIMIT ?",
            (guild_id, limit))
        return [dict(r) for r in rows]
