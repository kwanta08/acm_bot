-- =====================================================================
-- 016_schedule_confirmed.sql
--
-- schedules に deleted_flag と confirmed_option_id を追加する（スキーマバージョン 17）。
--
-- **1つの版に2列をまとめてあるのは意図的。**
--   - deleted_flag        … G3-3（/schedule delete の論理削除）が使う
--   - confirmed_option_id … G3-4（/schedule confirm の確定日程）が使う
--
--   utils/db.py の _migrate_versioned() は version >= SCHEMA_VERSION で早期 return する。
--   G3-3 が v17 を切ったあとに G3-4 が同じ版へ ALTER を足しても、**既存 DB では
--   二度と実行されない**（新規 DB にだけ列がある状態になり、本番だけ
--   「column does not exist」で落ちる。ClaudeVault gotcha `bot-wont-start-undefined-column`
--   と同型）。そのため G3-3 の時点で両方を入れる。
--   G3-4 は新しい migration を作らないこと。
--
-- 背景:
--   /schedule delete は投票メッセージを削除してから DB を CASCADE 削除しており、
--   **票データが完全に消えていた**。誰がいつ参加と答えたかは、
--   Discord 側のメッセージを消した時点で他のどこにも残らない。
--   /team-remove・/skill-remove・/layer keta-remove は既に論理削除方式なので、
--   方針の統一にもなる。
--
-- 後方互換:
--   どちらも既定値つき（deleted_flag は 0 = 削除されていない）／NULL 許容
--   （confirmed_option_id は NULL = 未確定）の追加のみで、既存行の値は変わらない。
--   マイグレーションを当てただけでは、どの予定も削除済みにならない。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v17_schedule_confirmed() が
--   自動適用する（SQLite / PostgreSQL 共通）。本ファイルは PostgreSQL で
--   手動適用する場合・レビュー用の参照定義。
--
--   psql "$DATABASE_URL" -f migrations/016_schedule_confirmed.sql
--
-- 注意:
--   - down はない（このリポジトリのマイグレーションは全て up のみ）。
--     適用前に pg_dump -Fc を取ること（docs/OPERATION.md §8.2 / docs/DASHBOARD_SETUP.md §11）
--   - asyncpg はクエリ文字列単位でプリペアド文をキャッシュするため、
--     手動適用後は bot を再起動して接続プールを作り直すこと（migrations/005 と同じ注意点）
--
-- 冪等性:
--   ADD COLUMN IF NOT EXISTS のため繰り返し実行しても安全
--   （utils/db.py 側は information_schema / PRAGMA で列の有無を確認してからスキップする）。
-- =====================================================================

BEGIN;

-- 論理削除。1 なら一覧・集計・催促から外す（票は消さない）
ALTER TABLE schedules
    ADD COLUMN IF NOT EXISTS deleted_flag INTEGER NOT NULL DEFAULT 0;

-- 確定した候補（schedule_options.option_id）。NULL は未確定
ALTER TABLE schedules
    ADD COLUMN IF NOT EXISTS confirmed_option_id TEXT;

-- スキーマバージョンを 17 へ
INSERT INTO schema_meta (id, version) VALUES (1, 17)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT column_name, data_type, column_default FROM information_schema.columns
--  WHERE table_name = 'schedules'
--    AND column_name IN ('deleted_flag', 'confirmed_option_id');
--  -- deleted_flag は integer / default 0、confirmed_option_id は text であること
-- SELECT COUNT(*) FROM schedules WHERE deleted_flag <> 0;   -- 0 であること（誰も削除済みにならない）
-- SELECT version FROM schema_meta WHERE id = 1;             -- 17 になっていること
