"""
権限モジュール（マルチテナント版）

L1 一般メンバー / L2 班長 / L3 幹部 / L4 Bot管理者。
ロール ID はギルド別設定（config.for_guild）から取得する。
L4 は L3,L2,L1 を内包する階層判定。

また、コマンドがサーバー内で実行されたことを確認する ensure_guild() を提供する
（DM 実行時はギルド ID を解決できないため拒否する）。
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


# レベルの人間向けラベル。
#
# 「L2 以上の権限が必要です」とだけ返していたが、L1〜L4 が何を指すかは
# ソースのコメントにしか書かれておらず、Discord のどの出力にも凡例が無い。
# 新入生には「Bot が壊れている」としか見えないため、必ずこの語で出す。
LEVEL_LABELS: dict[Level, str] = {
    Level.L1: "一般メンバー",
    Level.L2: "班長",
    Level.L3: "幹部",
    Level.L4: "Bot管理者",
}


def level_label(level: Level | None) -> str:
    """レベルの日本語ラベル。宣言が無い（誰でも実行できる）場合は「全員」。"""
    if level is None:
        return "全員"
    return LEVEL_LABELS.get(level, f"L{int(level)}")


def roles_for_level(gconf: GuildConfig, required: Level) -> list[int]:
    """required を満たすロール ID を、低い階層から順に返す。

    上位ロールも「頼める相手」なので含める（班長が居ない/不在でも
    幹部・Bot管理者に頼めることが分かるようにする）。
    """
    out: list[int] = []
    if required <= Level.L2:
        out.extend(gconf.leader_role_ids)
    if required <= Level.L3 and gconf.exec_role_id:
        out.append(gconf.exec_role_id)
    if required <= Level.L4 and gconf.admin_role_id:
        out.append(gconf.admin_role_id)
    # 重複を除きつつ順序は保つ
    seen: set[int] = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def denial_message(
    required: Level,
    *,
    current: Level | None = None,
    gconf: GuildConfig | None = None,
    manage_guild_ok: bool = False,
) -> str:
    """権限不足の案内文。「誰に頼めばいいか」まで書く。"""
    head = f"この操作は**{level_label(required)}以上**が実行できます"
    if manage_guild_ok:
        head += "（または「サーバー管理」権限を持つ人）"
    if current is not None:
        head += f"。あなたは{level_label(current)}です。"
    else:
        head += "。"

    if gconf is None:
        return head
    role_ids = roles_for_level(gconf, required)
    if role_ids:
        mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
        return f"{head}\n依頼先: {mentions}"
    return (
        f"{head}\n"
        f"このサーバーには{level_label(required)}ロールが設定されていないため、"
        "実行できる人がいません。サーバー管理者に `/setup` での設定を依頼してください。"
    )


# 権限チェック関数に「必要レベル」を刻む属性名。
# require() が返す check はクロージャなので、外からは必要レベルを読めない。
# /help がコマンド一覧に権限バッジを出せるよう、predicate 自身に持たせる。
REQUIRED_LEVEL_ATTR = "__club_required_level__"


def _mark_required_level(predicate, level: Level):
    """権限チェック関数に必要レベルを刻んで返す。"""
    setattr(predicate, REQUIRED_LEVEL_ATTR, level)
    return predicate


def command_required_level(command) -> Level | None:
    """app_commands のコマンドに宣言された必要レベルを返す。

    宣言が無い（誰でも実行できる）場合は None。
    親グループに付いた check も辿るため、サブコマンド単体で判定できる。
    """
    seen = command
    while seen is not None:
        for check in getattr(seen, "checks", []) or []:
            level = getattr(check, REQUIRED_LEVEL_ATTR, None)
            if level is not None:
                return level
        seen = getattr(seen, "parent", None)
    return None


def get_level(member: discord.Member, gconf: GuildConfig) -> Level:
    """
    メンバーの権限レベルを判定する。最も高いものを返す。
    ロール ID はギルド別設定 gconf を参照する。"""
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


def has_manage_guild_or_level(member: discord.Member, gconf: GuildConfig, required: Level) -> bool:
    """Discord 標準の「サーバー管理（Manage Server）」権限、
    またはロール階層で required 以上なら True。

    ロール未設定の新規サーバーでも Manage Server 保持者は
    追加設定なしでコマンドを実行できる（マルチサーバー対応のデフォルト権限）。
    """
    if member.guild_permissions.manage_guild:
        return True
    return has_level(member, gconf, required)


class PermissionDenied(app_commands.CheckFailure):
    """
    権限不足を表す例外（PERMISSION_DENIED）
    """

    def __init__(
        self,
        required: Level,
        message: str | None = None,
        *,
        current: Level | None = None,
        gconf: GuildConfig | None = None,
        manage_guild_ok: bool = False,
    ):
        self.required = required
        self.current = current
        super().__init__(
            message
            or denial_message(
                required, current=current, gconf=gconf, manage_guild_ok=manage_guild_ok
            )
        )


async def _guild_config_for(interaction: discord.Interaction) -> GuildConfig:
    """interaction が属するギルドの解決済み設定を返す。"""
    return await config.for_guild(interaction.guild.id)


def require(level: Level):
    """
    スラッシュコマンド用の権限チェックデコレータ。
    ギルド別設定のロール ID で判定する（DM からの実行は拒否）。
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            raise PermissionDenied(level)
        gconf = await _guild_config_for(interaction)
        if not has_level(member, gconf, level):
            raise PermissionDenied(level, current=get_level(member, gconf), gconf=gconf)
        return True

    return app_commands.check(_mark_required_level(predicate, level))


def require_manage_guild_or(level: Level):
    """「サーバー管理」権限またはロール階層 level 以上で通す権限チェック。

    /progress setup 等、サーバーごとのセルフサービス設定コマンド用。
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            raise PermissionDenied(level)
        gconf = await _guild_config_for(interaction)
        if not has_manage_guild_or_level(member, gconf, level):
            raise PermissionDenied(
                level,
                current=get_level(member, gconf),
                gconf=gconf,
                manage_guild_ok=True,
            )
        return True

    return app_commands.check(_mark_required_level(predicate, level))


async def is_admin(interaction: discord.Interaction) -> bool:
    """
    管理者権限チェック（L4 以上）
    """
    member = interaction.user
    if not isinstance(member, discord.Member) or interaction.guild is None:
        raise PermissionDenied(Level.L4)
    gconf = await _guild_config_for(interaction)
    if not has_level(member, gconf, Level.L4):
        raise PermissionDenied(Level.L4, current=get_level(member, gconf), gconf=gconf)
    return True


# @app_commands.check(is_admin) で直接使われるため、関数自体に必要レベルを刻む
_mark_required_level(is_admin, Level.L4)


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
        "このコマンドはサーバー内でのみ使用できます（DM ではギルドを特定できません）。"
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    # エラー通知の送信失敗は握りつぶす（DM 拒否の通知が送れなくても処理は継続）
    except Exception:  # noqa: BLE001, S110
        pass
    return None
