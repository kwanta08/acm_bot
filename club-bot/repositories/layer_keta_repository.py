"""layer_keta テーブルの CRUD（桁名マスタ管理）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
桁名は (guild_id, keta_name) で一意。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database


class LayerKetaRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def add(self, guild_id: int, keta_name: str, created_by: str, created_at: str) -> None:
        await self.db.execute(
            """
            INSERT INTO layer_keta (guild_id, keta_name, active_flag, created_by, created_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, keta_name) DO UPDATE SET active_flag = 1
            """,
            (guild_id, keta_name, created_by, created_at),
        )

    async def deactivate(self, guild_id: int, keta_name: str) -> bool:
        cur = await self.db.execute(
            "UPDATE layer_keta SET active_flag = 0"
            " WHERE guild_id = ? AND keta_name = ? AND active_flag = 1",
            (guild_id, keta_name))
        return cur.rowcount > 0

    async def exists_active(self, guild_id: int, keta_name: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM layer_keta WHERE guild_id = ? AND keta_name = ? AND active_flag = 1",
            (guild_id, keta_name))
        return row is not None

    async def list_active(self, guild_id: int) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT keta_name FROM layer_keta WHERE guild_id = ? AND active_flag = 1"
            " ORDER BY keta_name",
            (guild_id,))
        return [r["keta_name"] for r in rows]

    async def list_all(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM layer_keta WHERE guild_id = ? ORDER BY active_flag DESC, keta_name",
            (guild_id,))
        return [dict(r) for r in rows]
