-- =====================================================================
-- 006_directus_access.sql
--
-- Directus（外部 DB 閲覧 UI）のギルド別アクセス発行状況を保持する
-- directus_access テーブルの追加（スキーマバージョン 7）。
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. 実行:
--        sqlite3 data/club.db ".read migrations/006_directus_access.sql"
--
-- 使い方（PostgreSQL。INTEGER を BIGINT に読み替えて適用する）:
--   docker compose -f deploy/docker-compose.directus.yml exec -T postgres \
--     psql -U clubbot clubdb -c "CREATE TABLE IF NOT EXISTS directus_access (
--       guild_id BIGINT PRIMARY KEY CHECK (guild_id > 0),
--       directus_user_id TEXT NOT NULL, email TEXT NOT NULL,
--       status TEXT NOT NULL DEFAULT 'invited', created_by TEXT NOT NULL,
--       created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
--
-- ※ Bot 側の自動マイグレーション（init_schema / _migrate_v7_directus_access）も
--   同等の定義を作成するため、通常は Bot を起動するだけでよい。
--   本ファイルは手動適用・検証用（冪等）。
-- ※ このテーブルは秘密情報を保持しない。Directus のパスワードは
--   Directus 自身のメール招待フローが扱い、bot は生成も保存もしない。
-- ※ 本スクリプトは user_version を更新しない。Bot 起動時に 8 に更新される。
--
-- !!! 注意（v9 で改名済み） !!!
--   本ファイルが作成する directus_access は Directus 11 のシステム
--   テーブル名と衝突し、Directus の初回セットアップを失敗させる。
--   現行のテーブル名は guild_directus_access であり、改名は
--   migrations/008_rename_directus_access.sql（スキーマバージョン 9）が
--   行う。本ファイルは履歴として残しているだけなので、新規に適用しては
--   ならない。新規 DB では Bot 起動時に正しい名前で作成される。
-- =====================================================================

BEGIN TRANSACTION;

-- Directus アクセス発行状況（1ギルド1件）。
-- status: invited（招待済み）/ active（利用中）/ revoked（失効）
CREATE TABLE IF NOT EXISTS directus_access (
    guild_id         INTEGER PRIMARY KEY CHECK (guild_id > 0),
    directus_user_id TEXT NOT NULL,
    email            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'invited',
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

COMMIT;

-- 検証クエリ例:
--   PRAGMA table_info(directus_access);
--   SELECT guild_id, email, status FROM directus_access;
