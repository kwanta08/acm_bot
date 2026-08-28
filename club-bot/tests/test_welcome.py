"""新入生オンボーディングのテスト（G3-6）。

新歓期に30〜50人が入るのに bot は新入生の存在を知らず、幹部が
`/member register` を1人ずつ手打ちしていた。

**このファイルが特に固定しているもの**

1. **OFF のとき何も起きない**（受入基準。既定 OFF ＝ ADR 0024）。
   ヘルパ関数ではなく**実物の Cog のリスナー**を呼んで確かめる
2. **参加しただけでは `members` に登録しない。** 登録は班を選んだときだけ。
   ここを外すと訪問者・OB まで台帳に入り、名簿を母集団にしている
   未回答催促（G3-2 / ADR 0025）が誤爆する
3. **`cogs.welcome` が bot.COGS に載っている。** これが無いと
   「本番で一度も読まれない Cog」でも OFF テストは緑になる
4. **`DynamicItem` を使うなら requirements の下限が 2.4 以上。**
   venv は 2.7.1 なので 2.3 環境の破綻はテストで検出できない
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import discord

from bot import COGS
from cogs.welcome import MAX_TEAM_OPTIONS, TeamPickButton, TeamPickView, Welcome
from config import config
from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from utils.db import Database

G1 = 100000000000000001
G2 = 200000000000000002
USER = 501
OTHER = 777
BOT_ROOT = Path(__file__).resolve().parent.parent


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


def _cleanup_config() -> None:
    config._db = None
    config.clear_guild_cache()


async def _enable(db: Database, guild_id: int = G1, channel_id: str | None = None) -> None:
    repo = SettingsRepository(db)
    await repo.set(guild_id, "WELCOME_ENABLED", "1")
    if channel_id:
        await repo.set(guild_id, "WELCOME_CHANNEL_ID", channel_id)
    config.clear_guild_cache()


async def _add_teams(
    db: Database, guild_id: int = G1, count: int = 2, with_roles: bool = True
) -> None:
    """班を作る。

    `with_roles=False` は「ロールが紐付いていない班」——最小権限の招待
    （ADR 0017）ではロールの自動作成が失敗するので、新規ギルドの既定に近い。
    """
    repo = MemberRepository(db)
    for i in range(1, count + 1):
        await repo.upsert_team(guild_id, f"team{i}", f"班{i}")
        if with_roles:
            await repo.set_team_roles(guild_id, f"team{i}", member_role_id=str(7000 + i))


# ---------------------------------------------------------------------
# ダブル
# ---------------------------------------------------------------------
class _Permissions:
    def __init__(self, **kwargs):
        self.view_channel = kwargs.get("view_channel", True)
        self.send_messages = kwargs.get("send_messages", True)
        self.embed_links = kwargs.get("embed_links", True)


class _Channel:
    def __init__(self, channel_id: int = 900, visible_to_member: bool = True, bot_ok: bool = True):
        self.id = channel_id
        self.sent: list[dict] = []
        self._visible = visible_to_member
        self._bot_ok = bot_ok

    def permissions_for(self, who):
        if getattr(who, "is_bot_user", False):
            return _Permissions(
                view_channel=self._bot_ok, send_messages=self._bot_ok, embed_links=self._bot_ok
            )
        return _Permissions(view_channel=self._visible)

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        self.sent.append({"content": content, "embed": embed, "view": view})


class _Category:
    """`send` を持たないチャンネル（カテゴリ・フォーラム）。"""

    def __init__(self, channel_id: int = 900):
        self.id = channel_id

    def permissions_for(self, who):
        return _Permissions()


class _Member:
    def __init__(self, user_id: int = USER, guild=None, dm_ok: bool = True, is_bot: bool = False):
        self.id = user_id
        self.bot = is_bot
        self.display_name = f"user{user_id}"
        self.mention = f"<@{user_id}>"
        self.guild = guild
        self.dms: list[dict] = []
        self._dm_ok = dm_ok
        self.roles_added: list[int] = []

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        if not self._dm_ok:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="dm"), "cannot send")
        self.dms.append({"embed": embed, "view": view})


class _Guild:
    def __init__(self, guild_id: int = G1, channel=None, members=None):
        self.id = guild_id
        self.name = "テストサーバー"
        self._channel = channel
        self._members = {m.id: m for m in (members or [])}
        self.me = SimpleNamespace(id=1, is_bot_user=True)
        # **「送信できる最初のチャンネル」へ落とす実装に戻したら赤くなる**
        # ように、実物と同じ入口を持たせておく（これが無いと、
        # フォールバック先を勝手に決める実装でもテストが緑のまま通る）
        self.text_channels = [channel] if channel is not None else []
        self.system_channel = channel

    def get_channel(self, channel_id: int):
        return self._channel if self._channel and self._channel.id == channel_id else None

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class _Interaction:
    def __init__(self, user_id: int = USER, guild_id: int | None = None):
        self.user = SimpleNamespace(id=user_id, display_name=f"user{user_id}")
        self.guild_id = guild_id
        self.sent: list[dict] = []
        self.client = None
        self.response = SimpleNamespace(send_message=self._send)

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1].get("embed")
        if embed is None:
            return ""
        return (embed.title or "") + "\n" + (embed.description or "")


def _cog(db: Database, guild=None, members_cog=None) -> Welcome:
    bot = SimpleNamespace(
        db=db,
        guilds=[],
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_cog=lambda name: members_cog if name == "Members" else None,
    )
    return Welcome(bot)


class _MembersCog:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    async def _sync_roles(self, guild, member, user_id):
        if self.fail:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="roles"), "no perms")
        self.calls.append(user_id)


# =====================================================================
# 1. 既定 OFF
# =====================================================================
def test_nothing_happens_when_welcome_is_disabled():
    """**受入基準。** OFF のギルドでは DM も台帳登録も起きない。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            guild = _Guild()
            member = _Member(guild=guild)
            cog = _cog(db, guild=guild)
            await Welcome.on_member_join(cog, member)

            assert member.dms == [], "OFF なのに DM を送っている"
            assert await MemberRepository(db).list_members(G1) == [], "OFF なのに台帳へ登録した"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_dm_is_sent_when_enabled():
    """OFF の裏返し。これが無いと「常に何もしない実装」が通る。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            await _enable(db)
            guild = _Guild()
            member = _Member(guild=guild)
            cog = _cog(db, guild=guild)
            await Welcome.on_member_join(cog, member)

            assert len(member.dms) == 1, "ON なのに DM が飛んでいない"
            view = member.dms[0]["view"]
            assert view is not None, "ボタンが付いていない"
            # 参加イベント → custom_id への埋め込みの配線まで見る。
            # user_id を取り違えると、他人が押せるボタンになる
            custom_ids = [getattr(i, "custom_id", None) for i in view.children]
            assert f"welcome:team:{G1}:{USER}" in custom_ids, custom_ids
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_setting_is_per_guild():
    """A で ON、B で OFF。B の参加者には何も起きない。"""

    async def _main():
        db = await _make_db()
        try:
            await _enable(db, G1)
            guild_b = _Guild(guild_id=G2)
            member_b = _Member(guild=guild_b)
            cog = _cog(db, guild=guild_b)
            await Welcome.on_member_join(cog, member_b)
            assert member_b.dms == [], "他ギルドの設定が漏れている"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_bots_are_ignored():
    async def _main():
        db = await _make_db()
        try:
            await _enable(db)
            guild = _Guild()
            member = _Member(guild=guild, is_bot=True)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert member.dms == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_an_invalid_setting_value_does_not_raise():
    """不正値でギルドの全コマンドを道連れにしない。"""

    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "WELCOME_ENABLED", "たぶん")
            config.clear_guild_cache()
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.welcome_enabled is False
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_toggle_round_trips_through_setup():
    """`/setup` の保存値をリスナーが解釈できること。

    set() は str(value) を書くので、Python の True を渡すと "True" が
    保存される。ヘルパの単体テストだけだと、トグルが無言の no-op に
    なっていても気づけない。
    """

    async def _main():
        from cogs.setup_wizard import SetupWizard

        db = await _make_db()
        try:
            await _add_teams(db)
            wizard = SetupWizard(SimpleNamespace(db=db))
            await wizard.save_setting(G1, "WELCOME_ENABLED", "1")
            config.clear_guild_cache()

            guild = _Guild()
            member = _Member(guild=guild)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert len(member.dms) == 1, "/setup で ON にしてもリスナーが動かない"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


# =====================================================================
# 2. DM 拒否時のフォールバック
# =====================================================================
def test_a_blocked_dm_falls_back_to_the_configured_channel():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            channel = _Channel()
            await _enable(db, channel_id=str(channel.id))
            guild = _Guild(channel=channel)
            member = _Member(guild=guild, dm_ok=False)
            await Welcome.on_member_join(_cog(db, guild=guild), member)

            assert len(channel.sent) == 1
            assert channel.sent[0]["content"] == member.mention
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_nothing_is_sent_when_no_welcome_channel_is_configured():
    """送信できる最初のチャンネルへ落とさない（新入生が読めない先へ出さない）。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            await _enable(db)  # チャンネル未設定
            channel = _Channel()
            guild = _Guild(channel=channel)
            member = _Member(guild=guild, dm_ok=False)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert channel.sent == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_channel_the_member_cannot_see_is_not_used():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            channel = _Channel(visible_to_member=False)
            await _enable(db, channel_id=str(channel.id))
            guild = _Guild(channel=channel)
            member = _Member(guild=guild, dm_ok=False)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert channel.sent == [], "本人に見えないチャンネルへ出している"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_channel_the_bot_cannot_post_to_is_not_used():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            channel = _Channel(bot_ok=False)
            await _enable(db, channel_id=str(channel.id))
            guild = _Guild(channel=channel)
            member = _Member(guild=guild, dm_ok=False)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert channel.sent == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_category_setting_does_not_raise():
    """設定値がカテゴリ・フォーラムを指していても例外にしない。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            category = _Category()
            await _enable(db, channel_id=str(category.id))
            guild = _Guild(channel=category)
            member = _Member(guild=guild, dm_ok=False)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


# =====================================================================
# 3. ボタン → 班選択 → 登録
# =====================================================================
def test_joining_alone_does_not_create_a_member_row():
    """**参加しただけでは台帳へ入れない。**

    ここを外すと訪問者・OB まで名簿に入り、名簿を母集団にしている
    未回答催促（G3-2 / ADR 0025）が誤爆する。
    """

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            await _enable(db)
            guild = _Guild()
            member = _Member(guild=guild)
            await Welcome.on_member_join(_cog(db, guild=guild), member)
            assert await MemberRepository(db).list_members(G1) == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_picking_a_team_registers_and_assigns_the_role():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            guild = _Guild(members=[_Member()])
            members_cog = _MembersCog()
            cog = _cog(db, guild=guild, members_cog=members_cog)
            interaction = _Interaction()
            await cog.register_team(interaction, G1, USER, "team1")

            row = await MemberRepository(db).get_member(G1, str(USER))
            assert row is not None and row["primary_team"] == "team1"
            assert members_cog.calls == [str(USER)], "ロール同期を呼んでいない"
            assert "班1" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_registration_succeeds_even_without_manage_roles():
    """最小権限の招待ではロールを付けられない（ADR 0017）。登録は通す。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            guild = _Guild(members=[_Member()])
            cog = _cog(db, guild=guild, members_cog=_MembersCog(fail=True))
            interaction = _Interaction()
            await cog.register_team(interaction, G1, USER, "team1")

            row = await MemberRepository(db).get_member(G1, str(USER))
            assert row is not None and row["primary_team"] == "team1"
            assert "ロールは付けられませんでした" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_team_without_a_role_says_so():
    """`_sync_roles` は紐付けが無い班では何もせず正常終了する。

    戻り値だけを見ていると「ロールが付いた」と誤解させる。最小権限の
    招待（ADR 0017）ではロールの自動作成が失敗するので、これは例外では
    なく新規ギルドの既定に近い状態。
    """

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db, with_roles=False)
            members_cog = _MembersCog()
            cog = _cog(db, guild=_Guild(members=[_Member()]), members_cog=members_cog)
            interaction = _Interaction()
            await cog.register_team(interaction, G1, USER, "team1")

            row = await MemberRepository(db).get_member(G1, str(USER))
            assert row is not None and row["primary_team"] == "team1", "登録は通すこと"
            assert "ロールが紐付いていない" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_registration_does_not_touch_status_or_active_flag():
    """再参加した卒業生が自動で現役に戻らないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            repo = MemberRepository(db)
            await repo.upsert_member(G1, str(USER), "卒業生")
            await repo.set_status(G1, str(USER), "alumni", left_season="2025年度")

            guild = _Guild(members=[_Member()])
            cog = _cog(db, guild=guild, members_cog=_MembersCog())
            await cog.register_team(_Interaction(), G1, USER, "team1")

            row = await repo.get_member(G1, str(USER))
            assert row["status"] == "alumni", "卒業生が自動で現役へ戻っている"
            assert row["active_flag"] == 1
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_a_member_who_left_cannot_register():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            guild = _Guild(members=[])  # 押す前に退出
            cog = _cog(db, guild=guild, members_cog=_MembersCog())
            interaction = _Interaction()
            await cog.register_team(interaction, G1, USER, "team1")
            assert await MemberRepository(db).list_members(G1) == []
            assert "参加していない" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_picker_warns_before_replacing_an_existing_team():
    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            repo = MemberRepository(db)
            await repo.upsert_member(G1, str(USER), "既存部員", primary_team="team2")
            cog = _cog(db, guild=_Guild(members=[_Member()]))
            interaction = _Interaction()
            await cog.open_team_picker(interaction, G1, USER)
            assert "入れ替わります" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_picker_explains_when_no_team_exists():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db, guild=_Guild(members=[_Member()]))
            interaction = _Interaction()
            await cog.open_team_picker(interaction, G1, USER)
            assert "まだ班が登録されていません" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_more_than_25_teams_are_truncated_with_a_note():
    """Select は25件上限。超えると押下時に HTTPException になる。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db, count=MAX_TEAM_OPTIONS + 3)
            cog = _cog(db, guild=_Guild(members=[_Member()]))
            interaction = _Interaction()
            await cog.open_team_picker(interaction, G1, USER)
            view = interaction.sent[-1]["view"]
            assert len(view.picker.options) == MAX_TEAM_OPTIONS
            assert "先頭" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_picker_is_ephemeral_in_a_guild_but_not_in_dm():
    """公開面に選択 UI を残さない（チャンネルへ落ちた場合）。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            cog = _cog(db, guild=_Guild(members=[_Member()]))
            in_guild = _Interaction(guild_id=G1)
            await cog.open_team_picker(in_guild, G1, USER)
            assert in_guild.sent[-1]["ephemeral"] is True

            in_dm = _Interaction(guild_id=None)
            await cog.open_team_picker(in_dm, G1, USER)
            assert in_dm.sent[-1]["ephemeral"] is False
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


# =====================================================================
# 4. 他人・他ギルドの押下
# =====================================================================
def _button() -> TeamPickButton:
    return TeamPickButton(G1, USER)


def test_someone_else_cannot_use_the_button():
    """チャンネルへ落ちたボタンが「誰でも押せる班ロール自販機」にならない。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            button = _button()
            interaction = _Interaction(user_id=OTHER, guild_id=G1)
            assert await button.interaction_check(interaction) is False
            assert await MemberRepository(db).list_members(G1) == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_button_is_scoped_to_its_guild():
    async def _main():
        button = _button()
        interaction = _Interaction(user_id=USER, guild_id=G2)
        assert await button.interaction_check(interaction) is False

    run(_main())


