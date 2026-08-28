"""日程調整の通知の抜けのテスト（G2-3 の 1 と 2）。

1. `/schedule remind` は `target_role_id` が無いと `return 0` なのに、
   呼び出し側が緑の成功 Embed で「対象: 0 名」と出していた。さらに定期
   リマインドは 0 名でも `mark_reminder_sent` を打つので、**後から対象
   ロールを付けても永久に再送されない**。
   → 対象を特定できない場合は「成功」ではなくエラー / skipped にし、
     送信済みフラグを立てない。

2. 作成時の投票メッセージにロールメンションが無く、対象者は投票が
   始まったことに気付けない。→ 先頭の投票メッセージにメンションを付ける。

台帳ベースの未回答判定（ADR 0025 の覆す条件）は G3-2 で扱う。
ここではエラー表示までに留める。
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
from config import GuildConfig
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database

G1 = 111


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id

    async def add_reaction(self, emoji):
        return None


class _FakeChannel:
    def __init__(self, channel_id: int = 555):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.sent: list[dict] = []

    async def send(self, content=None, *, embed=None, **kwargs):
        self.sent.append({"content": content, "embed": embed})
        return _FakeMessage(1000 + len(self.sent))


class _FakeGuild:
    def __init__(self, guild_id: int = G1, roles: dict | None = None):
        self.id = guild_id
        self.emojis = []
        self._roles = roles or {}

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_member(self, user_id: int):
        # ロール保持者から引く（名簿の user_id を Member へ解決する経路）
        for role in self._roles.values():
            for member in getattr(role, "members", []):
                if member.id == user_id:
                    return member
        return None

    def get_emoji(self, emoji_id: int):
        return None


class _Interaction:
    def __init__(self, guild):
        self.guild = guild
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.channel = None
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_embed(self):
        return self.sent[-1]["embed"]


async def _record_dm(text):
    """DM を受け取れるダミー（送信の成否だけが要るので中身は捨てる）。"""


def _cog(db: Database, guild=None, channel=None) -> Schedule:
    bot = SimpleNamespace(
        db=db,
        guilds=[],
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_channel=lambda cid: channel,
    )
    return Schedule(bot)


async def _seed(db: Database, target_role_id: str | None = None) -> str:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1,
        "sch_1",
        "秋合宿",
        None,
        None,
        target_role_id,
        "2026-10-01T23:59:00",
        "tester",
        "555",
    )
    return "sch_1"


# ---------------------------------------------------------------------
# (1) notify_unanswered — 「対象を特定できない」と「0名」を区別する
# ---------------------------------------------------------------------
def test_notify_unanswered_returns_none_without_a_target_role():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id=None)
            cog = _cog(db, guild=_FakeGuild())
            schedule = await ScheduleRepository(db).get_schedule(G1, "sch_1")
            assert await cog.notify_unanswered(schedule) is None
        finally:
            await db.close()

    run(_main())


def test_notify_unanswered_returns_none_when_the_role_is_gone():
    """設定後にロールが削除された場合も「特定できない」扱い。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            cog = _cog(db, guild=_FakeGuild(roles={}))  # 900 は存在しない
            schedule = await ScheduleRepository(db).get_schedule(G1, "sch_1")
            assert await cog.notify_unanswered(schedule) is None
        finally:
            await db.close()

    run(_main())


