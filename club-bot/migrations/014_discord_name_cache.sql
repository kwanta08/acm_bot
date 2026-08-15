-- =====================================================================
-- 014_discord_name_cache.sql
--
-- Discord 表示名のギルド別キャッシュを追加する（スキーマバージョン 15）。
--
-- 背景:
--   ダッシュボードは設計上 Discord Bot トークンを持たない別プロセスで、
--   Discord API からユーザー名・チャンネル名を取得できない。
--   このため画面には Discord ユーザー ID・チャンネル ID が生のまま
--   表示されていた。
--
-- 設計:
--   bot がギルドキャッシュ（gateway のイベントと起動時の全同期）から
--   本テーブルへ名前を書き込み、ダッシュボードは表示時にここを読む。
--   - entity_type='user'    : そのギルドでの表示名
--                             （ニックネーム → グローバル表示名 → ユーザー名）
--   - entity_type='channel' : チャンネル名（表示時に # を付ける）
--
--   ユーザーの行はメンバーがサーバーを抜けても消さない（過去の出欠・
--   作業記録の表示名として最後に知られた名前を使えるようにするため）。
--   チャンネルの行は削除イベントで消す（削除済みはフォールバック表示）。
--
--   これは**キャッシュでありデータの正本ではない**。全行を消しても
--   bot の次回起動時の同期で復元される。DB の各テーブルは従来どおり
--   ID を保持し、表示名への解決は表示層（dashboard/display.py）で行う。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v15_name_cache() が
--   自動適用する（SQLite / PostgreSQL 共通）。本ファイルは PostgreSQL で
--   手動適用する場合・レビュー用の参照定義。
--
--   psql "$DATABASE_URL" -f migrations/014_discord_name_cache.sql
--
-- 冪等性:
--   CREATE TABLE IF NOT EXISTS のため繰り返し実行しても安全。
-- =====================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS discord_name_cache (
    guild_id    BIGINT NOT NULL CHECK (guild_id >= 0),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('user', 'channel')),
    entity_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (guild_id, entity_type, entity_id)
);

-- 参照は常に (guild_id, entity_type) 前方一致なので複合 PK で足りる
-- （追加のインデックスは張らない）。

-- スキーマバージョンを 15 へ
INSERT INTO schema_meta (id, version) VALUES (1, 15)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT entity_type, COUNT(*) FROM discord_name_cache GROUP BY entity_type;
--   -- bot を一度起動（再接続）すると user / channel の行が入ること
-- SELECT version FROM schema_meta WHERE id = 1;   -- 15 になっていること