def test_the_owner_can_use_the_button():
    async def _main():
        button = _button()
        assert await button.interaction_check(_Interaction(user_id=USER, guild_id=G1)) is True
        assert await button.interaction_check(_Interaction(user_id=USER, guild_id=None)) is True

    run(_main())


def test_the_custom_id_round_trips():
    button = _button()
    match = re.fullmatch(
        TeamPickButton.__discord_ui_compiled_template__.pattern, button.item.custom_id
    )
    assert match is not None
    assert int(match["guild_id"]) == G1
    assert int(match["user_id"]) == USER


def test_picking_from_another_user_is_rejected():
    """Select 側でも本人確認する（View は短命だが公開面に出ることがある）。"""

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            teams = await MemberRepository(db).list_teams(G1)
            cog = _cog(db, guild=_Guild(members=[_Member()]), members_cog=_MembersCog())
            view = TeamPickView(cog, G1, USER, teams)
            view.picker._selected_values = ["team1"]
            await view._on_pick(_Interaction(user_id=OTHER, guild_id=G1))
            assert await MemberRepository(db).list_members(G1) == []
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


# =====================================================================
# 5. 配線（これが無いと本番で一度も動かない）
# =====================================================================
def test_the_cog_is_loaded_by_the_bot():
    assert "cogs.welcome" in COGS


