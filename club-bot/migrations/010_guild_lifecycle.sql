-- =====================================================================
-- 010_guild_lifecycle.sql
--
-- guilds に退出日時（left_at）と自動削除の予定日時（purge_after）を
-- 追加する（スキーマバージョン 11）。
--
-- 背景:
--   on_guild_remove ハンドラが存在せず、Bot をサーバーからキックしても
--   データが残り続けていた（docs/PRIVACY.md にも「削除は運営者へ連絡」と
--   書かれている状態）。公開配布する Bot として、導入サークルが自分の
--   データを自分で消せる／外したら消えることは必須要件。
--
-- 方針:
--   **退出しただけでは消さない。** 退出時に left_at と purge_after を
--   記録し、purge_after を過ぎたギルドだけを日次ジョブが削除する。
--   誤ってキックした場合や一時的に外した場合、猶予期間内に再招待すれば
--   データはそのまま復活する（on_guild_join で両列を NULL に戻す）。
--
--   猶予は既定30日。ギルド別設定 DATA_RETENTION_DAYS で上書きできる
--   （0 を指定すると退出時点で削除対象になる）。
--
-- 後方互換:
--   どちらの列も NULL 許容で追加するため既存行は壊れない。
--   既存の全ギルドは left_at IS NULL = 「参加中」として扱われ、
--   マイグレーションによって削除対象になることはない。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v11_guild_lifecycle() が
--   自動適用する（SQLite / PostgreSQL 共通）。本ファイルは PostgreSQL で
--   手動適用する場合・レビュー用の参照定義。
--
--   psql "$DATABASE_URL" -f migrations/010_guild_lifecycle.sql
--
-- 冪等性:
--   ADD COLUMN IF NOT EXISTS のため繰り返し実行しても安全。
-- =====================================================================

BEGIN;

-- 退出日時（ISO 文字列。参加中は NULL）
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS left_at TEXT;

-- 自動削除の予定日時（ISO 文字列。参加中は NULL）
-- この日時を過ぎたギルドのデータを日次ジョブが全テーブルから削除する。
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS purge_after TEXT;

-- 削除対象の抽出は purge_after の範囲検索で行う
CREATE INDEX IF NOT EXISTS idx_guilds_purge_after
    ON guilds(purge_after);

-- スキーマバージョンを 11 へ
INSERT INTO schema_meta (id, version) VALUES (1, 11)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'guilds' AND column_name IN ('left_at', 'purge_after');
-- SELECT version FROM schema_meta WHERE id = 1;   -- 11 になっていること
-- SELECT guild_id, guild_name, left_at, purge_after FROM guilds;