def test_notify_unanswered_returns_zero_when_everyone_answered():
    """本当に0名なら 0（None と区別できること）。

    **フィクスチャを差し替えた（G3-2）。** 元は `members=[]` のロールで
    「0名」を作っていたが、それは「全員回答済み」ではなく
    「ロールに誰も居ない」状態で、テスト名と中身が食い違っていた。
    ロール保持者1名が回答済み、という本来の意味の0名にする。
    「ロールに誰も居ない」は下の別ケースで None を期待する。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            repo = ScheduleRepository(db)
            await repo.add_option(G1, "opt_1", "sch_1", "10/1", "2026-10-01T18:00:00", None, "1")
            await repo.set_vote(G1, "opt_1", "7", "ok")
            role = SimpleNamespace(members=[SimpleNamespace(id=7, bot=False)])
            cog = _cog(db, guild=_FakeGuild(roles={900: role}))
            schedule = await repo.get_schedule(G1, "sch_1")
            assert await cog.notify_unanswered(schedule) == 0
        finally:
            await db.close()

    run(_main())


def test_notify_unanswered_returns_none_when_the_role_has_no_members():
    """ロールは生きているが保持者が1人も見えないときは 0 にしない。

    誰も付けていないロール（正常）とメンバーキャッシュの欠落を区別
    できないので、「全員回答済み」とは主張しない（ADR 0021 / 0022）。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            role = SimpleNamespace(members=[])
            cog = _cog(db, guild=_FakeGuild(roles={900: role}))
            schedule = await ScheduleRepository(db).get_schedule(G1, "sch_1")
            assert await cog.notify_unanswered(schedule) is None
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# (1) /schedule remind — 緑の嘘をやめる
# ---------------------------------------------------------------------
def test_remind_reports_success_when_it_actually_sends():
    """本当に送ったときは成功を出すこと（否定 assert だけで固めない）。

    「嘘の成功を出さない」側だけを検査していると、成功 Embed を
    到達不能にする改変が緑のまま通る。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            member = SimpleNamespace(
                id=7, bot=False, display_name="user7", mention="<@7>", send=_record_dm
            )
            role = SimpleNamespace(members=[member])
            guild = _FakeGuild(roles={900: role})
            cog = _cog(db, guild=guild)
            interaction = _Interaction(guild)
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")

            embed = interaction.last_embed
            text = f"{embed.title or ''} {embed.description or ''}"
            assert "再通知しました" in text
            assert "1 名" in text
        finally:
            await db.close()

    run(_main())


def test_remind_with_zero_unanswered_is_not_reported_as_sent():
    """未回答0名のときに「再通知しました」と言わない（G3-2）。

    1通も送っていないのに成功と表示するのは、このファイルが潰した
    「嘘の成功」と同じ形。定期リマインド側も 0 では送信済みにしない。
    """

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            repo = ScheduleRepository(db)
            await repo.add_option(G1, "opt_1", "sch_1", "10/1", "2026-10-01T18:00:00", None, "1")
            await repo.set_vote(G1, "opt_1", "7", "ok")
            role = SimpleNamespace(members=[SimpleNamespace(id=7, bot=False)])
            guild = _FakeGuild(roles={900: role})
            cog = _cog(db, guild=guild)
            interaction = _Interaction(guild)
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")

            embed = interaction.last_embed
            text = f"{embed.title or ''} {embed.description or ''}"
            assert "再通知しました" not in text, "1通も送っていないのに成功と表示している"
            assert "未回答者は居ませんでした" in text
        finally:
            await db.close()

    run(_main())


def test_remind_without_a_target_role_is_an_error_not_success():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id=None)
            guild = _FakeGuild()
            cog = _cog(db, guild=guild)
            interaction = _Interaction(guild)
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")

            embed = interaction.last_embed
            text = f"{embed.title or ''} {embed.description or ''}"
            assert "再通知しました" not in text, "対象0名なのに成功と表示している"
            # 対象を特定できない理由は「ロール未設定」だけではなくなった
            # （名簿が空でも特定できない）。案内先を両方出していること
            assert "対象ロール" in text
            assert "/member register" in text
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# (1) 定期リマインド — skipped を送信済みにしない
# ---------------------------------------------------------------------
def _reminders_cog(db: Database, schedule_cog):
    from cogs.reminders import Reminders

    bot = mock.MagicMock()
    bot.db = db
    bot.guilds = []
    bot.get_cog = lambda name: schedule_cog if name == "Schedule" else None
    return Reminders(bot)


def test_periodic_reminder_skips_without_marking_sent():
    """対象ロールが無い間は送信済みにせず、後から付ければ再送されること。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id=None)
            repo = ScheduleRepository(db)

            class _StubScheduleCog:
                async def notify_unanswered(self, schedule):
                    return None  # 対象を特定できない

            reminders = _reminders_cog(db, _StubScheduleCog())
            # 締切1時間前のウィンドウに入るよう締切を近づける
            from datetime import timedelta

            from utils.parser import now, to_iso

            await repo.update_deadline(G1, "sch_1", to_iso(now() + timedelta(minutes=30)))
            await reminders._process_schedule_reminders(G1)

            row = await repo.get_schedule(G1, "sch_1")
            assert row["reminder_sent_flag"] == 0, "skipped なのに送信済みが立っている"

            logs = await db.fetchall(
                "SELECT status FROM reminders_log WHERE guild_id = ? AND target_id = ?",
                (G1, "sch_1"),
            )
            assert [r["status"] for r in logs] == ["skipped"]
        finally:
            await db.close()

    run(_main())


