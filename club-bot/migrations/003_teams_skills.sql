-- =====================================================================
-- 003_teams_skills.sql
--
-- 班・技能タグの DB 管理化マイグレーション（スキーマバージョン 3）:
--   1. skill_tags（技能タグ マスタ）テーブルの追加
--   2. teams に member_role_id / secondary_role_id / created_at / updated_at を追加
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. 実行:
--        sqlite3 data/club.db ".read migrations/003_teams_skills.sql"
--
-- ※ SQLite の ALTER TABLE ADD COLUMN には IF NOT EXISTS が無い。
--   「duplicate column name」エラーはそのカラムが適用済みであることを
--   意味する。Bot 側の自動マイグレーション（utils/db.py の
--   _migrate_v3_teams_skills）はカラム存在を確認してから追加するため冪等。
--
-- ※ 本スクリプトは DDL のみで user_version を 3 にしない。
--   settings の PRIMARY_TEAM_ROLE_IDS / SECONDARY_TEAM_ROLE_IDS から
--   teams カラムへのバックフィル（CSV 形式のパースを含む）は、
--   Bot 起動時の自動マイグレーションが行う。本スクリプト適用後に
--   Bot を起動すれば、カラム存在を検出してバックフィルのみ実行され、
--   user_version が 3 に更新される。
--
-- ※ 前提: 001（guild_id 導入）・002（guilds / audit_log）適用済みであること。
-- =====================================================================

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- skill_tags: 技能タグ マスタ（ギルド別。名前はギルド内で一意）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL CHECK (guild_id >= 0),
    skill_name   TEXT NOT NULL,
    active_flag  INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (guild_id, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_skill_tags_guild ON skill_tags(guild_id, active_flag);

-- ---------------------------------------------------------------------
-- teams: ロール紐付け・タイムスタンプ カラム追加
--   member_role_id    : 主所属メンバーに付与する Discord ロール ID
--   secondary_role_id : 副所属メンバーに付与する Discord ロール ID
--   （settings の PRIMARY_TEAM_ROLE_IDS / SECONDARY_TEAM_ROLE_IDS から
--     Bot 起動時にバックフィルされる）
-- ---------------------------------------------------------------------
ALTER TABLE teams ADD COLUMN member_role_id TEXT;
ALTER TABLE teams ADD COLUMN secondary_role_id TEXT;
ALTER TABLE teams ADD COLUMN created_at TEXT;
ALTER TABLE teams ADD COLUMN updated_at TEXT;

COMMIT;
PRAGMA foreign_keys = ON;

-- 検証クエリ例:
--   PRAGMA table_info(teams);      -- 4 カラムが追加されていること
--   SELECT * FROM skill_tags;
--   -- Bot 起動後:
--   PRAGMA user_version;           -- 3 が返ること
--   SELECT team_key, member_role_id, secondary_role_id FROM teams;
