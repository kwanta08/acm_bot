"""todoist_sections テーブルの CRUD。

Todoist のセクション（section_id）と班（team_key）の対応表を管理する。
班別チャンネル通知で、どのセクションをどの班チャンネルへ送るか決めるのに使う。
"""
from __future__ import annotations

from typing import Any

from utils.db import Database
from utils.parser import now, to_iso


class SectionRepository:
    def __init__(self, db: Database):
        self.db = db

    async def link(self, section_id: str, team_key: str,
                   section_name: str | None = None) -> None:
        """セクションと班を紐付け登録（既存なら上書き）。"""
        await self.db.execute(
            """
            INSERT INTO todoist_sections (section_id, team_key, section_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(section_id) DO UPDATE SET
                team_key = excluded.team_key,
                section_name = excluded.section_name,
                updated_at = excluded.updated_at
            """,
            (str(section_id), team_key, section_name, to_iso(now())),
        )

    async def unlink(self, section_id: str) -> None:
        await self.db.execute(
            "DELETE FROM todoist_sections WHERE section_id = ?", (str(section_id),))

    async def list_links(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM todoist_sections ORDER BY team_key")
        return [dict(r) for r in rows]

    async def get_by_section(self, section_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM todoist_sections WHERE section_id = ?", (str(section_id),))
        return dict(row) if row else None

    async def team_for_section(self, section_id: str) -> str | None:
        row = await self.get_by_section(section_id)
        return row["team_key"] if row else None
