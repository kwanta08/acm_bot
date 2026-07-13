"""schedules / schedule_options / schedule_votes の CRUD（仕様 10.3〜10.5）。"""
from __future__ import annotations

from typing import Any

from utils.db import Database
from utils.parser import now, to_iso


class ScheduleRepository:
    def __init__(self, db: Database):
        self.db = db

    # ---------- schedules ----------
    async def create_schedule(self, schedule_id: str, title: str, description: str | None,
                              place: str | None, target_role_id: str | None,
                              deadline_iso: str, created_by: str, channel_id: str) -> None:
        await self.db.execute(
            """
            INSERT INTO schedules
                (schedule_id, title, description, place, target_role_id, deadline,
                 created_by, channel_id, closed_flag, reminder_sent_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (schedule_id, title, description, place, target_role_id,
             deadline_iso, created_by, channel_id),
        )

    async def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,))
        return dict(row) if row else None

    async def list_open_schedules(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE closed_flag = 0 ORDER BY deadline")
        return [dict(r) for r in rows]

    async def list_due_schedules(self, deadline_iso: str) -> list[dict[str, Any]]:
        """締切を過ぎた未クローズの投票を返す。"""
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE closed_flag = 0 AND deadline <= ?",
            (deadline_iso,))
        return [dict(r) for r in rows]

    async def list_reminder_candidates(self, from_iso: str, to_iso_: str) -> list[dict[str, Any]]:
        """締切が [from, to] にあり、まだ催促未送信の投票を返す。"""
        rows = await self.db.fetchall(
            """
            SELECT * FROM schedules
            WHERE closed_flag = 0 AND reminder_sent_flag = 0
              AND deadline >= ? AND deadline <= ?
            """,
            (from_iso, to_iso_))
        return [dict(r) for r in rows]

    async def close_schedule(self, schedule_id: str) -> None:
        await self.db.execute(
            "UPDATE schedules SET closed_flag = 1 WHERE schedule_id = ?", (schedule_id,))

    async def mark_reminder_sent(self, schedule_id: str) -> None:
        await self.db.execute(
            "UPDATE schedules SET reminder_sent_flag = 1 WHERE schedule_id = ?", (schedule_id,))

    async def delete_schedule(self, schedule_id: str) -> None:
        await self.db.execute(
            "DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))

    # ---------- options ----------
    async def add_option(self, option_id: str, schedule_id: str, label: str,
                         start_at: str, end_at: str | None, message_id: str | None) -> None:
        await self.db.execute(
            """
            INSERT INTO schedule_options
                (option_id, schedule_id, label, start_at, end_at, message_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (option_id, schedule_id, label, start_at, end_at, message_id),
        )

    async def set_option_message(self, option_id: str, message_id: str) -> None:
        await self.db.execute(
            "UPDATE schedule_options SET message_id = ? WHERE option_id = ?",
            (message_id, option_id))

    async def list_options(self, schedule_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedule_options WHERE schedule_id = ? ORDER BY start_at",
            (schedule_id,))
        return [dict(r) for r in rows]

    async def get_option_by_message(self, message_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM schedule_options WHERE message_id = ?", (message_id,))
        return dict(row) if row else None

    # ---------- votes ----------
    async def set_vote(self, option_id: str, user_id: str, status: str) -> None:
        """1候補1ユーザー1状態（仕様 11.2.3）。upsert。"""
        await self.db.execute(
            """
            INSERT INTO schedule_votes (option_id, user_id, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(option_id, user_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (option_id, user_id, status, to_iso(now())),
        )

    async def remove_vote(self, option_id: str, user_id: str) -> None:
        await self.db.execute(
            "DELETE FROM schedule_votes WHERE option_id = ? AND user_id = ?",
            (option_id, user_id))

    async def list_votes(self, option_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedule_votes WHERE option_id = ?", (option_id,))
        return [dict(r) for r in rows]

    async def list_voters_for_schedule(self, schedule_id: str) -> set[str]:
        """投票内のいずれかの候補に1票でも入れたユーザー ID 集合。"""
        rows = await self.db.fetchall(
            """
            SELECT DISTINCT v.user_id
            FROM schedule_votes v
            JOIN schedule_options o ON v.option_id = o.option_id
            WHERE o.schedule_id = ?
            """,
            (schedule_id,))
        return {r["user_id"] for r in rows}
    
    async def set_schedule_sheet_title(self, schedule_id: str, sheet_title: str):
        await self.db.execute(
            "UPDATE schedules SET sheet_title = ? WHERE schedule_id = ?",
            (sheet_title, schedule_id),
        )
