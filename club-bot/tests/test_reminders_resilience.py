"""定期通知ループの失敗耐性テスト。

discord.ext.tasks は未処理例外でループ自体を停止する。1サーバーの
壊れたデータ（数字でない班チャンネル ID・解釈できない期限など）で
ループが止まると、**全サーバー**の自動通知が bot 再起動まで復旧しない。
ギルド単位・ジョブ単位で例外を握り、他へ波及しないことを検証する。
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.reminders import Reminders, _channel_id_of

G1 = 111
G2 = 222


def run(coro):
    return asyncio.run(coro)


class FakeChannel:
    def __init__(self, channel_id: int = 999):
        self.id = channel_id
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeBot:
    def __init__(self, guild_ids, channel=None):
        self.db = None
        self.guilds = [SimpleNamespace(id=g, name=str(g)) for g in guild_ids]
        self._channel = channel
        self.logged = []

    def get_channel(self, channel_id):
        return self._channel

    def get_cog(self, name):
        return None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


def _cog(bot) -> Reminders:
    """リポジトリを作らずに Reminders を組み立てる（DB へは触れない）。"""
    cog = Reminders.__new__(Reminders)
    cog.bot = bot
    return cog


# ---------------------------------------------------------------------
# _channel_id_of: TEXT 列の値を安全に int 化する
# ---------------------------------------------------------------------
def test_channel_id_of_parses_digits():
    assert _channel_id_of({"channel_id": "123456789012345678"}) == 123456789012345678
    assert _channel_id_of({"channel_id": " 42 "}) == 42


def test_channel_id_of_rejects_non_numeric():
    """旧データ・手入力の「#general」等で int() が落ちないこと。"""
    for raw in (None, "", "  ", "#general", "<#123>", "abc", "12.5"):
        assert _channel_id_of({"channel_id": raw}) is None


# ---------------------------------------------------------------------
# ループ本体: 1ギルドの失敗が他ギルドとループを止めない
# ---------------------------------------------------------------------
def test_schedule_tick_survives_one_guild_failure():
    bot = FakeBot([G1, G2])
    cog = _cog(bot)
    seen: list[int] = []

    async def boom(guild_id):
        seen.append(guild_id)
        if guild_id == G1:
            raise RuntimeError("DB 接続断")

    cog._process_schedule_reminders = boom
    cog._process_schedule_close = boom

    run(cog.schedule_tick())  # 例外が漏れないこと（漏れるとループが停止する）
    assert G2 in seen, "先行ギルドの失敗で後続ギルドが処理されていない"


def test_daily_morning_isolates_each_job_and_guild():
    bot = FakeBot([G1, G2])
    cog = _cog(bot)
    calls: list[tuple[str, int]] = []

    def job(label, fail_on=None):
        async def _run(guild_id):
            calls.append((label, guild_id))
            if guild_id == fail_on:
                raise ValueError("壊れたデータ")
        return _run

    cog._notify_due_within_7days = job("due7", fail_on=G1)
    cog._notify_today_label = job("today", fail_on=G1)
    cog.push_section_tasks = job("section")

    run(cog.daily_morning())

    # 同じギルドの後続ジョブも、他ギルドも止まらない
    assert ("today", G1) in calls
    assert ("section", G1) in calls
    assert [c for c in calls if c[1] == G2] == [
        ("due7", G2), ("today", G2), ("section", G2)]


def test_daily_night_survives_dispatch_failure():
    bot = FakeBot([G1, G2])
    cog = _cog(bot)
    dispatched: list[int] = []

    async def list_overdue(guild_id, _today):
        return [{"title": "t", "due_date": "2026-08-01", "team_key": None}]

    async def dispatch(guild_id, tasks_, **kwargs):
        dispatched.append(guild_id)
        if guild_id == G1:
            raise ValueError("壊れたデータ")

    cog.task_repo = SimpleNamespace(list_overdue=list_overdue)
    cog._dispatch_by_team = dispatch

    run(cog.daily_night())
    assert dispatched == [G1, G2]


# ---------------------------------------------------------------------
# _dispatch_by_team: 壊れた行を捨てて残りを通知する
# ---------------------------------------------------------------------
def test_dispatch_skips_unparsable_due_date():
    channel = FakeChannel()
    bot = FakeBot([G1], channel=channel)
    cog = _cog(bot)

    async def team_map(_guild_id):
        return {}

    async def task_channel(_guild_id):
        return channel

    async def log_reminder(*args, **kwargs):
        return None

    cog._team_map = team_map
    cog._task_channel = task_channel
    cog._log_reminder = log_reminder

    from datetime import date
    today = date(2026, 8, 11)
    run(cog._dispatch_by_team(
        G1,
        [{"title": "壊れた行", "due_date": "not-a-date", "team_key": None},
         {"title": "正常な行", "due_date": "2026-08-12", "team_key": None}],
        title="テスト", reminder_type="task_due_7days",
        period_desc="今日から7日以内",
        period_start=today, period_end=today))

    assert len(channel.sent) == 1
    description = channel.sent[0]["embed"].description
    assert "正常な行" in description
    assert "壊れた行" not in description


def test_dispatch_sends_nothing_when_all_rows_are_broken():
    channel = FakeChannel()
    bot = FakeBot([G1], channel=channel)
    cog = _cog(bot)

    async def team_map(_guild_id):
        return {}

    async def task_channel(_guild_id):
        return channel

    async def log_reminder(*args, **kwargs):
        return None

    cog._team_map = team_map
    cog._task_channel = task_channel
    cog._log_reminder = log_reminder

    from datetime import date
    today = date(2026, 8, 11)
    run(cog._dispatch_by_team(
        G1, [{"title": "壊れた行", "due_date": None, "team_key": None}],
        title="テスト", reminder_type="task_due_7days",
        period_desc="今日から7日以内",
        period_start=today, period_end=today))

    assert channel.sent == []
