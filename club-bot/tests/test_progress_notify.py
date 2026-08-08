"""プロジェクト別タスク通知（push_project_tasks）の失敗耐性テスト。

Discord へは接続せず、フェイクの bot / チャンネル / Todoist サービスで
「一部の送信先が使えなくても他への通知と bot 全体の処理が止まらない」ことを
検証する（bot が特定サーバーから削除された場合等を想定）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord

from cogs import progress as progress_cog
from cogs.progress import Progress
from config import GuildConfig
from services import progress_sheet_service as pss
from utils.parser import now

G1 = 111


def run(coro):
    return asyncio.run(coro)


@dataclass
class FakeTask:
    id: str
    content: str
    priority: int = 1
    due: SimpleNamespace | None = None


@dataclass
class FakeProject:
    id: str
    name: str


class FakeTodoist:
    enabled = True

    def __init__(self, projects, tasks_by_project):
        self._projects = projects
        self._tasks = tasks_by_project

    async def get_projects(self):
        return self._projects

    async def get_tasks(self, project_id=None):
        return self._tasks.get(project_id, [])


class FakeSheetClient:
    """read_mapping_grid / read_settings_grid だけを返す最小フェイク。"""

    def __init__(self, mapping_grid, settings_grid=None):
        self.mapping_grid = mapping_grid
        self.settings_grid = settings_grid or [pss.SETTINGS_HEADER]

    def read_mapping_grid(self, spreadsheet_id):
        return self.mapping_grid

    def read_settings_grid(self, spreadsheet_id):
        return self.settings_grid


class FakeChannel:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.sent: list = []

    async def send(self, *args, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append(kwargs.get("embed") or (args[0] if args else None))


@dataclass
class FakeBot:
    channels: dict = field(default_factory=dict)
    logged: list = field(default_factory=list)
    guilds: list = field(default_factory=list)
    db: object = None

    def __post_init__(self):
        bot = self

        class _Manager:
            async def for_guild(self, guild_id):
                return bot.todoist

        self.todoist_manager = _Manager()
        self.todoist = None

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append(message)


def _forbidden() -> discord.Forbidden:
    return discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


def _due_task(task_id: str, content: str) -> FakeTask:
    return FakeTask(
        task_id, content,
        due=SimpleNamespace(date=now().date() + timedelta(days=1)))


def _make_cog(monkeypatch, bot, client):
    cog = Progress(bot, client_factory=lambda: client)

    async def _sid(db, guild_id):
        return "sid"

    async def _gconf(guild_id):
        return GuildConfig(guild_id=guild_id)

    monkeypatch.setattr(progress_cog.progress_sync_service,
                        "get_spreadsheet_id", _sid)
    monkeypatch.setattr(progress_cog.config, "for_guild", _gconf)
    return cog


def test_send_failure_does_not_stop_other_projects(monkeypatch):
    """1つ目の送信先が Forbidden でも 2つ目のプロジェクトへは通知される。"""
    mapping = [pss.MAPPING_HEADER,
               ["A班", "a", "100", str(G1)],
               ["B班", "b", "200", str(G1)]]
    bot = FakeBot(channels={100: FakeChannel(fail=_forbidden()),
                            200: FakeChannel()})
    bot.todoist = FakeTodoist(
        [FakeProject("1", "A班"), FakeProject("2", "B班")],
        {"1": [_due_task("t1", "リブ切り出し")],
         "2": [_due_task("t2", "桁巻き")]})
    cog = _make_cog(monkeypatch, bot, FakeSheetClient(mapping))

    sent = run(cog.push_project_tasks(G1))
    assert sent == 1                       # B班のみ成功
    assert len(bot.channels[200].sent) == 1
    assert any("送信失敗" in m for m in bot.logged)


def test_missing_channel_does_not_stop_other_projects(monkeypatch):
    """送信先チャンネルが存在しなくても（削除・bot追放）他は継続する。"""
    mapping = [pss.MAPPING_HEADER,
               ["A班", "a", "999", str(G1)],   # 存在しないチャンネル
               ["B班", "b", "200", str(G1)]]
    bot = FakeBot(channels={200: FakeChannel()})
    bot.todoist = FakeTodoist(
        [FakeProject("1", "A班"), FakeProject("2", "B班")],
        {"1": [_due_task("t1", "リブ切り出し")],
         "2": [_due_task("t2", "桁巻き")]})
    cog = _make_cog(monkeypatch, bot, FakeSheetClient(mapping))

    sent = run(cog.push_project_tasks(G1))
    assert sent == 1
    assert any("送信先チャンネルがありません" in m for m in bot.logged)


def test_daily_notify_isolates_guild_failures(monkeypatch):
    """1ギルドの通知処理が例外でも他ギルドの通知は実行される。"""
    bot = FakeBot(guilds=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    cog = Progress(bot, client_factory=lambda: None)
    notified: list[int] = []

    async def _push(guild_id):
        if guild_id == 1:
            raise RuntimeError("boom")
        notified.append(guild_id)
        return 1

    monkeypatch.setattr(cog, "push_project_tasks", _push)
    run(cog.daily_project_notify.coro(cog))
    assert notified == [2]
