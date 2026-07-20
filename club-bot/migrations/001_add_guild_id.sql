-- =====================================================================
-- 001_add_guild_id.sql
--
-- マルチテナント化マイグレーション:
--   全テーブルに guild_id を追加し、既存行をレガシーギルド ID で
--   バックフィルした上で、複合キー・インデックスを再作成する
--   （SQLite 互換のテーブル再作成方式）。
--
-- 使い方（sqlite3 CLI）:
--   1. 必ず DB のバックアップを取る:
--        cp data/club.db data/club.db.bak
--   2. レガシーギルド ID（= これまでの GUILD_ID）をバインドして実行:
--        sqlite3 data/club.db \
--          -cmd ".parameter set :legacy_guild_id 123456789012345678" \
--          ".read migrations/001_add_guild_id.sql"
--      ※ :legacy_guild_id を置き換えてもよい（エディタ一括置換など）。
--      ※ Bot 側の utils/db.py _migrate() も同等の処理を自動実行するため、
--        通常は Bot を起動するだけでこの移行が完了する（GUILD_ID 環境変数が
--        バックフィル値として使われる）。本ファイルは手動移行・検証用。
--
-- guild_id は SQLite 上 INTEGER（8バイト符号付き）だが、PostgreSQL 移行時は
-- BIGINT に対応させること。CHECK (guild_id >= 0) で非負を保証している。
-- =====================================================================

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- settings: PK (guild_id, setting_key)
-- ---------------------------------------------------------------------
ALTER TABLE settings RENAME TO settings_legacy;
CREATE TABLE settings (
    guild_id      INTEGER NOT NULL CHECK (guild_id >= 0),
    setting_key   TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (guild_id, setting_key)
);
INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
SELECT :legacy_guild_id, setting_key, setting_value, updated_at FROM settings_legacy;
DROP TABLE settings_legacy;

-- ---------------------------------------------------------------------
-- teams: UNIQUE (guild_id, team_key)
-- ---------------------------------------------------------------------
ALTER TABLE teams RENAME TO teams_legacy;
CREATE TABLE teams (
    team_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL CHECK (guild_id >= 0),
    team_key       TEXT NOT NULL,
    team_name      TEXT NOT NULL,
    leader_role_id TEXT,
    channel_id     TEXT,
    active_flag    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (guild_id, team_key)
);
INSERT INTO teams (guild_id, team_id, team_key, team_name, leader_role_id, channel_id, active_flag)
SELECT :legacy_guild_id, team_id, team_key, team_name, leader_role_id, channel_id, active_flag
FROM teams_legacy;
DROP TABLE teams_legacy;

-- ---------------------------------------------------------------------
-- members: PK (guild_id, user_id)
-- ---------------------------------------------------------------------
ALTER TABLE members RENAME TO members_legacy;
CREATE TABLE members (
    guild_id        INTEGER NOT NULL CHECK (guild_id >= 0),
    user_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);
INSERT INTO members (guild_id, user_id, display_name, primary_team, secondary_teams,
                     is_leader, skills, notes, joined_at, active_flag)
SELECT :legacy_guild_id, user_id, display_name, primary_team, secondary_teams,
       is_leader, skills, notes, joined_at, active_flag
FROM members_legacy;
DROP TABLE members_legacy;

-- ---------------------------------------------------------------------
-- schedules: PK schedule_id + guild_id
-- ---------------------------------------------------------------------
ALTER TABLE schedules RENAME TO schedules_legacy;
CREATE TABLE schedules (
    schedule_id        TEXT PRIMARY KEY,
    guild_id           INTEGER NOT NULL CHECK (guild_id >= 0),
    title              TEXT NOT NULL,
    description        TEXT,
    place              TEXT,
    target_role_id     TEXT,
    deadline           TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    channel_id         TEXT NOT NULL,
    closed_flag        INTEGER NOT NULL DEFAULT 0,
    reminder_sent_flag INTEGER NOT NULL DEFAULT 0,
    sheet_title        TEXT
);
INSERT INTO schedules (guild_id, schedule_id, title, description, place, target_role_id,
                       deadline, created_by, channel_id, closed_flag, reminder_sent_flag,
                       sheet_title)