def test_periodic_reminder_marks_sent_on_success():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed(db, target_role_id="900")
            repo = ScheduleRepository(db)

            class _StubScheduleCog:
                async def notify_unanswered(self, schedule):
                    return 3

            reminders = _reminders_cog(db, _StubScheduleCog())
            from datetime import timedelta

            from utils.parser import now, to_iso

            await repo.update_deadline(G1, "sch_1", to_iso(now() + timedelta(minutes=30)))
            await reminders._process_schedule_reminders(G1)

            row = await repo.get_schedule(G1, "sch_1")
            assert row["reminder_sent_flag"] == 1

            logs = await db.fetchall(
                "SELECT status FROM reminders_log WHERE guild_id = ? AND target_id = ?",
                (G1, "sch_1"),
            )
            assert [r["status"] for r in logs] == ["success"]
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# (2) 作成時のロールメンション
# ---------------------------------------------------------------------
def test_create_mentions_the_target_role_on_the_first_vote_message():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            channel = _FakeChannel()
            guild = _FakeGuild()
            cog = _cog(db, guild=guild, channel=channel)
            interaction = _Interaction(guild)
            interaction.channel = channel
            role = SimpleNamespace(id=900, mention="<@&900>")

            import cogs.schedule as schedule_mod

            original = schedule_mod.config.for_guild

            async def _fake_for_guild(gid):
                return GuildConfig(guild_id=gid)

            schedule_mod.config.for_guild = _fake_for_guild
            try:
                await Schedule.create.callback(
                    cog,
                    interaction,
                    title="秋合宿",
                    options="2026-10-01; 2026-10-02",
                    deadline="2026-09-20",
                    target_role=role,
                )
            finally:
                schedule_mod.config.for_guild = original

            vote_messages = [m for m in channel.sent if m["embed"] is not None]
            assert vote_messages, "投票メッセージが投稿されていない"
            assert vote_messages[0]["content"] and "<@&900>" in vote_messages[0]["content"], (
                "先頭の投票メッセージにロールメンションが無い"
            )
        finally:
            await db.close()

    run(_main())


def test_create_without_a_target_role_has_no_mention():
    """対象ロール無しの作成では余計な content を付けない（従来どおり）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            channel = _FakeChannel()
            guild = _FakeGuild()
            cog = _cog(db, guild=guild, channel=channel)
            interaction = _Interaction(guild)
            interaction.channel = channel

            import cogs.schedule as schedule_mod

            original = schedule_mod.config.for_guild

            async def _fake_for_guild(gid):
                return GuildConfig(guild_id=gid)

            schedule_mod.config.for_guild = _fake_for_guild
            try:
                await Schedule.create.callback(
                    cog,
                    interaction,
                    title="秋合宿",
                    options="2026-10-01",
                    deadline="2026-09-20",
                )
            finally:
                schedule_mod.config.for_guild = original

            vote_messages = [m for m in channel.sent if m["embed"] is not None]
            assert vote_messages
            assert not vote_messages[0]["content"]
        finally:
            await db.close()

    run(_main())
