"""年度替わり（スキーマ v14 / migrations/013）のテスト。

**このフェーズで最も重要なのは後方互換**。members への列追加で既存の
サークルのデータが壊れたり、誰かが勝手に卒業扱いになってはいけない。

- v13 相当の既存 DB からのマイグレーションで、既存メンバーが
  **全員 status='active' のまま**、他の列も保持されること
- seasons の「現役の年度」が ended_at IS NULL の最新1件になること
- 卒業者は削除せず status を動かすだけであること
- すべて guild_id スコープであること
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from cogs.data import build_export_zip
from cogs.season import Season, rollover_result_embed, snapshot_filename
from repositories.member_repository import MemberRepository
from repositories.season_repository import SeasonRepository
from services.season_service import RolloverResult, perform_rollover
from utils.db import SCHEMA_VERSION, Database
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
NOW = "2026-08-13T10:00:00+09:00"


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


async def _columns(db: Database, table: str) -> set[str]:
    return {r["name"] for r in await db.fetchall(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_schema_version_is_at_least_14():
    assert SCHEMA_VERSION >= 14


def test_fresh_schema_has_seasons_and_member_status():
    async def _main():
        db = await _connected_db()
        try:
            season_cols = await _columns(db, "seasons")
            assert {
                "season_id",
                "guild_id",
                "name",
                "started_at",
                "ended_at",
                "created_at",
            } <= season_cols

            member_cols = await _columns(db, "members")
            assert {"status", "left_season"} <= member_cols
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_new_member_defaults_to_active():
    async def _main():
        db = await _connected_db()
        try:
            await MemberRepository(db).upsert_member(G1, "u1", "山田", primary_team="struct")
            row = await db.fetchone("SELECT * FROM members WHERE guild_id = ?", (G1,))
            assert row["status"] == "active"
            assert row["left_season"] is None
        finally:
            await db.close()

    run(_main())


def test_season_indexes_exist():
    async def _main():
        db = await _connected_db()
        try:
            rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")
            names = {r["name"] for r in rows}
            assert "idx_seasons_guild_ended" in names
            assert "idx_members_guild_status" in names
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# v13 → v14 マイグレーション（後方互換の要）
# ---------------------------------------------------------------------
def _make_v13_db() -> str:
    """status / left_season を持たない members の DB（v13 相当）。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE members (
            member_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        INTEGER NOT NULL,
            user_id         TEXT NOT NULL,
            display_name    TEXT NOT NULL,
            primary_team    TEXT,
            secondary_teams TEXT,
            is_leader       INTEGER NOT NULL DEFAULT 0,
            skills          TEXT,
            notes           TEXT,
            joined_at       TEXT NOT NULL,
            active_flag     INTEGER NOT NULL DEFAULT 1,
            UNIQUE (guild_id, user_id)
        );
        PRAGMA user_version = 13;
        """
    )
    rows = [
        (G1, "u1", "山田", "struct", '["elec"]', 1, '["溶接"]', "メモ"),
        (G1, "u2", "田中", "elec", "[]", 0, "[]", None),
        (G2, "u3", "別大学の人", "wing", "[]", 1, "[]", None),
    ]
    for guild_id, user_id, name, team, subs, leader, skills, notes in rows:
        conn.execute(
            "INSERT INTO members (guild_id, user_id, display_name,"
            " primary_team, secondary_teams, is_leader, skills, notes,"
            " joined_at, active_flag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (guild_id, user_id, name, team, subs, leader, skills, notes, "2026-04-01"),
        )
    conn.commit()
    conn.close()
    return path


def test_existing_members_all_become_active():
    """**移行で誰も勝手に卒業扱いにならない。**"""

    async def _main():
        db = Database(_make_v13_db())
        await db.connect()
        try:
            rows = await db.fetchall("SELECT * FROM members ORDER BY user_id")
            assert len(rows) == 3, "既存メンバーが失われた"
            assert all(r["status"] == "active" for r in rows)
            assert all(r["left_season"] is None for r in rows)
        finally:
            await db.close()

    run(_main())


def test_existing_member_columns_are_preserved():
    async def _main():
        db = Database(_make_v13_db())
        await db.connect()
        try:
            row = await db.fetchone(
                "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (G1, "u1")
            )
            assert row["display_name"] == "山田"
            assert row["primary_team"] == "struct"
            assert json.loads(row["secondary_teams"]) == ["elec"]
            assert row["is_leader"] == 1
            assert json.loads(row["skills"]) == ["溶接"]
            assert row["notes"] == "メモ"
            assert row["joined_at"] == "2026-04-01"
            assert row["active_flag"] == 1
        finally:
            await db.close()

    run(_main())


def test_migration_creates_seasons_table():
    async def _main():
        db = Database(_make_v13_db())
        await db.connect()
        try:
            assert await db.fetchall("PRAGMA table_info(seasons)")
            assert await SeasonRepository(db).list_all(G1) == []
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_season_migration_is_idempotent():
    async def _main():
        path = _make_v13_db()
        for _ in range(2):
            db = Database(path)
            await db.connect()
            rows = await db.fetchall("SELECT status FROM members")
            assert all(r["status"] == "active" for r in rows)
            await db.close()

    run(_main())


def test_existing_member_reads_still_work_after_migration():
    """既存の一覧取得が移行後もそのまま動く。"""

    async def _main():
        db = Database(_make_v13_db())
        await db.connect()
        try:
            members = await MemberRepository(db).list_members(G1)
            assert [m["display_name"] for m in members] == ["山田", "田中"]
            assert members[0]["skills"] == ["溶接"]
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 年度
# ---------------------------------------------------------------------
def test_current_season_is_the_latest_unfinished_one():
    async def _main():
        db = await _connected_db()
        try:
            repo = SeasonRepository(db)
            assert await repo.current(G1) is None

            await repo.create(G1, "2025年度", started_at="2025-04-01")
            await repo.end_current(G1, "2026-03-31")
            await repo.create(G1, "2026年度", started_at="2026-04-01")

            current = await repo.current(G1)
            assert current["name"] == "2026年度"
            assert current["ended_at"] is None
        finally:
            await db.close()

    run(_main())


def test_start_new_ends_the_previous_season():
    async def _main():
        db = await _connected_db()
        try:
            repo = SeasonRepository(db)
            await repo.create(G1, "2025年度", started_at="2025-04-01")

            ended, _ = await repo.start_new(G1, "2026年度", at=NOW)

            assert ended == "2025年度"
            previous = await repo.get_by_name(G1, "2025年度")
            assert previous["ended_at"] == NOW
            assert (await repo.current(G1))["name"] == "2026年度"
        finally:
            await db.close()

    run(_main())


def test_start_new_without_previous_season():
    async def _main():
        db = await _connected_db()
        try:
            ended, _ = await SeasonRepository(db).start_new(G1, "第1期")
            assert ended is None
            assert (await SeasonRepository(db).current(G1))["name"] == "第1期"
        finally:
            await db.close()

    run(_main())


def test_duplicate_season_name_is_rejected():
    async def _main():
        db = await _connected_db()
        try:
            repo = SeasonRepository(db)
            await repo.create(G1, "2026年度")
            with pytest.raises(ValueError):
                await repo.create(G1, "2026年度")
        finally:
            await db.close()

    run(_main())


def test_same_season_name_allowed_in_different_guilds():
    async def _main():
        db = await _connected_db()
        try:
            repo = SeasonRepository(db)
            await repo.create(G1, "2026年度")
            await repo.create(G2, "2026年度")
            assert len(await repo.list_all(G1)) == 1
            assert len(await repo.list_all(G2)) == 1
        finally:
            await db.close()

    run(_main())


def test_seasons_are_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = SeasonRepository(db)
            await repo.create(G1, "A大学2026")
            await repo.create(G2, "B大学2026")

            await repo.start_new(G1, "A大学2027")

            assert (await repo.current(G1))["name"] == "A大学2027"
            assert (await repo.current(G2))["name"] == "B大学2026", (
                "他サーバーの年度を終わらせてはいけない"
            )
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 在籍状態
# ---------------------------------------------------------------------
def test_set_status_keeps_the_row():
    """卒業者は削除しない（過去の記録の担当者名が引けなくなるため）。"""

    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_member(G1, "u1", "山田", primary_team="struct")

            assert await repo.set_status(G1, "u1", "alumni", left_season="2026年度") is True

            row = await db.fetchone(
                "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (G1, "u1")
            )
            assert row is not None, "行が消えている"
            assert row["status"] == "alumni"
            assert row["left_season"] == "2026年度"
            assert row["display_name"] == "山田"
        finally:
            await db.close()

    run(_main())


def test_reset_leaders_clears_every_leader_flag():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            for user_id, leader in (("u1", True), ("u2", True), ("u3", False)):
                await repo.upsert_member(G1, user_id, f"名前{user_id}")
                await repo.set_leader(G1, user_id, leader)

            assert await repo.reset_leaders(G1) == 2

            rows = await db.fetchall("SELECT is_leader FROM members WHERE guild_id = ?", (G1,))
            assert all(r["is_leader"] == 0 for r in rows)
        finally:
            await db.close()

    run(_main())


def test_reset_leaders_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_member(G1, "u1", "A大学")
            await repo.upsert_member(G2, "u2", "B大学")
            await repo.set_leader(G1, "u1", True)
            await repo.set_leader(G2, "u2", True)

            await repo.reset_leaders(G1)

            other = await db.fetchone("SELECT is_leader FROM members WHERE guild_id = ?", (G2,))
            assert other["is_leader"] == 1, "他サーバーの班長を外してはいけない"
        finally:
            await db.close()

    run(_main())


def test_alumni_are_excluded_from_default_listing():
    """卒業者は既定の一覧・検索から外れる。"""

    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_member(G1, "u1", "現役", primary_team="struct")
            await repo.add_skill(G1, "u1", "溶接")
            await repo.upsert_member(G1, "u2", "卒業生", primary_team="struct")
            await repo.add_skill(G1, "u2", "溶接")
            await repo.set_status(G1, "u2", "alumni", left_season="2026年度")

            names = [m["display_name"] for m in await repo.list_members(G1)]
            assert names == ["現役"]

            found = await repo.search_support(G1, "struct", "溶接")
            assert [m["display_name"] for m in found] == ["現役"]
        finally:
            await db.close()

    run(_main())


def test_alumni_can_be_included_explicitly():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_member(G1, "u1", "現役", primary_team="struct")
            await repo.add_skill(G1, "u1", "溶接")
            await repo.upsert_member(G1, "u2", "卒業生", primary_team="struct")
            await repo.add_skill(G1, "u2", "溶接")
            await repo.set_status(G1, "u2", "alumni", left_season="2026年度")

            names = {m["display_name"] for m in await repo.list_members(G1, include_alumni=True)}
            assert names == {"現役", "卒業生"}

            found = await repo.search_support(G1, "struct", "溶接", include_alumni=True)
            assert len(found) == 2
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 年度替わりの実行（F5-2）
# ---------------------------------------------------------------------
async def _seed_members(db, guild_id: int) -> None:
    repo = MemberRepository(db)
    for user_id, name, leader in (
        ("u1", "続ける人", True),
        ("u2", "卒業する人", True),
        ("u3", "もう1人", False),
    ):
        await repo.upsert_member(guild_id, user_id, name, primary_team="struct")
        await repo.set_leader(guild_id, user_id, leader)


def test_rollover_moves_only_selected_members_to_alumni():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)
            await SeasonRepository(db).create(G1, "2026年度", started_at="2026-04-01")

            result = await perform_rollover(db, G1, "2027年度", ["u2"])

            repo = MemberRepository(db)
            counts = await repo.count_by_status(G1)
            assert counts == {"active": 2, "alumni": 1}
            assert result.alumni == ["u2"]
            assert result.ended_season == "2026年度"
            assert result.new_season == "2027年度"
        finally:
            await db.close()

    run(_main())


def test_rollover_does_not_touch_unselected_members():
    """**選ばれなかった人の status は勝手に変えない。**"""

    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)

            await perform_rollover(db, G1, "2027年度", ["u2"])

            rows = await MemberRepository(db).list_members(G1)
            assert {m["user_id"] for m in rows} == {"u1", "u3"}
            assert all(m["status"] == "active" for m in rows)
            assert all(m["left_season"] is None for m in rows)
        finally:
            await db.close()

    run(_main())


def test_rollover_resets_every_leader_flag():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)

            result = await perform_rollover(db, G1, "2027年度", [])

            assert result.leaders_reset == 2
            rows = await db.fetchall("SELECT is_leader FROM members WHERE guild_id = ?", (G1,))
            assert all(r["is_leader"] == 0 for r in rows)
        finally:
            await db.close()

    run(_main())


def test_rollover_keeps_alumni_rows():
    """卒業者の行は消さない（過去の記録の担当者名を残すため）。"""

    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)

            await perform_rollover(db, G1, "2027年度", ["u2"])

            row = await db.fetchone(
                "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (G1, "u2")
            )
            assert row is not None
            assert row["display_name"] == "卒業する人"
            assert row["status"] == "alumni"
        finally:
            await db.close()

    run(_main())


def test_rollover_records_the_season_the_member_left():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)
            await SeasonRepository(db).create(G1, "2026年度", started_at="2026-04-01")

            await perform_rollover(db, G1, "2027年度", ["u2"])

            row = await db.fetchone(
                "SELECT left_season FROM members WHERE guild_id = ? AND user_id = ?", (G1, "u2")
            )
            assert row["left_season"] == "2026年度"
        finally:
            await db.close()

    run(_main())


def test_rollover_starts_the_new_season():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)
            await SeasonRepository(db).create(G1, "2026年度", started_at="2026-04-01")

            await perform_rollover(db, G1, "2027年度", [])

            repo = SeasonRepository(db)
            assert (await repo.current(G1))["name"] == "2027年度"
            assert (await repo.get_by_name(G1, "2026年度"))["ended_at"]
        finally:
            await db.close()

    run(_main())


def test_rollover_is_guild_scoped():
    """他サーバーのメンバー・班長・年度を巻き込まない。"""

    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)
            await _seed_members(db, G2)
            await SeasonRepository(db).create(G2, "B大学2026")

            await perform_rollover(db, G1, "2027年度", ["u2"])

            repo = MemberRepository(db)
            assert await repo.count_by_status(G2) == {"active": 3}
            rows = await db.fetchall("SELECT is_leader FROM members WHERE guild_id = ?", (G2,))
            assert sum(r["is_leader"] for r in rows) == 2, "他サーバーの班長を外してはいけない"
            assert (await SeasonRepository(db).current(G2))["name"] == "B大学2026"
        finally:
            await db.close()

    run(_main())


def test_rollover_rejects_duplicate_season_name():
    async def _main():
        db = await _connected_db()
        try:
            await SeasonRepository(db).create(G1, "2027年度")
            with pytest.raises(ValueError):
                await perform_rollover(db, G1, "2027年度", [])
        finally:
            await db.close()

    run(_main())


def test_rollover_summary_is_readable():
    result = RolloverResult(
        new_season="2027年度", ended_season="2026年度", alumni=["u1", "u2"], leaders_reset=3
    )
    summary = result.summary()
    assert "2026年度 を終了" in summary
    assert "2027年度 を開始" in summary
    assert "卒業 2 名" in summary
    assert "3 件" in summary


def test_rollover_embed_states_that_data_is_kept():
    embed = rollover_result_embed(
        RolloverResult(
            new_season="2027年度", ended_season="2026年度", alumni=["u1"], leaders_reset=2
        )
    )
    assert "削除していません" in (embed.description or "")
    assert len(embed) <= 6000


# ---------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------
class _FakeSeasonBot:
    db = None
    guilds = ()


def _command(qualified: str):
    for cmd in Season(_FakeSeasonBot()).walk_app_commands():
        if cmd.qualified_name == qualified:
            return cmd
    raise AssertionError(f"/{qualified} が見つからない")


def test_season_commands_require_expected_levels():
    """年度の作成と切り替えは L4。一覧は誰でも見られる。"""
    assert command_required_level(_command("season new")) == Level.L4
    assert command_required_level(_command("season rollover")) == Level.L4
    assert command_required_level(_command("season list")) == Level.L1


def test_season_commands_are_registered():
    names = {c.qualified_name for c in Season(_FakeSeasonBot()).walk_app_commands()}
    assert {"season list", "season new", "season rollover"} <= names


def test_snapshot_filename_has_no_guild_name():
    assert snapshot_filename(G1) == f"club-bot-season-snapshot-{G1}.zip"


def test_rollover_snapshot_reuses_the_export_zip():
    """年度スナップショットは /data export と同じ中身（再実装しない）。"""

    async def _main():
        db = await _connected_db()
        try:
            await _seed_members(db, G1)
            payload, counts = await build_export_zip(db, G1)

            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                names = set(zf.namelist())
            assert "members.csv" in names
            assert counts["members"] == 3
            # 他サーバーのデータは入らない（F2-2 の保証がそのまま効く）
            await _seed_members(db, G2)
            payload_a, counts_a = await build_export_zip(db, G1)
            assert counts_a["members"] == 3
            assert len(payload_a) > 0
        finally:
            await db.close()

    run(_main())


def test_count_by_status():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            for user_id in ("u1", "u2", "u3"):
                await repo.upsert_member(G1, user_id, f"名前{user_id}")
            await repo.set_status(G1, "u3", "alumni", left_season="2026年度")

            counts = await repo.count_by_status(G1)
            assert counts == {"active": 2, "alumni": 1}
        finally:
            await db.close()

    run(_main())
