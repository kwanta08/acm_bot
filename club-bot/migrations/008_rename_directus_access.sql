-- =====================================================================
-- 008_rename_directus_access.sql
--
-- directus_access を guild_directus_access へ改名する
-- （スキーマバージョン 9）。
--
-- 背景:
--   Directus は業務 DB（clubdb）と同じデータベースへ自身のシステム
--   テーブルを作成する。Directus 11 にはシステムコレクション
--   `directus_access`（ユーザー/ロールとポリシーの紐付け）があり、
--   006_directus_access.sql が作成した bot 側の同名テーブルと衝突する。
--   衝突したまま Directus を初回起動すると、Directus が自身の
--   directus_access へ INSERT する際に bot 側の guild_id NOT NULL 制約に
--   掛かり、次のエラーで初期化できない:
--
--     Value for field "guild_id" in collection "directus_access"
--     can't be null.
--
--   このため bot 側テーブルを `directus_` で始まらない名前へ改名する。
--
-- 安全性:
--   Directus 自身の directus_access には guild_id 列が無い。本スクリプトは
--   bot が作成した旧テーブル（guild_id 列を持つ）だけを対象とする前提で
--   あり、適用前に必ず下記の確認クエリで対象を確かめること。
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. 確認（guild_id 列があれば bot のテーブル）:
--        sqlite3 data/club.db "PRAGMA table_info(directus_access);"
--   3. 実行:
--        sqlite3 data/club.db ".read migrations/008_rename_directus_access.sql"
--
-- 使い方（PostgreSQL）:
--   docker compose -f deploy/docker-compose.directus.yml exec -T postgres \
--     psql -U clubbot clubdb -c \
--     "ALTER TABLE directus_access RENAME TO guild_directus_access"
--
-- ※ Bot 側の自動マイグレーション（_migrate_v9_rename_directus_access）が
--   同等の処理を冪等に行うため、通常は Bot を起動するだけでよい。
--   自動マイグレーションは guild_id 列の有無で bot のテーブルかどうかを
--   判定するため、Directus のシステムテーブルには一切触れない。
-- ※ 本スクリプトは user_version を更新しない。Bot 起動時に 9 に更新される。
-- =====================================================================

BEGIN TRANSACTION;

-- Directus アクセス発行状況（1ギルド1件）。
-- status: invited（招待済み）/ active（利用中）/ revoked（失効）
-- 新規 DB 用の定義（既に改名済み・新規作成済みなら何もしない）
CREATE TABLE IF NOT EXISTS guild_directus_access (
    guild_id         INTEGER PRIMARY KEY CHECK (guild_id > 0),
    directus_user_id TEXT NOT NULL,
    email            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'invited',
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

COMMIT;

-- 旧テーブルが残っている場合の移行（上記確認クエリで guild_id 列が
-- あることを確かめてから、以下を手動で実行する）:
--   INSERT INTO guild_directus_access
--       (guild_id, directus_user_id, email, status,
--        created_by, created_at, updated_at)
--   SELECT guild_id, directus_user_id, email, status,
--          created_by, created_at, updated_at
--   FROM directus_access
--   ON CONFLICT (guild_id) DO NOTHING;
--   DROP TABLE directus_access;

-- 検証クエリ例:
--   PRAGMA table_info(guild_directus_access);
--   SELECT guild_id, email, status FROM guild_directus_access;
