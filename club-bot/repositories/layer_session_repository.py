"""layer_sessions テーブルの CRUD（仕様 10.8, 11.8.7）。"""
from __future__ import annotations

from typing import Any

from utils.db import Database


class LayerSessionRepository:
    def __init__(self, db: Database):
        self.db = db

    async def get_by_user(self, user_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM layer_sessions WHERE user_id = ?", (user_id,))
        return dict(row) if row else None

    async def start(self, user_id: str, keta: str, layer_num: int, started_at: str) -> None:
        """1人1セッション。既存があれば UNIQUE 制約で衝突する想定（呼び出し側で事前確認）。"""
        await self.db.execute(
            """
            INSERT INTO layer_sessions (user_id, keta, layer_num, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, keta, layer_num, started_at),
        )

    async def end(self, user_id: str) -> None:
        await self.db.execute(
            "DELETE FROM layer_sessions WHERE user_id = ?", (user_id,))

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM layer_sessions ORDER BY started_at")
        return [dict(r) for r in rows]
