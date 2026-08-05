"""
Setup コグ（/setup 設定ウィザード）

導入直後のサーバー向けに、ギルド別設定の状況を Embed で一覧し、
Select / ChannelSelect / RoleSelect で対話的に設定・保存する。

- 実行はギルド管理者（L4）のみ。DM からの実行は拒否する
- 設定はギルド別 settings テーブルに (guild_id, setting_key) で保存され、
  config.for_guild(guild_id) 経由で解決される（他ギルドへは影響しない）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import GuildConfig, config
from repositories.settings_repository import SettingsRepository
from utils.embeds import error_embed, info_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("setup_wizard")

# /setup で設定できる項目（キー, 表示名, 種別）
#   種別は "channel" / "role"。キー名の小文字化が GuildConfig の属性名と一致する
CHANNEL_SETTINGS: list[tuple[str, str]] = [
    ("DEFAULT_ANNOUNCE_CHANNEL_ID", "お知らせチャンネル"),
    ("DEFAULT_SCHEDULE_CHANNEL_ID", "日程調整チャンネル"),
    ("DEFAULT_PROGRESS_CHANNEL_ID", "進捗チャンネル"),
    ("DEFAULT_TASK_CHANNEL_ID", "タスク通知チャンネル"),
    ("TODAY_LABEL_CHANNEL_ID", "今日やること通知チャンネル"),
    ("BOT_LOG_CHANNEL_ID", "Botログチャンネル"),
]
ROLE_SETTINGS: list[tuple[str, str]] = [
    ("ADMIN_ROLE_ID", "Bot管理者ロール"),
    ("EXEC_ROLE_ID", "実行役ロール"),
]
ALL_SETUP_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS + ROLE_SETTINGS}
_CHANNEL_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS}
_ROLE_KEYS: set[str] = {k for k, _ in ROLE_SETTINGS}


def build_setup_embed(gconf: GuildConfig) -> discord.Embed:
    """
    ギルド別設定の一覧 Embed を生成する。
    未設定項目には「未設定」を明示する。
    """
    lines: list[str] = []
    missing = 0
    for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS:
        value = getattr(gconf, key.lower())
        if value is None:
            lines.append(f"**{label}**: ⚠️ 未設定")
            missing += 1
        elif key in _CHANNEL_KEYS:
            lines.append(f"**{label}**: <#{value}>")
        else:
            lines.append(f"**{label}**: <@&{value}>")

    if missing:
        summary = f"未設定が {missing} 件あります。下のセレクトで項目を選んで設定してください。"
    else:
        summary = "すべての項目が設定済みです。"
    embed = info_embed("セットアップ状況", "\n".join(lines) + "\n\n" + summary)
    return embed


class SetupWizardView(discord.ui.View):
    """/setup の ephemeral メッセージに付ける対話 View（5分で無効化）。"""

    def __init__(self, cog: "SetupWizard", guild_id: int, owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.selected_key: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # コマンド実行者以外は操作不可
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"),
                ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """保存後に元メッセージの Embed を最新状態へ更新する。"""
        gconf = await config.for_guild(self.guild_id, db=self.cog.db, force_reload=True)
        await interaction.response.edit_message(
            embed=build_setup_embed(gconf), view=self)

    @discord.ui.select(
        placeholder="設定したい項目を選択…",
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS
        ],
    )
    async def select_item(self, interaction: discord.Interaction,
                          select: discord.ui.Select):
        self.selected_key = select.values[0]
        label = dict(CHANNEL_SETTINGS + ROLE_SETTINGS)[self.selected_key]
        kind = "チャンネル" if self.selected_key in _CHANNEL_KEYS else "ロール"
        await interaction.response.send_message(
            embed=info_embed(
                "項目を選択しました",
                f"**{label}** を設定します。\n下の{kind}セレクトで対象を選んでください。"),
            ephemeral=True)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="チャンネルを選択（先に項目を選択）",
        channel_types=[discord.ChannelType.text],
    )
    async def select_channel(self, interaction: discord.Interaction,
                             select: discord.ui.ChannelSelect):
        if self.selected_key not in _CHANNEL_KEYS:
            await interaction.response.send_message(
                embed=error_embed(
                    "先に「設定したい項目」でチャンネル系の項目を選択してください。"),
                ephemeral=True)
            return
        channel = select.values[0]
        await self.cog.save_setting(self.guild_id, self.selected_key, str(channel.id))
        log.info("/setup で保存 (guild=%s): %s=%s",
                 self.guild_id, self.selected_key, channel.id)
        await self._refresh(interaction)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="ロールを選択（先に項目を選択）",
    )
    async def select_role(self, interaction: discord.Interaction,
                          select: discord.ui.RoleSelect):
        if self.selected_key not in _ROLE_KEYS:
            await interaction.response.send_message(
                embed=error_embed(
                    "先に「設定したい項目」でロール系の項目を選択してください。"),
                ephemeral=True)
            return
        role = select.values[0]
        await self.cog.save_setting(self.guild_id, self.selected_key, str(role.id))
        log.info("/setup で保存 (guild=%s): %s=%s",
                 self.guild_id, self.selected_key, role.id)
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class SetupWizard(commands.Cog):
    """初期設定ウィザード コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.settings_repo = SettingsRepository(self.db)

    async def save_setting(self, guild_id: int, key: str, value: str) -> None:
        """
        ギルド別 settings に値を保存し、解決キャッシュを更新する。
        /setup で扱わないキーは拒否する。
        """
        if key not in ALL_SETUP_KEYS:
            raise ValueError(f"/setup では設定できないキーです: {key}")
        await self.settings_repo.set(guild_id, key, value)
        config.invalidate_guild(guild_id)
        # レガシーギルドのグローバル設定を再読込
        await config.load_from_db(self.db)

    @app_commands.command(
        name="setup",
        description="このサーバーの初期設定を対話的に行います（管理者）。")
    @app_commands.check(is_admin)
    async def setup(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            gconf = await config.for_guild(guild_id, db=self.db, force_reload=True)
            embed = build_setup_embed(gconf)
            view = SetupWizardView(self, guild_id, interaction.user.id)
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True)
        except Exception as e:
            log.exception("/setup 表示エラー: %s", e)
            embed = error_embed("セットアップ画面の表示に失敗しました")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupWizard(bot))
