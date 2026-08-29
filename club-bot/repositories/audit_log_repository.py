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

    async def record(
        self,
        guild_id: int,
        actor_id: str,
        action: str,
        target: str | None = None,
        detail: str | None = None,
    ) -> int:
        """操作を記録する。戻り値は audit_id。"""
        cur = await self.db.execute(
            """
            INSERT INTO audit_log (guild_id, actor_id, action, target, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, actor_id, action, target, detail, to_iso(now())),
        )
        return cur.lastrowid

    async def list_recent(
        self, guild_id: int, limit: int = 10, actor_id: str | None = None
    ) -> list[dict[str, Any]]:
        """指定ギルドの直近ログを新しい順に返す。

        actor_id を渡すとその実行者の分だけに絞る（`/report changes` の
        `actor` 引数）。絞り込みは SQL の bind 値で行い、
        リクエスト由来の文字列を SQL へ埋め込まない。
        """
        if actor_id is None:
            rows = await self.db.fetchall(
                "SELECT * FROM audit_log WHERE guild_id = ? ORDER BY audit_id DESC LIMIT ?",
                (guild_id, limit),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM audit_log WHERE guild_id = ? AND actor_id = ?"
                " ORDER BY audit_id DESC LIMIT ?",
                (guild_id, str(actor_id), limit),
            )
        return [dict(r) for r in rows]

    async def list_actors(self, guild_id: int, limit: int = 25) -> list[str]:
        """最近操作した実行者の ID を新しい順に返す（オートコンプリート用）。

        ギルドの全メンバーではなく**実際にログへ出てくる人**だけを候補にする。
        """
        rows = await self.db.fetchall(
            "SELECT actor_id, MAX(audit_id) AS latest FROM audit_log"
            " WHERE guild_id = ? GROUP BY actor_id ORDER BY latest DESC LIMIT ?",
            (guild_id, limit),
        )
        return [str(r["actor_id"]) for r in rows]
