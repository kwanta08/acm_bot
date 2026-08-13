"""seasons テーブル（年度＝世代の境界）の CRUD。

サークルには毎年必ず代替わりが来るのに、これまで区切りを表す仕組みが
無かった。とはいえ全テーブルに season_id を張ると既存クエリの全面改修が
必要で後方互換のリスクが大きいため、ここで持つのは**年度の境界だけ**に
留める。各記録の年度は created_at の範囲で絞り込める。

「現役の年度」は ended_at IS NULL のうち最も新しいもの。
年度名に既定値は持たない（「2026年度」も「第30代」もサークル次第）。
"""
from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class SeasonRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def list_all(self, guild_id: int) -> list[dict[str, Any]]:
        """新しい順に年度を返す。"""
        rows = await self.db.fetchall(
            "SELECT * FROM seasons WHERE guild_id = ?"
            " ORDER BY started_at DESC, season_id DESC",
            (guild_id,))
        return [dict(r) for r in rows]

    async def current(self, guild_id: int) -> dict[str, Any] | None:
        """現役の年度（終了していないもののうち最新）。無ければ None。"""
        row = await self.db.fetchone(
            "SELECT * FROM seasons WHERE guild_id = ? AND ended_at IS NULL"
            " ORDER BY started_at DESC, season_id DESC LIMIT 1",
            (guild_id,))
        return dict(row) if row else None

    async def get_by_name(self, guild_id: int,
                          name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM seasons WHERE guild_id = ? AND name = ?",
            (guild_id, name))
        return dict(row) if row else None

    async def end_current(self, guild_id: int,
                          ended_at: str | None = None) -> str | None:
        """現役の年度に終了日を打つ。終了した年度名を返す（無ければ None）。"""
        season = await self.current(guild_id)
        if season is None:
            return None
        await self.db.execute(
            "UPDATE seasons SET ended_at = ? WHERE guild_id = ? AND season_id = ?",
            (ended_at or to_iso(now()), guild_id, season["season_id"]),
        )
        return str(season["name"])

    async def create(self, guild_id: int, name: str,
                     started_at: str | None = None) -> int:
        """新しい年度を作る。同名が既にあれば ValueError。"""
        if await self.get_by_name(guild_id, name) is not None:
            raise ValueError(name)
        stamp = started_at or to_iso(now())
        cur = await self.db.execute(
            """
            INSERT INTO seasons (guild_id, name, started_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, name, stamp, to_iso(now())),
        )
        return cur.lastrowid

    async def start_new(self, guild_id: int, name: str,
                        at: str | None = None) -> tuple[str | None, int]:
        """現役の年度を終わらせてから新しい年度を始める。

        戻り値は (終了した年度名, 新しい season_id)。
        """
        stamp = at or to_iso(now())
        ended = await self.end_current(guild_id, stamp)
        season_id = await self.create(guild_id, name, stamp)
        return ended, season_id
