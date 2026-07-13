"""layer_keta テーブルの CRUD（桁名マスタ管理）。"""
from __future__ import annotations

from typing import Any

from utils.db import Database


class LayerKetaRepository:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, keta_name: str, created_by: str, created_at: str) -> None:
        await self.db.execute(
            """
            INSERT INTO layer_keta (keta_name, active_flag, created_by, created_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(keta_name) DO UPDATE SET active_flag = 1
            """,
            (keta_name, created_by, created_at),
        )

    async def deactivate(self, keta_name: str) -> bool:
        cur = await self.db.execute(
            "UPDATE layer_keta SET active_flag = 0 WHERE keta_name = ? AND active_flag = 1",
            (keta_name,))
        return cur.rowcount > 0

    async def exists_active(self, keta_name: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM layer_keta WHERE keta_name = ? AND active_flag = 1", (keta_name,))
        return row is not None

    async def list_active(self) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT keta_name FROM layer_keta WHERE active_flag = 1 ORDER BY keta_name")
        return [r["keta_name"] for r in rows]

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM layer_keta ORDER BY active_flag DESC, keta_name")
        return [dict(r) for r in rows]