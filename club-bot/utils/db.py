"""
SQLite データベース層（改訂版）

aiosqlite による非同期アクセス。起動時に全テーブルを自動生成する
（改訂版: 設定テーブル追加）
"""
from __future__ import annotations

import os

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")

# 改訂版スキーマ（設定テーブル追加）
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

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
    reminder_sent_flag INTEGER NOT NULL DEFAULT 0,
    sheet_title         TEXT
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

CREATE TABLE IF NOT EXISTS todoist_sections (
    section_id   TEXT PRIMARY KEY,
    team_key     TEXT NOT NULL,
    section_name TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT UNIQUE NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  INTEGER NOT NULL,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS layer_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    keta        TEXT NOT NULL,
    layer_num   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    synced_flag INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS layer_keta (
    keta_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    keta_name   TEXT UNIQUE NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_layer_records_synced ON layer_records(synced_flag);
CREATE INDEX IF NOT EXISTS idx_votes_option ON schedule_votes(option_id);
CREATE INDEX IF NOT EXISTS idx_options_schedule ON schedule_options(schedule_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(setting_key);
"""


class Database:
    """
唯一接続を保持する軽いラッパー。
"""

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
        await self._migrate()
        log.info("SQLite に接続しました: %s", self.path)

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def _migrate(self) -> None:
        """
既存 DB に備えられなかったカラムを追加する（移行用）
"""
        assert self._conn is not None
        cur = await self._conn.execute("PRAGMA table_info(schedules)")
        cols = {row[1] for row in await cur.fetchall()}
        await cur.close()
        if "sheet_title" not in cols:
            await self._conn.execute("ALTER TABLE schedules ADD COLUMN sheet_title TEXT")
            await self._conn.commit()
            log.info("schedules テーブルに sheet_title カラムを追加しました。")

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

    # 設定関連メソッド
    async def get_setting(self, key: str) -> str | None:
        """設定値を取得する"""
        row = await self.fetchone(
            "SELECT setting_value FROM settings WHERE setting_key = ?", (key,)
        )
        return row["setting_value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """設定値を保存する（存在すれば更新、なければ挿入）"""
        await self.execute(
            """INSERT INTO settings (setting_key, setting_value, updated_at)
               VALUES (?, ?, datetime('now', 'localtime'))
               ON CONFLICT(setting_key) DO UPDATE SET
               setting_value = excluded.setting_value,
               updated_at = datetime('now', 'localtime')""",
            (key, value)
        )

    async def delete_setting(self, key: str) -> bool:
        """設定値を削除する"""
        cur = await self.conn.execute(
            "DELETE FROM settings WHERE setting_key = ?", (key,)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_all_settings(self) -> dict[str, str]:
        """全ての設定を辞書で取得する"""
        rows = await self.fetchall("SELECT setting_key, setting_value FROM settings")
        return {row["setting_key"]: row["setting_value"] for row in rows}
