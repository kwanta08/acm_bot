"""tasks テーブルの CRUD（仕様 10.6）。"""
from __future__ import annotations

from typing import Any

from utils.db import Database
from utils.parser import now, to_iso


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    async def create_task(self, title: str, created_by: str,
                          todoist_task_id: str | None = None,
                          assignee_id: str | None = None,
                          team_key: str | None = None,
                          due_date: str | None = None,
                          priority: int | None = None,
                          location_key: str | None = None) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO tasks
                (todoist_task_id, title, assignee_id, team_key, due_date, priority,
                 location_key, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (todoist_task_id, title, assignee_id, team_key, due_date, priority,
             location_key, created_by, to_iso(now())),
        )
        return cur.lastrowid

    async def get_task(self, local_task_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM tasks WHERE local_task_id = ?", (local_task_id,))
        return dict(row) if row else None

    async def set_todoist_id(self, local_task_id: int, todoist_task_id: str) -> None:
        await self.db.execute(
            "UPDATE tasks SET todoist_task_id = ? WHERE local_task_id = ?",
            (todoist_task_id, local_task_id))

    async def complete_task(self, local_task_id: int) -> None:
        await self.db.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE local_task_id = ?",
            (to_iso(now()), local_task_id))

    async def delete_task(self, local_task_id: int) -> None:
        await self.db.execute(
            "UPDATE tasks SET status = 'archived' WHERE local_task_id = ?", (local_task_id,))

    async def set_assignee(self, local_task_id: int, assignee_id: str | None) -> None:
        await self.db.execute(
            "UPDATE tasks SET assignee_id = ? WHERE local_task_id = ?",
            (assignee_id, local_task_id))

    async def set_priority(self, local_task_id: int, priority: int) -> None:
        await self.db.execute(
            "UPDATE tasks SET priority = ? WHERE local_task_id = ?",
            (priority, local_task_id))

    async def list_tasks(self, status: str = "open",
                         assignee_id: str | None = None,
                         team_key: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE status = ?"
        params: list[Any] = [status]
        if assignee_id:
            sql += " AND assignee_id = ?"
            params.append(assignee_id)
        if team_key:
            sql += " AND team_key = ?"
            params.append(team_key)
        sql += " ORDER BY (due_date IS NULL), due_date, priority DESC"
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    async def list_overdue(self, today_iso_date: str) -> list[dict[str, Any]]:
        """期限が今日より前の未完了タスク。"""
        rows = await self.db.fetchall(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND due_date IS NOT NULL AND due_date < ?
            ORDER BY due_date
            """,
            (today_iso_date,))
        return [dict(r) for r in rows]

    async def list_due_within(self, today_iso_date: str, until_iso_date: str) -> list[dict[str, Any]]:
        """期限が [today, until] にある未完了タスク（仕様 11.3.3 7日以内）。"""
        rows = await self.db.fetchall(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND due_date IS NOT NULL
              AND due_date >= ? AND due_date <= ?
            ORDER BY due_date
            """,
            (today_iso_date, until_iso_date))
        return [dict(r) for r in rows]

    async def list_all_for_export(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM tasks WHERE status != 'archived' ORDER BY local_task_id")
        return [dict(r) for r in rows]
