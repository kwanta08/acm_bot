"""`/report member-attendance` のテスト（G4-6）。

`/report attendance-rate` は投票ごとの ok 率で、**「最近来ていない人」が
特定できない**。「3回連続で未回答」は退部のほぼ確実な予兆。

このファイルが特に固定しているもの:

1. **連続未回答は直近から数える。** 順序を取り違えると「昔サボっていて
   最近は来ている人」が要注意人物として上がる
2. **母集団は G3-2（`select_unanswered_targets`）と同じ。**
   ここが食い違うと、DM が飛ぶ相手とこの表の対象がずれる
3. **ephemeral 固定・公開オプションなし**（晒しにしない）
4. **対象になったことが無い人は「回答率0%」ではない**（ADR 0021）
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.reports import Reports
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from services.attendance_service import (
    MemberAttendance,
    ScheduleAnswers,
    aggregate_member_attendance,
    format_rate,
)
from utils.db import Database
from utils.parser import TZ, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
NOW = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def _entry(schedule_id: str, targets, answered=(), ok=()) -> ScheduleAnswers:
    return ScheduleAnswers(
        schedule_id=schedule_id,
        targets={str(t) for t in targets},
        answered={str(a) for a in answered},
        ok={str(o) for o in ok},
    )


# =====================================================================
# 1. 集計（純関数）
# =====================================================================
def test_rates_use_the_documented_denominators():
    entries = [
        _entry("s3", ["a"], answered=["a"], ok=["a"]),
        _entry("s2", ["a"], answered=["a"]),
        _entry("s1", ["a"]),
    ]
    (member,) = aggregate_member_attendance(entries)
    assert member.targeted == 3
    assert member.answered == 2
    assert member.ok == 1
    assert member.answer_rate == 2 / 3
    assert member.ok_rate == 1 / 2, "ok 率の分母が「対象回数」になっている"


def test_streak_counts_from_the_newest_schedule():
    """直近から連続して未回答の回数。新しい順で渡す前提。"""
    entries = [
        _entry("s4", ["a"]),
        _entry("s3", ["a"]),
        _entry("s2", ["a"], answered=["a"]),
        _entry("s1", ["a"]),
    ]
    (member,) = aggregate_member_attendance(entries)
    assert member.streak_unanswered == 2, "回答した回で連続が切れていない"


def test_a_recent_answer_resets_the_streak():
    entries = [
        _entry("s3", ["a"], answered=["a"]),
        _entry("s2", ["a"]),
        _entry("s1", ["a"]),
    ]
    (member,) = aggregate_member_attendance(entries)
    assert member.streak_unanswered == 0, "昔の未回答を直近として数えている"


def test_schedules_where_someone_was_not_targeted_are_skipped_for_the_streak():
    entries = [
        _entry("s3", ["a"]),
        _entry("s2", ["b"]),  # a は対象外
        _entry("s1", ["a"]),
    ]
    (a,) = [m for m in aggregate_member_attendance(entries) if m.user_id == "a"]
    assert a.targeted == 2
    assert a.streak_unanswered == 2, "対象外の回で連続が切れている"


def test_sorted_by_answer_rate_ascending():
    entries = [
        _entry("s2", ["a", "b", "c"], answered=["a", "b"], ok=["a"]),
        _entry("s1", ["a", "b", "c"], answered=["a"], ok=["a"]),
    ]
    order = [m.user_id for m in aggregate_member_attendance(entries)]
    assert order == ["c", "b", "a"]


def test_ties_put_the_person_with_more_data_first():
    entries = [
        _entry("s2", ["a", "b"]),
        _entry("s1", ["a"]),
    ]
    order = [m.user_id for m in aggregate_member_attendance(entries)]
    assert order == ["a", "b"], "同率のとき対象回数の多い人を先に出していない"


def test_a_never_targeted_person_has_no_rate_not_zero():
    """ADR 0021: 分からないものを 0 にしない。"""
    member = MemberAttendance(user_id="a")
    assert member.answer_rate is None
    assert member.ok_rate is None
    assert format_rate(None) == "—"


def test_someone_who_never_answered_has_no_ok_rate():
    entries = [_entry("s1", ["a"])]
    (member,) = aggregate_member_attendance(entries)
    assert member.answer_rate == 0.0
    assert member.ok_rate is None, "回答0回なのに ok 率を 0% と主張している"


def test_no_schedules_produces_no_rows():
    assert aggregate_member_attendance([]) == []


# =====================================================================
# 2. 母集団の作り方（Cog 側）
# =====================================================================
class _Role:
    def __init__(self, role_id: int, member_ids):
        self.id = role_id
        self.members = [SimpleNamespace(id=i, bot=False) for i in member_ids]


class _Guild:
    def __init__(self, roles: dict[int, _Role] | None = None):
        self.id = G1
        self._roles = roles or {}

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_member(self, _user_id: int):
        return None


def _cog(db: Database) -> Reports:
    return Reports(SimpleNamespace(db=db, guilds=[], user=None))


async def _closed_schedule(
    db: Database,
    schedule_id: str,
    *,
    guild_id: int = G1,
    days_ago: int = 7,
    target_role_id: str | None = None,
    votes: dict[str, str] | None = None,
) -> None:
    repo = ScheduleRepository(db)
    deadline = NOW - timedelta(days=days_ago)
    await repo.create_schedule(
        guild_id,
        schedule_id,
        f"予定{schedule_id}",
        None,
        "部室",
        target_role_id,
        to_iso(deadline),
        "tester",
        "555",
    )
    await repo.add_option(
        guild_id, f"{schedule_id}_o1", schedule_id, "候補", to_iso(deadline), None, None
    )
    for user_id, status in (votes or {}).items():
        await repo.set_vote(guild_id, f"{schedule_id}_o1", user_id, status)
    await repo.close_schedule(guild_id, schedule_id)


async def _roster(db: Database, guild_id: int = G1) -> None:
    repo = MemberRepository(db)
    for user_id, name in (("1", "たろう"), ("2", "はなこ"), ("3", "やめた人")):
        await repo.upsert_member(guild_id, user_id, name)
    await repo.set_status(guild_id, "3", "alumni")


def test_the_population_is_the_active_roster_when_no_role_is_set():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "s1", votes={"1": "ok"})
            members, count = await _cog(db).collect_member_attendance(
                G1, _Guild(), months=3, now_dt=NOW
            )
            assert count == 1
            assert {m.user_id for m in members} == {"1", "2"}, "退部者が母集団に入っている"
        finally:
            await db.close()

    run(_main())


def test_the_population_is_the_role_minus_retired_members():
    """G3-2 と同じ規則（積集合にしない）。名簿未登録のロール保持者も残す。"""

    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "s1", target_role_id="900", votes={"1": "ok"})
            guild = _Guild({900: _Role(900, [1, 2, 3, 99])})
            members, _ = await _cog(db).collect_member_attendance(G1, guild, months=3, now_dt=NOW)
            ids = {m.user_id for m in members}
            assert "99" in ids, "名簿未登録のロール保持者を落としている（積集合になっている）"
            assert "3" not in ids, "退部者を差し引いていない"
        finally:
            await db.close()

    run(_main())


def test_a_schedule_whose_role_cannot_be_resolved_is_skipped():
    """誰が対象か分からない予定を「全員未回答」と数えないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "s1", target_role_id="900")
            members, count = await _cog(db).collect_member_attendance(
                G1, _Guild(), months=3, now_dt=NOW
            )
            assert count == 0
            assert members == []
        finally:
            await db.close()

    run(_main())


