"""
データベース層（マルチテナント版 / SQLite・PostgreSQL 両対応）

- ローカル開発・テスト: SQLite（aiosqlite、DB_PATH）
- 本番（NocoDB 構成）: PostgreSQL（asyncpg、DATABASE_URL）
  DATABASE_URL が設定されていれば PostgreSQL、未設定なら SQLite に接続する。

マルチテナント化:
- 全テーブルに guild_id カラム（Discord ギルド ID, 64bit 整数）を保持する。
  SQLite 上は INTEGER、PostgreSQL 上は BIGINT（to_pg_ddl() で変換）。
  CHECK (guild_id >= 0) で負値を排除している。
- 既存 SQLite DB は _migrate() がテーブル再作成方式で guild_id を
  バックフィルする（バックフィル値は環境変数 GUILD_ID、未設定時は 0）。
- スキーマバージョンは SQLite では PRAGMA user_version、PostgreSQL では
  schema_meta テーブルに記録し、_migrate_versioned() が冪等に適用する。
- リポジトリ層の SQL は SQLite 方言（? プレースホルダ）に統一し、
  PostgreSQL 利用時は本モジュールが $n へ変換する。
"""
from __future__ import annotations

import os
import re

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")

try:
    import asyncpg
except Exception:  # asyncpg 未導入でも SQLite だけは動くように
    asyncpg = None  # type: ignore

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
    # ギルド台帳。guild_id がそのまま PK（唯一 guild_id をカラムとして持たない
    # 形の例外ではなく、PK 自体が guild_id）。正のギルド ID のみ許可する。
    "guilds": """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id      INTEGER PRIMARY KEY CHECK (guild_id > 0),
    guild_name    TEXT NOT NULL,
    joined_at     TEXT NOT NULL,
    setup_version INTEGER NOT NULL DEFAULT 2
);
""",
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
CREATE TABLE IF NOT EXISTS teams (
    team_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    team_key          TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    leader_role_id    TEXT,
    member_role_id    TEXT,
    secondary_role_id TEXT,
    channel_id        TEXT,
    active_flag       INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT,
    updated_at        TEXT,
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
    # 監査ログ（管理者操作の証跡。機密値は保存しない運用）
    "audit_log": f"""
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    actor_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);
""",
    # 技能タグ マスタ（ギルド別。名前はギルド内で一意）
    "skill_tags": f"""
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    skill_name   TEXT NOT NULL,
    active_flag  INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (guild_id, skill_name)
);
""",
    # Todoist 接続設定（1ギルド1件。トークンは Fernet 暗号文で保存し、
    # 平文は保存しない。専用テーブルとすることで NocoDB 等の外部 UI で
    # テーブル単位の非表示・アクセス制限ができる）
    "todoist_configs": """
CREATE TABLE IF NOT EXISTS todoist_configs (
    guild_id            INTEGER PRIMARY KEY CHECK (guild_id > 0),
    api_token_encrypted TEXT NOT NULL,
    project_id          TEXT,
    today_label_name    TEXT NOT NULL DEFAULT '今日やること',
    enabled_flag        INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_tasks_guild_status ON tasks(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_guild ON reminders_log(guild_id, reminder_id);
CREATE INDEX IF NOT EXISTS idx_sections_guild_team ON todoist_sections(guild_id, team_key);
CREATE INDEX IF NOT EXISTS idx_layer_records_guild_synced ON layer_records(guild_id, synced_flag);
CREATE INDEX IF NOT EXISTS idx_layer_records_synced ON layer_records(synced_flag);
CREATE INDEX IF NOT EXISTS idx_audit_log_guild ON audit_log(guild_id, audit_id);
CREATE INDEX IF NOT EXISTS idx_skill_tags_guild ON skill_tags(guild_id, active_flag);
"""

# ---------------------------------------------------------------------------
# ビュー定義（NocoDB 等の外部 UI 向け。機密列を含まない安全な参照用）
#
# ビュー本体（SELECT 文）を1箇所に集約し、実行用 DDL はドライバ別に生成する:
#   SQLITE_VIEW_DDL   : DROP VIEW IF EXISTS + CREATE VIEW の安全な再作成方式
#                       （SQLite は CREATE OR REPLACE VIEW をサポートしない）
#   POSTGRES_VIEW_DDL : CREATE OR REPLACE VIEW
#                       （PostgreSQL は CREATE VIEW IF NOT EXISTS をサポートしない）
# 実行は両ドライバとも複数文をネイティブに処理する
# （aiosqlite executescript / asyncpg execute）ため、split(';') による
# 文字列分割は行わない。
# ---------------------------------------------------------------------------
_VIEW_BODIES: dict[str, str] = {
    # Todoist 連携状態（暗号文を含まない）
    "v_todoist_status": """
SELECT guild_id, project_id, today_label_name, enabled_flag, updated_at
FROM todoist_configs
""",
    # 出欠一覧（旧 Google Sheets の attendance シート相当。
    # 正本は schedule_votes / schedule_options / schedules）
    "v_attendance": """
SELECT s.guild_id,
       s.schedule_id,
       s.title       AS event_title,
       o.label       AS option_label,
       v.user_id,
       v.status,
       v.updated_at,
       s.deadline
FROM schedule_votes v
JOIN schedule_options o
  ON o.guild_id = v.guild_id AND o.option_id = v.option_id
JOIN schedules s
  ON s.guild_id = o.guild_id AND s.schedule_id = o.schedule_id
""",
    # 班サマリ（旧 Google Sheets の team_summary シート相当。正本は teams / members）
    "v_team_summary": """
SELECT t.guild_id,
       t.team_key,
       t.team_name,
       COUNT(m.user_id)              AS member_count,
       COALESCE(SUM(m.is_leader), 0) AS leader_count
FROM teams t
LEFT JOIN members m
  ON m.guild_id = t.guild_id
 AND m.primary_team = t.team_key
 AND m.active_flag = 1
WHERE t.active_flag = 1
GROUP BY t.guild_id, t.team_key, t.team_name
""",
}

SQLITE_VIEW_DDL = "\n".join(
    f"DROP VIEW IF EXISTS {name};\nCREATE VIEW {name} AS{body};"
    for name, body in _VIEW_BODIES.items()
)

POSTGRES_VIEW_DDL = "\n".join(
    f"CREATE OR REPLACE VIEW {name} AS{body};"
    for name, body in _VIEW_BODIES.items()
)

# スキーマバージョン（SQLite: PRAGMA user_version / PostgreSQL: schema_meta）。
# 1: guild_id 導入済みの初期マルチテナントスキーマ（旧版は user_version=0 として扱う）
# 2: guilds（ギルド台帳）・audit_log（監査ログ）追加
# 3: skill_tags 追加。teams に member_role_id / secondary_role_id /
#    created_at / updated_at を追加し、settings のロールマップをバックフィル
# 4: todoist_configs 追加（Todoist トークンのギルド別暗号化保存）
# 5: v_attendance / v_team_summary ビュー追加（Sheets 廃止に伴う NocoDB 表示用）
SCHEMA_VERSION = 5

# 改訂版スキーマ（マルチテナント版）。テーブル定義のみ。
SCHEMA = "\n".join(TABLE_DDL.values())

# PostgreSQL: スキーマバージョン管理テーブル（SQLite は PRAGMA user_version を使用）
SCHEMA_META_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
"""

# SERIAL 相当の PK カラム（PostgreSQL の RETURNING / シーケンス修復に使用）
_PK_COLUMNS: dict[str, str] = {
    "teams": "team_id",
    "schedule_votes": "vote_id",
    "tasks": "local_task_id",
    "reminders_log": "reminder_id",
    "layer_sessions": "session_id",
    "layer_records": "record_id",
    "layer_keta": "keta_id",
    "audit_log": "audit_id",
    "skill_tags": "skill_tag_id",
}

_INSERT_TABLE_RE = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE)


def to_pg_ddl(sqlite_ddl: str) -> str:
    """SQLite 用 DDL を PostgreSQL 用に機械変換する。

    - INTEGER PRIMARY KEY AUTOINCREMENT → BIGINT GENERATED BY DEFAULT AS IDENTITY
      （明示的な ID 挿入を許可するため BY DEFAULT を使う。移行スクリプトが
      SQLite の ID をそのまま入れられる）
    - guild_id INTEGER → BIGINT（ギルド台帳の PK 含む）
    - datetime('now', 'localtime') → to_char(CURRENT_TIMESTAMP, ...)（同じ書式）
    """
    s = sqlite_ddl.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
    s = s.replace(f"guild_id {GUILD_ID_TYPE} NOT NULL", "guild_id BIGINT NOT NULL")
    s = s.replace("guild_id      INTEGER PRIMARY KEY", "guild_id      BIGINT PRIMARY KEY")
    s = s.replace("datetime('now', 'localtime')",
                  "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')")
    return s


TABLE_DDL_PG: dict[str, str] = {name: to_pg_ddl(ddl) for name, ddl in TABLE_DDL.items()}


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


class _PgCursor:
    """aiosqlite.Cursor 相当のインターフェース（rowcount / lastrowid）。"""

    def __init__(self, rowcount: int, lastrowid: int | None = None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid


class Database:
    """
    SQLite / PostgreSQL 両対応の軽いラッパー。

    - database_url 指定時: PostgreSQL（asyncpg プール）
    - それ以外: SQLite（aiosqlite、path）
    リポジトリ層は SQLite 方言（? プレースホルダ）のまま利用できる。
    """

    def __init__(self, path: str, database_url: str | None = None):
        self.path = path
        self.database_url = (database_url or "").strip() or None
        self._conn: aiosqlite.Connection | None = None
        self._pool = None  # asyncpg.Pool

    @property
    def _is_pg(self) -> bool:
        return self.database_url is not None

    @property
    def driver_name(self) -> str:
        """接続中の DB 種別（表示用）。"""
        return "PostgreSQL" if self._is_pg else "SQLite"

    # ------------------------------------------------------------------
    # 接続・スキーマ初期化
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._is_pg:
            await self._connect_pg()
            return

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        # NocoDB 等の外部ツールとの同時アクセスに備え、ロック待ちを許容する
        await self._conn.execute("PRAGMA busy_timeout = 5000;")
        await self.init_schema()
        await self._migrate()
        # インデックスは guild_id カラムの存在が確定した後に作成する
        await self._conn.executescript(INDEX_DDL)
        await self._conn.executescript(SQLITE_VIEW_DDL)
        await self._conn.commit()
        log.info("SQLite に接続しました: %s", self.path)

    async def _connect_pg(self) -> None:
        if asyncpg is None:
            raise RuntimeError(
                "DATABASE_URL が設定されていますが asyncpg がありません。"
                " pip install asyncpg を実行してください。")
        self._pool = await asyncpg.create_pool(
            dsn=self.database_url, min_size=1, max_size=5)
        # スキーマ作成（冪等）→ バージョン付きマイグレーション → シーケンス修復
        try:
            async with self._pool.acquire() as con:
                for name, ddl in TABLE_DDL_PG.items():
                    await self._pg_exec_ddl(con, f"table:{name}", ddl)
                await self._pg_exec_ddl(con, "table:schema_meta", SCHEMA_META_DDL)
                await self._pg_exec_ddl(con, "indexes", INDEX_DDL)
                await self._pg_exec_ddl(con, "views", POSTGRES_VIEW_DDL)
        except Exception:
            await self.close()
            raise
        await self._migrate_versioned()
        await self._pg_fix_sequences()
        log.info("PostgreSQL に接続しました（%s）",
                 re.sub(r"://[^@]*@", "://***@", self.database_url))

    async def _pg_exec_ddl(self, con, label: str, sql: str) -> None:
        """DDL を実行し、失敗時は失敗した SQL を安全に記録する。

        DDL には秘密情報は含まれない（DATABASE_URL・パスワードは出力しない）。
        """
        try:
            await con.execute(sql)
        except Exception as e:
            log.error("PostgreSQL DDL 実行失敗 (%s): %s\n%s",
                      label, type(e).__name__, sql.strip())
            raise

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def is_healthy(self) -> bool:
        """接続確認（/health 用）。"""
        try:
            await self.fetchone("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # ドライバ差分の吸収
    # ------------------------------------------------------------------
    def _prepare(self, sql: str, params: tuple) -> tuple[str, list]:
        """SQLite 方言（? プレースホルダ）をドライバに合わせて変換する。"""
        if not self._is_pg:
            return sql, list(params)
        out: list[str] = []
        idx = 0
        for ch in sql:
            if ch == "?":
                idx += 1
                out.append(f"${idx}")
            else:
                out.append(ch)
        return "".join(out), list(params)

    def _now_sql(self) -> str:
        """現在時刻を返す SQL 式（settings.updated_at 等の互換用）。"""
        if self._is_pg:
            return "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
        return "datetime('now', 'localtime')"

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database が未接続です（SQLite ではありません）")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()):
        if self._is_pg:
            return await self._execute_pg(sql, params)
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def _execute_pg(self, sql: str, params: tuple) -> _PgCursor:
        assert self._pool is not None
        stmt, args = self._prepare(sql, params)
        upper = stmt.lstrip().upper()
        m = _INSERT_TABLE_RE.search(stmt)
        pk = _PK_COLUMNS.get(m.group(1)) if m else None
        async with self._pool.acquire() as con:
            if upper.startswith("INSERT") and pk and "RETURNING" not in upper:
                row = await con.fetchrow(f"{stmt} RETURNING {pk}", *args)
                return _PgCursor(1 if row is not None else 0,
                                 row[pk] if row is not None else None)
            status = await con.execute(stmt, *args)
        # ステータス文字列（"INSERT 0 1" / "UPDATE 3" 等）から件数を取り出す
        try:
            rowcount = int(status.split()[-1])
        except (ValueError, IndexError):
            rowcount = 0
        return _PgCursor(rowcount)

    async def fetchone(self, sql: str, params: tuple = ()):
        if self._is_pg:
            assert self._pool is not None
            stmt, args = self._prepare(sql, params)
            async with self._pool.acquire() as con:
                return await con.fetchrow(stmt, *args)
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: tuple = ()) -> list:
        if self._is_pg:
            assert self._pool is not None
            stmt, args = self._prepare(sql, params)
            async with self._pool.acquire() as con:
                return list(await con.fetch(stmt, *args))
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)

    async def _executescript(self, sql: str) -> None:
        """複数文の実行（マイグレーション用）。"""
        if self._is_pg:
            assert self._pool is not None
            async with self._pool.acquire() as con:
                await con.execute(sql)
            return
        await self.conn.executescript(sql)

    async def _table_columns(self, table: str) -> list[str]:
        if self._is_pg:
            rows = await self.fetchall(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = ?",
                (table,))
            return [r["column_name"] for r in rows]
        cur = await self.conn.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [row[1] for row in rows]

    # ------------------------------------------------------------------
    # マイグレーション
    # ------------------------------------------------------------------
    async def _migrate(self) -> None:
        """
        既存 DB の簡易マイグレーション（SQLite 専用）。

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
        await self._migrate_versioned()

    async def _user_version(self) -> int:
        if self._is_pg:
            row = await self.fetchone(
                "SELECT version FROM schema_meta WHERE id = 1")
            return int(row["version"]) if row else 0
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0

    async def _set_user_version(self, version: int) -> None:
        if self._is_pg:
            await self.execute(
                "INSERT INTO schema_meta (id, version) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET version = excluded.version",
                (version,))
            return
        await self.conn.execute(f"PRAGMA user_version = {version}")
        await self.conn.commit()

    async def _migrate_versioned(self) -> None:
        """
        スキーマバージョン管理によるバージョン付きマイグレーション。

        user_version=0 は「guild_id 導入前または v1 相当の DB」を表す。
        v1（guild_id 導入）は _migrate_guild_id() が担うため、ここでは
        v2 以降を適用する。各ステップは冪等。
        """
        version = await self._user_version()
        if version >= SCHEMA_VERSION:
            return

        if version < 2:
            await self._migrate_v2_guild_foundation()

        if version < 3:
            await self._migrate_v3_teams_skills()

        if version < 5:
            # v4: todoist_configs（init_schema で作成済み）
            # v5: Sheets 廃止に伴う NocoDB 表示用ビュー（最新定義で作り直す）
            await self._migrate_v5_views()

        await self._set_user_version(SCHEMA_VERSION)
        log.info("スキーマバージョンを %d に更新しました。", SCHEMA_VERSION)

    async def _migrate_v5_views(self) -> None:
        """v5: 表示用ビューを最新定義で作り直す（冪等）。

        PostgreSQL は CREATE OR REPLACE VIEW、SQLite は
        DROP VIEW IF EXISTS + CREATE VIEW の再作成方式
        （DDL 内に DROP を含む）で最新化する。
        """
        if self._is_pg:
            await self._executescript(POSTGRES_VIEW_DDL)
            return
        await self._executescript(SQLITE_VIEW_DDL)

    async def _migrate_v2_guild_foundation(self) -> None:
        """
        v2: guilds（ギルド台帳）と audit_log を導入する。

        テーブル自体は init_schema（CREATE TABLE IF NOT EXISTS）で作成済み。
        ここでは既存データからギルド台帳をバックフィルする。
        台帳の名称は settings の GUILD_NAME（あれば）を使い、
        無ければ '(unknown)' とする（起動時の _ensure_guild_setup が正しい名称で
        上書きする）。
        """
        rows = await self.fetchall(
            "SELECT DISTINCT guild_id FROM settings WHERE guild_id > 0")
        for row in rows:
            gid = int(row["guild_id"])
            name_row = await self.fetchone(
                "SELECT setting_value FROM settings"
                " WHERE guild_id = ? AND setting_key = 'GUILD_NAME'",
                (gid,))
            name = name_row["setting_value"] if name_row else "(unknown)"
            await self.execute(
                "INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version)"
                f" VALUES (?, ?, {self._now_sql()}, 2)"
                " ON CONFLICT(guild_id) DO NOTHING",
                (gid, name))
        if rows:
            log.info("ギルド台帳をバックフィルしました（%d ギルド）。", len(rows))

    async def _migrate_v3_teams_skills(self) -> None:
        """
        v3: 班・技能タグの DB 管理化。

        - skill_tags テーブルは init_schema（CREATE TABLE IF NOT EXISTS）で作成済み。
        - teams に member_role_id / secondary_role_id / created_at / updated_at を
          追加する（既に存在する場合はスキップ）。
        - settings の PRIMARY_TEAM_ROLE_IDS / SECONDARY_TEAM_ROLE_IDS（書式:
          "team_key:role_id,team_key:role_id"）を teams.member_role_id /
          secondary_role_id へバックフィルする（未設定の行のみ）。
          settings のキー自体は後方互換のフォールバックとして残す。
        """
        cols = await self._table_columns("teams")
        for col in ("member_role_id", "secondary_role_id", "created_at", "updated_at"):
            if col not in cols:
                await self.execute(f"ALTER TABLE teams ADD COLUMN {col} TEXT")
                log.info("teams テーブルに %s カラムを追加しました。", col)

        rows = await self.fetchall(
            "SELECT guild_id, setting_key, setting_value FROM settings"
            " WHERE setting_key IN ('PRIMARY_TEAM_ROLE_IDS', 'SECONDARY_TEAM_ROLE_IDS')")
        backfilled = 0
        for row in rows:
            target_col = ("member_role_id" if row["setting_key"] == "PRIMARY_TEAM_ROLE_IDS"
                          else "secondary_role_id")
            for part in (row["setting_value"] or "").split(","):
                part = part.strip()
                if ":" not in part:
                    continue
                key, _, val = part.partition(":")
                key, val = key.strip(), val.strip()
                if not key or not val.isdigit():
                    continue
                cur = await self.execute(
                    f"UPDATE teams SET {target_col} = ?"
                    f" WHERE guild_id = ? AND team_key = ? AND {target_col} IS NULL",
                    (val, int(row["guild_id"]), key))
                backfilled += cur.rowcount
        if backfilled:
            log.info("teams のロール紐付けをバックフィルしました（%d 件）。", backfilled)

    async def _migrate_guild_id(self) -> None:
        """
        guild_id を持たない旧テーブルを新スキーマへ移行する（テーブル再作成方式）。
        SQLite 専用（PostgreSQL では新規スキーマで開始する）。

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

    async def _pg_fix_sequences(self) -> None:
        """
        PostgreSQL の IDENTITY シーケンスを既存最大値に合わせる。

        明示的な ID 挿入（SQLite からのデータ移行など）のあとに
        シーケンスが実データより小さいと PK 衝突が起きるため、
        接続のたびに冪等に修復する。
        """
        for table, pk in _PK_COLUMNS.items():
            await self.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'),"
                f" COALESCE((SELECT MAX({pk}) FROM {table}), 1),"
                f" (SELECT MAX({pk}) FROM {table}) IS NOT NULL)")

    async def _count(self, table: str) -> int:
        row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # 設定関連メソッド（guild_id スコープ）
    # ------------------------------------------------------------------
    async def get_setting(self, guild_id: int, key: str) -> str | None:
        """設定値を取得する"""
        row = await self.fetchone(
            "SELECT setting_value FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
        )
        return row["setting_value"] if row else None

    async def set_setting(self, guild_id: int, key: str, value: str) -> None:
        """設定値を保存する（存在すれば更新、なければ挿入）"""
        now_sql = self._now_sql()
        await self.execute(
            f"""INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
               VALUES (?, ?, ?, {now_sql})
               ON CONFLICT(guild_id, setting_key) DO UPDATE SET
               setting_value = excluded.setting_value,
               updated_at = {now_sql}""",
            (guild_id, key, value)
        )

    async def delete_setting(self, guild_id: int, key: str) -> bool:
        """設定値を削除する"""
        cur = await self.execute(
            "DELETE FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
        )
        return cur.rowcount > 0

    async def get_all_settings(self, guild_id: int) -> dict[str, str]:
        """指定ギルドの全ての設定を辞書で取得する"""
        rows = await self.fetchall(
            "SELECT setting_key, setting_value FROM settings WHERE guild_id = ?",
            (guild_id,),
        )
        return {row["setting_key"]: row["setting_value"] for row in rows}
