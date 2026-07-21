-- =====================================================================
-- 002_guild_foundation.sql
--
-- DB 基盤強化マイグレーション（スキーマバージョン 2）:
--   1. guilds（ギルド台帳）テーブルの追加
--   2. audit_log（監査ログ）テーブルの追加
--   3. settings に存在するギルドから guilds 台帳をバックフィル
--   4. PRAGMA user_version を 2 に更新
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. 実行:
--        sqlite3 data/club.db ".read migrations/002_guild_foundation.sql"
--
-- ※ Bot 側の utils/db.py _migrate_versioned() も同等の処理を起動時に
--   自動実行するため、通常は Bot を起動するだけでこの移行が完了する。
--   本ファイルは手動移行・検証用。冪等（何度実行しても安全）。
-- ※ 前提: 001_add_guild_id.sql（guild_id 導入）が適用済みであること。
--   guild_id 未導入の DB では settings.guild_id が存在せず失敗する。
-- =====================================================================

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- guilds: ギルド台帳（guild_id がそのまま PK。正のギルド ID のみ）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS guilds (
    guild_id      INTEGER PRIMARY KEY CHECK (guild_id > 0),
    guild_name    TEXT NOT NULL,
    joined_at     TEXT NOT NULL,
    setup_version INTEGER NOT NULL DEFAULT 2
);

-- ---------------------------------------------------------------------
-- audit_log: 監査ログ（管理者操作の証跡。機密値は保存しない）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL CHECK (guild_id >= 0),
    actor_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_guild ON audit_log(guild_id, audit_id);

-- ---------------------------------------------------------------------
-- ギルド台帳のバックフィル
--   settings に存在する正の guild_id を台帳へ登録する。
--   名称は GUILD_NAME 設定（あれば）を使い、無ければ '(unknown)'。
--   （Bot 起動時の _ensure_guild_setup が正しい名称で上書きする）
-- ---------------------------------------------------------------------
INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version)
SELECT s.guild_id,
       COALESCE(
           (SELECT g.setting_value FROM settings g
             WHERE g.guild_id = s.guild_id AND g.setting_key = 'GUILD_NAME'),
           '(unknown)'),
       datetime('now', 'localtime'),
       2
FROM (SELECT DISTINCT guild_id FROM settings WHERE guild_id > 0) s
WHERE true
ON CONFLICT(guild_id) DO NOTHING;

-- スキーマバージョンを 2 に更新
PRAGMA user_version = 2;

COMMIT;
PRAGMA foreign_keys = ON;

-- 検証クエリ例:
--   SELECT * FROM guilds;
--   PRAGMA user_version;  -- 2 が返ること
