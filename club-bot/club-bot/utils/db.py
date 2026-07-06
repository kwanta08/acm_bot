"""
SQLite データベース層（仕様 10）。

aiosqlite による非同期アクセス。初回起動時に全テーブルを自動生成する
（仕様 11.1.2）。
"""
from __future__ import annotations

import os

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")

# 仕様 10 のスキーマ定義
SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    team_key       TEXT UNIQUE NOT NULL,
    team_name      TEXT NOT NULL,
    leader_role_id TEXT,
    channel_id     TEXT,
    active_flag    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS members (
    user_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id        TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    description        TEXT,
    place              TEXT,
    target_role_id     TEXT,
    deadline           TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    channel_id         TEXT NOT NULL,
    closed_flag        INTEGER NOT NULL DEFAULT 0,
    reminder_sent_flag INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_options (
    option_id   TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    message_id  TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schedule_votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (option_id, user_id),
    FOREIGN KEY (option_id) REFERENCES schedule_options(option_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    local_task_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    todoist_task_id TEXT,
    title           TEXT NOT NULL,
    assignee_id     TEXT,
    team_key        TEXT,
    due_date        TEXT,
    priority        INTEGER,
    location_key    TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS reminders_log (
    reminder_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    target_user_id TEXT,
    sent_channel_id TEXT,
    sent_at        TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT
);

CREATE TABLE IF NOT EXISTS layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT UNIQUE NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  INTEGER NOT NULL,
    started_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_votes_option ON schedule_votes(option_id);
CREATE INDEX IF NOT EXISTS idx_options_schedule ON schedule_options(schedule_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


class Database:
    """単一接続を保持する薄いラッパ。"""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self.init_schema()
        log.info("SQLite に接続しました: %s", self.path)

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database が未接続です")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)
