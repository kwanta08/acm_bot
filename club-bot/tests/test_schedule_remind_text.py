"""未回答リマインドの文面を、その予定の投票 UI に合わせる（H1-4）。

以前は DM 本文が「投票チャンネルでリアクションをお願いします。」で固定だった。
既定の UI はボタン式で、**ボタン式のボードに付けたリアクションは投票として
扱われない**（`_handle_reaction` が ui_style == "buttons" を無視する）。
催促された人が言われたとおりにしても票が入らなかった。

- 文面は純粋関数 `unanswered_reminder_text` が組む（DB も Discord も触らない）
- `/schedule remind`（手動）と締切前の自動催促の**両方の経路を実際に通して**、
  実 DB の行の ui_style で文面が分岐し、同じ本文になることを固定する。
  このテストが守るのは「各経路のクエリ（get_schedule / list_reminder_candidates）が
  ui_style を返し、notify_unanswered がそれで分岐する」こと
- 読むのは**予定の行**の ui_style で、ギルド設定の SCHEDULE_UI_STYLE ではない
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from test_intents import _iter_source_files

from cogs.schedule import Schedule
from config import config
from repositories.schedule_repository import ScheduleRepository
from services.schedule_service import unanswered_reminder_text
from utils.db import Database
from utils.parser import now, to_iso

BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
G1 = 111
ROLE_ID = 900
CHANNEL_ID = 555
HEADING = "【日程調整リマインド】"


def run(coro):
    return asyncio.run(coro)


# =====================================================================
# 1. 純粋関数
# =====================================================================
def test_text_for_buttons():
    text = unanswered_reminder_text("秋合宿", "10/1(水) 23:59", "buttons")
    assert "ボタン" in text
    assert "リアクション" not in text
    assert "秋合宿" in text and "10/1(水) 23:59" in text


def test_text_for_reaction():
    text = unanswered_reminder_text("秋合宿", "10/1(水) 23:59", "reaction")
    assert "リアクション" in text
    assert "ボタン" not in text
    assert "秋合宿" in text and "10/1(水) 23:59" in text


@pytest.mark.parametrize("ui_style", [None, "", "   ", "unknown", "Buttons"])
def test_text_falls_back_to_buttons(ui_style):
    """欠損・未知の値は例外を投げず、既定（ボタン式）の文面。"""
    assert unanswered_reminder_text("t", "d", ui_style) == unanswered_reminder_text(
        "t", "d", "buttons"
    )


def test_text_tolerates_case_and_spaces():
    assert unanswered_reminder_text("t", "d", " Reaction ") == unanswered_reminder_text(
        "t", "d", "reaction"
    )


# =====================================================================
# 2. 実際の経路（手動 /schedule remind・締切前の自動催促）
# =====================================================================
class _Member:
    def __init__(self, user_id: int, *, dm_closed: bool = False):
        self.id = user_id
        self.bot = False
        self.mention = f"<@{user_id}>"
        self.dm_closed = dm_closed
        self.dms: list[str] = []

    async def send(self, text):
        if self.dm_closed:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="dm closed"), "denied")
        self.dms.append(text)


class _Channel:
    def __init__(self):
        self.id = CHANNEL_ID
        self.sent: list[str] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class _Guild:
    def __init__(self, members, channel):
        self.id = G1
        self.emojis = []
        self._members = {m.id: m for m in members}
        self._role = SimpleNamespace(id=ROLE_ID, members=list(members))
        self._channel = channel

    def get_role(self, role_id):
        return self._role if role_id == ROLE_ID else None

    def get_member(self, user_id):
        return self._members.get(user_id)

    def get_channel_or_thread(self, channel_id):
        return self._channel if channel_id == CHANNEL_ID else None


class _Interaction:
    def __init__(self, guild):
        self.guild = guild
        self.guild_id = guild.id
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, *args, **kwargs):
        self.sent.append(kwargs)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _env(members, channel):
    """実 DB・本物の Schedule cog・本物の Reminders を組む。

    ギルド設定の SCHEDULE_UI_STYLE は**未設定のまま**（既定は buttons）。
    予定の行の ui_style だけが文面を決めることを見るため。
    """
    from cogs.reminders import Reminders

    db = Database(_tmp_db_path())
    await db.connect()
    guild = _Guild(members, channel)
    schedule_bot = SimpleNamespace(
        db=db,
        user=None,
        guilds=[guild],
        get_guild=lambda gid: guild if gid == G1 else None,
    )
    schedule_cog = Schedule(schedule_bot)
    # Reminders 側の bot は別物（MagicMock の get_guild を Schedule に渡さない）
    reminders_bot = mock.MagicMock()
    reminders_bot.db = db
    reminders_bot.guilds = []
    reminders_bot.get_cog = lambda name: schedule_cog if name == "Schedule" else None
    return db, guild, schedule_cog, Reminders(reminders_bot)


async def _seed(db, ui_style: str) -> None:
    """締切が催促の窓（今から1時間以内）に入る予定を、ui_style を明示して作る。"""
    await ScheduleRepository(db).create_schedule(
        G1,
        schedule_id="sch_1",
        title="秋合宿",
        description=None,
        place=None,
        target_role_id=str(ROLE_ID),
        deadline_iso=to_iso(now() + timedelta(minutes=30)),
        created_by="tester",
        channel_id=str(CHANNEL_ID),
        ui_style=ui_style,
    )


@pytest.mark.parametrize(
    ("ui_style", "word", "other"),
    [("buttons", "ボタン", "リアクション"), ("reaction", "リアクション", "ボタン")],
)
def test_manual_and_automatic_reminders_send_the_same_text(ui_style, word, other):
    async def _main():
        member = _Member(7)
        db, guild, schedule_cog, reminders = await _env([member], _Channel())
        try:
            await _seed(db, ui_style)

            # 手動（/schedule remind）
            await Schedule.remind.callback(schedule_cog, _Interaction(guild), schedule_id="sch_1")
            assert len(member.dms) == 1, "手動の催促が届いていない"

            # 締切前の自動催促
            await reminders._process_schedule_reminders(G1)
            assert len(member.dms) == 2, "自動の催促が届いていない"
            row = await ScheduleRepository(db).get_schedule(G1, "sch_1")
            assert row["reminder_sent_flag"] == 1

            manual, automatic = member.dms
            assert manual == automatic
            assert word in manual
            assert other not in manual
        finally:
            await db.close()
            config.clear_guild_cache()

    run(_main())


@pytest.mark.parametrize(
    ("ui_style", "word", "other"),
    [("buttons", "ボタン", "リアクション"), ("reaction", "リアクション", "ボタン")],
)
def test_channel_fallback_uses_the_same_text(ui_style, word, other):
    """DM を拒否した人へのチャンネルのフォールバックも、DM と同じ本文。"""

    async def _main():
        reachable, closed = _Member(7), _Member(8, dm_closed=True)
        channel = _Channel()
        db, _guild, schedule_cog, _reminders = await _env([reachable, closed], channel)
        try:
            await _seed(db, ui_style)
            schedule = await ScheduleRepository(db).get_schedule(G1, "sch_1")
            assert await schedule_cog.notify_unanswered(dict(schedule)) == 2

            assert len(reachable.dms) == 1
            assert len(channel.sent) == 1, "DM 不可の人へのフォールバックが無い"
            posted = channel.sent[0]
            assert closed.mention in posted
            assert word in posted and other not in posted
            assert posted.endswith(reachable.dms[0]), "チャンネルの本文が DM と違う"
        finally:
            await db.close()
            config.clear_guild_cache()

    run(_main())


def test_missing_ui_style_falls_back_to_the_buttons_text():
    """ui_style キーの無い予定（壊れた行・古い呼び出し）でも例外を投げず、ボタンの文面。"""

    async def _main():
        member = _Member(7)
        db, _guild, schedule_cog, _reminders = await _env([member], _Channel())
        try:
            await _seed(db, "reaction")
            schedule = dict(await ScheduleRepository(db).get_schedule(G1, "sch_1"))
            del schedule["ui_style"]
            assert await schedule_cog.notify_unanswered(schedule) == 1
            assert len(member.dms) == 1
            assert "ボタン" in member.dms[0]
            assert "リアクション" not in member.dms[0]
        finally:
            await db.close()
            config.clear_guild_cache()

    run(_main())


def test_rows_from_before_ui_style_get_the_reaction_text():
    """ui_style 列を省いて入れた行（v23 以前の予定と同じ形）は DB の既定 'reaction'。

    既存の予定は書き換えず、それぞれの方式の文面になる。
    """

    async def _main():
        member = _Member(7)
        db, _guild, schedule_cog, _reminders = await _env([member], _Channel())
        try:
            await db.execute(
                "INSERT INTO schedules (guild_id, schedule_id, title, target_role_id,"
                " deadline, created_by, channel_id, closed_flag, reminder_sent_flag)"
                " VALUES (?, 'old_1', '夏合宿', ?, ?, 'tester', ?, 0, 0)",
                (G1, str(ROLE_ID), to_iso(now() + timedelta(minutes=30)), str(CHANNEL_ID)),
            )
            schedule = dict(await ScheduleRepository(db).get_schedule(G1, "old_1"))
            assert schedule["ui_style"] == "reaction"
            assert await schedule_cog.notify_unanswered(schedule) == 1
            assert "リアクション" in member.dms[0]
        finally:
            await db.close()
            config.clear_guild_cache()

    run(_main())


# =====================================================================
# 3. 再発の検出: 文面は1箇所で組む
# =====================================================================
def _rel(path: str) -> str:
    return os.path.relpath(path, BOT_ROOT).replace(os.sep, "/")


def test_reminder_text_is_built_in_one_place():
    """見出し `【日程調整リマインド】` は services/schedule_service.py にしか現れない。

    3本目の送信経路が文面を自前で組むのを防ぐ。**生のソースをそのまま検索する**
    （文字列リテラルを潰す走査では、探したい文字列そのものが消える）。
    """
    found = set()
    for path in _iter_source_files():
        rel = _rel(path)
        if not rel.startswith(("cogs/", "services/", "utils/")):
            continue
        with open(path, encoding="utf-8") as f:
            if HEADING in f.read():
                found.add(rel)
    assert "services/schedule_service.py" in found, "見出しが変わって検査が空回りしている"
    assert found == {"services/schedule_service.py"}, f"文面を別の場所で組んでいる: {found}"
    assert unanswered_reminder_text("t", "d", "buttons").startswith(HEADING)
