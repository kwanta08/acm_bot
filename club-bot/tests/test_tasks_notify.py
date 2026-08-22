"""タスク割り当て通知のテスト（G2-3 の 3）。

タスク作成時に担当者を指定しても、担当者への DM もメンションも
送られていなかった。本人は `/task list mine:True` を自分で叩かない限り
割り当てに気付けない。

- 担当者へ DM を送る
- `discord.Forbidden`（DM 拒否）なら班チャンネルへメンション
- 班チャンネルが無ければ既定のタスクチャンネル

DM→チャンネルのフォールバックは `cogs/schedule.py` の未回答リマインドに
既存実装があったので、`utils/notify.py` へ切り出して共用する。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord

from utils.db import Database
from utils.notify import dm_each_with_channel_fallback

G1 = 111


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _FakeChannel:
    def __init__(self, channel_id: int = 555, fail: bool = False):
        self.id = channel_id
        self.sent: list[str] = []
        self._fail = fail

    async def send(self, content=None, **kwargs):
        if self._fail:
            raise discord.HTTPException(mock.MagicMock(status=500), "boom")
        self.sent.append(content or "")
        return SimpleNamespace(id=1)


def _member(user_id: int, *, dm_ok: bool = True):
    member = mock.MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = False
    member.display_name = f"user{user_id}"
    member.mention = f"<@{user_id}>"
    if dm_ok:
        member.send = mock.AsyncMock(return_value=None)
    else:
        member.send = mock.AsyncMock(
            side_effect=discord.Forbidden(mock.MagicMock(status=403), "DM closed")
        )
    return member


# ---------------------------------------------------------------------
# utils/notify.dm_each_with_channel_fallback
# ---------------------------------------------------------------------
def test_dm_success_does_not_touch_the_channel():
    async def _main():
        channel = _FakeChannel()
        member = _member(1, dm_ok=True)
        outcome = await dm_each_with_channel_fallback([member], "本文", channel)
        assert outcome.dm_sent == [member]
        assert outcome.fell_back == []
        assert outcome.failed == []
        assert channel.sent == []

    run(_main())


def test_dm_forbidden_falls_back_to_one_channel_mention():
    """DM 不可が複数いても、チャンネルには1通にまとめる（既存の作法）。"""

    async def _main():
        channel = _FakeChannel()
        blocked1 = _member(1, dm_ok=False)
        blocked2 = _member(2, dm_ok=False)
        outcome = await dm_each_with_channel_fallback([blocked1, blocked2], "本文", channel)
        assert outcome.fell_back == [blocked1, blocked2]
        assert len(channel.sent) == 1
        assert "<@1>" in channel.sent[0] and "<@2>" in channel.sent[0]
        assert "本文" in channel.sent[0]

    run(_main())


def test_no_channel_and_no_dm_is_reported_as_failed():
    async def _main():
        member = _member(1, dm_ok=False)
        outcome = await dm_each_with_channel_fallback([member], "本文", None)
        assert outcome.failed == [member]

    run(_main())


def test_channel_send_failure_is_reported_as_failed():
    async def _main():
        member = _member(1, dm_ok=False)
        outcome = await dm_each_with_channel_fallback([member], "本文", _FakeChannel(fail=True))
        assert outcome.failed == [member]

    run(_main())


# ---------------------------------------------------------------------
# タスク作成 — 担当者へ通知が飛ぶこと
# ---------------------------------------------------------------------
class _Interaction:
    def __init__(self):
        self.guild = SimpleNamespace(id=G1)
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)


class _DisabledTodoist:
    async def for_guild(self, guild_id):
        return SimpleNamespace(enabled=False)


def _tasks_cog(db: Database, channel=None):
    from cogs.tasks import Tasks

    bot = SimpleNamespace(
        db=db,
        guilds=[],
        todoist_manager=_DisabledTodoist(),
        get_channel=lambda cid: channel,
    )
    return Tasks(bot)


def test_finalize_add_task_dms_the_assignee():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _tasks_cog(db)
            assignee = _member(777, dm_ok=True)
            interaction = _Interaction()
            await cog._finalize_add_task(
                interaction,
                guild_id=G1,
                section_id=None,
                title="主桁の積層",
                due_iso=None,
                due_string=None,
                due=None,
                assignee=assignee,
                team_key=None,
                team_name=None,
                priority=None,
                location=None,
                note=None,
            )
            assignee.send.assert_awaited()
            text = assignee.send.await_args.args[0]
            assert "主桁の積層" in text
            assert "担当" in text
        finally:
            await db.close()

    run(_main())


def test_finalize_add_task_without_assignee_sends_no_dm():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _tasks_cog(db)
            interaction = _Interaction()
            await cog._finalize_add_task(
                interaction,
                guild_id=G1,
                section_id=None,
                title="主桁の積層",
                due_iso=None,
                due_string=None,
                due=None,
                assignee=None,
                team_key=None,
                team_name=None,
                priority=None,
                location=None,
                note=None,
            )
            # 担当者なし → 通知なしで正常終了（作成の成功 Embed は出る）
            assert interaction.sent
        finally:
            await db.close()

    run(_main())


def test_finalize_add_task_falls_back_to_the_team_channel():
    """DM 拒否なら班チャンネルへメンション。"""

    async def _main():
        from repositories.member_repository import MemberRepository

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            channel = _FakeChannel(channel_id=888)
            await MemberRepository(db).upsert_team(G1, "wing", "翼班", channel_id="888")
            cog = _tasks_cog(db, channel=channel)
            assignee = _member(777, dm_ok=False)
            interaction = _Interaction()
            await cog._finalize_add_task(
                interaction,
                guild_id=G1,
                section_id=None,
                title="主桁の積層",
                due_iso=None,
                due_string=None,
                due=None,
                assignee=assignee,
                team_key="wing",
                team_name="翼班",
                priority=None,
                location=None,
                note=None,
            )
            assert len(channel.sent) == 1
            assert "<@777>" in channel.sent[0]
        finally:
            await db.close()

    run(_main())


def test_assign_command_notifies_the_new_assignee():
    """/task assign でも本人へ通知が飛ぶこと（担当になる2つ目の入口）。"""

    async def _main():
        from cogs.tasks import Tasks
        from repositories.task_repository import TaskRepository

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await TaskRepository(db).create_task(G1, "リブ切り出し", created_by="501")
            cog = _tasks_cog(db)
            assignee = _member(777, dm_ok=True)
            interaction = _Interaction()
            await Tasks.assign.callback(cog, interaction, task_id=task_id, assignee=assignee)
            assignee.send.assert_awaited()
            assert "リブ切り出し" in assignee.send.await_args.args[0]
        finally:
            await db.close()

    run(_main())
