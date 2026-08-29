"""未回答者の母集団を members 台帳へ寄せたテスト（G3-2 / ADR 0025 の更新）。

bot 側（ロール基準）とダッシュボード側（台帳基準）で「未回答」が
二重定義になっていた。対象ロール未設定の予定では bot が完全に沈黙する。

**積集合ではなく差集合にしたのが要点。** 対象ロールがあるときは
ロール保持者から「名簿で退部・休止と分かっている人」だけを差し引き、
名簿に無い人は残す。積集合にすると、`/member register` がまだ進んで
いないギルド（ロール保持者20名・登録済み3名など）で対象が 0 名になり、
**今日届いている DM が止まる**（G2-3 が塞いだ沈黙の再発）。

検査は純関数だけでなく `cog.notify_unanswered()` 経由でも行い、
**誰に DM が飛ぶか（中身）**まで見る。件数だけの assert では
「全員に飛んだ」と「1人も飛ばなかった」を取り違える。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.schedule import Schedule
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from services.schedule_service import select_unanswered_targets
from utils.db import Database

G1 = 111
ROLE_ID = 900


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


# ---------------------------------------------------------------------
# 1. 純関数
# ---------------------------------------------------------------------
def test_role_targets_keep_members_who_are_not_in_the_roster():
    """名簿に無い人は「退部か未登録か区別できない」ので残す。"""
    got = select_unanswered_targets(
        role_member_ids={"1", "2", "3"},
        roster_active_ids={"1"},  # 登録済みは1人だけ
        roster_retired_ids=set(),
        answered_ids=set(),
    )
    assert got == {"1", "2", "3"}, "積集合にすると未登録の2人へ催促が飛ばなくなる"


def test_role_targets_drop_only_known_retirees():
    got = select_unanswered_targets(
        role_member_ids={"1", "2", "3"},
        roster_active_ids={"1"},
        roster_retired_ids={"3"},  # 退部と分かっている
        answered_ids={"2"},
    )
    assert got == {"1"}


def test_without_a_role_the_roster_is_the_population():
    got = select_unanswered_targets(
        role_member_ids=None,
        roster_active_ids={"1", "2"},
        roster_retired_ids={"9"},
        answered_ids={"2"},
    )
    assert got == {"1"}


def test_without_a_role_and_an_empty_roster_is_unknown():
    """0 は「全員回答済み」という主張になるので None（ADR 0021 / 0022）。"""
    assert (
        select_unanswered_targets(
            role_member_ids=None,
            roster_active_ids=set(),
            roster_retired_ids=set(),
            answered_ids=set(),
        )
        is None
    )


def test_ids_are_normalised_across_int_and_str():
    """名簿は TEXT 列、discord.Member.id は int。混ざっても取り違えない。"""
    got = select_unanswered_targets(
        role_member_ids={1, 2},  # discord 側は int
        roster_active_ids={"1"},
        roster_retired_ids={"2"},  # TEXT 側の "2" で int の 2 が消えること
        answered_ids=set(),
    )
    assert got == {"1"}


# ---------------------------------------------------------------------
# 2. notify_unanswered 経由（誰に飛ぶかを見る）
# ---------------------------------------------------------------------
class _Member:
    """DM を受け取れるメンバーのダブル。

    `dm_each_with_channel_fallback` をモックせず**本物を通す**。
    モックすると「誰に DM が飛んだか」ではなく「何を渡したか」しか
    測れず、送信側の分岐（bot 除外・DM 拒否のフォールバック）が
    テストの外に出てしまう。
    """

    def __init__(self, user_id: int, bot: bool = False):
        self.id = user_id
        self.bot = bot
        self.display_name = f"user{user_id}"
        self.mention = f"<@{user_id}>"
        self.dms: list[str] = []

    async def send(self, text: str):
        self.dms.append(text)


class _Role:
    def __init__(self, members):
        self.id = ROLE_ID
        self.members = members


class _Guild:
    def __init__(self, members, role_members=None):
        self.id = G1
        self._members = {m.id: m for m in members}
        self._role = _Role(role_members) if role_members is not None else None

    def get_role(self, role_id: int):
        return self._role if self._role and role_id == ROLE_ID else None

    def get_member(self, user_id: int):
        return self._members.get(user_id)

    def all_members(self):
        """ロールにしか居ないメンバーも含めて、DM の届き先を数える。"""
        seen = dict(self._members)
        for member in self._role.members if self._role else []:
            seen.setdefault(member.id, member)
        return list(seen.values())


def _cog(db: Database, guild) -> Schedule:
    bot = SimpleNamespace(
        db=db,
        guilds=[],
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_channel=lambda cid: None,
    )
    return Schedule(bot)


async def _seed_schedule(db: Database, target_role_id: str | None) -> dict:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1, "sch_1", "秋合宿", None, None, target_role_id, "2026-10-01T23:59:00", "tester", "555"
    )
    await repo.add_option(G1, "opt_1", "sch_1", "10/1", "2026-10-01T18:00:00", None, "1")
    return await repo.get_schedule(G1, "sch_1")


async def _register(db: Database, user_id: int, status: str = "active") -> None:
    repo = MemberRepository(db)
    await repo.upsert_member(G1, str(user_id), f"user{user_id}")
    if status != "active":
        await repo.set_status(G1, str(user_id), status)


async def _notify(cog, schedule, guild) -> tuple[int | None, list[int]]:
    """notify_unanswered を実行し、(戻り値, DM が届いた user_id) を返す。"""
    count = await cog.notify_unanswered(schedule)
    sent = sorted(m.id for m in guild.all_members() if m.dms)
    return count, sent


def test_alumni_are_not_reminded():
    """退部者（status='alumni'）はロール保持者でも対象外。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, str(ROLE_ID))
            await _register(db, 1)
            await _register(db, 2, status="alumni")
            guild = _Guild([_Member(1), _Member(2)], role_members=[_Member(1), _Member(2)])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert sent == [1], f"退部者へ催促が飛んでいる: {sent}"
            assert count == 1
        finally:
            await db.close()

    run(_main())