def test_the_dynamic_item_is_registered():
    """登録を忘れると、再起動を挟んだ瞬間にボタンが無反応になる。"""
    source = (BOT_ROOT / "bot.py").read_text(encoding="utf-8")
    assert "add_dynamic_items" in source, "bot.py で DynamicItem を登録していない"
    assert "TeamPickButton" in source


def test_requirements_declare_a_discord_py_that_has_dynamic_item():
    """`DynamicItem` は discord.py 2.4 以降。

    venv は 2.7.1 なので 2.3 環境の破綻はテストで検出できない。しかも
    Cog のロード失敗は bot.py が握り潰すので無言になる。下限を宣言で守る。
    """
    uses_dynamic = any(
        "DynamicItem" in path.read_text(encoding="utf-8")
        for path in (BOT_ROOT / "cogs").glob("*.py")
    )
    if not uses_dynamic:
        return

    requirements = (BOT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(r"discord\.py>=(\d+)\.(\d+)", requirements)
    assert match is not None, "requirements.txt が discord.py の下限を宣言していない"
    assert (int(match[1]), int(match[2])) >= (2, 4), (
        f"DynamicItem を使うなら下限は 2.4 以上（現在: {match[0]}）"
    )


# =====================================================================
# 6. /setup のトグルと /setup-status の分岐
# =====================================================================
class _EditInteraction:
    """コンポーネント操作の interaction（edit_message を記録する）。"""

    def __init__(self, user_id: int = USER):
        self.user = SimpleNamespace(id=user_id, display_name="admin")
        self.edited: list[dict] = []
        self.messages: list[dict] = []
        self.response = SimpleNamespace(edit_message=self._edit, send_message=self._send)

    async def _edit(self, **kwargs):
        self.edited.append(kwargs)

    async def _send(self, **kwargs):
        self.messages.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.edited[-1].get("embed")
        if embed is None:
            return ""
        return (embed.title or "") + "\n" + (embed.description or "")


def test_the_setup_toggle_flips_the_stored_value():
    """トグル本体を通す。

    `save_setting` を直接呼ぶテストだけだと、`turning_on` の反転や
    `"1"` を書く行を消しても緑のまま通る。
    """

    async def _main():
        from cogs.setup_wizard import SetupWizard, SetupWizardView

        db = await _make_db()
        try:
            cog = SetupWizard(SimpleNamespace(db=db))
            view = SetupWizardView(cog, G1, owner_id=USER)
            repo = SettingsRepository(db)

            await SetupWizardView.toggle_welcome(view, _EditInteraction(), None)
            assert await repo.get(G1, "WELCOME_ENABLED") == "1"

            await SetupWizardView.toggle_welcome(view, _EditInteraction(), None)
            assert await repo.get(G1, "WELCOME_ENABLED") == "0", "2回目で OFF に戻らない"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_turning_it_on_warns_about_a_missing_welcome_channel():
    """ON にしたその場で言う（後で /setup-status を見に行かせない）。"""

    async def _main():
        from cogs.setup_wizard import SetupWizard, SetupWizardView

        db = await _make_db()
        try:
            cog = SetupWizard(SimpleNamespace(db=db))
            view = SetupWizardView(cog, G1, owner_id=USER)
            interaction = _EditInteraction()
            await SetupWizardView.toggle_welcome(view, interaction, None)
            # `**新入生の案内チャンネル**: ⚠️ 未設定` の行は notice の有無に
            # 関わらず出るので、警告文そのものを見る
            assert "案内チャンネル**が未設定" in interaction.text

            # チャンネルを設定してから OFF→ON し直すと警告は出ない
            await SettingsRepository(db).set(G1, "WELCOME_CHANNEL_ID", "900")
            config.clear_guild_cache()
            await SetupWizardView.toggle_welcome(view, _EditInteraction(), None)  # OFF
            quiet = _EditInteraction()
            await SetupWizardView.toggle_welcome(view, quiet, None)  # ON
            assert "案内チャンネル**が未設定" not in quiet.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_only_the_command_runner_can_flip_the_toggle():
    """`/setup` の View は実行者本人にしか触らせない。

    トグルのテストは関数を直接呼ぶので `interaction_check` を通らない。
    「他人が押せない」ことはここで別に固定する
    （`tests/test_confirm_view.py` の「他人は実行できない」と同じ形）。
    """

    async def _main():
        from cogs.setup_wizard import SetupWizard, SetupWizardView

        db = await _make_db()
        try:
            view = SetupWizardView(SetupWizard(SimpleNamespace(db=db)), G1, owner_id=USER)
            assert await view.interaction_check(_EditInteraction(user_id=USER)) is True
            other = _EditInteraction(user_id=OTHER)
            assert await view.interaction_check(other) is False
            assert other.messages, "拒否の理由を返していない"
            assert other.edited == [], "他人の操作で画面を書き換えている"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_setup_status_checks_the_welcome_channel_only_when_enabled():
    """OFF のギルドに、使わない機能の未設定を突きつけない。"""

    async def _main():
        from cogs.help import collect_setup_status
        from config import GuildConfig

        db = await _make_db()
        try:
            off = await collect_setup_status(db, GuildConfig(guild_id=G1))
            assert not any(i.name == "新入生の案内チャンネル" for i in off)

            on = await collect_setup_status(db, GuildConfig(guild_id=G1, welcome_enabled=True))
            item = next(i for i in on if i.name == "新入生の案内チャンネル")
            assert item.done is False
            assert "届きません" in item.hint, "未設定だと何が起きるかを書いていない"

            configured = await collect_setup_status(
                db, GuildConfig(guild_id=G1, welcome_enabled=True, welcome_channel_id=900)
            )
            assert next(i for i in configured if i.name == "新入生の案内チャンネル").done is True
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_setup_embed_does_not_count_the_welcome_channel_when_disabled():
    """`/setup` と `/setup-status` が同じ設定について違うことを言わない。"""
    from cogs.setup_wizard import build_setup_embed
    from config import GuildConfig

    off = build_setup_embed(GuildConfig(guild_id=G1)).description or ""
    on = build_setup_embed(GuildConfig(guild_id=G1, welcome_enabled=True)).description or ""
    # 全10項目。OFF なら案内チャンネルを数えないので 9 件
    assert "未設定が 9 件" in off
    assert "未設定が 10 件" in on, "ON なら案内チャンネルの未設定を数えること"


def test_a_member_who_left_cannot_open_the_picker():
    """退出した人に班名の一覧を見せない。

    `register_team` 側は担保されているが、picker 側の在籍検査は
    既存4テストがすべてメンバーを渡す形なので、消しても緑になる。
    """

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            cog = _cog(db, guild=_Guild(members=[]))
            interaction = _Interaction()
            await cog.open_team_picker(interaction, G1, USER)
            assert "参加していない" in interaction.text
            assert "班1" not in interaction.text, "元部員に班名一覧が見えている"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_button_callback_uses_the_id_from_the_custom_id():
    """`callback` が `custom_id` 側の user_id を使うこと。

    `interaction.user.id` を渡す実装に変えると、他人の押下を弾く
    `interaction_check` を通り抜けた後の処理が別人で動きうる。
    `callback` を通すテストが無いと、この配線は無検査になる。
    """

    async def _main():
        db = await _make_db()
        try:
            await _add_teams(db)
            cog = _cog(db, guild=_Guild(members=[_Member()]))
            seen: list[tuple[int, int]] = []

            async def _capture(interaction, guild_id, user_id):
                seen.append((guild_id, user_id))

            cog.open_team_picker = _capture
            button = TeamPickButton(G1, USER)
            # interaction の押下者をあえて別人にする。同じにすると
            # どちらの実装でも同じ値になり、差が出ない
            interaction = _Interaction(user_id=OTHER, guild_id=G1)
            interaction.client = SimpleNamespace(get_cog=lambda name: cog)
            await button.callback(interaction)
            assert seen == [(G1, USER)], "custom_id ではなく押下者の ID を使っている"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_the_dynamic_item_is_actually_registered_on_the_client():
    """grep ではなく登録結果で確認する。

    `add_dynamic_items` の呼び出しを `if False:` にしても、ソースを
    文字列検索するだけのテストは緑のまま通る。
    """
    import discord
    from discord.ext import commands

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    before = len(bot._connection._view_store._dynamic_items)
    bot.add_dynamic_items(TeamPickButton)
    assert len(bot._connection._view_store._dynamic_items) == before + 1

    # bot.py がその登録を実際に行っていること。**コメントアウトを弾く**
    # （文字列一致だけだと `pass  # self.add_dynamic_items(...)` でも通る）
    source = (BOT_ROOT / "bot.py").read_text(encoding="utf-8")
    assert re.search(
        r"^\s*self\.add_dynamic_items\(TeamPickButton\)\s*$", source, re.MULTILINE
    ), "bot.py が DynamicItem を登録していない（コメントアウトされている可能性）"


def test_get_bool_accepts_the_forms_its_docstring_promises():
    """docstring が「大文字小文字を無視する」と書いている経路を通す。

    いま書き手は `/setup` の `"1"/"0"` だけだが、`.env` や手作業で
    `true` が入ったときに黙って OFF 扱いになると原因を追いにくい。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            for raw, expected in (
                ("1", True),
                ("true", True),
                ("TRUE", True),
                (" on ", True),
                ("yes", True),
                ("0", False),
                ("false", False),
                ("OFF", False),
                ("たぶん", False),
                ("", False),
            ):
                await repo.set(G1, "WELCOME_ENABLED", raw)
                assert await repo.get_bool(G1, "WELCOME_ENABLED") is expected, raw
        finally:
            await db.close()
            _cleanup_config()

    run(_main())
