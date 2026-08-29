"""tools / tool_loans テーブルの CRUD（G4-9）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
工具は (guild_id, tool_name) で一意。

**貸出中かどうかは `tool_loans.returned_at IS NULL` で表す。**
tools 側にフラグを置くと、行を消したときに貸出の事実まで消える。
"""

from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database

#: 一覧・督促で取る貸出の列（SELECT * に依存しない）
_LOAN_COLUMNS = (
    "l.loan_id, l.tool_id, l.user_id, l.borrowed_at, l.due_date,"
    " l.returned_at, l.note, l.overdue_notified_flag, t.tool_name"
)


class ToolRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    # ---------- 工具マスタ ----------
    async def get_tool(self, guild_id: int, tool_name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM tools WHERE guild_id = ? AND tool_name = ?", (guild_id, tool_name)
        )
        return dict(row) if row else None

    async def add_tool(
        self, guild_id: int, tool_name: str, created_by: str, created_at: str, note: str | None
    ) -> int:
        """工具を登録する。無効化済みの同名があれば有効化する（layer_keta と同型）。"""
        existing = await self.get_tool(guild_id, tool_name)
        if existing is not None:
            await self.db.execute(
                "UPDATE tools SET active_flag = 1, note = COALESCE(?, note)"
                " WHERE guild_id = ? AND tool_id = ?",
                (note, guild_id, existing["tool_id"]),
            )
            return int(existing["tool_id"])
        cur = await self.db.execute(
            "INSERT INTO tools (guild_id, tool_name, note, active_flag, created_by, created_at)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (guild_id, tool_name, note, created_by, created_at),
        )
        return cur.lastrowid

    async def deactivate_tool(self, guild_id: int, tool_name: str) -> bool:
        cur = await self.db.execute(
            "UPDATE tools SET active_flag = 0"
            " WHERE guild_id = ? AND tool_name = ? AND active_flag = 1",
            (guild_id, tool_name),
        )
        return cur.rowcount > 0

    async def list_tools(self, guild_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tools WHERE guild_id = ?"
        if active_only:
            sql += " AND active_flag = 1"
        sql += " ORDER BY tool_name"
        rows = await self.db.fetchall(sql, (guild_id,))
        return [dict(r) for r in rows]

    async def list_tool_names(self, guild_id: int) -> list[str]:
        rows = await self.db.fetchall(
            "SELECT tool_name FROM tools WHERE guild_id = ? AND active_flag = 1 ORDER BY tool_name",
            (guild_id,),
        )
        return [str(r["tool_name"]) for r in rows]

    # ---------- 貸出 ----------
    async def get_open_loan(self, guild_id: int, tool_id: int) -> dict[str, Any] | None:
        """その工具の貸出中の行。無ければ None。"""
        row = await self.db.fetchone(
            f"SELECT {_LOAN_COLUMNS} FROM tool_loans l"
            " JOIN tools t ON t.guild_id = l.guild_id AND t.tool_id = l.tool_id"
            " WHERE l.guild_id = ? AND l.tool_id = ? AND l.returned_at IS NULL"
            " ORDER BY l.loan_id DESC",
            (guild_id, tool_id),
        )
        return dict(row) if row else None

    async def borrow(
        self,
        guild_id: int,
        tool_id: int,
        user_id: str,
        borrowed_at: str,
        due_date: str | None,
        note: str | None,
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO tool_loans"
            " (guild_id, tool_id, user_id, borrowed_at, due_date, returned_at, note,"
            "  overdue_notified_flag)"
            " VALUES (?, ?, ?, ?, ?, NULL, ?, 0)",
            (guild_id, tool_id, user_id, borrowed_at, due_date, note),
        )
        return cur.lastrowid

    async def give_back(self, guild_id: int, loan_id: int, returned_at: str) -> bool:
        """貸出を返却済みにする。既に返却済み・他ギルドなら False。"""
        cur = await self.db.execute(
            "UPDATE tool_loans SET returned_at = ?"
            " WHERE guild_id = ? AND loan_id = ? AND returned_at IS NULL",
            (returned_at, guild_id, loan_id),
        )
        return cur.rowcount > 0

    async def list_open_loans(self, guild_id: int) -> list[dict[str, Any]]:
        """貸出中の一覧（借りた日の古い順）。"""
        rows = await self.db.fetchall(
            f"SELECT {_LOAN_COLUMNS} FROM tool_loans l"
            " JOIN tools t ON t.guild_id = l.guild_id AND t.tool_id = l.tool_id"
            " WHERE l.guild_id = ? AND l.returned_at IS NULL"
            " ORDER BY l.borrowed_at",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def set_overdue_notified(self, guild_id: int, loan_id: int, notified: bool) -> None:
        """督促を送ったかどうかを記録する（1貸出につき1回）。"""
        await self.db.execute(
            "UPDATE tool_loans SET overdue_notified_flag = ? WHERE guild_id = ? AND loan_id = ?",
            (1 if notified else 0, guild_id, loan_id),
        )
