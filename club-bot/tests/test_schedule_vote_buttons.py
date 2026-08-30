"""ボタン投票（ui_style='buttons'。候補を1メッセージに横並び）のテスト。

従来は候補ごとに1メッセージ＋リアクション投票で、候補が縦に並んで
見比べられなかった。全候補を1つの投票ボードに集約し、
「候補ボタン → 自分にだけ見えるステータス選択」で投票する。

このファイルが固定するもの:

1. **作成**: 既定（buttons）ではボードが1通だけ投稿され、候補は
   inline field、候補ごとにボタンが付き、message_id は全候補で共有される。
   候補 26 件以上はページ分割（Embed field・ボタンとも上限 25）
2. **投票**: apply_vote が票を書き、ボードの Embed を描き直す。
   取り消し（clear）で票が消える。締切済み・他ギルド・DM は拒否
3. **リアクションの無視**: ボードに付いたリアクションは投票にならない
   （message_id がボードを指すため、放っておくと落書きリアクションが
   「先頭候補への投票」に化ける）
4. **オプトアウト**: SCHEDULE_UI_STYLE='reaction' なら従来どおり
   候補ごとに1メッセージ＋リアクションで投稿される
5. **マイグレーション**: v22 の既存 DB に ui_style が追加され、
   既存行は 'reaction' のまま（投稿済みメッセージの挙動を変えない）
6. **再起動耐性**: custom_id が DynamicItem の template にマッチし、
   bot.py で登録されている（登録を忘れると再起動後に無反応になる）
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import discord

from cogs.schedule import (
    Schedule,
    VoteOptionButton,
    VoteStatusButton,
    build_status_picker_view,
)
from config import GuildConfig
from repositories.schedule_repository import ScheduleRepository
from services import schedule_service as svc
from utils.db import TABLE_DDL, TABLE_DDL_PG, Database
from utils.parser import TZ, to_iso

G1 = 100000000000000001
G2 = 200000000000000002
DAY = datetime(2026, 10, 1, 18, 0, tzinfo=TZ)


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


# ---------------------------------------------------------------------
# フェイク（test_schedule_notify.py と同じ作法。view / edit を足してある）
# ---------------------------------------------------------------------
class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.edits: list[dict] = []

    async def add_reaction(self, emoji):
        return None

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self, channel_id: int = 555, guild=None):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.guild = guild
        self.sent: list[dict] = []
        self.messages: dict[int, _FakeMessage] = {}

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        msg = _FakeMessage(1000 + len(self.sent) + 1)
        self.sent.append({"content": content, "embed": embed, "view": view, "id": msg.id})
        self.messages[msg.id] = msg
        return msg

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        try:
            return self.messages[int(message_id)]
        except KeyError:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "not found"
            ) from None


class _FakeGuild:
    def __init__(self, guild_id: int = G1):
        self.id = guild_id
        self.emojis = []

    def get_role(self, role_id: int):
        return None

    def get_member(self, user_id: int):
        return None

    def get_emoji(self, emoji_id: int):
        return None


class _Interaction:
    """スラッシュコマンド用（defer + followup.send）。"""

    def __init__(self, guild):
        self.guild = guild
        self.guild_id = guild.id if guild else None
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.channel = None
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)


class _ComponentInteraction:
    """ボタン押下用（response.send_message / response.edit_message）。"""

    def __init__(self, guild, guild_id=None, user_id: int = 42):
        self.guild = guild
        self.guild_id = guild.id if guild_id is None and guild else guild_id
        self.user = SimpleNamespace(id=user_id, display_name=f"user{user_id}")
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.response = SimpleNamespace(
            send_message=self._send_message, edit_message=self._edit_message
        )

    async def _send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def _edit_message(self, **kwargs):
        self.edited.append(kwargs)


def _cog(db: Database, guild=None, channel=None) -> Schedule:
    bot = SimpleNamespace(
        db=db,
        user=None,
        guilds=[],
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_channel=lambda cid: channel,
        get_user=lambda uid: None,
    )
    return Schedule(bot)


async def _create_via_command(db, *, style: str | None = None, option_count: int = 2):
    """/schedule create を実行し (cog, channel, schedule_id) を返す。"""
    import cogs.schedule as schedule_mod

    guild = _FakeGuild()
    channel = _FakeChannel(guild=guild)
    cog = _cog(db, guild=guild, channel=channel)
    interaction = _Interaction(guild)
    interaction.channel = channel

    kwargs = {"guild_id": G1}
    if style is not None:
        kwargs["schedule_ui_style"] = style

    original = schedule_mod.config.for_guild

    async def _fake_for_guild(gid):
        return GuildConfig(**kwargs)

    schedule_mod.config.for_guild = _fake_for_guild
    try:
        options = "; ".join(
            (DAY + timedelta(days=i)).strftime("%Y-%m-%d %H:%M") for i in range(option_count)
        )
        await Schedule.create.callback(
            cog,
            interaction,
            title="秋合宿",
            options=options,
            deadline="2026-09-20",
            place="部室",
        )
    finally:
        schedule_mod.config.for_guild = original

    rows = await ScheduleRepository(db).list_all(G1)
    assert rows, "予定が作成されていない"
    return cog, channel, rows[0]["schedule_id"]


async def _seed_board(db, *, closed: bool = False, message_id: str | None = "1001"):
    """ボタン式の予定（候補2件・ボード投稿済み想定）を直接シードする。"""
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1,
        "sch_b",
        "秋合宿",
        None,
        "部室",
        None,
        "2026-09-25T23:59:00+09:00",
        "tester",
        "555",
        ui_style="buttons",
    )
    await repo.add_option(G1, "opt1", "sch_b", "10/1 18:00", to_iso(DAY), None, message_id)
    await repo.add_option(
        G1, "opt2", "sch_b", "10/2 18:00", to_iso(DAY + timedelta(days=1)), None, message_id
    )
    if closed:
        await repo.close_schedule(G1, "sch_b")
    return repo


# =====================================================================
# 1. 作成（ボード投稿）
# =====================================================================
def test_create_posts_one_board_with_inline_candidates():
    """既定（buttons）は候補2件でもメッセージ1通。候補は inline field。"""

    async def _main():
        db = await _make_db()
        try:
            cog, channel, schedule_id = await _create_via_command(db)

            boards = [m for m in channel.sent if m["embed"] is not None]
            assert len(boards) == 1, "候補ごとにメッセージが分かれている（縦積みに戻っている）"
            embed = boards[0]["embed"]
            assert len(embed.fields) == 2
            assert all(f.inline for f in embed.fields), "候補が inline field でない"

            view = boards[0]["view"]
            assert view is not None, "投票ボタンが付いていない"
            custom_ids = [item.custom_id for item in view.children]
            assert len(custom_ids) == 2
            assert all(cid.startswith("sched:opt:") for cid in custom_ids)

            repo = ScheduleRepository(db)
            schedule = await repo.get_schedule(G1, schedule_id)
            assert schedule["ui_style"] == "buttons"
            options = await repo.list_options(G1, schedule_id)
            mids = {o["message_id"] for o in options}
            assert mids == {str(boards[0]["id"])}, "候補がボードの message_id を共有していない"
        finally:
            await db.close()

    run(_main())


def test_create_splits_boards_beyond_25_options():
    """候補 26 件以上はページ分割（field・ボタンとも上限 25）。"""

    async def _main():
        db = await _make_db()
        try:
            cog, channel, schedule_id = await _create_via_command(db, option_count=30)

            boards = [m for m in channel.sent if m["embed"] is not None]
            assert len(boards) == 2, "25 件を超えた候補が1通に詰め込まれている"
            assert len(boards[0]["view"].children) == svc.MAX_BOARD_OPTIONS
            assert len(boards[1]["view"].children) == 5
            assert len(boards[0]["embed"].fields) == svc.MAX_BOARD_OPTIONS
            assert "（1/2）" in boards[0]["embed"].title
            assert "（2/2）" in boards[1]["embed"].title
        finally:
            await db.close()

    run(_main())


def test_mention_is_only_on_the_first_board():
    async def _main():
        db = await _make_db()
        try:
            import cogs.schedule as schedule_mod

            guild = _FakeGuild()
            channel = _FakeChannel(guild=guild)
            cog = _cog(db, guild=guild, channel=channel)
            interaction = _Interaction(guild)
            interaction.channel = channel
            role = SimpleNamespace(id=900, mention="<@&900>")

            original = schedule_mod.config.for_guild

            async def _fake_for_guild(gid):
                return GuildConfig(guild_id=gid)

            schedule_mod.config.for_guild = _fake_for_guild
            try:
                options = "; ".join(
                    (DAY + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)
                )
                await Schedule.create.callback(
                    cog,
                    interaction,
                    title="秋合宿",
                    options=options,
                    deadline="2026-09-20",
                    target_role=role,
                )
            finally:
                schedule_mod.config.for_guild = original

            boards = [m for m in channel.sent if m["embed"] is not None]
            assert len(boards) == 2
            assert boards[0]["content"] and "<@&900>" in boards[0]["content"]
            assert not boards[1]["content"], "2ページ目までメンションが鳴っている"
        finally:
            await db.close()

    run(_main())


def test_reaction_style_opt_out_keeps_the_legacy_flow():
    """SCHEDULE_UI_STYLE='reaction' なら従来どおり候補ごとに1メッセージ。"""

    async def _main():
        db = await _make_db()
        try:
            cog, channel, schedule_id = await _create_via_command(db, style="reaction")

            vote_messages = [m for m in channel.sent if m["embed"] is not None]
            assert len(vote_messages) == 2, "reaction 設定なのに候補ごとのメッセージでない"
            assert all(m["view"] is None for m in vote_messages), "reaction 設定にボタンが付いた"

            schedule = await ScheduleRepository(db).get_schedule(G1, schedule_id)
            assert schedule["ui_style"] == "reaction"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. 投票（apply_vote / open_vote_picker）
# =====================================================================
def test_apply_vote_writes_the_vote_and_redraws_the_board():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_board(db)
            guild = _FakeGuild()
            channel = _FakeChannel(guild=guild)
            board = _FakeMessage(1001)
            channel.messages[1001] = board
            cog = _cog(db, guild=guild, channel=channel)

            interaction = _ComponentInteraction(guild)
            await cog.apply_vote(interaction, "opt1", "ok")

            votes = await repo.list_votes(G1, "opt1")
            assert [(v["user_id"], v["status"]) for v in votes] == [("42", "ok")]

            assert interaction.edited, "ステータス選択（ephemeral）が更新されていない"
            assert "参加" in interaction.edited[-1]["content"]

            assert board.edits, "投票ボードが描き直されていない"
            fields = board.edits[-1]["embed"].fields
            assert fields[0].value.splitlines()[0].startswith("参加 1"), (
                "ボードの集計に票が反映されていない"
            )
        finally:
            await db.close()

    run(_main())


def test_clear_removes_the_vote():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_board(db)
            guild = _FakeGuild()
            channel = _FakeChannel(guild=guild)
            channel.messages[1001] = _FakeMessage(1001)
            cog = _cog(db, guild=guild, channel=channel)

            await cog.apply_vote(_ComponentInteraction(guild), "opt1", "maybe")
            assert await repo.list_votes(G1, "opt1")

            await cog.apply_vote(_ComponentInteraction(guild), "opt1", "clear")
            assert await repo.list_votes(G1, "opt1") == [], "取り消しで票が消えていない"
        finally:
            await db.close()

    run(_main())


def test_voting_is_refused_after_the_deadline():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_board(db, closed=True)
            guild = _FakeGuild()
            cog = _cog(db, guild=guild, channel=_FakeChannel(guild=guild))

            interaction = _ComponentInteraction(guild)
            await cog.apply_vote(interaction, "opt1", "ok")

            assert await repo.list_votes(G1, "opt1") == [], "締切済みなのに票が書けた"
            assert interaction.sent and "締切済み" in (
                interaction.sent[-1]["embed"].description or ""
            )
        finally:
            await db.close()

    run(_main())


def test_votes_are_scoped_to_the_guild():
    """他ギルドから同じ option_id を押しても書けない（custom_id の持ち込み）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_board(db)
            other = _FakeGuild(G2)
            cog = _cog(db, guild=other, channel=_FakeChannel(guild=other))

            interaction = _ComponentInteraction(other)
            await cog.apply_vote(interaction, "opt1", "ok")

            assert await repo.list_votes(G1, "opt1") == [], "他ギルドから票が書けた"
            assert interaction.sent and "見つかりません" in (
                interaction.sent[-1]["embed"].description or ""
            )
        finally:
            await db.close()

    run(_main())


