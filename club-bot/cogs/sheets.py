"""
Sheets コグ（Google Sheets エクスポート連携・ギルド別）

ギルドごとのスプレッドシートへ DB のタスク・メンバー一覧を
エクスポートする任意連携の管理コマンド。

- /set_sheet   : スプレッドシート ID をギルド別 settings に登録（管理者）
- /sheet_sync  : 登録済みのシートへエクスポートを実行（管理者）。
                 未設定ギルドでは案内を返すだけでエラーにしない
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from services import sheets_service
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("sheets")


class Sheets(commands.Cog):
    """Google Sheets エクスポート連携コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore

    def _exporter(self) -> sheets_service.SheetsExporter:
        return sheets_service.SheetsExporter(
            credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", ""))

    @app_commands.command(
        name="set_sheet",
        description="エクスポート先のスプレッドシート ID を登録します（管理者）。")
    @app_commands.describe(spreadsheet_id="スプレッドシートの ID（URL の /d/ と /edit の間の文字列）")
    @app_commands.check(is_admin)
    async def set_sheet(self, interaction: discord.Interaction, spreadsheet_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        spreadsheet_id = spreadsheet_id.strip()
        if not spreadsheet_id:
            await interaction.followup.send(
                embed=error_embed("スプレッドシート ID を指定してください。"),
                ephemeral=True)
            return

        try:
            from repositories.settings_repository import SettingsRepository
            await SettingsRepository(self.db).set(
                guild_id, sheets_service.SETTINGS_KEY, spreadsheet_id)
            config.invalidate_guild(guild_id)
            await config.load_from_db(self.db)
            await interaction.followup.send(
                embed=success_embed(
                    "スプレッドシートを登録しました",
                    f"ID: `{spreadsheet_id}`\n"
                    "サービスアカウントをこのスプレッドシートに"
                    "「編集者」として共有してください。\n"
                    "`/sheet_sync` でエクスポートを実行できます。",
                    executor=interaction.user.display_name),
                ephemeral=True)
        except Exception as e:
            log.exception("スプレッドシート ID 保存エラー: %s", e)
            await interaction.followup.send(
                embed=error_embed("スプレッドシート ID の保存に失敗しました。"),
                ephemeral=True)

    @app_commands.command(
        name="sheet_sync",
        description="タスク・メンバー一覧をスプレッドシートへ出力します（管理者）。")
    @app_commands.check(is_admin)
    async def sheet_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        # 未設定ギルドは自動スキップ（エラーにせず案内のみ）
        if await sheets_service.get_spreadsheet_id(self.db, guild_id) is None:
            await interaction.followup.send(
                embed=info_embed(
                    "Sheets 連携は未設定です",
                    "このサーバーではスプレッドシートが登録されていません。\n"
                    "連携する場合は `/set_sheet` でスプレッドシート ID を"
                    "登録してください。"),
                ephemeral=True)
            return

        try:
            result = await sheets_service.sync_guild(
                self.db, guild_id, self._exporter())
        except sheets_service.SheetsUnavailable as e:
            await interaction.followup.send(
                embed=error_embed(f"Sheets 連携を実行できません: {e}"),
                ephemeral=True)
            return
        except Exception as e:  # noqa: BLE001  (gspread の API エラー等)
            log.warning("Sheets エクスポート失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed(
                    "スプレッドシートへの書き込みに失敗しました。\n"
                    "サービスアカウントの共有設定とシート ID を確認してください。"),
                ephemeral=True)
            return

        if result is None:
            await interaction.followup.send(
                embed=info_embed("Sheets 連携は未設定です",
                                 "`/set_sheet` でスプレッドシート ID を登録してください。"),
                ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed(
                "Sheets へエクスポートしました",
                f"tasks: {result['tasks']} 件 / members: {result['members']} 件",
                executor=interaction.user.display_name),
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Sheets(bot))