def test_only_closed_schedules_within_the_period_are_counted():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "recent", days_ago=10)
            await _closed_schedule(db, "old", days_ago=200)
            _, count = await _cog(db).collect_member_attendance(G1, _Guild(), months=3, now_dt=NOW)
            assert count == 1, "期間外の投票まで数えている"

            _, wide = await _cog(db).collect_member_attendance(G1, _Guild(), months=12, now_dt=NOW)
            assert wide == 2
        finally:
            await db.close()

    run(_main())


def test_open_schedules_are_not_counted():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            repo = ScheduleRepository(db)
            await repo.create_schedule(
                G1, "open1", "開催中", None, "部室", None, to_iso(NOW), "tester", "555"
            )
            _, count = await _cog(db).collect_member_attendance(G1, _Guild(), months=3, now_dt=NOW)
            assert count == 0, "締切前の投票を未回答として数えている"
        finally:
            await db.close()

    run(_main())


def test_deleted_schedules_are_not_counted():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "s1")
            await ScheduleRepository(db).soft_delete_schedule(G1, "s1")
            _, count = await _cog(db).collect_member_attendance(G1, _Guild(), months=3, now_dt=NOW)
            assert count == 0
        finally:
            await db.close()

    run(_main())


def test_another_guilds_schedules_are_not_counted():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db, guild_id=G1)
            await _roster(db, guild_id=G2)
            await _closed_schedule(db, "b1", guild_id=G2)
            _, count = await _cog(db).collect_member_attendance(G1, _Guild(), months=3, now_dt=NOW)
            assert count == 0, "他ギルドの投票を数えている"
        finally:
            await db.close()

    run(_main())


