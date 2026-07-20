"""
<<<<<<< HEAD
権限モジュール（改訂版）

L1 一般メンバー / L2 班長 / L3 幹部 / L4 Bot管理者。
ロール ID は config から取得する。L4 は L3,L2,L1 を内包する階層判定。
（改訂版: is_admin チェックを追加）
=======
権限モジュール（マルチテナント版）

L1 一般メンバー / L2 班長 / L3 幹部 / L4 Bot管理者。
ロール ID はギルド別設定（config.for_guild）から取得する。
L4 は L3,L2,L1 を内包する階層判定。

また、コマンドがサーバー内で実行されたことを確認する ensure_guild() を提供する
（DM 実行時はギルド ID を解決できないため拒否する）。
>>>>>>> 803617a (v4.0)
"""
from __future__ import annotations

from enum import IntEnum

import discord
from discord import app_commands

from config import GuildConfig, config
from utils.embeds import error_embed


class Level(IntEnum):
    L1 = 1  # 一般メンバー
    L2 = 2  # 班長
    L3 = 3  # 幹部
    L4 = 4  # Bot 管理者


<<<<<<< HEAD
def get_level(member: discord.Member) -> Level:
    """
メンバーの権限レベルを判定する。最も高いものを返す。"""
=======
def get_level(member: discord.Member, gconf: GuildConfig) -> Level:
    """
    メンバーの権限レベルを判定する。最も高いものを返す。
    ロール ID はギルド別設定 gconf を参照する。"""
>>>>>>> 803617a (v4.0)
    if member.guild and member.id == member.guild.owner_id:
        return Level.L4

    role_ids = {r.id for r in member.roles}

    if gconf.admin_role_id and gconf.admin_role_id in role_ids:
        return Level.L4
    # サーバー管理者権限を持つ場合も L4 とみなす
    if member.guild_permissions.administrator:
        return Level.L4
    if gconf.exec_role_id and gconf.exec_role_id in role_ids:
        return Level.L3
    if gconf.leader_role_ids and role_ids.intersection(gconf.leader_role_ids):
        return Level.L2
    return Level.L1


def has_level(member: discord.Member, gconf: GuildConfig, required: Level) -> bool:
    return get_level(member, gconf) >= required


class PermissionDenied(app_commands.CheckFailure):
    """
<<<<<<< HEAD
権限不足を表す例外（PERMISSION_DENIED）
"""
=======
    権限不足を表す例外（PERMISSION_DENIED）
    """
>>>>>>> 803617a (v4.0)

    def __init__(self, required: Level):
        self.required = required
        super().__init__(f"この操作には L{int(required)} 以上の権限が必要です。")


async def _guild_config_for(interaction: discord.Interaction) -> GuildConfig:
    """interaction が属するギルドの解決済み設定を返す。"""
    return await config.for_guild(interaction.guild.id)


def require(level: Level):
    """
<<<<<<< HEAD
スラッシュコマンド用の権限チェックデコレータ。
"""
=======
    スラッシュコマンド用の権限チェックデコレータ。
    ギルド別設定のロール ID で判定する（DM からの実行は拒否）。
    """
>>>>>>> 803617a (v4.0)

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            raise PermissionDenied(level)
        gconf = await _guild_config_for(interaction)
        if not has_level(member, gconf, level):
            raise PermissionDenied(level)
        return True

    return app_commands.check(predicate)


async def is_admin(interaction: discord.Interaction) -> bool:
    """
    管理者権限チェック（L4 以上）
    """
    member = interaction.user
<<<<<<< HEAD
    if not isinstance(member, discord.Member):
        raise PermissionDenied(Level.L4)
    if not has_level(member, Level.L4):
        raise PermissionDenied(Level.L4)
    return True
=======
    if not isinstance(member, discord.Member) or interaction.guild is None:
        raise PermissionDenied(Level.L4)
    gconf = await _guild_config_for(interaction)
    if not has_level(member, gconf, Level.L4):
        raise PermissionDenied(Level.L4)
    return True


async def ensure_guild(interaction: discord.Interaction) -> int | None:
    """
    コマンドがサーバー内で実行されたことを確認し、guild_id を返す。

    DM 等で guild_id を解決できない場合は拒否メッセージを送って None を返す。
    各コマンドハンドラは次の規約で使用する:

        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
    """
    if interaction.guild is not None:
        return interaction.guild.id
    embed = error_embed(
        "このコマンドはサーバー内でのみ使用できます（DM ではギルドを特定できません）。")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:  # noqa: BLE001
        pass
    return None
>>>>>>> 803617a (v4.0)
