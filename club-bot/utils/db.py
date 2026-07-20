"""
<<<<<<< HEAD
SQLite データベース層（改訂版）

aiosqlite による非同期アクセス。起動時に全テーブルを自動生成する
（改訂版: 設定テーブル追加）
=======
SQLite データベース層（マルチテナント版）

aiosqlite による非同期アクセス。起動時に全テーブルを自動生成する。

マルチテナント化:
- 全テーブルに guild_id カラム（Discord ギルド ID, 64bit 整数）を保持する。
- SQLite 上は INTEGER（最大 8 バイト符号付き）だが、将来 PostgreSQL へ
  移行する際は BIGINT に対応させる。カラム型を GUILD_ID_TYPE に集約し、
  CHECK (guild_id >= 0) で負値を排除している。
- 既存 DB は _migrate() がテーブル再作成方式で guild_id をバックフィルする
  （バックフィル値は環境変数 GUILD_ID、未設定時は 0 = レガシー/未帰属）。
>>>>>>> 803617a (v4.0)
"""
from __future__ import annotations

import os

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")

<<<<<<< HEAD
# 改訂版スキーマ（設定テーブル追加）
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

=======
# Discord ギルド ID のカラム型。
# SQLite: INTEGER（8バイト符号付き）。PostgreSQL 移行時は BIGINT に読み替える。
GUILD_ID_TYPE = "INTEGER"

# guild_id カラム定義（CHECK で 0 以上に限定し、BIGINT 相当の非負整数を保証）
_GUILD_COL = f"guild_id {GUILD_ID_TYPE} NOT NULL CHECK (guild_id >= 0)"

# ---------------------------------------------------------------------------
# テーブル定義（テーブル名 → CREATE TABLE 文）
# init_schema と既存 DB のマイグレーション（テーブル再作成）の両方から参照する。
# すべてのテーブルが guild_id を保持し、複合キー/ユニーク制約の先頭に置く。
# ---------------------------------------------------------------------------
TABLE_DDL: dict[str, str] = {
    "settings": f"""
CREATE TABLE IF NOT EXISTS settings (
    {_GUILD_COL},
    setting_key   TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (guild_id, setting_key)
);
""",
    "teams": f"""
>>>>>>> 803617a (v4.0)
CREATE TABLE IF NOT EXISTS teams (
    team_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    team_key       TEXT NOT NULL,
    team_name      TEXT NOT NULL,
    leader_role_id TEXT,
    channel_id     TEXT,
    active_flag    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (guild_id, team_key)
);
""",
    "members": f"""
CREATE TABLE IF NOT EXISTS members (
    {_GUILD_COL},
    user_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);
""",
    "schedules": f"""
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id        TEXT PRIMARY KEY,
    {_GUILD_COL},
    title              TEXT NOT NULL,
    description        TEXT,
    place              TEXT,
    target_role_id     TEXT,
    deadline           TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    channel_id         TEXT NOT NULL,
    closed_flag        INTEGER NOT NULL DEFAULT 0,
    reminder_sent_flag INTEGER NOT NULL DEFAULT 0,
    sheet_title        TEXT
);
""",
    "schedule_options": f"""
CREATE TABLE IF NOT EXISTS schedule_options (
    option_id   TEXT PRIMARY KEY,
    {_GUILD_COL},
    schedule_id TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    message_id  TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE
);
""",
    "schedule_votes": f"""
CREATE TABLE IF NOT EXISTS schedule_votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    option_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (guild_id, option_id, user_id),
    FOREIGN KEY (option_id) REFERENCES schedule_options(option_id) ON DELETE CASCADE
);
""",
    "tasks": f"""
CREATE TABLE IF NOT EXISTS tasks (
    local_task_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
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
""",
    "reminders_log": f"""
CREATE TABLE IF NOT EXISTS reminders_log (
    reminder_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    reminder_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    target_user_id TEXT,
    sent_channel_id TEXT,
    sent_at        TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT
);
""",
    "todoist_sections": f"""
CREATE TABLE IF NOT EXISTS todoist_sections (
    {_GUILD_COL},
    section_id   TEXT NOT NULL,
    team_key     TEXT NOT NULL,
    section_name TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (guild_id, section_id)
);
""",
    "layer_sessions": f"""
CREATE TABLE IF NOT EXISTS layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    user_id    TEXT NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    UNIQUE (guild_id, user_id)
);
""",
    "layer_records": f"""
CREATE TABLE IF NOT EXISTS layer_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    user_id     TEXT NOT NULL,
    keta        TEXT NOT NULL,
    layer_num   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    synced_flag INTEGER NOT NULL DEFAULT 0
);
""",
    "layer_keta": f"""
CREATE TABLE IF NOT EXISTS layer_keta (
    keta_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    keta_name   TEXT NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (guild_id, keta_name)
);
""",
}

