"""audit_log テーブル（監査ログ）の CRUD。

管理者操作（設定変更・トークン登録・班/技能マスタ変更等）の証跡を
ギルド単位で記録する。機密値（トークン・暗号鍵等）は保存しないこと。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class AuditLogRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def record(self, guild_id: int, actor_id: str, action: str,
                     target: str | None = None, detail: str | None = None) -> int:
        """操作を記録する。戻り値は audit_id。"""
        cur = await self.db.execute(
            """
            INSERT INTO audit_log (guild_id, actor_id, action, target, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, actor_id, action, target, detail, to_iso(now())),
        )
        return cur.lastrowid

    async def list_recent(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """指定ギルドの直近ログを新しい順に返す。"""
        rows = await self.db.fetchall(
            "SELECT * FROM audit_log WHERE guild_id = ?"
            " ORDER BY audit_id DESC LIMIT ?",
            (guild_id, limit))
        return [dict(r) for r in rows]