SELECT :legacy_guild_id, schedule_id, title, description, place, target_role_id,
       deadline, created_by, channel_id, closed_flag, reminder_sent_flag,
       sheet_title
FROM schedules_legacy;
DROP TABLE schedules_legacy;

-- ---------------------------------------------------------------------
-- schedule_options: 親の guild_id を冗長保持
-- ---------------------------------------------------------------------
ALTER TABLE schedule_options RENAME TO schedule_options_legacy;
CREATE TABLE schedule_options (
    option_id   TEXT PRIMARY KEY,
    guild_id    INTEGER NOT NULL CHECK (guild_id >= 0),
    schedule_id TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    message_id  TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE
);
INSERT INTO schedule_options (guild_id, option_id, schedule_id, label, start_at, end_at, message_id)
SELECT :legacy_guild_id, option_id, schedule_id, label, start_at, end_at, message_id
FROM schedule_options_legacy;
DROP TABLE schedule_options_legacy;

-- ---------------------------------------------------------------------
-- schedule_votes: UNIQUE (guild_id, option_id, user_id)
-- ---------------------------------------------------------------------
ALTER TABLE schedule_votes RENAME TO schedule_votes_legacy;
CREATE TABLE schedule_votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL CHECK (guild_id >= 0),
    option_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (guild_id, option_id, user_id),
    FOREIGN KEY (option_id) REFERENCES schedule_options(option_id) ON DELETE CASCADE
);
INSERT INTO schedule_votes (guild_id, vote_id, option_id, user_id, status, updated_at)
SELECT :legacy_guild_id, vote_id, option_id, user_id, status, updated_at
FROM schedule_votes_legacy;
DROP TABLE schedule_votes_legacy;

-- ---------------------------------------------------------------------
-- tasks
-- ---------------------------------------------------------------------
ALTER TABLE tasks RENAME TO tasks_legacy;
CREATE TABLE tasks (
    local_task_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL CHECK (guild_id >= 0),
    todoist_task_id TEXT,
    title           TEXT NOT NULL,
    assignee_id     TEXT,
    team_key        TEXT,
    due_date        TEXT,
    priority        INTEGER,
    location_key    TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);
INSERT INTO tasks (guild_id, local_task_id, todoist_task_id, title, assignee_id, team_key,
                   due_date, priority, location_key, status, created_by, created_at, completed_at)
SELECT :legacy_guild_id, local_task_id, todoist_task_id, title, assignee_id, team_key,
       due_date, priority, location_key, status, created_by, created_at, completed_at
FROM tasks_legacy;
DROP TABLE tasks_legacy;

-- ---------------------------------------------------------------------
-- reminders_log
-- ---------------------------------------------------------------------
ALTER TABLE reminders_log RENAME TO reminders_log_legacy;
CREATE TABLE reminders_log (
    reminder_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL CHECK (guild_id >= 0),
    reminder_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    target_user_id TEXT,
    sent_channel_id TEXT,
    sent_at        TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT
);
INSERT INTO reminders_log (guild_id, reminder_id, reminder_type, target_id, target_user_id,
                           sent_channel_id, sent_at, status, error_message)
SELECT :legacy_guild_id, reminder_id, reminder_type, target_id, target_user_id,
       sent_channel_id, sent_at, status, error_message
FROM reminders_log_legacy;
DROP TABLE reminders_log_legacy;

