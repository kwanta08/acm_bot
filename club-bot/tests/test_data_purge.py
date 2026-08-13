"""自動パージ（F2-4）のテスト。

**本 Bot で唯一の破壊的な定期処理**なので、次の3点を重点的に検証する。

1. 期限切れギルドの行が **全テーブルから** 消えること
2. **他ギルドの行が1件も減らないこと**
3. TABLE_DDL に新しいテーブルを足すと網羅テストが落ちること（消し漏れ検出）

加えて、1ギルドの削除失敗が他ギルドの処理を止めないことを確認する。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.reminders import Reminders
from repositories.guild_repository import GuildRepository, purge_target_tables
from utils.db import TABLE_DDL, Database
from utils.parser import now

GA = 100000000000000001
GB = 200000000000000002


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# 各テーブルへ1行入れるための最小 INSERT。
# プレースホルダは必ず (guild_id, *extra) の順。extra はギルド間で衝突する
# TEXT 主キーにだけ使い、呼び出し側が suffix を付ける。
#
# **この表は手書きのまま維持する。** TABLE_DDL にテーブルを足すと
# test_seed_covers_every_purge_target が落ち、削除処理への追随を促す。
_SEED_SQL: dict[str, tuple[str, tuple[str, ...]]] = {
    "guilds": (
        ("INSERT INTO guilds (guild_id, guild_name, joined_at)"
         " VALUES (?, 'サークル', '2026-01-01')"),
        (),
    ),
    "settings": (
        ("INSERT INTO settings (guild_id, setting_key, setting_value)"
         " VALUES (?, 'K', 'V')"),
        (),
    ),
    "teams": (
        ("INSERT INTO teams (guild_id, team_key, team_name)"
         " VALUES (?, 'struct', '構造')"),
        (),
    ),
    "members": (
        ("INSERT INTO members (guild_id, user_id, display_name, joined_at)"
         " VALUES (?, 'u1', '山田', '2026-01-01')"),
        (),
    ),
    "schedules": (
        ("INSERT INTO schedules (guild_id, schedule_id, title, deadline,"
         " created_by, channel_id)"
         " VALUES (?, ?, '練習', '2026-02-01', 'u1', 'c1')"),
        ("sched",),
    ),
    # schedule_options → schedules、schedule_votes → schedule_options の
    # 外部キーがあるため、参照先と同じ suffix 付き ID を使う
    "schedule_options": (
        ("INSERT INTO schedule_options (guild_id, option_id, schedule_id,"
         " label, start_at) VALUES (?, ?, ?, '候補1', '2026-02-01')"),
        ("opt", "sched"),
    ),
    "schedule_votes": (
        ("INSERT INTO schedule_votes (guild_id, option_id, user_id, status,"
         " updated_at) VALUES (?, ?, 'u1', 'ok', '2026-01-01')"),
        ("opt",),
    ),
    "tasks": (
        ("INSERT INTO tasks (guild_id, title, created_by, created_at)"
         " VALUES (?, 'タスク', 'u1', '2026-01-01')"),
        (),
    ),
    "reminders_log": (
        ("INSERT INTO reminders_log (guild_id, reminder_type, target_id,"
         " sent_at, status) VALUES (?, 'task', 't1', '2026-01-01', 'ok')"),
        (),
    ),
    "todoist_sections": (
        ("INSERT INTO todoist_sections (guild_id, section_id, team_key,"
         " updated_at) VALUES (?, 's1', 'struct', '2026-01-01')"),
        (),
    ),
    "layer_sessions": (
        ("INSERT INTO layer_sessions (guild_id, user_id, keta, layer_num,"
         " started_at) VALUES (?, 'u1', '主桁', '1', '2026-01-01')"),
        (),
    ),
    "layer_records": (
        ("INSERT INTO layer_records (guild_id, user_id, keta, layer_num,"
         " started_at, ended_at, minutes)"
         " VALUES (?, 'u1', '主桁', '1', '2026-01-01', '2026-01-01', 30)"),
        (),
    ),
    "layer_keta": (
        ("INSERT INTO layer_keta (guild_id, keta_name, created_by, created_at)"
         " VALUES (?, '主桁', 'u1', '2026-01-01')"),
        (),
    ),
    "audit_log": (
        ("INSERT INTO audit_log (guild_id, actor_id, action, created_at)"
         " VALUES (?, 'u1', 'test', '2026-01-01')"),
        (),
    ),
    "skill_tags": (
        ("INSERT INTO skill_tags (guild_id, skill_name, created_by, created_at)"
         " VALUES (?, '溶接', 'u1', '2026-01-01')"),
        (),
    ),
    "todoist_configs": (
        ("INSERT INTO todoist_configs (guild_id, api_token_encrypted,"
         " created_by, created_at, updated_at)"
         " VALUES (?, 'tok', 'u1', '2026-01-01', '2026-01-01')"),
        (),
    ),
    "guild_directus_access": (
        ("INSERT INTO guild_directus_access (guild_id, directus_user_id,"
         " email, created_by, created_at, updated_at)"
         " VALUES (?, 'd1', 'a@example.com', 'u1', '2026-01-01',"
         " '2026-01-01')"),
        (),
    ),
    "progress_nodes": (
        ("INSERT INTO progress_nodes (guild_id, node_id, name, created_at,"
         " updated_at)"
         " VALUES (?, 'n1', '主翼', '2026-01-01', '2026-01-01')"),
        (),
    ),
    "progress_todoist_links": (
        ("INSERT INTO progress_todoist_links (guild_id, project_name, node_id,"
         " created_at, updated_at)"
         " VALUES (?, 'p1', 'n1', '2026-01-01', '2026-01-01')"),
        (),
    ),
    "progress_spar_links": (
        ("INSERT INTO progress_spar_links (guild_id, keta_name, node_id,"
         " target_layers, created_at, updated_at)"
         " VALUES (?, '主桁', 'n1', 5, '2026-01-01', '2026-01-01')"),
        (),
    ),
    "progress_milestones": (
        ("INSERT INTO progress_milestones (guild_id, node_id, name, due_date,"
         " created_at, updated_at)"
         " VALUES (?, 'n1', '接着完了', '2026-09-01', '2026-01-01',"
         " '2026-01-01')"),
        (),
    ),
    "seasons": (
        ("INSERT INTO seasons (guild_id, name, started_at, created_at)"
         " VALUES (?, '2026年度', '2026-04-01', '2026-01-01')"),
        (),
    ),
}


async def _seed_all_tables(db: Database, guild_id: int, suffix: str) -> None:
    """全テーブルへ1行ずつ入れる（TEXT 主キーは suffix で衝突を避ける）。"""
    for sql, extra in _SEED_SQL.values():
        await db.execute(sql, (guild_id, *(f"{v}{suffix}" for v in extra)))


async def _count(db: Database, table: str, guild_id: int) -> int:
    row = await db.fetchone(
        f"SELECT COUNT(*) AS n FROM {table} WHERE guild_id = ?", (guild_id,))
    return int(row["n"])


# ---------------------------------------------------------------------
# (3) 網羅性 — 消し漏れの検出
# ---------------------------------------------------------------------
def test_purge_targets_cover_every_table_in_table_ddl():
    """対象テーブルは TABLE_DDL 全体から導出されること。

    新しいテーブルを TABLE_DDL に足したのに削除処理へ入れ忘れる、
    という消し漏れをここで検出する。
    """
    assert set(purge_target_tables()) == set(TABLE_DDL)


def test_purge_targets_have_no_duplicates_and_end_with_guilds():
    targets = purge_target_tables()
    assert len(targets) == len(set(targets))
    assert targets[-1] == "guilds", "台帳は最後に消す"


def test_seed_covers_every_purge_target():
    """テスト自身が全テーブルを網羅していること（空振り防止）。"""
    assert set(_SEED_SQL) == set(purge_target_tables())


# ---------------------------------------------------------------------
# (1) 期限切れギルドが全テーブルから消える
# ---------------------------------------------------------------------
def test_purge_removes_rows_from_every_table():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_all_tables(db, GA, "a")
            for table in purge_target_tables():
                assert await _count(db, table, GA) == 1, f"{table} の準備に失敗"

            deleted = await GuildRepository(db).purge_guild(GA)

            for table in purge_target_tables():
                assert await _count(db, table, GA) == 0, f"{table} が残っている"
            # 削除件数がすべてのテーブルぶん記録されること
            # （子から先に消すので CASCADE に食われて 0 件になる表は無い）
            assert set(deleted) == set(purge_target_tables())
            assert sum(deleted.values()) == len(purge_target_tables())
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# (2) 他ギルドの行が1件も減らない
# ---------------------------------------------------------------------
def test_purge_does_not_touch_other_guilds():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_all_tables(db, GA, "a")
            await _seed_all_tables(db, GB, "b")
            before = {t: await _count(db, t, GB) for t in purge_target_tables()}

            await GuildRepository(db).purge_guild(GA)

            for table in purge_target_tables():
                assert await _count(db, table, GB) == before[table], \
                    f"{table} で他サーバーの行が減った"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 期限判定
# ---------------------------------------------------------------------
def test_list_purge_due_selects_only_expired():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.ensure(GB, "B大学")
            # A は猶予切れ、B はまだ猶予中
            await repo.mark_left(GA, 30, left_at=now() - timedelta(days=31))
            await repo.mark_left(GB, 30)

            due = [int(r["guild_id"]) for r in await repo.list_purge_due()]
            assert due == [GA]
        finally:
            await db.close()

    run(_main())


def test_guild_without_purge_after_is_never_due():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")  # 参加中。purge_after は NULL
            assert await repo.list_purge_due() == []
        finally:
            await db.close()

    run(_main())


def test_broken_purge_after_is_not_deleted():
    """壊れた日時が入っていても削除対象にしない（消さない側に倒す）。"""
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await db.execute(
                "UPDATE guilds SET purge_after = 'not-a-date' WHERE guild_id = ?",
                (GA,))
            assert await repo.list_purge_due() == []
        finally:
            await db.close()

    run(_main())


def test_self_requested_delete_is_due_immediately():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.request_purge(GA)

            due = [int(r["guild_id"]) for r in await repo.list_purge_due()]
            assert due == [GA]
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 定期ジョブ（1ギルドの失敗が他を止めない）
# ---------------------------------------------------------------------
class _FakeBot:
    def __init__(self, db):
        self.db = db
        self.guilds = []

    def get_channel(self, channel_id):
        return None

    async def log_to_channel(self, *a, **kw):
        return None


def _cog(db) -> Reminders:
    cog = Reminders.__new__(Reminders)   # ループを起動せずに組み立てる
    cog.bot = _FakeBot(db)
    return cog


def test_run_purge_deletes_due_guilds():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await _seed_all_tables(db, GA, "a")
            await _seed_all_tables(db, GB, "b")
            await repo.mark_left(GA, 30, left_at=now() - timedelta(days=31))

            results = await _cog(db).run_purge()

            assert set(results) == {GA}
            assert await _count(db, "tasks", GA) == 0
            assert await _count(db, "tasks", GB) == 1
        finally:
            await db.close()

    run(_main())


def test_one_guild_failure_does_not_stop_others():
    """1ギルドの削除が例外を投げても、他ギルドの削除は続行する。"""
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await _seed_all_tables(db, GA, "a")
            await _seed_all_tables(db, GB, "b")
            past = now() - timedelta(days=31)
            await repo.mark_left(GA, 30, left_at=past)
            await repo.mark_left(GB, 30, left_at=past)

            cog = _cog(db)
            original = GuildRepository.purge_guild
            failed_for = {"guild_id": GA}

            async def flaky(self, guild_id: int):
                if guild_id == failed_for["guild_id"]:
                    raise RuntimeError("疑似的な削除失敗")
                return await original(self, guild_id)

            GuildRepository.purge_guild = flaky
            try:
                results = await cog.run_purge()
            finally:
                GuildRepository.purge_guild = original

            assert set(results) == {GB}, "失敗したギルド以外は処理されるべき"
            assert await _count(db, "tasks", GA) == 1, "失敗したギルドは消えない"
            assert await _count(db, "tasks", GB) == 0
        finally:
            await db.close()

    run(_main())


def test_run_purge_with_nothing_due_is_noop():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_all_tables(db, GA, "a")
            assert await _cog(db).run_purge() == {}
            assert await _count(db, "tasks", GA) == 1
        finally:
            await db.close()

    run(_main())


def test_purge_loop_is_registered():
    """日次ループとして登録され、cog_unload で止められること。"""
    assert hasattr(Reminders, "daily_purge")
    assert hasattr(Reminders.daily_purge, "start")
    assert hasattr(Reminders.daily_purge, "cancel")


def test_fake_bot_shape_matches_usage():
    """テスト用の偽 bot が実装の呼び出しを満たしていること。"""
    bot = _FakeBot(SimpleNamespace())
    assert bot.get_channel(1) is None