def test_role_holders_missing_from_the_roster_are_still_reminded():
    """名簿の登録が進んでいないギルドで催促が止まらないこと（設計の要）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, str(ROLE_ID))
            await _register(db, 1)  # 登録済みは1人だけ
            role_members = [_Member(1), _Member(2), _Member(3)]
            guild = _Guild(role_members, role_members=role_members)
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert sent == [1, 2, 3], f"未登録のロール保持者が漏れている: {sent}"
            assert count == 3
        finally:
            await db.close()

    run(_main())


def test_without_a_role_the_active_roster_is_reminded():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, None)
            for user_id in (1, 2, 3):
                await _register(db, user_id)
            await ScheduleRepository(db).set_vote(G1, "opt_1", "2", "ok")  # 2 は回答済み
            guild = _Guild([_Member(1), _Member(2), _Member(3)])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert sent == [1, 3]
            assert count == 2
        finally:
            await db.close()

    run(_main())


def test_without_a_role_and_an_empty_roster_returns_none():
    """従来の「対象ロール未設定なら None」を、名簿が空のときだけに狭める。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, None)
            guild = _Guild([])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert count is None, "0 だと『全員回答済み』という嘘になる"
            assert sent == []
        finally:
            await db.close()

    run(_main())


def test_bots_and_departed_members_are_excluded():
    """bot と、名簿に残っているがサーバーに居ない人を外す。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, None)
            for user_id in (1, 2, 3):
                await _register(db, user_id)
            # 2 は bot、3 はサーバーに居ない（get_member が None）
            guild = _Guild([_Member(1), _Member(2, bot=True)])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert sent == [1]
            assert count == 1
        finally:
            await db.close()

    run(_main())


def test_unresolvable_candidates_are_not_reported_as_zero():
    """候補は居るのに1人も解決できないときは 0 ではなく None。

    0 を返すと「全員回答済み」という嘘になり、定期リマインドが
    送信済みフラグを立てて永久に沈黙する。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, None)
            for user_id in (1, 2):
                await _register(db, user_id)
            guild = _Guild([])  # 名簿には居るがキャッシュに1人も居ない
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert count is None
            assert sent == []
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 3. 定期リマインドとの結線（送っていないなら送信済みにしない）
# ---------------------------------------------------------------------
class _RecordingScheduleRepo:
    def __init__(self, rows):
        self.rows = rows
        self.marked: list[str] = []

    async def list_reminder_candidates(self, guild_id, from_iso_, to_iso_):
        return self.rows

    async def mark_reminder_sent(self, guild_id, schedule_id):
        self.marked.append(schedule_id)


