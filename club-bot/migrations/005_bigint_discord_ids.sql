-- =====================================================================
-- 005_bigint_discord_ids.sql（PostgreSQL 専用）
--
-- Discord Snowflake ID（64bit 整数）を格納する guild_id 列を
-- INTEGER(int4) から BIGINT(int8) へ変更する。
--
-- 背景:
--   to_pg_ddl() の変換漏れにより、一部テーブル（todoist_configs）の
--   guild_id が int4 で作成されていた。2^31 を超えるギルド ID で
--   asyncpg.exceptions.DataError: value out of int32 range が発生する。
--
-- 使い方（本番 PostgreSQL に適用）:
--   1. 事前バックアップ:
--        docker compose -f deploy/docker-compose.nocodb.yml exec postgres \
--          pg_dump -U clubbot clubdb > backup_$(date +%Y%m%d).sql
--   2. 適用:
--        docker compose -f deploy/docker-compose.nocodb.yml exec -T postgres \
--          psql -U clubbot clubdb < migrations/005_bigint_discord_ids.sql
--   3. bot を再起動する（重要。asyncpg はクエリ文字列単位でプリペアド文を
--      キャッシュしており、ALTER 前の int4 型推論がプロセス内に残るため）:
--        sudo systemctl restart club-bot
--
-- 安全性:
--   - int4 → int8 は値を失わない拡張変換。既に BIGINT の列への ALTER は
--     実質 no-op（冪等）。
--   - guild_id を参照する FK は存在しない（schedules 系の FK は
--     schedule_id(TEXT) 参照のため）。PK 列の TYPE 変更は PostgreSQL で
--     許可される。
--   - ビュー（v_todoist_status 等）は列の型変更の影響を受けるため、
--     先に DROP してから再作成する（冪等）。
--   - インデックス・主キー・UNIQUE 制約は型変更後も維持される。
-- =====================================================================

BEGIN;

-- 1. 依存ビューを一時削除（最後に再作成する）
DROP VIEW IF EXISTS v_todoist_status;
DROP VIEW IF EXISTS v_attendance;
DROP VIEW IF EXISTS v_team_summary;

-- 2. guild_id を BIGINT へ変更（全テーブル。既に BIGINT でも no-op）
ALTER TABLE guilds            ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE settings          ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE teams             ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE members           ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE schedules         ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE schedule_options  ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE schedule_votes    ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE tasks             ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE reminders_log     ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE todoist_sections  ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE todoist_configs   ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE layer_sessions    ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE layer_records     ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE layer_keta        ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE audit_log         ALTER COLUMN guild_id TYPE BIGINT;
ALTER TABLE skill_tags        ALTER COLUMN guild_id TYPE BIGINT;

-- 3. ビューを再作成（POSTGRES_VIEW_DDL と同一内容）
CREATE OR REPLACE VIEW v_todoist_status AS
SELECT guild_id, project_id, today_label_name, enabled_flag, updated_at
FROM todoist_configs;

CREATE OR REPLACE VIEW v_attendance AS
SELECT s.guild_id,
       s.schedule_id,
       s.title       AS event_title,
       o.label       AS option_label,
       v.user_id,
       v.status,
       v.updated_at,
       s.deadline
FROM schedule_votes v
JOIN schedule_options o
  ON o.guild_id = v.guild_id AND o.option_id = v.option_id
JOIN schedules s
  ON s.guild_id = o.guild_id AND s.schedule_id = o.schedule_id;

CREATE OR REPLACE VIEW v_team_summary AS
SELECT t.guild_id,
       t.team_key,
       t.team_name,
       COUNT(m.user_id)              AS member_count,
       COALESCE(SUM(m.is_leader), 0) AS leader_count
FROM teams t
LEFT JOIN members m
  ON m.guild_id = t.guild_id
 AND m.primary_team = t.team_key
 AND m.active_flag = 1
WHERE t.active_flag = 1
GROUP BY t.guild_id, t.team_key, t.team_name;

COMMIT;

-- 検証クエリ例（すべて bigint = 8 であること）:
--   SELECT table_name, column_name, data_type
--   FROM information_schema.columns
--   WHERE column_name = 'guild_id' ORDER BY table_name;
-- 動作確認例（2^31 超のギルド ID でエラーにならないこと）:
--   INSERT INTO todoist_configs (guild_id, api_token_encrypted, created_by, created_at, updated_at)
--   VALUES (3000000000000000001, 'dummy', 'mig', '2026-01-01', '2026-01-01');
--   DELETE FROM todoist_configs WHERE guild_id = 3000000000000000001;
