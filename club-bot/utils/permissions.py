"""
権限モジュール（改訂版）

L1 一般メンバー / L2 班長 / L3 幹部 / L4 Bot管理者。
ロール ID は config から取得する。L4 は L3,L2,L1 を内包する階層判定。
（改訂版: is_admin チェックを追加）
"""
from __future__ import annotations

from enum import IntEnum

import discord
from discord import app_commands

from config import config


class Level(IntEnum):
    L1 = 1  # 一般メンバー
    L2 = 2  # 班長
    L3 = 3  # 幹部
    L4 = 4  # Bot 管理者


def get_level(member: discord.Member) -> Level:
    """
メンバーの権限レベルを判定する。最も高いものを返す。"""
    if member.guild and member.id == member.guild.owner_id:
        return Level.L4

    role_ids = {r.id for r in member.roles}

    if config.admin_role_id and config.admin_role_id in role_ids:
        return Level.L4
    # サーバー管理者権限を持つ場合も L4 とみなす
    if member.guild_permissions.administrator:
        return Level.L4
    if config.exec_role_id and config.exec_role_id in role_ids:
        return Level.L3
    if config.leader_role_ids and role_ids.intersection(config.leader_role_ids):
        return Level.L2
    return Level.L1


def has_level(member: discord.Member, required: Level) -> bool:
    return get_level(member) >= required


class PermissionDenied(app_commands.CheckFailure):
    """
権限不足を表す例外（PERMISSION_DENIED）
"""

    def __init__(self, required: Level):
        self.required = required
        super().__init__(f"この操作には L{int(required)} 以上の権限が必要です。")


def require(level: Level):
    """
スラッシュコマンド用の権限チェックデコレータ。
"""

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise PermissionDenied(level)
        if not has_level(member, level):
            raise PermissionDenied(level)
        return True

    return app_commands.check(predicate)


async def is_admin(interaction: discord.Interaction) -> bool:
    """
    管理者権限チェック（L4 以上）
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        raise PermissionDenied(Level.L4)
    if not has_level(member, Level.L4):
        raise PermissionDenied(Level.L4)
    return True