def test_dm_interactions_are_refused():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_board(db)
            cog = _cog(db)
            interaction = _ComponentInteraction(None, guild_id=None)
            await cog.apply_vote(interaction, "opt1", "ok")
            assert await repo.list_votes(G1, "opt1") == []
            assert interaction.sent, "DM 拒否の返信が無い"
        finally:
            await db.close()

    run(_main())


def test_the_picker_shows_status_buttons_and_my_answer():
    async def _main():
        db = await _make_db()
        try:
            import cogs.schedule as schedule_mod

            repo = await _seed_board(db)
            await repo.set_vote(G1, "opt1", "42", "maybe")
            guild = _FakeGuild()
            cog = _cog(db, guild=guild, channel=_FakeChannel(guild=guild))

            original = schedule_mod.config.for_guild

            async def _fake_for_guild(gid):
                return GuildConfig(guild_id=gid)

            schedule_mod.config.for_guild = _fake_for_guild
            try:
                interaction = _ComponentInteraction(guild)
                await cog.open_vote_picker(interaction, "opt1")
            finally:
                schedule_mod.config.for_guild = original

            assert interaction.sent
            picked = interaction.sent[-1]
            assert picked.get("ephemeral") is True, "ステータス選択が公開で出ている"
            assert "未定" in picked["content"], "自分の現在の回答が出ていない"
            custom_ids = [item.custom_id for item in picked["view"].children]
            assert custom_ids == [
                "sched:vote:opt1:ok",
                "sched:vote:opt1:maybe",
                "sched:vote:opt1:ng",
                "sched:vote:opt1:clear",
            ]
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. リアクションの無視（ボードは message_id で候補に引っかかる）
# =====================================================================
def test_reactions_on_a_button_board_do_not_vote():
    async def _main():
        db = await _make_db()
        try:
            import cogs.schedule as schedule_mod

            repo = await _seed_board(db)
            guild = _FakeGuild()
            cog = _cog(db, guild=guild, channel=_FakeChannel(guild=guild))

            payload = SimpleNamespace(
                user_id=42,
                guild_id=G1,
                channel_id=555,
                message_id=1001,
                emoji=discord.PartialEmoji(name="✅"),
                member=None,
            )

            original = schedule_mod.config.for_guild

            async def _fake_for_guild(gid):
                return GuildConfig(guild_id=gid)

            schedule_mod.config.for_guild = _fake_for_guild
            try:
                await cog._handle_reaction(payload, added=True)
            finally:
                schedule_mod.config.for_guild = original

            assert await repo.list_votes(G1, "opt1") == [], (
                "ボードへのリアクションが投票に化けた"
            )
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. スキーマとマイグレーション
# =====================================================================
def test_ui_style_is_declared_in_both_ddls():
    for ddl in (TABLE_DDL["schedules"], TABLE_DDL_PG["schedules"]):
        assert re.search(r"ui_style\s+TEXT NOT NULL DEFAULT 'reaction'", ddl)


