-- =====================================================================
-- 021_remove_features.sql
--
-- 技能タグ・工具の貸出・ヒヤリハット報告の廃止と、タスクの Todoist 一本化
-- （スキーマバージョン 22）。
--
-- 背景:
--   運用してみて使われなかった機能を落とし、タスク管理は Todoist に
--   一本化する方針に変えた。bot は「Discord からの操作面」に徹し、
--   タスクの正本は Todoist 側だけに置く。
--
-- 落とすもの:
--   - skill_tags（技能タグ マスタ）と members.skills（本人が持つタグ）
--   - tools / tool_loans（工具・機材の貸出）
--   - incidents（ヒヤリハット・事故報告）
--   - tasks（ローカルのタスク台帳）
--
-- 残すもの:
--   - todoist_configs（ギルド別の Todoist 接続設定）
--   - todoist_sections（班 ↔ Todoist セクションの紐付け）
--     どちらもタスクそのものではなく**ギルド別設定**なので残す。
--     班別の通知・配置先の解決に必要。
--
-- **これは破壊的変更である。**
--   上記テーブルの行は戻らない。適用前に必ず pg_dump -Fc を取ること。
--   （bot は起動時に自動適用する。運用者が中身を残したい場合は、
--     起動前に該当テーブルを別名でコピーしておくこと）
--
-- members.skills を落とす理由:
--   技能タグ機能そのものを廃止したため、参照するコードが無くなった。
--   列を残すと「読み書きされないのに個人情報を保持し続ける」状態になる
--   （docs/PRIVACY.md の「使わないものは持たない」方針）。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v22_remove_features() が
--   自動適用する。本ファイルは PostgreSQL で手動適用する場合・レビュー用。
--
--   psql "$DATABASE_URL" -f migrations/021_remove_features.sql
--
-- 注意:
--   - down はない
--   - asyncpg のプリペアド文キャッシュのため、手動適用後は bot を再起動すること
--
-- 冪等性:
--   DROP TABLE IF EXISTS / DROP COLUMN IF EXISTS のため繰り返し実行しても安全。
--   v19〜v21 を一度も通っていない DB（tools / incidents が存在しない）でも
--   そのまま通る。
-- =====================================================================

BEGIN;

-- 技能タグ
DROP TABLE IF EXISTS skill_tags;
ALTER TABLE members DROP COLUMN IF EXISTS skills;

-- 工具・機材の貸出（tool_loans が tools を参照するので子から）
DROP TABLE IF EXISTS tool_loans;
DROP TABLE IF EXISTS tools;

-- ヒヤリハット・事故報告
DROP TABLE IF EXISTS incidents;

-- ローカルのタスク台帳（正本は Todoist へ）
DROP TABLE IF EXISTS tasks;

-- スキーマバージョンを 22 へ
INSERT INTO schema_meta (id, version) VALUES (1, 22)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT table_name FROM information_schema.tables
--  WHERE table_schema = 'public'
--    AND table_name IN ('skill_tags', 'tools', 'tool_loans', 'incidents', 'tasks');
--  -- 0 行であること
-- SELECT column_name FROM information_schema.columns
--  WHERE table_name = 'members' AND column_name = 'skills';
--  -- 0 行であること
-- SELECT version FROM schema_meta WHERE id = 1;  -- 22 になっていること