-- ---------------------------------------------------------------------
-- todoist_sections: PK (guild_id, section_id)
-- ---------------------------------------------------------------------
ALTER TABLE todoist_sections RENAME TO todoist_sections_legacy;
CREATE TABLE todoist_sections (
    guild_id     INTEGER NOT NULL CHECK (guild_id >= 0),
    section_id   TEXT NOT NULL,
    team_key     TEXT NOT NULL,
    section_name TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (guild_id, section_id)
);
INSERT INTO todoist_sections (guild_id, section_id, team_key, section_name, updated_at)
SELECT :legacy_guild_id, section_id, team_key, section_name, updated_at
FROM todoist_sections_legacy;
DROP TABLE todoist_sections_legacy;

-- ---------------------------------------------------------------------
-- layer_sessions: UNIQUE (guild_id, user_id)
-- ---------------------------------------------------------------------
ALTER TABLE layer_sessions RENAME TO layer_sessions_legacy;
CREATE TABLE layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL CHECK (guild_id >= 0),
    user_id    TEXT NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    UNIQUE (guild_id, user_id)
);
INSERT INTO layer_sessions (guild_id, session_id, user_id, keta, layer_num, started_at)
SELECT :legacy_guild_id, session_id, user_id, keta, layer_num, started_at
FROM layer_sessions_legacy;
DROP TABLE layer_sessions_legacy;

-- ---------------------------------------------------------------------
-- layer_records
-- ---------------------------------------------------------------------
ALTER TABLE layer_records RENAME TO layer_records_legacy;
CREATE TABLE layer_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL CHECK (guild_id >= 0),
    user_id     TEXT NOT NULL,
    keta        TEXT NOT NULL,
    layer_num   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    synced_flag INTEGER NOT NULL DEFAULT 0
);
INSERT INTO layer_records (guild_id, record_id, user_id, keta, layer_num, started_at,
                           ended_at, minutes, synced_flag)
SELECT :legacy_guild_id, record_id, user_id, keta, layer_num, started_at,
       ended_at, minutes, synced_flag
FROM layer_records_legacy;
DROP TABLE layer_records_legacy;

-- ---------------------------------------------------------------------
-- layer_keta: UNIQUE (guild_id, keta_name)
-- ---------------------------------------------------------------------
ALTER TABLE layer_keta RENAME TO layer_keta_legacy;
CREATE TABLE layer_keta (
    keta_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL CHECK (guild_id >= 0),
    keta_name   TEXT NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (guild_id, keta_name)
);
INSERT INTO layer_keta (guild_id, keta_id, keta_name, active_flag, created_by, created_at)
SELECT :legacy_guild_id, keta_id, keta_name, active_flag, created_by, created_at
FROM layer_keta_legacy;
DROP TABLE layer_keta_legacy;

-- ---------------------------------------------------------------------
-- インデックス再作成（guild_id 先頭の複合インデックス）
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_teams_guild ON teams(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_members_guild ON members(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_schedules_guild ON schedules(guild_id, closed_flag, deadline);
CREATE INDEX IF NOT EXISTS idx_options_guild_schedule ON schedule_options(guild_id, schedule_id);
CREATE INDEX IF NOT EXISTS idx_votes_guild_option ON schedule_votes(guild_id, option_id);
CREATE INDEX IF NOT EXISTS idx_votes_option ON schedule_votes(option_id);
CREATE INDEX IF NOT EXISTS idx_tasks_guild_status ON tasks(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_guild ON reminders_log(guild_id, reminder_id);
CREATE INDEX IF NOT EXISTS idx_sections_guild_team ON todoist_sections(guild_id, team_key);
CREATE INDEX IF NOT EXISTS idx_layer_records_guild_synced ON layer_records(guild_id, synced_flag);
CREATE INDEX IF NOT EXISTS idx_layer_records_synced ON layer_records(synced_flag);

COMMIT;
PRAGMA foreign_keys = ON;

-- 検証クエリ例:
--   SELECT 'settings' t, COUNT(*) FROM settings WHERE guild_id = :legacy_guild_id
--   UNION ALL SELECT 'members', COUNT(*) FROM members WHERE guild_id = :legacy_guild_id;
