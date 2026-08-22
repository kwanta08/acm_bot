"""utils/permissions の判定ロジックのユニットテスト。

/progress setup のデフォルト権限（Manage Server または班長以上）を担保する。
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import GuildConfig
from utils.permissions import Level, has_manage_guild_or_level

G1 = 111


def _member(
    user_id: int = 1,
    *,
    owner_id: int = 42,
    role_ids=(),
    administrator: bool = False,
    manage_guild: bool = False,
):
    return SimpleNamespace(
        id=user_id,
        guild=SimpleNamespace(owner_id=owner_id),
        roles=[SimpleNamespace(id=r) for r in role_ids],
        guild_permissions=SimpleNamespace(administrator=administrator, manage_guild=manage_guild),
    )


def test_manage_guild_passes_without_any_role_config():
    """新規サーバー（ロール未設定）でも Manage Server 保持者は通る。"""
    gconf = GuildConfig(guild_id=G1)  # 班長・幹部ロール未設定
    assert has_manage_guild_or_level(_member(manage_guild=True), gconf, Level.L2)


def test_plain_member_is_rejected():
    gconf = GuildConfig(guild_id=G1, leader_role_ids=[500])
    assert not has_manage_guild_or_level(_member(), gconf, Level.L2)


def test_leader_role_still_passes():
    """既存サーバーの班長（Manage Server なし）も従来どおり通る。"""
    gconf = GuildConfig(guild_id=G1, leader_role_ids=[500])
    assert has_manage_guild_or_level(_member(role_ids=(500,)), gconf, Level.L2)


def test_administrator_passes_via_level():
    gconf = GuildConfig(guild_id=G1)
    assert has_manage_guild_or_level(_member(administrator=True), gconf, Level.L2)


# ---------------------------------------------------------------------
# /progress setup に実際のデコレータが適用されていること
#
# has_manage_guild_or_level 単体が正しくても、コマンドが @require(L2) の
# ままなら「Manage Server は持つが班長ロールが無い」導入直後の管理者は
# 弾かれる。ヘルパとコマンドの乖離を検出する。
# ---------------------------------------------------------------------
def _discord_member(**kwargs):
    """isinstance(x, discord.Member) を満たすスタブ。

    permissions.require* の述語は discord.Member かどうかを判定するため、
    SimpleNamespace のままでは通せない（spec 付き Mock なら通る）。
    """
    import discord

    stub = _member(**kwargs)
    member = mock.MagicMock(spec=discord.Member)
    member.id = stub.id
    member.guild = stub.guild
    member.roles = stub.roles
    member.guild_permissions = stub.guild_permissions
    return member


async def _run_setup_check(member, gconf) -> bool:
    """/progress setup に付いている権限チェックを実際に実行する。"""
    from cogs.progress import Progress
    from utils import permissions

    checks = Progress.progress_setup.checks
    assert len(checks) == 1, "想定外のチェック数"

    interaction = SimpleNamespace(user=member, guild=SimpleNamespace(id=G1))
    original = permissions._guild_config_for

    async def _fake(_interaction):
        return gconf

    permissions._guild_config_for = _fake
    try:
        return await checks[0](interaction)
    finally:
        permissions._guild_config_for = original


def test_progress_setup_allows_manage_guild_without_leader_role():
    """導入直後（ロール未設定）でも Manage Server 保持者は実行できる。"""
    gconf = GuildConfig(guild_id=G1)
    member = _discord_member(manage_guild=True)
    assert asyncio.run(_run_setup_check(member, gconf))


def test_progress_setup_allows_leader_role():
    """班長ロールを設定済みのサーバーは従来どおり班長が実行できる。"""
    gconf = GuildConfig(guild_id=G1, leader_role_ids=[500])
    member = _discord_member(role_ids=(500,))
    assert asyncio.run(_run_setup_check(member, gconf))


def test_progress_setup_rejects_plain_member():
    from utils.permissions import PermissionDenied

    gconf = GuildConfig(guild_id=G1, leader_role_ids=[500])
    with pytest.raises(PermissionDenied):
        asyncio.run(_run_setup_check(_discord_member(), gconf))


# ---------------------------------------------------------------------
# 権限エラーの文面
#
# 「この操作には L2 以上の権限が必要です」だけでは、L2 が何を指すのか、
# 自分のサーバーで誰が L2 なのか、どうすれば実行してもらえるのかが
# 一切分からない。gconf にはロール ID が揃っているので、
# ラベルと依頼先まで書けるはずだった。
# ---------------------------------------------------------------------
def test_level_label_is_japanese():
    from utils.permissions import level_label

    assert level_label(Level.L1) == "一般メンバー"
    assert level_label(Level.L2) == "班長"
    assert level_label(Level.L3) == "幹部"
    assert level_label(Level.L4) == "Bot管理者"
    assert level_label(None) == "全員"


def test_roles_for_level_includes_higher_roles():
    """上位ロールも依頼先として出す（班長が不在でも幹部に頼める）。"""
    from utils.permissions import roles_for_level

    gconf = GuildConfig(
        guild_id=G1, leader_role_ids=[501, 502], exec_role_id=600, admin_role_id=700
    )
    assert roles_for_level(gconf, Level.L2) == [501, 502, 600, 700]
    assert roles_for_level(gconf, Level.L3) == [600, 700]
    assert roles_for_level(gconf, Level.L4) == [700]


def test_denial_message_names_the_level_and_contacts():
    from utils.permissions import denial_message

    gconf = GuildConfig(guild_id=G1, leader_role_ids=[501], admin_role_id=700)
    msg = denial_message(Level.L2, current=Level.L1, gconf=gconf)

    assert "班長" in msg
    assert "一般メンバー" in msg
    assert "<@&501>" in msg
    assert "<@&700>" in msg
    assert "L2" not in msg  # 内部表記を利用者に見せない


def test_denial_message_when_no_role_is_configured():
    """ロール未設定なら「実行できる人がいない」ことと直し方を書く。"""
    from utils.permissions import denial_message

    msg = denial_message(Level.L2, current=Level.L1, gconf=GuildConfig(guild_id=G1))

    assert "設定されていない" in msg
    assert "/setup" in msg


def test_denial_message_mentions_manage_guild_when_allowed():
    from utils.permissions import denial_message

    msg = denial_message(
        Level.L2, current=Level.L1, gconf=GuildConfig(guild_id=G1), manage_guild_ok=True
    )
    assert "サーバー管理" in msg


def test_permission_denied_uses_denial_message():
    from utils.permissions import PermissionDenied

    gconf = GuildConfig(guild_id=G1, leader_role_ids=[501])
    err = PermissionDenied(Level.L2, current=Level.L1, gconf=gconf)
    assert "班長" in str(err)
    assert "<@&501>" in str(err)
    assert err.required is Level.L2


def test_progress_setup_denial_tells_who_to_ask():
    """実際のコマンドの拒否メッセージにも依頼先が出る。"""
    from utils.permissions import PermissionDenied

    gconf = GuildConfig(guild_id=G1, leader_role_ids=[500], exec_role_id=600)
    with pytest.raises(PermissionDenied) as excinfo:
        asyncio.run(_run_setup_check(_discord_member(), gconf))
    message = str(excinfo.value)
    assert "班長" in message
    assert "<@&500>" in message
    assert "サーバー管理" in message
