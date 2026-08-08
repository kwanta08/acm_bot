"""guild_directus_access テーブル（Directus アクセス発行状況）の CRUD。

1ギルド1件（PK = guild_id）。このテーブルは秘密情報を保持しない:
パスワードは Directus 自身のメール招待フローが扱い、bot は生成も保存も
しない。directus_user_id は Directus 側のユーザー識別子であり、
これ単体では認証に使えない。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso

# status カラムの取りうる値
STATUS_INVITED = "invited"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"


class DirectusAccessRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def get(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM guild_directus_access WHERE guild_id = ?",
            (guild_id,))
        return dict(row) if row else None

    async def upsert(self, guild_id: int, directus_user_id: str, email: str,
                     actor_id: str, status: str = STATUS_INVITED) -> None:
        """発行状況を登録・更新する（再発行時は上書き）。"""
        now_iso = to_iso(now())
        await self.db.execute(
            """
            INSERT INTO guild_directus_access
                (guild_id, directus_user_id, email, status,
                 created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                directus_user_id = excluded.directus_user_id,
                email = excluded.email,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (guild_id, directus_user_id, email, status, actor_id,
             now_iso, now_iso),
        )

    async def set_status(self, guild_id: int, status: str) -> bool:
        """発行状態を更新する。対象が無ければ False。"""
        cur = await self.db.execute(
            "UPDATE guild_directus_access SET status = ?, updated_at = ?"
            " WHERE guild_id = ?",
            (status, to_iso(now()), guild_id),
        )
        return cur.rowcount > 0

    async def delete(self, guild_id: int) -> bool:
        """発行記録を削除する。対象が無ければ False。"""
        cur = await self.db.execute(
            "DELETE FROM guild_directus_access WHERE guild_id = ?",
            (guild_id,))
        return cur.rowcount > 0
