"""/help（コマンドカタログ）のテスト。

Discord へは接続せず、実際に全 Cog を読み込んだコマンドツリーを検査する。

- 登録済みの全コマンドがいずれかのカテゴリに現れること。
  新しい Cog を足してカテゴリ登録を忘れると「その他」に落ち、ここで落ちる
- 権限バッジが utils.permissions の宣言と一致すること
- Embed が Discord の 6000 文字 / 25 field 制限に収まること
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord import app_commands
from discord.ext import commands

from bot import COGS
from cogs.help import (
    CATEGORY_BY_COG,
    UNCATEGORIZED,
    build_catalog,
    category_embed,
    collect_setup_status,
    command_embed,
    level_badge,
    overview_embed,
    setup_status_embed,
)
from config import GuildConfig
from repositories.layer_keta_repository import LayerKetaRepository
from repositories.member_repository import MemberRepository
from utils.db import Database
from utils.permissions import Level, command_required_level

G1 = 111
G2 = 222
NOW = "2026-08-12 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _FakeBot(commands.Bot):
    """Discord へ接続しない、コマンドツリー検査用の Bot。"""

    def __init__(self):
        super().__init__(command_prefix="!club ", intents=discord.Intents.none())
        self.db = None
        self.todoist_manager = None


async def _load_all() -> _FakeBot:
    bot = _FakeBot()
    for cog in COGS:
        await bot.load_extension(cog)
    return bot


async def _unload_all(bot: _FakeBot) -> None:
    # 定期ループ（discord.ext.tasks）を止めてから閉じる
    for name in list(bot.extensions):
        await bot.unload_extension(name)


def _walk(bot: _FakeBot) -> list[app_commands.Command]:
    return [c for c in bot.tree.walk_commands() if isinstance(c, app_commands.Command)]


# ---------------------------------------------------------------------
# (1) 全コマンドがどれかのカテゴリに現れる（新コマンド追加で落ちる回帰テスト）
# ---------------------------------------------------------------------
def test_all_commands_appear_in_some_category():
    async def _main():
        bot = await _load_all()
        try:
            catalog = build_catalog(bot.tree)
            listed = {c.qualified_name for cmds in catalog.values() for c in cmds}
            registered = {c.qualified_name for c in _walk(bot)}
            assert registered, "コマンドが1件も収集できていない（テストが空振りしている）"
            assert registered == listed
        finally:
            await _unload_all(bot)

    run(_main())


def test_no_uncategorized_command_remains():
    """カテゴリ未登録の Cog を足すとここで落ちる。"""

    async def _main():
        bot = await _load_all()
        try:
            catalog = build_catalog(bot.tree)
            orphans = [c.qualified_name for c in catalog.get(UNCATEGORIZED, [])]
            assert not orphans, (
                "カテゴリ未登録のコマンドがあります。cogs/help.py の "
                f"CATEGORY_BY_COG に Cog を追加してください: {orphans}"
            )
        finally:
            await _unload_all(bot)

    run(_main())


def test_every_loaded_cog_with_commands_is_mapped():
    async def _main():
        bot = await _load_all()
        try:
            cogs_with_commands = set()
            for cmd in _walk(bot):
                binding = getattr(cmd, "binding", None)
                if binding is not None:
                    cogs_with_commands.add(type(binding).__name__)
            unmapped = cogs_with_commands - set(CATEGORY_BY_COG)
            assert not unmapped, f"CATEGORY_BY_COG に未登録の Cog: {sorted(unmapped)}"
        finally:
            await _unload_all(bot)

    run(_main())


# ---------------------------------------------------------------------
# (2) 権限バッジが宣言と一致する
# ---------------------------------------------------------------------
def test_declared_levels_are_readable():
    """require() / is_admin の必要レベルが外から読めること。

    クロージャに閉じたままだと /help がバッジを出せないため、
    代表的なコマンドで実際の宣言と一致することを確かめる。
    """
    expected = {
        "task list": Level.L1,  # @require(Level.L1)
        "layer keta-add": Level.L2,  # @require(Level.L2)
        "schedule delete": Level.L3,  # @require(Level.L3)
        "task sync": Level.L4,  # @require(Level.L4)
        "progress sync": Level.L4,  # @app_commands.check(is_admin)
        "team-add": Level.L4,  # @app_commands.check(is_admin)
    }

    async def _main():
        bot = await _load_all()
        try:
            by_name = {c.qualified_name: c for c in _walk(bot)}
            for name, level in expected.items():
                assert name in by_name, f"コマンドが見つからない: /{name}"
                assert command_required_level(by_name[name]) == level, (
                    f"/{name} の必要レベルが宣言と一致しない"
                )
            # 権限チェックを持たないコマンドは None
            assert command_required_level(by_name["ping"]) is None
        finally:
            await _unload_all(bot)

    run(_main())


def test_badge_shown_only_above_viewer_level():
    """実行者より上のレベルにだけバッジを付ける（非表示にはしない）。"""

    async def _main():
        bot = await _load_all()
        try:
            by_name = {c.qualified_name: c for c in _walk(bot)}
            l3_cmd = by_name["schedule delete"]  # L3
            l1_cmd = by_name["task list"]  # L1
            no_check = by_name["ping"]  # 制限なし

            assert level_badge(l3_cmd, Level.L1) == "幹部以上"
            assert level_badge(l3_cmd, Level.L2) == "幹部以上"
            assert level_badge(l3_cmd, Level.L3) == ""
            assert level_badge(l3_cmd, Level.L4) == ""
            assert level_badge(l1_cmd, Level.L1) == ""
            assert level_badge(no_check, Level.L1) == ""
            # 実行者レベルが不明なときは L1 とみなして案内する
            assert level_badge(l3_cmd, None) == "幹部以上"
        finally:
            await _unload_all(bot)

    run(_main())


def test_commands_are_never_hidden_from_lower_levels():
    """権限が足りなくても一覧からは消さない（何ができる bot かは全員に見せる）。"""

    async def _main():
        bot = await _load_all()
        try:
            catalog = build_catalog(bot.tree)
            listed = {c.qualified_name for cmds in catalog.values() for c in cmds}
            assert "progress sync" in listed  # L4 のコマンドも一覧に出る
            assert "team-add" in listed
        finally:
            await _unload_all(bot)

    run(_main())


# ---------------------------------------------------------------------
# (3) Embed の Discord 制限
# ---------------------------------------------------------------------
def test_category_embeds_fit_discord_limits():
    async def _main():
        bot = await _load_all()
        try:
            catalog = build_catalog(bot.tree)
            assert catalog, "カタログが空"
            for category, cmds in catalog.items():
                for viewer in (Level.L1, Level.L4):
                    embed = category_embed(category, cmds, viewer)
                    assert len(embed) <= 6000, f"{category} の Embed が 6000 文字超過"
                    assert len(embed.fields) <= 25, f"{category} の field が 25 超過"
                    assert len(embed.description or "") <= 4096
        finally:
            await _unload_all(bot)

    run(_main())


def test_command_embeds_fit_discord_limits():
    async def _main():
        bot = await _load_all()
        try:
            for cmd in _walk(bot):
                embed = command_embed(cmd, Level.L1)
                assert len(embed) <= 6000, f"/{cmd.qualified_name} の Embed が超過"
                assert len(embed.fields) <= 25
        finally:
            await _unload_all(bot)

    run(_main())


def test_embeds_keep_update_timestamp_footer():
    """utils.embeds が付ける更新時刻フッターを上書きしないこと（仕様 13.1）。"""

    async def _main():
        bot = await _load_all()
        try:
            catalog = build_catalog(bot.tree)
            category, cmds = next(iter(catalog.items()))
            embeds = [
                overview_embed(catalog, Level.L1),
                category_embed(category, cmds, Level.L1),
                command_embed(cmds[0], Level.L1),
            ]
            for embed in embeds:
                assert embed.footer.text, "フッターが消えている"
                assert "更新:" in embed.footer.text
        finally:
            await _unload_all(bot)

    run(_main())


def test_setup_status_embed_keeps_footer():
    async def _main():
        db = await _fresh_db()
        try:
            embed = setup_status_embed(await collect_setup_status(db, GuildConfig(guild_id=G1)))
            assert embed.footer.text and "更新:" in embed.footer.text
        finally:
            await db.close()

    run(_main())


def test_category_count_fits_select_menu_limit():
    """カテゴリ選択メニューは Discord の 25 option 制限に収まること。"""
    assert len(set(CATEGORY_BY_COG.values())) <= 25


def test_categories_contain_no_club_specific_terms():
    """カテゴリ名にサークル固有語（班名・サークル名・機体名）を含めない。

    鳥人間ドメイン語（桁巻き・機体）は AGENTS.md により許容される。
    """
    forbidden = ["班名", "電装", "構造班", "ACM", "鳥人間コンテスト"]
    for name in set(CATEGORY_BY_COG.values()):
        for word in forbidden:
            assert word not in name, f"カテゴリ名にサークル固有語: {name}"


# ---------------------------------------------------------------------
# F1-2: 初期設定の未完了チェック
# ---------------------------------------------------------------------
async def _fresh_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def test_setup_status_reports_all_unset_on_empty_guild():
    """空のサーバーでは全項目が未設定で、対応するコマンドが案内される。"""

    async def _main():
        db = await _fresh_db()
        try:
            items = await collect_setup_status(db, GuildConfig(guild_id=G1))
            assert items, "判定項目が空"
            assert all(not i.done for i in items), [i.name for i in items if i.done]
            hints = " ".join(i.hint for i in items)
            assert "/setup" in hints
            assert "/team-add" in hints
            assert "/layer keta-add" in hints
        finally:
            await db.close()

    run(_main())


def test_setup_status_all_done_after_configuration():
    async def _main():
        db = await _fresh_db()
        try:
            await MemberRepository(db).upsert_team(G1, "struct", "構造")
            await LayerKetaRepository(db).add(G1, "主桁", "tester", NOW)
            gconf = GuildConfig(
                guild_id=G1,
                default_task_channel_id=1001,
                bot_log_channel_id=1002,
                admin_role_id=2001,
                exec_role_id=2002,
                leader_role_ids=[3001],
                competition_date="2026-07-25",
            )
            items = await collect_setup_status(db, gconf)
            assert all(i.done for i in items), [i.name for i in items if not i.done]
        finally:
            await db.close()

    run(_main())


def test_setup_status_is_guild_scoped():
    """他サーバーの班・桁を自サーバーの完了扱いにしない。"""

    async def _main():
        db = await _fresh_db()
        try:
            await MemberRepository(db).upsert_team(G2, "struct", "構造")
            await LayerKetaRepository(db).add(G2, "主桁", "tester", NOW)
            gconf = GuildConfig(
                guild_id=G1,
                default_task_channel_id=1001,
                bot_log_channel_id=1002,
                admin_role_id=2001,
            )
            done = {i.name: i.done for i in await collect_setup_status(db, gconf)}
            assert done["班"] is False
            assert done["桁"] is False
            # チャンネル・ロールはギルド別設定なので完了のまま
            assert done["タスク通知チャンネル"] is True
        finally:
            await db.close()

    run(_main())


def test_setup_status_partial_configuration():
    """一部だけ設定済みのとき、未設定のものだけが未完了になる。"""

    async def _main():
        db = await _fresh_db()
        try:
            await MemberRepository(db).upsert_team(G1, "struct", "構造")
            gconf = GuildConfig(guild_id=G1, bot_log_channel_id=1002)
            done = {i.name: i.done for i in await collect_setup_status(db, gconf)}
            assert done["班"] is True
            assert done["ログチャンネル"] is True
            assert done["タスク通知チャンネル"] is False
            assert done["管理者ロール"] is False
            # 幹部ロール（L3 判定の実体）も未設定として拾う
            assert done["幹部ロール"] is False
            assert done["班長ロール"] is False
            assert done["桁"] is False
        finally:
            await db.close()

    run(_main())


def test_setup_status_embed_fits_limits():
    async def _main():
        db = await _fresh_db()
        try:
            for gconf in (
                GuildConfig(guild_id=G1),
                GuildConfig(
                    guild_id=G1,
                    default_announce_channel_id=1,
                    bot_log_channel_id=2,
                    admin_role_id=3,
                ),
            ):
                embed = setup_status_embed(await collect_setup_status(db, gconf))
                assert len(embed) <= 6000
                assert len(embed.fields) <= 25
        finally:
            await db.close()

    run(_main())


def test_setup_status_command_is_registered():
    """/setup-status がコマンドツリーに登録され、カタログにも現れること。"""

    async def _main():
        bot = await _load_all()
        try:
            names = {c.qualified_name for c in _walk(bot)}
            assert "setup-status" in names
            listed = {c.qualified_name for cmds in build_catalog(bot.tree).values() for c in cmds}
            assert "setup-status" in listed
        finally:
            await _unload_all(bot)

    run(_main())


# ---------------------------------------------------------------------
# /setup-status が見るのは「実際に挙動を左右する設定」であること
#
# 以前は DEFAULT_ANNOUNCE_CHANNEL_ID（どこからも送信に使われない）を見て
# 「すべて設定済み」と表示する一方、通知の落とし先である
# DEFAULT_TASK_CHANNEL_ID と、班長判定の唯一の根拠である LEADER_ROLE_IDS を
# 見ていなかった。設定したのに通知が来ない状態を検知できるようにする。
# ---------------------------------------------------------------------
def test_setup_status_checks_task_channel_not_announce():
    async def _main():
        db = await _fresh_db()
        try:
            # お知らせチャンネルだけ設定した状態は「未完了」でなければならない
            gconf = GuildConfig(guild_id=G1, default_announce_channel_id=1001)
            done = {i.name: i.done for i in await collect_setup_status(db, gconf)}
            assert "通知チャンネル" not in done
            assert done["タスク通知チャンネル"] is False

            gconf = GuildConfig(guild_id=G1, default_task_channel_id=1001)
            done = {i.name: i.done for i in await collect_setup_status(db, gconf)}
            assert done["タスク通知チャンネル"] is True
        finally:
            await db.close()

    run(_main())


def test_setup_status_checks_leader_role():
    """班長ロールが空なら未完了。members.is_leader では代替できない。"""

    async def _main():
        db = await _fresh_db()
        try:
            done = {
                i.name: i.done for i in await collect_setup_status(db, GuildConfig(guild_id=G1))
            }
            assert done["班長ロール"] is False

            gconf = GuildConfig(guild_id=G1, leader_role_ids=[3001])
            done = {i.name: i.done for i in await collect_setup_status(db, gconf)}
            assert done["班長ロール"] is True
        finally:
            await db.close()

    run(_main())


def test_setup_status_checks_competition_date():
    async def _main():
        db = await _fresh_db()
        try:
            items = await collect_setup_status(db, GuildConfig(guild_id=G1))
            item = next(i for i in items if i.name.startswith("大会日"))
            assert item.done is False
            assert "COMPETITION_DATE" in item.hint

            gconf = GuildConfig(guild_id=G1, competition_date="2026-07-25")
            items = await collect_setup_status(db, gconf)
            assert next(i for i in items if i.name.startswith("大会日")).done is True
        finally:
            await db.close()

    run(_main())
