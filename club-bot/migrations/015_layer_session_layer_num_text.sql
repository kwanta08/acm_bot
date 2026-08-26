-- =====================================================================
-- 015_layer_session_layer_num_text.sql
--
-- layer_sessions.layer_num を INTEGER から TEXT へ変更する（スキーマバージョン 16）。
--
-- 背景:
--   /layer start の層番号は数字のほか「シュリンク」等のテキストを受け付ける
--   仕様（コマンド定義の describe に明記。完了記録側の
--   layer_records.layer_num は当初から TEXT）だが、進行中セッションの
--   layer_sessions.layer_num だけ INTEGER で作られていた。
--
--   SQLite は動的型付けでテキストも保存できてしまうため開発環境では
--   顕在化せず、本番（PostgreSQL / asyncpg）だけが
--     DataError: invalid input for query argument $4: 'test'
--     ('str' object cannot be interpreted as an integer)
--   で /layer start に失敗していた。
--
-- 後方互換:
--   既存の数値の層番号は '3' のような文字列へ変換される。この列は
--   表示（/layer status・進行中エラーの案内・/layer end の完了記録への
--   引き渡し）にしか使わず、数値として比較・集計する箇所は無い。
--
-- 適用方法:
--   通常は bot 起動時に utils/db.py の _migrate_v16_layer_num_text() が
--   自動適用する（SQLite / PostgreSQL 共通）。本ファイルは PostgreSQL で
--   手動適用する場合・レビュー用の参照定義。
--
--   psql "$DATABASE_URL" -f migrations/015_layer_session_layer_num_text.sql
--
-- 注意:
--   - docs/GUILD_VIEWS.sql の雛形から layer_sessions を参照するビュー
--     （v_layer_sessions_g<guild_id> 等）を手動作成している場合、列の型変更が
--     ビュー依存で失敗する（cannot alter type of a column used by a view）。
--     これは **bot 起動時の自動適用でも同じ**で、該当ビューがあると起動に
--     失敗する（bot はビューの定義を知らないため自動では退避できない）。
--     デプロイ前に
--       SELECT viewname FROM pg_views WHERE definition LIKE '%layer_sessions%';
--     で有無を確認し、あれば先に DROP → 適用（または bot 起動）後に作り直すこと
--   - asyncpg はクエリ文字列単位でプリペアド文をキャッシュするため、
--     手動適用後は bot を再起動して接続プールを作り直すこと
--     （migrations/005 と同じ注意点）
--
-- 冪等性:
--   TEXT への ALTER は既に TEXT の列にも成功するため繰り返し実行しても安全
--   （utils/db.py 側は information_schema で型を確認してからスキップする）。
-- =====================================================================

BEGIN;

-- 進行中セッションの層番号をテキスト化（既存の数値は文字列へ変換）
ALTER TABLE layer_sessions
    ALTER COLUMN layer_num TYPE TEXT USING layer_num::text;

-- スキーマバージョンを 16 へ
INSERT INTO schema_meta (id, version) VALUES (1, 16)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version;

COMMIT;

-- =====================================================================
-- 適用後の確認
-- =====================================================================
-- SELECT data_type FROM information_schema.columns
--  WHERE table_name = 'layer_sessions' AND column_name = 'layer_num';
--  -- text になっていること
-- SELECT version FROM schema_meta WHERE id = 1;   -- 16 になっていること
