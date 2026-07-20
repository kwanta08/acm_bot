"""マルチテナント（guild_id スコープ）の単体テスト。

- 新規 DB のスキーマが guild_id を持つこと
- 旧スキーマ DB が自動マイグレーションで guild_id をバックフィルされること
- 2ギルドのデータがリポジトリ経由で混ざらないこと

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402

from config import Config  # noqa: E402
from repositories.layer_keta_repository import LayerKetaRepository  # noqa: E402
from repositories.layer_session_repository import LayerSessionRepository  # noqa: E402
from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.schedule_repository import ScheduleRepository  # noqa: E402
from repositories.section_repository import SectionRepository  # noqa: E402
from repositories.settings_repository import SettingsRepository  # noqa: E402
from repositories.task_repository import TaskRepository  # noqa: E402
from utils.db import Database  # noqa: E402

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2
LEGACY_G = 300000000000000003  # レガシーギルド（マイグレーション用）

ALL_TABLES = [
    "settings", "teams", "members", "schedules", "schedule_options",
    "schedule_votes", "tasks", "reminders_log", "todoist_sections",
    "layer_sessions", "layer_records", "layer_keta",
]

# マイグレーション検証用の旧スキーマ（guild_id 無し・単一サーバー版）
LEGACY_SCHEMA = """
CREATE TABLE settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE teams (
    team_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    team_key       TEXT UNIQUE NOT NULL,
    team_name      TEXT NOT NULL,
    leader_role_id TEXT,
    channel_id     TEXT,
    active_flag    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE members (
    user_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE schedules (
    schedule_id        TEXT PRIMARY KEY,
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
CREATE TABLE schedule_options (
    option_id   TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    message_id  TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE
);
CREATE TABLE schedule_votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (option_id, user_id),
    FOREIGN KEY (option_id) REFERENCES schedule_options(option_id) ON DELETE CASCADE
);
CREATE TABLE tasks (
    local_task_id   INTEGER PRIMARY KEY AUTOINCREMENT,
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
CREATE TABLE reminders_log (
    reminder_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    target_user_id TEXT,
    sent_channel_id TEXT,
    sent_at        TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT
);
CREATE TABLE todoist_sections (
    section_id   TEXT PRIMARY KEY,
    team_key     TEXT NOT NULL,
    section_name TEXT,
    updated_at   TEXT NOT NULL
);
CREATE TABLE layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT UNIQUE NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  INTEGER NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE layer_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    keta        TEXT NOT NULL,
    layer_num   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    synced_flag INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE layer_keta (
    keta_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    keta_name   TEXT UNIQUE NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # connect() に作成させる
    return path


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_fresh_schema_has_guild_id():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            for table in ALL_TABLES:
                rows = await db.fetchall(f"PRAGMA table_info({table})")
                cols = {r["name"] for r in rows}
                assert "guild_id" in cols, f"{table} に guild_id がありません"
            # settings は (guild_id, setting_key) が PK: 同一キーを2ギルドで保存できる
            await db.set_setting(G1, "X", "1")
            await db.set_setting(G2, "X", "2")
            assert await db.get_setting(G1, "X") == "1"
            assert await db.get_setting(G2, "X") == "2"
        finally:
            await db.close()
    run(_main())


def test_legacy_migration_backfills_guild_id():
    async def _main():
        path = _tmp_db_path()
        # 旧スキーマ + 旧データを準備
        conn = await aiosqlite.connect(path)
        await conn.executescript(LEGACY_SCHEMA)
        await conn.execute(
            "INSERT INTO settings (setting_key, setting_value) VALUES ('TZ', 'Asia/Tokyo')")
        await conn.execute(
            "INSERT INTO teams (team_key, team_name) VALUES ('design', '設計')")
        await conn.execute(
            "INSERT INTO members (user_id, display_name, joined_at) VALUES ('42', 'taro', '2026-01-01')")
        await conn.execute(
            "INSERT INTO schedules (schedule_id, title, deadline, created_by, channel_id)"
            " VALUES ('s1', 'mtg', '2026-01-02', '42', '99')")
        await conn.execute(
            "INSERT INTO schedule_options (option_id, schedule_id, label, start_at)"
            " VALUES ('o1', 's1', '候補1', '2026-01-03')")
        await conn.execute(
            "INSERT INTO schedule_votes (option_id, user_id, status, updated_at)"
            " VALUES ('o1', '42', 'ok', '2026-01-01')")
        await conn.execute(
            "INSERT INTO tasks (title, created_by, created_at) VALUES ('task1', '42', '2026-01-01')")
        await conn.execute(
            "INSERT INTO reminders_log (reminder_type, target_id, sent_at, status)"
            " VALUES ('test', 'x', '2026-01-01', 'success')")
        await conn.execute(
            "INSERT INTO todoist_sections (section_id, team_key, updated_at)"
            " VALUES ('sec1', 'design', '2026-01-01')")
        await conn.execute(
            "INSERT INTO layer_sessions (user_id, keta, layer_num, started_at)"
            " VALUES ('42', '桁A', 1, '2026-01-01')")
        await conn.execute(
            "INSERT INTO layer_records (user_id, keta, layer_num, started_at, ended_at, minutes)"
            " VALUES ('42', '桁A', '1', '2026-01-01', '2026-01-01', 30)")
        await conn.execute(
            "INSERT INTO layer_keta (keta_name, created_by, created_at)"
            " VALUES ('桁A', '42', '2026-01-01')")
        await conn.commit()
        await conn.close()

        # env GUILD_ID を設定して接続 → 自動マイグレーション
        os.environ["GUILD_ID"] = str(LEGACY_G)
        try:
            db = Database(path)
            await db.connect()
            try:
                for table in ALL_TABLES:
                    row = await db.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
                    assert row["c"] == 1, f"{table} の行数が不正: {row['c']}"
                    row = await db.fetchone(f"SELECT guild_id FROM {table} LIMIT 1")
                    assert row["guild_id"] == LEGACY_G, (
                        f"{table} の guild_id がレガシー値でバックフィルされていない: "
                        f"{row['guild_id']}")
                # マイグレーション後も新規ギルドのデータを追加できる
                repo = MemberRepository(db)
                await repo.upsert_team(G2, "design", "設計")
                teams = await repo.list_teams(G2)
                assert len(teams) == 1
                assert (await repo.list_teams(LEGACY_G))[0]["guild_id"] == LEGACY_G
            finally:
                await db.close()
        finally:
            del os.environ["GUILD_ID"]
    run(_main())


# ---------------------------------------------------------------------
# ギルド分離
# ---------------------------------------------------------------------
async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def test_member_and_team_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            # 同名ユーザーを2ギルドに登録
            await repo.upsert_member(G1, "u1", "G1の田中", "design")
            await repo.upsert_member(G2, "u1", "G2の田中", "wing")
            m1 = await repo.get_member(G1, "u1")
            m2 = await repo.get_member(G2, "u1")
            assert m1["display_name"] == "G1の田中"
            assert m2["display_name"] == "G2の田中"
            assert m1["primary_team"] == "design"
            assert m2["primary_team"] == "wing"

            # 片方の更新が他方に影響しない
            await repo.set_primary_team(G1, "u1", "cfrp")
            assert (await repo.get_member(G1, "u1"))["primary_team"] == "cfrp"
            assert (await repo.get_member(G2, "u1"))["primary_team"] == "wing"

            # スキル・一覧も分離
            await repo.add_skill(G1, "u1", "CAD")
            assert (await repo.get_member(G2, "u1"))["skills"] == []
            assert len(await repo.list_members(G1)) == 1
            assert len(await repo.list_members(G2)) == 1

            # 班も分離
            await repo.upsert_team(G1, "design", "設計", channel_id="111")
            await repo.upsert_team(G2, "design", "設計", channel_id="222")
            assert (await repo.get_team(G1, "design"))["channel_id"] == "111"
            assert (await repo.get_team(G2, "design"))["channel_id"] == "222"
        finally:
            await db.close()
    run(_main())


def test_task_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = TaskRepository(db)
            id1 = await repo.create_task(G1, "G1タスク", created_by="u1", due_date="2020-01-01")
            id2 = await repo.create_task(G2, "G2タスク", created_by="u1", due_date="2020-01-01")
            assert [t["title"] for t in await repo.list_tasks(G1)] == ["G1タスク"]
            assert [t["title"] for t in await repo.list_tasks(G2)] == ["G2タスク"]
            # 他ギルドの ID では取得できない
            assert await repo.get_task(G2, id1) is None
            assert await repo.get_task(G1, id2) is None
            # 超過・エクスポートも分離
            assert len(await repo.list_overdue(G1, "2021-01-01")) == 1
            assert len(await repo.list_all_for_export(G2)) == 1
            # 完了操作もギルドスコープ
            await repo.complete_task(G1, id1)
            assert (await repo.get_task(G1, id1))["status"] == "done"
            assert (await repo.get_task(G2, id2))["status"] == "open"
        finally:
            await db.close()
    run(_main())


def test_schedule_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = ScheduleRepository(db)
            for gid, sid in ((G1, "sch1"), (G2, "sch2")):
                await repo.create_schedule(
                    gid, schedule_id=sid, title=f"title-{sid}", description=None,
                    place=None, target_role_id=None, deadline_iso="2099-01-01T00:00:00",
                    created_by="u1", channel_id="ch")
                await repo.add_option(gid, f"opt-{sid}", sid, "候補", "2099-01-01", None, "msg")
            # 一覧はギルド別
            assert [s["schedule_id"] for s in await repo.list_open_schedules(G1)] == ["sch1"]
            assert [s["schedule_id"] for s in await repo.list_open_schedules(G2)] == ["sch2"]
            # 他ギルドの ID では取得できない
            assert await repo.get_schedule(G1, "sch2") is None
            # リマインド候補・締切対象もギルド別
            assert len(await repo.list_reminder_candidates(G1, "2098-01-01", "2099-06-01")) == 1
            assert len(await repo.list_due_schedules(G1, "2099-06-01")) == 1
            # 投票はギルド別
            await repo.set_vote(G1, "opt-sch1", "u1", "ok")
            assert await repo.list_votes(G2, "opt-sch1") == []
            assert await repo.list_voters_for_schedule(G2, "sch1") == set()
            assert await repo.list_voters_for_schedule(G1, "sch1") == {"u1"}
            # message_id 検索もギルド別
            assert (await repo.get_option_by_message(G1, "msg"))["option_id"] == "opt-sch1"
            # クローズはギルドスコープ
            await repo.close_schedule(G1, "sch1")
            assert await repo.list_open_schedules(G1) == []
            assert len(await repo.list_open_schedules(G2)) == 1
        finally:
            await db.close()
    run(_main())


def test_settings_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "BOT_LOG_CHANNEL_ID", "111")
            assert await repo.get_int(G1, "BOT_LOG_CHANNEL_ID") == 111
            assert await repo.get_int(G2, "BOT_LOG_CHANNEL_ID") is None
            assert await repo.get_all(G2) == {}
            await repo.delete(G1, "BOT_LOG_CHANNEL_ID")
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") is None
            # set_if_absent は既存値を上書きしない
            await repo.set(G1, "K", "v1")
            assert await repo.set_if_absent(G1, "K", "v2") is False
            assert await repo.get(G1, "K") == "v1"
            assert await repo.set_if_absent(G2, "K", "v2") is True
        finally:
            await db.close()
    run(_main())


def test_section_and_layer_isolation():
    async def _main():
        db = await _connected_db()
        try:
            sec = SectionRepository(db)
            await sec.link(G1, "sec1", "design", "設計セクション")
            await sec.link(G2, "sec1", "wing", "翼セクション")
            assert (await sec.get_by_section(G1, "sec1"))["team_key"] == "design"
            assert (await sec.get_by_section(G2, "sec1"))["team_key"] == "wing"
            assert len(await sec.list_links(G1)) == 1

            keta = LayerKetaRepository(db)
            await keta.add(G1, "桁A", "u1", "2026-01-01")
            assert await keta.exists_active(G1, "桁A") is True
            assert await keta.exists_active(G2, "桁A") is False

            ses = LayerSessionRepository(db)
            # 同一ユーザーが別ギルドで同時にセッションを持てる
            await ses.start(G1, "u1", "桁A", "1", "2026-01-01T00:00:00")
            await ses.start(G2, "u1", "桁B", "2", "2026-01-01T00:00:00")
            assert (await ses.get_by_user(G1, "u1"))["keta"] == "桁A"
            assert (await ses.get_by_user(G2, "u1"))["keta"] == "桁B"
            rid1 = await ses.add_record(G1, "u1", "桁A", "1", "2026-01-01", "2026-01-01", 10)
            await ses.add_record(G2, "u1", "桁B", "2", "2026-01-01", "2026-01-01", 20)
            await ses.mark_synced(G1, rid1)
            assert len(await ses.list_unsynced(G1)) == 0
            assert len(await ses.list_unsynced(G2)) == 1
        finally:
            await db.close()
    run(_main())


def test_guild_bound_proxy():
    """services 互換の guild 固定プロキシが guild_id を自動注入すること。"""
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            bound1 = repo.for_guild(G1)
            bound2 = repo.for_guild(G2)
            await bound1.upsert_member("u9", "プロキシG1")
            await bound2.upsert_member("u9", "プロキシG2")
            assert (await bound1.get_member("u9"))["display_name"] == "プロキシG1"
            assert (await bound2.get_member("u9"))["display_name"] == "プロキシG2"
            # プロキシ経由でも他ギルドは見えない
            assert len(await bound1.list_members()) == 1
            assert bound1.guild_id == G1
        finally:
            await db.close()
    run(_main())


def test_config_for_guild_resolution():
    """ギルド別設定解決: DB（ギルド別） > env フォールバック。"""
    async def _main():
        db = await _connected_db()
        try:
            conf = Config()
            conf.admin_role_id = 555  # env 相当のグローバルフォールバック
            gconf = await conf.for_guild(G1, db=db)
            # DB 未設定 → フォールバック値
            assert gconf.admin_role_id == 555
            assert gconf.bot_log_channel_id is None

            repo = SettingsRepository(db)
            await repo.set(G1, "ADMIN_ROLE_ID", "999")
            await repo.set(G2, "ADMIN_ROLE_ID", "777")
            conf.invalidate_guild(G1)
            gconf1 = await conf.for_guild(G1, db=db)
            gconf2 = await conf.for_guild(G2, db=db)
            assert gconf1.admin_role_id == 999
            assert gconf2.admin_role_id == 777

            # キャッシュ: invalidate しないと古い値が返る
            await repo.set(G1, "ADMIN_ROLE_ID", "1000")
            assert (await conf.for_guild(G1, db=db)).admin_role_id == 999
            conf.invalidate_guild(G1)
            assert (await conf.for_guild(G1, db=db)).admin_role_id == 1000
        finally:
            await db.close()
    run(_main())


if __name__ == "__main__":
    test_fresh_schema_has_guild_id()
    print("test_fresh_schema_has_guild_id: OK")
    test_legacy_migration_backfills_guild_id()
    print("test_legacy_migration_backfills_guild_id: OK")
    test_member_and_team_isolation()
    print("test_member_and_team_isolation: OK")
    test_task_isolation()
    print("test_task_isolation: OK")
    test_schedule_isolation()
    print("test_schedule_isolation: OK")
    test_settings_isolation()
    print("test_settings_isolation: OK")
    test_section_and_layer_isolation()
    print("test_section_and_layer_isolation: OK")
    test_guild_bound_proxy()
    print("test_guild_bound_proxy: OK")
    test_config_for_guild_resolution()
    print("test_config_for_guild_resolution: OK")
    print("全テスト成功")
