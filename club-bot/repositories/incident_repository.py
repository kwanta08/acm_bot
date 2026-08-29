"""incidents テーブルの CRUD（ヒヤリハット・事故報告。G4-10）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。

**匿名報告でも `reporter_id` は保存する**（悪用・虚偽報告への対処に要る）。
表示に使うのは `reporter_name` だけで、匿名報告ではこれが NULL になる。
取得系のメソッドは **`reporter_id` を返さない**——表示層が「うっかり出す」
経路そのものを作らないため（ADR 0008: 規律ではなく構造で守る）。
"""

from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database

#: 表示・一覧で取る列。**reporter_id を含めない。**
DISPLAY_COLUMNS = (
    "incident_id, occurred_at, place, description, injury, prevention,"
    " anonymous_flag, reporter_name, created_at"
)


class IncidentRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def report(
        self,
        guild_id: int,
        *,
        occurred_at: str,
        place: str,
        description: str,
        injury: str | None,
        prevention: str | None,
        reporter_id: str,
        reporter_name: str | None,
        anonymous: bool,
        created_at: str,
    ) -> int:
        """報告を1件保存する。戻り値は incident_id。

        `anonymous` が True なら `reporter_name` は保存しない（NULL）。
        呼び出し側が名前を渡してきても**ここで落とす**——
        Cog 側の if に頼ると、別の呼び出し経路が増えたときに漏れる。
        """
        cur = await self.db.execute(
            """
            INSERT INTO incidents
                (guild_id, occurred_at, place, description, injury, prevention,
                 anonymous_flag, reporter_id, reporter_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                occurred_at,
                place,
                description,
                injury,
                prevention,
                1 if anonymous else 0,
                str(reporter_id),
                None if anonymous else reporter_name,
                created_at,
            ),
        )
        return cur.lastrowid

    async def list_recent(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """直近の報告を新しい順に返す（`reporter_id` は含まない）。"""
        rows = await self.db.fetchall(
            f"SELECT {DISPLAY_COLUMNS} FROM incidents WHERE guild_id = ?"
            " ORDER BY incident_id DESC LIMIT ?",
            (guild_id, limit),
        )
        return [dict(r) for r in rows]

    async def get(self, guild_id: int, incident_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            f"SELECT {DISPLAY_COLUMNS} FROM incidents WHERE guild_id = ? AND incident_id = ?",
            (guild_id, incident_id),
        )
        return dict(row) if row else None

    async def count(self, guild_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM incidents WHERE guild_id = ?", (guild_id,)
        )
        return int(row["n"]) if row else 0