# インデックス（guild_id を先頭に含む複合インデックス）
INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_teams_guild ON teams(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_members_guild ON members(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_schedules_guild ON schedules(guild_id, closed_flag, deadline);
CREATE INDEX IF NOT EXISTS idx_options_guild_schedule ON schedule_options(guild_id, schedule_id);
CREATE INDEX IF NOT EXISTS idx_votes_guild_option ON schedule_votes(guild_id, option_id);
CREATE INDEX IF NOT EXISTS idx_votes_option ON schedule_votes(option_id);
<<<<<<< HEAD
CREATE INDEX IF NOT EXISTS idx_options_schedule ON schedule_options(schedule_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(setting_key);
=======
CREATE INDEX IF NOT EXISTS idx_tasks_guild_status ON tasks(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_guild ON reminders_log(guild_id, reminder_id);
CREATE INDEX IF NOT EXISTS idx_sections_guild_team ON todoist_sections(guild_id, team_key);
CREATE INDEX IF NOT EXISTS idx_layer_records_guild_synced ON layer_records(guild_id, synced_flag);
CREATE INDEX IF NOT EXISTS idx_layer_records_synced ON layer_records(synced_flag);
>>>>>>> 803617a (v4.0)
"""

# 改訂版スキーマ（マルチテナント版）。テーブル定義のみ。
# インデックスは guild_id を参照するため、既存 DB のマイグレーション完了後に
# 作成する（connect() 内で INDEX_DDL を実行する）。
SCHEMA = "\n".join(TABLE_DDL.values())


def legacy_guild_id() -> int:
    """
    既存単一サーバー運用のレガシー guild_id（環境変数 GUILD_ID）。
    未設定・不正値の場合は 0（レガシー/未帰属データ）を返す。
    """
    raw = (os.getenv("GUILD_ID") or "").strip().strip('"').strip("'")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


class Database:
    """
<<<<<<< HEAD
唯一接続を保持する軽いラッパー。
"""
=======
    唯一接続を保持する軽いラッパー。
    """
>>>>>>> 803617a (v4.0)

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
        # インデックスは guild_id カラムの存在が確定した後に作成する
        await self._conn.executescript(INDEX_DDL)
        await self._conn.commit()
        log.info("SQLite に接続しました: %s", self.path)

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

<<<<<<< HEAD
    async def _migrate(self) -> None:
        """
既存 DB に備えられなかったカラムを追加する（移行用）
"""
=======
    async def _table_columns(self, table: str) -> list[str]:
>>>>>>> 803617a (v4.0)
        assert self._conn is not None
        cur = await self._conn.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [row[1] for row in rows]

    async def _migrate(self) -> None:
        """
        既存 DB の簡易マイグレーション。

        1. schedules.sheet_title の追加（旧来の移行）
        2. 全テーブルへの guild_id 追加（テーブル再作成方式）。
           既存行の guild_id は環境変数 GUILD_ID（レガシーギルド）で
           バックフィルする。未設定時は 0（レガシー/未帰属）。
        """
        assert self._conn is not None
        cols = await self._table_columns("schedules")
        if cols and "sheet_title" not in cols:
            await self._conn.execute("ALTER TABLE schedules ADD COLUMN sheet_title TEXT")
            await self._conn.commit()
            log.info("schedules テーブルに sheet_title カラムを追加しました。")

        await self._migrate_guild_id()

    async def _migrate_guild_id(self) -> None:
        """
        guild_id を持たない旧テーブルを新スキーマへ移行する（テーブル再作成方式）。

        手順（migrations/001_add_guild_id.sql と同等）:
          1. 旧テーブルを <table>_legacy にリネーム
          2. 新スキーマでテーブルを作成
          3. guild_id をバックフィルしつつデータをコピー
          4. 旧テーブルを削除
        """
        assert self._conn is not None
        targets: dict[str, list[str]] = {}
        for table in TABLE_DDL:
            cols = await self._table_columns(table)
            if not cols:
                continue  # テーブル自体が無い（init_schema で作成済みのはずだが念のため）
            if "guild_id" not in cols:
                targets[table] = cols
        if not targets:
            return

        legacy = legacy_guild_id()
        log.warning(
            "guild_id を持たない旧テーブルを検出しました（%s）。"
            "guild_id=%d でバックフィルして移行します。",
            ", ".join(sorted(targets)), legacy,
        )

        # FK 参照の張り替えを避けるため、移行中は FK 強制を一時停止する
        await self._conn.execute("PRAGMA foreign_keys = OFF;")
        await self._conn.commit()
        try:
            for table, cols in targets.items():
                col_list = ", ".join(cols)
                await self._conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
                await self._conn.execute(TABLE_DDL[table])
                await self._conn.execute(
                    f"INSERT INTO {table} (guild_id, {col_list}) "
                    f"SELECT ?, {col_list} FROM {table}_legacy",
                    (legacy,),
                )
                await self._conn.execute(f"DROP TABLE {table}_legacy")
                log.info("%s テーブルを guild_id 付きスキーマへ移行しました（%d 行）",
                         table, await self._count(table))
            await self._conn.executescript(INDEX_DDL)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        log.warning("guild_id マイグレーションが完了しました。")

    async def _count(self, table: str) -> int:
        row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
        return int(row["c"]) if row else 0

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

<<<<<<< HEAD
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
=======
    # 設定関連メソッド（guild_id スコープ）
    async def get_setting(self, guild_id: int, key: str) -> str | None:
        """設定値を取得する"""
        row = await self.fetchone(
            "SELECT setting_value FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
        )
        return row["setting_value"] if row else None

    async def set_setting(self, guild_id: int, key: str, value: str) -> None:
        """設定値を保存する（存在すれば更新、なければ挿入）"""
        await self.execute(
            """INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
               VALUES (?, ?, ?, datetime('now', 'localtime'))
               ON CONFLICT(guild_id, setting_key) DO UPDATE SET
               setting_value = excluded.setting_value,
               updated_at = datetime('now', 'localtime')""",
            (guild_id, key, value)
        )

    async def delete_setting(self, guild_id: int, key: str) -> bool:
        """設定値を削除する"""
        cur = await self.conn.execute(
            "DELETE FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
>>>>>>> 803617a (v4.0)
        )
        await self.conn.commit()
        return cur.rowcount > 0

<<<<<<< HEAD
    async def get_all_settings(self) -> dict[str, str]:
        """全ての設定を辞書で取得する"""
        rows = await self.fetchall("SELECT setting_key, setting_value FROM settings")
=======
    async def get_all_settings(self, guild_id: int) -> dict[str, str]:
        """指定ギルドの全ての設定を辞書で取得する"""
        rows = await self.fetchall(
            "SELECT setting_key, setting_value FROM settings WHERE guild_id = ?",
            (guild_id,),
        )
>>>>>>> 803617a (v4.0)
        return {row["setting_key"]: row["setting_value"] for row in rows}
