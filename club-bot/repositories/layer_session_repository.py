"""layer_sessions / layer_records テーブルの CRUD（仕様 10.8, 11.8.7）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
layer_sessions は (guild_id, user_id) で1人1セッションを保証する。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database


class LayerSessionRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    # ---------- 進行中セッション ----------
    async def get_by_user(self, guild_id: int, user_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM layer_sessions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id))
        return dict(row) if row else None

    async def start(self, guild_id: int, user_id: str, keta: str, layer_num: str,
                    started_at: str) -> None:
        """1人1セッション。既存があれば UNIQUE 制約で衝突する想定（呼び出し側で事前確認）。"""
        await self.db.execute(
            """
            INSERT INTO layer_sessions (guild_id, user_id, keta, layer_num, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, keta, layer_num, started_at),
        )

    async def end(self, guild_id: int, user_id: str) -> None:
        """進行中セッションを削除する。"""
        await self.db.execute(
            "DELETE FROM layer_sessions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id))

    async def list_all(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM layer_sessions WHERE guild_id = ? ORDER BY started_at",
            (guild_id,))
        return [dict(r) for r in rows]

    # ---------- 完了記録（Sheets 同期用） ----------
    async def add_record(self, guild_id: int, user_id: str, keta: str, layer_num: str,
                         started_at: str, ended_at: str, minutes: int) -> int:
        """完了記録をDBへ保存。戻り値は record_id。"""
        cur = await self.db.execute(
            """
            INSERT INTO layer_records
                (guild_id, user_id, keta, layer_num, started_at, ended_at, minutes, synced_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (guild_id, user_id, keta, layer_num, started_at, ended_at, minutes),
        )
        return cur.lastrowid

    async def mark_synced(self, guild_id: int, record_id: int) -> None:
        await self.db.execute(
            "UPDATE layer_records SET synced_flag = 1 WHERE guild_id = ? AND record_id = ?",
            (guild_id, record_id))

    async def list_unsynced(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM layer_records WHERE guild_id = ? AND synced_flag = 0 ORDER BY record_id",
            (guild_id,))
        return [dict(r) for r in rows]