def test_migration_adds_ui_style_and_keeps_existing_rows_on_reaction():
    """v22 の既存 DB: 列が足され、既存の予定は 'reaction' のまま。"""

    async def _main():
        path = _tmp_db_path()
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schedules (
                schedule_id        TEXT PRIMARY KEY,
                guild_id           BIGINT NOT NULL,
                title              TEXT NOT NULL,
                description        TEXT,
                place              TEXT,
                target_role_id     TEXT,
                deadline           TEXT NOT NULL,
                created_by         TEXT NOT NULL,
                channel_id         TEXT NOT NULL,
                closed_flag        INTEGER NOT NULL DEFAULT 0,
                reminder_sent_flag INTEGER NOT NULL DEFAULT 0,
                sheet_title        TEXT,
                deleted_flag       INTEGER NOT NULL DEFAULT 0,
                confirmed_option_id TEXT
            );
            INSERT INTO schedules
                (schedule_id, guild_id, title, deadline, created_by, channel_id)
            VALUES ('old1', 100000000000000001, '既存の予定',
                    '2026-09-25T23:59:00+09:00', 'tester', '555');
            PRAGMA user_version = 22;
            """
        )
        conn.commit()
        conn.close()

        db = Database(path)
        await db.connect()
        try:
            row = await db.fetchone(
                "SELECT * FROM schedules WHERE schedule_id = ?", ("old1",)
            )
            assert row is not None, "マイグレーションで既存行が消えた"
            assert row["ui_style"] == "reaction", "既存の予定が reaction のままでない"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 5. 再起動耐性（DynamicItem の登録と custom_id の対応）
# =====================================================================
def test_custom_ids_match_the_dynamic_item_templates():
    opt = VoteOptionButton(svc.new_option_id(), "10/1 18:00")
    assert re.fullmatch(
        VoteOptionButton.__discord_ui_compiled_template__.pattern, opt.item.custom_id
    )
    for status in ("ok", "maybe", "ng", "clear"):
        btn = VoteStatusButton(svc.new_option_id(), status)
        assert re.fullmatch(
            VoteStatusButton.__discord_ui_compiled_template__.pattern, btn.item.custom_id
        )


def test_dynamic_items_are_registered_in_bot_py():
    """bot.py で登録していないと、再起動後にボードが無反応になる。"""
    bot_py = os.path.join(os.path.dirname(__file__), "..", "bot.py")
    with open(bot_py, encoding="utf-8") as f:
        source = f.read()
    assert "VoteOptionButton" in source
    assert "VoteStatusButton" in source


def test_status_picker_view_has_persistent_children():
    view = build_status_picker_view(GuildConfig(guild_id=G1), None, "opt1")
    assert len(view.children) == 4
    assert view.timeout is None, "timeout があると再起動前でも時間切れで死ぬ"


# =====================================================================
# 6. ギルド別設定 SCHEDULE_UI_STYLE の解決
# =====================================================================
def test_guild_setting_overrides_ui_style_and_bad_values_fall_back():
    async def _main():
        db = await _make_db()
        try:
            from config import config as global_config
            from repositories.settings_repository import SettingsRepository

            settings = SettingsRepository(db)

            global_config.invalidate_guild(G1)
            gc = await global_config.for_guild(G1, db=db)
            assert gc.schedule_ui_style == "buttons", "既定が buttons でない"

            await settings.set(G1, "SCHEDULE_UI_STYLE", "reaction")
            global_config.invalidate_guild(G1)
            gc = await global_config.for_guild(G1, db=db)
            assert gc.schedule_ui_style == "reaction"

            await settings.set(G1, "SCHEDULE_UI_STYLE", "banana")
            global_config.invalidate_guild(G1)
            gc = await global_config.for_guild(G1, db=db)
            assert gc.schedule_ui_style == "buttons", "不正値が既定へ落ちていない"
        finally:
            from config import config as global_config

            global_config.invalidate_guild(G1)
            await db.close()

    run(_main())
