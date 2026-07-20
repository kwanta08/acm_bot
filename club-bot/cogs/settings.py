"""
<<<<<<< HEAD
Settings コグ

ボットの設定をコマンドで管理するためのモジュール
管理者のみが設定を変更できる
=======
Settings コグ（マルチテナント版）

ボットの設定をコマンドで管理するためのモジュール。
設定はギルドごと（guild_id 単位）に保存され、他ギルドへは影響しない。
管理者のみが設定を変更できる。
>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
from utils.permissions import is_admin
=======
from utils.permissions import ensure_guild, is_admin
>>>>>>> 803617a (v4.0)

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("settings")


class Settings(commands.Cog):
    """ボット設定管理コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.settings_repo = SettingsRepository(self.db)

<<<<<<< HEAD
=======
    async def _after_change(self, guild_id: int) -> None:
        """設定変更後の反映処理: ギルド別キャッシュ破棄 + グローバル再読込。"""
        config.invalidate_guild(guild_id)
        # レガシーギルドのグローバル設定（Todoist/Sheets 等）を再読込
        await config.load_from_db(self.db)

>>>>>>> 803617a (v4.0)
    @app_commands.command(name="settings_list", description="全ての設定を表示します")
    @app_commands.check(is_admin)
    async def settings_list(self, interaction: discord.Interaction):
        """全ての設定を表示する"""
        await interaction.response.defer(ephemeral=True)
<<<<<<< HEAD
        
        try:
            settings = await self.settings_repo.get_all()
            
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            settings = await self.settings_repo.get_all(guild_id)

>>>>>>> 803617a (v4.0)
            if not settings:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
<<<<<<< HEAD
            
=======

>>>>>>> 803617a (v4.0)
            # 設定をカテゴリ別に整理
            categories = {
                "チャンネル": [],
                "ロール": [],
                "Todoist": [],
                "Google Sheets": [],
                "共通": [],
                "その他": []
            }
<<<<<<< HEAD
            
=======

>>>>>>> 803617a (v4.0)
            channel_keys = {
                "BOT_LOG_CHANNEL_ID", "DEFAULT_ANNOUNCE_CHANNEL_ID",
                "DEFAULT_SCHEDULE_CHANNEL_ID", "DEFAULT_PROGRESS_CHANNEL_ID",
                "DEFAULT_TASK_CHANNEL_ID", "TODAY_LABEL_CHANNEL_ID"
            }
            role_keys = {
                "EXEC_ROLE_ID", "ADMIN_ROLE_ID", "LEADER_ROLE_IDS",
                "PRIMARY_TEAM_ROLE_IDS", "SECONDARY_TEAM_ROLE_IDS"
            }
            todoist_keys = {
                "TODOIST_API_TOKEN", "TODOIST_PROJECT_ID", "TODAY_LABEL_NAME"
            }
            sheets_keys = {
                "GOOGLE_CREDENTIALS_PATH", "SPREADSHEET_ID", "LAYER_SPREADSHEET_ID",
                "SHEET_TASKS", "SHEET_ATTENDANCE", "SHEET_MEMBERS",
                "SHEET_TEAM_SUMMARY", "SHEET_AUDIT_LOG"
            }
            common_keys = {"TZ", "DB_PATH"}
<<<<<<< HEAD
            
=======

>>>>>>> 803617a (v4.0)
            for key, value in settings.items():
                if key in channel_keys:
                    categories["チャンネル"].append((key, value))
                elif key in role_keys:
                    categories["ロール"].append((key, value))
                elif key in todoist_keys:
                    categories["Todoist"].append((key, value))
                elif key in sheets_keys:
                    categories["Google Sheets"].append((key, value))
                elif key in common_keys:
                    categories["共通"].append((key, value))
                else:
                    categories["その他"].append((key, value))
<<<<<<< HEAD
            
=======

>>>>>>> 803617a (v4.0)
            # Embed 作成
            embeds = []
            for category, items in categories.items():
                if not items:
                    continue
<<<<<<< HEAD
                
                description = "\n".join([f"**{key}**: `{value}`" for key, value in items])
                embed = info_embed(f"設定 - {category}", description)
                embeds.append(embed)
            
