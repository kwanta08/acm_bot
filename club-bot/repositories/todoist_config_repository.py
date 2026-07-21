"""todoist_configs テーブル（Todoist 接続設定）の CRUD。

1ギルド1件（PK = guild_id）。api_token_encrypted は Fernet 暗号文であり、
この層では復号しない（復号は services/todoist_service.py で
API 呼び出しの都度行う）。平文トークンをこの層で扱ってはならない。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class TodoistConfigRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def get(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM todoist_configs WHERE guild_id = ?", (guild_id,))
        return dict(row) if row else None

    async def upsert(self, guild_id: int, api_token_encrypted: str,
                     project_id: str | None, today_label_name: str,
                     actor_id: str) -> None:
        """設定を登録・更新する。api_token_encrypted は暗号文を渡すこと。"""
        now_iso = to_iso(now())
        await self.db.execute(
            """
            INSERT INTO todoist_configs
                (guild_id, api_token_encrypted, project_id, today_label_name,
                 enabled_flag, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                api_token_encrypted = excluded.api_token_encrypted,
                project_id = excluded.project_id,
                today_label_name = excluded.today_label_name,
                enabled_flag = 1,
                updated_at = excluded.updated_at
            """,
            (guild_id, api_token_encrypted, project_id, today_label_name,
             actor_id, now_iso, now_iso),
        )

    async def delete(self, guild_id: int) -> bool:
        """設定を削除する。対象が無ければ False。"""
        cur = await self.db.execute(
            "DELETE FROM todoist_configs WHERE guild_id = ?", (guild_id,))
        return cur.rowcount > 0
