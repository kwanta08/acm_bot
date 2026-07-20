-- =====================================================================
-- GUILD_VIEWS.sql（雛形）
--
-- NocoDB などの外部 BI / DB ツールから利用するための
-- 「guild_id でフィルタしたビュー」定義テンプレート。
--
-- 設計方針:
--   - テーブル名・カラム名はスネークケースを維持
--   - 各テーブルは単一の整数 PK（AUTOINCREMENT）か、
--     (guild_id, ...) の分かりやすい複合キーを持つ
--   - ギルドごとのビュー名は v_<table>_g<guild_id> とする
--
-- 使い方:
--   :guild_id を対象ギルドの ID に置き換えてから実行する
--   （sqlite3 CLI なら `.parameter set :guild_id 123456789012345678`）。
--   ギルドごとに必要なビューだけ CREATE すればよい。
-- =====================================================================

-- 設定（ギルド別）
CREATE VIEW IF NOT EXISTS v_settings_g:guild_id AS
SELECT setting_key, setting_value, updated_at
FROM settings
WHERE guild_id = :guild_id;

-- 班マスタ（ギルド別）
CREATE VIEW IF NOT EXISTS v_teams_g:guild_id AS
SELECT team_id, team_key, team_name, leader_role_id, channel_id, active_flag
FROM teams
WHERE guild_id = :guild_id;

-- メンバー（ギルド別）
CREATE VIEW IF NOT EXISTS v_members_g:guild_id AS
SELECT user_id, display_name, primary_team, secondary_teams, is_leader,
       skills, notes, joined_at, active_flag
FROM members
WHERE guild_id = :guild_id;

-- 日程調整（ギルド別）
CREATE VIEW IF NOT EXISTS v_schedules_g:guild_id AS
SELECT schedule_id, title, description, place, target_role_id, deadline,
       created_by, channel_id, closed_flag, reminder_sent_flag, sheet_title
FROM schedules
WHERE guild_id = :guild_id;

-- 日程調整の候補（ギルド別）
CREATE VIEW IF NOT EXISTS v_schedule_options_g:guild_id AS
SELECT option_id, schedule_id, label, start_at, end_at, message_id
FROM schedule_options
WHERE guild_id = :guild_id;

-- 日程調整の投票（ギルド別）
CREATE VIEW IF NOT EXISTS v_schedule_votes_g:guild_id AS
SELECT vote_id, option_id, user_id, status, updated_at
FROM schedule_votes
WHERE guild_id = :guild_id;

-- タスク（ギルド別）
CREATE VIEW IF NOT EXISTS v_tasks_g:guild_id AS
SELECT local_task_id, todoist_task_id, title, assignee_id, team_key, due_date,
       priority, location_key, status, created_by, created_at, completed_at
FROM tasks
WHERE guild_id = :guild_id;

-- 通知ログ（ギルド別）
CREATE VIEW IF NOT EXISTS v_reminders_log_g:guild_id AS
SELECT reminder_id, reminder_type, target_id, target_user_id, sent_channel_id,
       sent_at, status, error_message
FROM reminders_log
WHERE guild_id = :guild_id;

-- Todoist セクション紐付け（ギルド別）
CREATE VIEW IF NOT EXISTS v_todoist_sections_g:guild_id AS
SELECT section_id, team_key, section_name, updated_at
FROM todoist_sections
WHERE guild_id = :guild_id;

-- 層塗り 進行中セッション（ギルド別）
CREATE VIEW IF NOT EXISTS v_layer_sessions_g:guild_id AS
SELECT session_id, user_id, keta, layer_num, started_at
FROM layer_sessions
WHERE guild_id = :guild_id;

-- 層塗り 完了記録（ギルド別）
CREATE VIEW IF NOT EXISTS v_layer_records_g:guild_id AS
SELECT record_id, user_id, keta, layer_num, started_at, ended_at, minutes, synced_flag
FROM layer_records
WHERE guild_id = :guild_id;

-- 桁名マスタ（ギルド別）
CREATE VIEW IF NOT EXISTS v_layer_keta_g:guild_id AS
SELECT keta_id, keta_name, active_flag, created_by, created_at
FROM layer_keta
WHERE guild_id = :guild_id;
