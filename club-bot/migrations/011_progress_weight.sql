-- =====================================================================
-- 011_progress_weight.sql
--
-- progress_nodes に目標重量・実測重量を追加する（スキーマバージョン 12）。
--
-- 背景:
--   人力飛行機は機体重量が競技成績に直結するのに、progress_nodes は
--   進捗率しか持っていなかった。機体 → パーツ → 部品の木構造は進捗と
--   重量でまったく同じなので、既存の木と再帰集計をそのまま流用する。
--
-- 設計:
--   独立テーブルを作らず progress_nodes へ列を追加する。理由は
--   (1) 木の形が進捗と同一、(2) /progress のツリーオートコンプリートと
--   load_tree() をそのまま使える、(3) 別テーブルにするとノード削除時の
--   整合管理が増える。
--
--   単位は **グラム固定**（列名の _g で明示。単位設定は作らない）。
--   NULL は「未入力」を意味し、集計では子の合計で補われる。
--
-- 後方互換:
--   どちらの列も NULL 許容で追加するため既存ノードは壊れない。
--   重量を入れていないサーバーでは /progress view の表示も変わらない。
--
-- 型について:
--   PostgreSQL の REAL は 4 バイト float で SQLite の REAL（8 バイト）より
--   精度が低いため DOUBLE PRECISION を使う（utils/db.py の to_pg_ddl と同じ方針）。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v12_progress_weight() が
--   自動適用する（SQLite / PostgreSQL 共通）。本ファイルは PostgreSQL で
--   手動適用する場合・レビュー用の参照定義。
--
--   psql "$DATABASE_URL" -f migrations/011_progress_weight.sql
--
-- 冪等性:
--   ADD COLUMN IF NOT EXISTS のため繰り返し実行しても安全。
-- =====================================================================

BEGIN;

-- 目標重量（g）。設計上の目標値。NULL は未設定
ALTER TABLE progress_nodes
    ADD COLUMN IF NOT EXISTS target_weight_g DOUBLE PRECISION;

-- 実測重量（g）。実際に量った値。NULL は未計測
ALTER TABLE progress_nodes
    ADD COLUMN IF NOT EXISTS actual_weight_g DOUBLE PRECISION;

-- スキーマバージョンを 12 へ
INSERT INTO schema_meta (id, version) VALUES (1, 12)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT column_name, data_type FROM information_schema.columns
--  WHERE table_name = 'progress_nodes'
--    AND column_name IN ('target_weight_g', 'actual_weight_g');
--  -- どちらも double precision になっていること
-- SELECT version FROM schema_meta WHERE id = 1;   -- 12 になっていること
