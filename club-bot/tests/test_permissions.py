"""utils/permissions の判定ロジックのユニットテスト。

/progress setup のデフォルト権限（Manage Server または班長以上）を担保する。
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

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