def _reminders_cog(count):
    """notify_unanswered が count を返す状況の Reminders を組み立てる。"""
    from cogs.reminders import Reminders

    row = {"schedule_id": "sch_1", "title": "秋合宿"}
    repo = _RecordingScheduleRepo([row])

    class _ScheduleCog:
        async def notify_unanswered(self, schedule):
            return count

    bot = SimpleNamespace(
        db=None,
        guilds=[],
        get_cog=lambda name: _ScheduleCog(),
        log_to_channel=None,
    )
    cog = Reminders.__new__(Reminders)
    cog.bot = bot
    cog.schedule_repo = repo
    cog.log_repo = SimpleNamespace(add=None)

    async def _noop_log(*args, **kwargs):
        return None

    cog._log_reminder = _noop_log
    return cog, repo


def test_zero_unanswered_does_not_mark_the_reminder_as_sent():
    """1通も送っていないのに送信済みにしない（G2-3 の原理を 0 にも適用）。

    立てると、キャッシュ欠落で一瞬 0 になったときや、回答を取り消した人が
    出たときに二度と催促されない。
    """
    cog, repo = _reminders_cog(0)
    run(cog._process_schedule_reminders(G1))
    assert repo.marked == [], "送っていないのに送信済みにしている"


def test_none_does_not_mark_the_reminder_as_sent():
    cog, repo = _reminders_cog(None)
    run(cog._process_schedule_reminders(G1))
    assert repo.marked == []


def test_actual_sends_do_mark_the_reminder_as_sent():
    """送ったときは従来どおり送信済みにする（多重送信の防止は維持）。"""
    cog, repo = _reminders_cog(3)
    run(cog._process_schedule_reminders(G1))
    assert repo.marked == ["sch_1"]


def test_an_empty_role_never_falls_back_to_the_roster():
    """ロールが空でも名簿へフォールバックしないこと。

    フォールバックすると、班限定の予定の催促が**サークル全員へ飛ぶ**。
    戻り値だけを見るテストでは（名簿が空のフィクスチャなら None のまま
    なので）この改変を検出できないので、**名簿に現役を入れたうえで**
    1通も飛ばないことまで見る。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, str(ROLE_ID))
            for user_id in (1, 2, 3):
                await _register(db, user_id)  # 名簿には現役が3名いる
            guild = _Guild([_Member(1), _Member(2), _Member(3)], role_members=[])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert count is None
            assert sent == [], "班限定の予定なのに名簿の全員へ飛んでいる"
        finally:
            await db.close()

    run(_main())


def test_a_broken_user_id_does_not_stop_the_others():
    """名簿に数字でない user_id が混ざっても、他の人への催促は続く。

    members.user_id は TEXT 列で、ダッシュボードや手動 INSERT で
    数字以外が入りうる。int() を直に呼ぶとそのギルドの締切前催促が
    まるごと落ちる（gotcha `all-guilds-stop-getting-notifications` と同型）。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            schedule = await _seed_schedule(db, None)
            await _register(db, 1)
            # リポジトリを通さず壊れた行を直接入れる
            await db.execute(
                "INSERT INTO members (guild_id, user_id, display_name, secondary_teams,"
                " is_leader, joined_at, active_flag, status)"
                " VALUES (?, ?, ?, '[]', 0, ?, 1, 'active')",
                (G1, "unknown", "壊れた行", "2026-01-01T00:00:00"),
            )
            guild = _Guild([_Member(1)])
            count, sent = await _notify(_cog(db, guild), schedule, guild)
            assert sent == [1], "壊れた行1件で他の人への催促が止まっている"
            assert count == 1
        finally:
            await db.close()

    run(_main())
