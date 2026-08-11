"""プロジェクト別タスク通知（push_project_tasks）の失敗耐性テスト。

Discord へは接続せず、フェイクの bot / チャンネル / Todoist サービスと
実際の SQLite DB で「一部の送信先が使えなくても他への通知と bot 全体の
処理が止まらない」ことを検証する（bot が特定サーバーから削除された場合等）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord

from cogs import progress as progress_cog
from cogs.progress import Progress
from config import GuildConfig
from repositories.progress_repository import ProgressRepository
from utils.db import Database
from utils.parser import now

G1 = 111
NOW = "2026-08-11 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


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


async def _make_cog(monkeypatch, bot, links: list[tuple[str, str, str]]):
    """DB を用意し、Todoist 紐付けを登録したコグを返す。"""
    db = Database(_tmp_db_path())
    await db.connect()
    bot.db = db
    repo = ProgressRepository(db)
    for project_name, node_id, channel_id in links:
        await repo.upsert_node(G1, node_id, name=node_id, now_text=NOW)
        await repo.upsert_todoist_link(G1, project_name, node_id, NOW,
                                       notify_channel_id=channel_id)

    async def _gconf(guild_id):
        return GuildConfig(guild_id=guild_id)

    monkeypatch.setattr(progress_cog.config, "for_guild", _gconf)
    return Progress(bot), db


def test_send_failure_does_not_stop_other_projects(monkeypatch):
    """1つ目の送信先が Forbidden でも 2つ目のプロジェクトへは通知される。"""
    async def _main():
        bot = FakeBot(channels={100: FakeChannel(fail=_forbidden()),
                                200: FakeChannel()})
        bot.todoist = FakeTodoist(
            [FakeProject("1", "A班"), FakeProject("2", "B班")],
            {"1": [_due_task("t1", "リブ切り出し")],
             "2": [_due_task("t2", "桁巻き")]})
        cog, db = await _make_cog(monkeypatch, bot,
                                  [("A班", "a", "100"), ("B班", "b", "200")])
        try:
            sent = await cog.push_project_tasks(G1)
            assert sent == 1                       # B班のみ成功
            assert len(bot.channels[200].sent) == 1
            assert any("送信失敗" in m for m in bot.logged)
        finally:
            await db.close()

    run(_main())


def test_missing_channel_does_not_stop_other_projects(monkeypatch):
    """送信先チャンネルが存在しなくても（削除・bot追放）他は継続する。"""
    async def _main():
        bot = FakeBot(channels={200: FakeChannel()})
        bot.todoist = FakeTodoist(
            [FakeProject("1", "A班"), FakeProject("2", "B班")],
            {"1": [_due_task("t1", "リブ切り出し")],
             "2": [_due_task("t2", "桁巻き")]})
        cog, db = await _make_cog(monkeypatch, bot,
                                  [("A班", "a", "999"),  # 存在しないチャンネル
                                   ("B班", "b", "200")])
        try:
            sent = await cog.push_project_tasks(G1)
            assert sent == 1
            assert any("送信先チャンネルがありません" in m for m in bot.logged)
        finally:
            await db.close()

    run(_main())


def test_no_links_sends_nothing(monkeypatch):
    """紐付けが無いギルドでは Todoist を呼ばずに 0 件で終わる。"""
    async def _main():
        bot = FakeBot()
        bot.todoist = FakeTodoist([], {})
        cog, db = await _make_cog(monkeypatch, bot, [])
        try:
            assert await cog.push_project_tasks(G1) == 0
        finally:
            await db.close()

    run(_main())


def test_daily_notify_isolates_guild_failures(monkeypatch):
    """1ギルドの通知処理が例外でも他ギルドの通知は実行される。"""
    bot = FakeBot(guilds=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    cog = Progress(bot)
    notified: list[int] = []

    async def _push(guild_id):
        if guild_id == 1:
            raise RuntimeError("boom")
        notified.append(guild_id)
        return 1

    monkeypatch.setattr(cog, "push_project_tasks", _push)
    run(cog.daily_project_notify.coro(cog))
    assert notified == [2]