=======

                description = "\n".join([f"**{key}**: `{value}`" for key, value in items])
                embed = info_embed(f"設定 - {category}", description)
                embeds.append(embed)

>>>>>>> 803617a (v4.0)
            if not embeds:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
<<<<<<< HEAD
            
            for embed in embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)
                
=======

            for embed in embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
        try:
            value = await self.settings_repo.get(setting_key)
            
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            value = await self.settings_repo.get(guild_id, setting_key)

>>>>>>> 803617a (v4.0)
            if value is None:
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
<<<<<<< HEAD
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
=======

            await interaction.followup.send(embed=embed, ephemeral=True)

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
        try:
            await self.settings_repo.set(setting_key, value)
            embed = success_embed(
                "設定保存完了",
                f"**{setting_key}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # config を更新（次回起動時にも反映されるように）
            await config.load_from_db(self.db)
            
=======
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

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
        try:
            deleted = await self.settings_repo.delete(setting_key)
            
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            deleted = await self.settings_repo.delete(guild_id, setting_key)

>>>>>>> 803617a (v4.0)
            if deleted:
                embed = success_embed(
                    "設定削除完了",
                    f"**{setting_key}** を削除しました"
                )
            else:
                embed = info_embed("設定削除", f"**{setting_key}** は存在しませんでした")
<<<<<<< HEAD
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
=======

            await interaction.followup.send(embed=embed, ephemeral=True)

            # ギルド別キャッシュとグローバル設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
        except Exception as e:
            log.exception("設定削除エラー: %s", e)
            embed = error_embed(f"設定 `{setting_key}` の削除に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # 便利なショートカットコマンド
<<<<<<< HEAD
    
=======

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

>>>>>>> 803617a (v4.0)
        try:
            # channel_id がメンション形式の場合、IDを抽出
            if channel_id.startswith("<#") and channel_id.endswith(">"):
                channel_id = channel_id[2:-1]
<<<<<<< HEAD
            
            await self.settings_repo.set(channel_type, channel_id)
            embed = success_embed(
                "チャンネル設定完了",
                f"**{channel_type}** = `{channel_id}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # config を更新
            await config.load_from_db(self.db)
            
=======

            await self.settings_repo.set(guild_id, channel_type, channel_id)
            embed = success_embed(
                "チャンネル設定完了",
                f"**{channel_type}** = `{channel_id}`\nをこのサーバーの設定として保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

>>>>>>> 803617a (v4.0)
        try:
            # role_id がメンション形式の場合、IDを抽出
            if role_id.startswith("<@&") and role_id.endswith(">"):
                role_id = role_id[3:-1]
<<<<<<< HEAD
            
            # LEADER_ROLE_IDS はカンマ区切りで複数指定可能
            if role_type == "LEADER_ROLE_IDS":
                current = await self.settings_repo.get(role_type, "")
=======

            # LEADER_ROLE_IDS はカンマ区切りで複数指定可能
            if role_type == "LEADER_ROLE_IDS":
                current = await self.settings_repo.get(guild_id, role_type, "")
>>>>>>> 803617a (v4.0)
                if current:
                    value = f"{current},{role_id}"
                else:
                    value = role_id
            else:
                value = role_id
<<<<<<< HEAD
            
            await self.settings_repo.set(role_type, value)
            embed = success_embed(
                "ロール設定完了",
                f"**{role_type}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # config を更新
            await config.load_from_db(self.db)
            
=======

            await self.settings_repo.set(guild_id, role_type, value)
            embed = success_embed(
                "ロール設定完了",
                f"**{role_type}** = `{value}`\nをこのサーバーの設定として保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
        except Exception as e:
            log.exception("ロール設定エラー: %s", e)
            embed = error_embed(f"ロール `{role_type}` の設定に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_sheets", description="Google Sheets 設定をします")
    @app_commands.describe(
        setting_type="設定タイプ",
        value="設定値"
    )
    @app_commands.choices(setting_type=[
        app_commands.Choice(name="スプレッドシートID", value="SPREADSHEET_ID"),
        app_commands.Choice(name="層塗りシートID", value="LAYER_SPREADSHEET_ID"),
        app_commands.Choice(name="タスクシート名", value="SHEET_TASKS"),
        app_commands.Choice(name="出席シート名", value="SHEET_ATTENDANCE"),
        app_commands.Choice(name="メンバーシート名", value="SHEET_MEMBERS"),
        app_commands.Choice(name="チームサマリシート名", value="SHEET_TEAM_SUMMARY"),
        app_commands.Choice(name="監査ログシート名", value="SHEET_AUDIT_LOG"),
    ])
    @app_commands.check(is_admin)
    async def set_sheets(self, interaction: discord.Interaction, setting_type: str, value: str):
        """Google Sheets 設定をするショートカット"""
        await interaction.response.defer(ephemeral=True)
<<<<<<< HEAD
        
        try:
            await self.settings_repo.set(setting_type, value)
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_type, value)
>>>>>>> 803617a (v4.0)
            embed = success_embed(
                "Sheets 設定完了",
                f"**{setting_type}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
<<<<<<< HEAD
            
            # config を更新
            await config.load_from_db(self.db)
            
=======

            # 設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
        except Exception as e:
            log.exception("Sheets 設定エラー: %s", e)
            embed = error_embed(f"Sheets 設定 `{setting_type}` に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_todoist", description="Todoist 設定をします")
    @app_commands.describe(
        setting_type="設定タイプ",
        value="設定値"
    )
    @app_commands.choices(setting_type=[
        app_commands.Choice(name="APIトークン", value="TODOIST_API_TOKEN"),
        app_commands.Choice(name="プロジェクトID", value="TODOIST_PROJECT_ID"),
        app_commands.Choice(name="今日やることラベル名", value="TODAY_LABEL_NAME"),
    ])
    @app_commands.check(is_admin)
    async def set_todoist(self, interaction: discord.Interaction, setting_type: str, value: str):
        """Todoist 設定をするショートカット"""
        await interaction.response.defer(ephemeral=True)
<<<<<<< HEAD
        
        try:
            await self.settings_repo.set(setting_type, value)
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_type, value)
>>>>>>> 803617a (v4.0)
            embed = success_embed(
                "Todoist 設定完了",
                f"**{setting_type}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
<<<<<<< HEAD
            
            # config を更新
            await config.load_from_db(self.db)
            
=======

            # 設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
        except Exception as e:
            log.exception("Todoist 設定エラー: %s", e)
            embed = error_embed(f"Todoist 設定 `{setting_type}` に失敗しました")
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
<<<<<<< HEAD
        
        try:
            await self.settings_repo.set(setting_type, value)
=======
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_type, value)
>>>>>>> 803617a (v4.0)
            embed = success_embed(
                "共通設定完了",
                f"**{setting_type}** = `{value}`\nを保存しました"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
<<<<<<< HEAD
            
            # config を更新
            await config.load_from_db(self.db)
            
=======

            # 設定を更新
            await self._after_change(guild_id)

>>>>>>> 803617a (v4.0)
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
<<<<<<< HEAD
        
        channels = interaction.guild.text_channels
        choices = []
        
=======

        channels = interaction.guild.text_channels
        choices = []

>>>>>>> 803617a (v4.0)
        for channel in channels:
            if current.lower() in channel.name.lower() or current in str(channel.id):
                choices.append(app_commands.Choice(
                    name=f"#{channel.name} ({channel.id})",
                    value=str(channel.id)
                ))
<<<<<<< HEAD
        
=======

>>>>>>> 803617a (v4.0)
        return choices[:25]  # 最大25件

    @set_role.autocomplete('role_id')
    async def role_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """ロールIDのオートコンプリート"""
        if interaction.guild is None:
            return []
<<<<<<< HEAD
        
        roles = interaction.guild.roles
        choices = []
        
=======

        roles = interaction.guild.roles
        choices = []

>>>>>>> 803617a (v4.0)
        for role in roles:
            if current.lower() in role.name.lower() or current in str(role.id):
                choices.append(app_commands.Choice(
                    name=f"{role.name} ({role.id})",
                    value=str(role.id)
                ))
<<<<<<< HEAD
        
=======

>>>>>>> 803617a (v4.0)
        return choices[:25]  # 最大25件


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
