"""todoist_sections テーブルの CRUD（マルチテナント版）。

Todoist のセクション（section_id）と班（team_key）の対応表を管理する。
班別チャンネル通知で、どのセクションをどの班チャンネルへ送るか決めるのに使う。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class SectionRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def link(self, guild_id: int, section_id: str, team_key: str,
                   section_name: str | None = None) -> None:
        """セクションと班を紐付け登録（既存なら上書き）。"""
        await self.db.execute(
            """
            INSERT INTO todoist_sections (guild_id, section_id, team_key, section_name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, section_id) DO UPDATE SET
                team_key = excluded.team_key,
                section_name = excluded.section_name,
                updated_at = excluded.updated_at
            """,
            (guild_id, str(section_id), team_key, section_name, to_iso(now())),
        )

    async def unlink(self, guild_id: int, section_id: str) -> None:
        await self.db.execute(
            "DELETE FROM todoist_sections WHERE guild_id = ? AND section_id = ?",
            (guild_id, str(section_id)))

    async def list_links(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM todoist_sections WHERE guild_id = ? ORDER BY team_key",
            (guild_id,))
        return [dict(r) for r in rows]

    async def get_by_section(self, guild_id: int, section_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM todoist_sections WHERE guild_id = ? AND section_id = ?",
            (guild_id, str(section_id)))
        return dict(row) if row else None

    async def team_for_section(self, guild_id: int, section_id: str) -> str | None:
        row = await self.get_by_section(guild_id, section_id)
        return row["team_key"] if row else None
