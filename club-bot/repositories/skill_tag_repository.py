"""skill_tags テーブル（技能タグ マスタ）の CRUD。

技能タグはギルド単位で管理し、タグ名は (guild_id, skill_name) で一意。
削除は論理削除（active_flag=0）とし、付与済みメンバーの技能表示を壊さない。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class SkillTagRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def add(self, guild_id: int, skill_name: str, created_by: str) -> None:
        """技能タグを登録する（無効化済みの同名タグは再有効化される）。"""
        await self.db.execute(
            """
            INSERT INTO skill_tags (guild_id, skill_name, active_flag, created_by, created_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, skill_name) DO UPDATE SET active_flag = 1
            """,
            (guild_id, skill_name, created_by, to_iso(now())),
        )

    async def deactivate(self, guild_id: int, skill_name: str) -> bool:
        """技能タグを無効化する。対象が無ければ False。"""
        cur = await self.db.execute(
            "UPDATE skill_tags SET active_flag = 0"
            " WHERE guild_id = ? AND skill_name = ? AND active_flag = 1",
            (guild_id, skill_name))
        return cur.rowcount > 0

    async def get(self, guild_id: int, skill_name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM skill_tags WHERE guild_id = ? AND skill_name = ?",
            (guild_id, skill_name))
        return dict(row) if row else None

    async def exists_active(self, guild_id: int, skill_name: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM skill_tags WHERE guild_id = ? AND skill_name = ? AND active_flag = 1",
            (guild_id, skill_name))
        return row is not None

    async def list_active(self, guild_id: int) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT skill_name FROM skill_tags WHERE guild_id = ? AND active_flag = 1"
            " ORDER BY skill_name",
            (guild_id,))
        return [r["skill_name"] for r in rows]

    async def list_all(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM skill_tags WHERE guild_id = ?"
            " ORDER BY active_flag DESC, skill_name",
            (guild_id,))
        return [dict(r) for r in rows]
