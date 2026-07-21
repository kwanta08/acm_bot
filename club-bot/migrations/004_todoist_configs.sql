-- =====================================================================
-- 004_todoist_configs.sql
--
-- Todoist トークンのギルド別暗号化保存マイグレーション（スキーマバージョン 4）:
--   1. todoist_configs テーブルの追加（トークンは Fernet 暗号文で保存）
--   2. v_todoist_status ビューの追加（暗号文を含まない参照用）
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. 実行:
--        sqlite3 data/club.db ".read migrations/004_todoist_configs.sql"
--
-- ※ Bot 側の自動マイグレーション（init_schema）も同等の定義を作成するため、
--   通常は Bot を起動するだけでよい。本ファイルは手動適用・検証用（冪等）。
-- ※ 既存の平文トークン（settings / 環境変数）の移行は本 SQL ではなく、
--   scripts/migrate_todoist_token.py を使用すること（暗号化が必要なため）。
-- ※ 本スクリプトは user_version を更新しない。Bot 起動時に 4 に更新される。
-- =====================================================================

BEGIN TRANSACTION;

-- Todoist 接続設定（1ギルド1件。api_token_encrypted は Fernet 暗号文。
-- 平文トークンは保存しない）
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

-- 暗号文を含まない安全な参照ビュー（NocoDB 等の外部 UI 向け）
CREATE VIEW IF NOT EXISTS v_todoist_status AS
SELECT guild_id, project_id, today_label_name, enabled_flag, updated_at
FROM todoist_configs;

COMMIT;

-- 検証クエリ例:
--   PRAGMA table_info(todoist_configs);
--   SELECT * FROM v_todoist_status;
