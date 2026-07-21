"""guilds テーブル（ギルド台帳）の CRUD。

参加中ギルドの登録簿。新規ギルド参加時・起動時の自動セットアップで
冪等に登録・名称更新される。guild_id がそのまま PK。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class GuildRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def ensure(self, guild_id: int, guild_name: str) -> None:
        """ギルドを台帳へ冪等登録する（既存なら名称のみ更新）。"""
        await self.db.execute(
            """
            INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version)
            VALUES (?, ?, ?, 2)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name = excluded.guild_name
            """,
            (guild_id, guild_name, to_iso(now())),
        )

    async def get(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        return dict(row) if row else None

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM guilds ORDER BY joined_at")
        return [dict(r) for r in rows]