def test_the_newest_schedule_leads_the_streak():
    """リポジトリの並び（deadline 降順）がそのまま連続未回答の起点になること。"""

    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "old", days_ago=30, votes={"1": "ok", "2": "ok"})
            await _closed_schedule(db, "mid", days_ago=20)
            await _closed_schedule(db, "new", days_ago=10)
            members, _ = await _cog(db).collect_member_attendance(
                G1, _Guild(), months=3, now_dt=NOW
            )
            by_id = {m.user_id: m for m in members}
            assert by_id["1"].streak_unanswered == 2
            assert by_id["1"].answered == 1
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. コマンド
# =====================================================================
class _Interaction:
    def __init__(self, guild=None):
        self.guild = guild if guild is not None else _Guild()
        self.user = SimpleNamespace(
            id=501,
            display_name="tester",
            guild=SimpleNamespace(owner_id=501),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
        self.deferred: list[dict] = []
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, **kwargs):
        self.deferred.append(kwargs)

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        parts = [embed.title or "", embed.description or ""]
        parts += [f"{f.name}\n{f.value}" for f in embed.fields]
        return "\n".join(parts)


def test_member_attendance_is_level_2():
    assert command_required_level(Reports.member_attendance) == Level.L2


def test_member_attendance_has_no_public_option():
    """晒しにならないよう、公開オプションを付けない。"""
    params = Reports.member_attendance._params
    assert "public" not in params, "公開オプションが付いている"
    assert set(params) == {"months"}


def test_member_attendance_is_always_ephemeral():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "s1", votes={"1": "ok"})
            interaction = _Interaction()
            await Reports.member_attendance.callback(_cog(db), interaction, months=3)
            assert interaction.deferred[-1]["ephemeral"] is True
            assert interaction.sent[-1]["ephemeral"] is True
        finally:
            await db.close()

    run(_main())


def test_member_attendance_shows_rates_and_the_streak():
    async def _main():
        db = await _make_db()
        try:
            await _roster(db)
            await _closed_schedule(db, "old", days_ago=30, votes={"1": "ok", "2": "ok"})
            await _closed_schedule(db, "new", days_ago=10, votes={"1": "ng"})
            interaction = _Interaction()
            await Reports.member_attendance.callback(_cog(db), interaction, months=3)
            text = interaction.text
            assert "回答率" in text and "ok率" in text, "定義が書かれていない"
            assert "1回連続で未回答" in text, "連続未回答が出ていない"
            assert "締切済み 2 件" in text
        finally:
            await db.close()

    run(_main())


def test_member_attendance_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Reports.member_attendance.callback(_cog(db), interaction, months=3)
            assert "`/schedule create`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_the_command_uses_the_same_target_rule_as_the_reminder():
    """母集団の決定が `select_unanswered_targets` を通っていること。

    ここを自前の条件式で書き直すと、DM が飛ぶ相手とこの表の対象がずれる
    （G3-2 が潰した「未回答が2つの定義で動く」形に戻る）。
    """
    source = inspect.getsource(Reports.collect_member_attendance)
    assert "select_unanswered_targets(" in source
