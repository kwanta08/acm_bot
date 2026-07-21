"""
Settings コグ（マルチテナント版）

ボットの設定をコマンドで管理するためのモジュール。
設定はギルドごと（guild_id 単位）に保存され、他ギルドへは影響しない。
管理者のみが設定を変更できる。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.settings_repository import SettingsRepository
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("settings")


class Settings(commands.Cog):
    """ボット設定管理コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.settings_repo = SettingsRepository(self.db)

    async def _after_change(self, guild_id: int) -> None:
        """設定変更後の反映処理: ギルド別キャッシュ破棄 + グローバル再読込。"""
        config.invalidate_guild(guild_id)
        # レガシーギルドのグローバル設定を再読込
        await config.load_from_db(self.db)

    @app_commands.command(name="settings_list", description="全ての設定を表示します")
    @app_commands.check(is_admin)
    async def settings_list(self, interaction: discord.Interaction):
        """全ての設定を表示する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            settings = await self.settings_repo.get_all(guild_id)

            if not settings:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # 設定をカテゴリ別に整理
            categories = {
                "チャンネル": [],
                "ロール": [],
                "共通": [],
                "その他": []
            }

            channel_keys = {
                "BOT_LOG_CHANNEL_ID", "DEFAULT_ANNOUNCE_CHANNEL_ID",
                "DEFAULT_SCHEDULE_CHANNEL_ID", "DEFAULT_PROGRESS_CHANNEL_ID",
                "DEFAULT_TASK_CHANNEL_ID", "TODAY_LABEL_CHANNEL_ID"
            }
            role_keys = {
                "EXEC_ROLE_ID", "ADMIN_ROLE_ID", "LEADER_ROLE_IDS",
                "PRIMARY_TEAM_ROLE_IDS", "SECONDARY_TEAM_ROLE_IDS"
            }
            common_keys = {"TZ", "DB_PATH"}

            for key, value in settings.items():
                if key in channel_keys:
                    categories["チャンネル"].append((key, value))
                elif key in role_keys:
                    categories["ロール"].append((key, value))
                elif key in common_keys:
                    categories["共通"].append((key, value))
                elif key.startswith("TODOIST_"):
                    # レガシーの平文 Todoist 設定は表示しない
                    # （/todoist-setup での暗号化登録に置き換わった）
                    categories["その他"].append((key, "（廃止: /todoist-setup で再登録してください）"))
                elif key.startswith("SHEET_") or key in (
                        "SPREADSHEET_ID", "LAYER_SPREADSHEET_ID", "GOOGLE_CREDENTIALS_PATH"):
                    # Google Sheets 連携は廃止（NocoDB 移行）
                    categories["その他"].append((key, "（廃止: Sheets 連携は廃止されました）"))
                else:
                    categories["その他"].append((key, value))

            # Embed 作成
            embeds = []
            for category, items in categories.items():
                if not items:
                    continue

                description = "\n".join([f"**{key}**: `{value}`" for key, value in items])
                embed = info_embed(f"設定 - {category}", description)
                embeds.append(embed)

            if not embeds:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            for embed in embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            log.exception("設定一覧取得エラー: %s", e)
            embed = error_embed("設定一覧の取得に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_get", description="指定した設定値を取得します")
    @app_commands.describe(setting_key="設定キー")
    @app_commands.check(is_admin)
    async def settings_get(self, interaction: discord.Interaction, setting_key: str):
        """指定した設定値を取得する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            value = await self.settings_repo.get(guild_id, setting_key)

            if setting_key.startswith("TODOIST_"):
                # レガシーの平文 Todoist 設定は値を表示しない
                embed = info_embed(
                    setting_key,
                    "このキーは廃止されました。`/todoist-setup` / `/todoist-status` "
                    "を使用してください（値は表示されません）")
            elif value is None:
                # 環境変数をチェック
                import os
                env_value = os.getenv(setting_key)
                if env_value:
                    embed = info_embed(
                        setting_key,
                        f"値: `{env_value}`\n（環境変数から取得）"
                    )
                else:
                    embed = info_embed(setting_key, "設定されていません")
            else:
                embed = info_embed(setting_key, f"値: `{value}`")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            log.exception("設定取得エラー: %s", e)
            embed = error_embed(f"設定 `{setting_key}` の取得に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_set", description="設定値を保存します")
    @app_commands.describe(setting_key="設定キー", value="設定値")
    @app_commands.check(is_admin)
    async def settings_set(self, interaction: discord.Interaction, setting_key: str, value: str):
        """設定値を保存する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_key, value)
            embed = success_embed(
                "設定保存完了",
                f"**{setting_key}** = `{value}`\nをこのサーバーの設定として保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # ギルド別キャッシュとグローバル設定を更新
            await self._after_change(guild_id)

        except Exception as e:
            log.exception("設定保存エラー: %s", e)
            embed = error_embed(f"設定 `{setting_key}` の保存に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_delete", description="設定値を削除します")
    @app_commands.describe(setting_key="設定キー")
    @app_commands.check(is_admin)
    async def settings_delete(self, interaction: discord.Interaction, setting_key: str):
        """設定値を削除する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            deleted = await self.settings_repo.delete(guild_id, setting_key)

            if deleted:
                embed = success_embed(
                    "設定削除完了",
                    f"**{setting_key}** を削除しました"
                )
            else:
                embed = info_embed("設定削除", f"**{setting_key}** は存在しませんでした")

            await interaction.followup.send(embed=embed, ephemeral=True)

            # ギルド別キャッシュとグローバル設定を更新
            await self._after_change(guild_id)

        except Exception as e:
            log.exception("設定削除エラー: %s", e)
            embed = error_embed(f"設定 `{setting_key}` の削除に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # 便利なショートカットコマンド

    @app_commands.command(name="set_channel", description="チャンネルIDを設定します")
    @app_commands.describe(
        channel_type="チャンネルタイプ",
        channel_id="チャンネルID"
    )
    @app_commands.choices(channel_type=[
        app_commands.Choice(name="Botログ", value="BOT_LOG_CHANNEL_ID"),
        app_commands.Choice(name="お知らせ", value="DEFAULT_ANNOUNCE_CHANNEL_ID"),
        app_commands.Choice(name="スケジュール", value="DEFAULT_SCHEDULE_CHANNEL_ID"),
        app_commands.Choice(name="進捗", value="DEFAULT_PROGRESS_CHANNEL_ID"),
        app_commands.Choice(name="タスク", value="DEFAULT_TASK_CHANNEL_ID"),
        app_commands.Choice(name="今日やること", value="TODAY_LABEL_CHANNEL_ID"),
    ])
    @app_commands.check(is_admin)
    async def set_channel(self, interaction: discord.Interaction, channel_type: str, channel_id: str):
        """チャンネルIDを設定するショートカット"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            # channel_id がメンション形式の場合、IDを抽出
            if channel_id.startswith("<#") and channel_id.endswith(">"):
                channel_id = channel_id[2:-1]

            await self.settings_repo.set(guild_id, channel_type, channel_id)
            embed = success_embed(
                "チャンネル設定完了",
                f"**{channel_type}** = `{channel_id}`\nをこのサーバーの設定として保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

        except Exception as e:
            log.exception("チャンネル設定エラー: %s", e)
            embed = error_embed(f"チャンネル `{channel_type}` の設定に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_role", description="ロールIDを設定します")
    @app_commands.describe(
        role_type="ロールタイプ",
        role_id="ロールID"
    )
    @app_commands.choices(role_type=[
        app_commands.Choice(name="実行役", value="EXEC_ROLE_ID"),
        app_commands.Choice(name="管理者", value="ADMIN_ROLE_ID"),
        app_commands.Choice(name="リーダー", value="LEADER_ROLE_IDS"),
    ])
    @app_commands.check(is_admin)
    async def set_role(self, interaction: discord.Interaction, role_type: str, role_id: str):
        """ロールIDを設定するショートカット"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            # role_id がメンション形式の場合、IDを抽出
            if role_id.startswith("<@&") and role_id.endswith(">"):
                role_id = role_id[3:-1]

            # LEADER_ROLE_IDS はカンマ区切りで複数指定可能
            if role_type == "LEADER_ROLE_IDS":
                current = await self.settings_repo.get(guild_id, role_type, "")
                if current:
                    value = f"{current},{role_id}"
                else:
                    value = role_id
            else:
                value = role_id

            await self.settings_repo.set(guild_id, role_type, value)
            embed = success_embed(
                "ロール設定完了",
                f"**{role_type}** = `{value}`\nをこのサーバーの設定として保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

        except Exception as e:
            log.exception("ロール設定エラー: %s", e)
            embed = error_embed(f"ロール `{role_type}` の設定に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_common", description="共通設定をします")
    @app_commands.describe(
        setting_type="設定タイプ",
        value="設定値"
    )
    @app_commands.choices(setting_type=[
        app_commands.Choice(name="タイムゾーン", value="TZ"),
        app_commands.Choice(name="データベースパス", value="DB_PATH"),
    ])
    @app_commands.check(is_admin)
    async def set_common(self, interaction: discord.Interaction, setting_type: str, value: str):
        """共通設定をするショートカット"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_type, value)
            embed = success_embed(
                "共通設定完了",
                f"**{setting_type}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

        except Exception as e:
            log.exception("共通設定エラー: %s", e)
            embed = error_embed(f"共通設定 `{setting_type}` に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @set_channel.autocomplete('channel_id')
    async def channel_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """チャンネルIDのオートコンプリート"""
        # 現在のギルドのチャンネルを取得
        if interaction.guild is None:
            return []

        channels = interaction.guild.text_channels
        choices = []

        for channel in channels:
            if current.lower() in channel.name.lower() or current in str(channel.id):
                choices.append(app_commands.Choice(
                    name=f"#{channel.name} ({channel.id})",
                    value=str(channel.id)
                ))

        return choices[:25]  # 最大25件

    @set_role.autocomplete('role_id')
    async def role_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """ロールIDのオートコンプリート"""
        if interaction.guild is None:
            return []

        roles = interaction.guild.roles
        choices = []

        for role in roles:
            if current.lower() in role.name.lower() or current in str(role.id):
                choices.append(app_commands.Choice(
                    name=f"{role.name} ({role.id})",
                    value=str(role.id)
                ))

        return choices[:25]  # 最大25件


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
