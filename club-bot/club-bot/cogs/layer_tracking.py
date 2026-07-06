"""
LayerTracking モジュール（仕様 11.8）。

桁巻き積層作業を /layer start / end の2コマンドで記録し、桁ごとの
Google Sheets シートへ追記する。進行中セッションは SQLite に永続化し、
Bot 再起動後も復元できる。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import LAYER_KETA_CHOICES
from repositories.layer_session_repository import LayerSessionRepository
from services.layer_tracking_service import LayerTrackingService
from services.sheets_service import SheetsError
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp
from utils.permissions import Level, require

log = get_logger("layer")

KETA_CHOICES = [app_commands.Choice(name=k, value=k) for k in LAYER_KETA_CHOICES]


class LayerTracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session_repo = LayerSessionRepository(bot.db)
        self.svc = LayerTrackingService(self.session_repo, bot.sheets)

    group = app_commands.Group(name="layer", description="桁巻き積層作業の記録")

    # ---------- start ----------
    @group.command(name="start", description="桁名と層番号を指定して積層開始を記録します。")
    @app_commands.describe(keta="桁名（選択）", layer_num="層番号")
    @app_commands.choices(keta=KETA_CHOICES)
    @require(Level.L1)
    async def start(self, interaction: discord.Interaction,
                    keta: app_commands.Choice[str],
                    layer_num: app_commands.Range[int, 1, 9999]):
        user_id = str(interaction.user.id)
        # 二重開始チェック（仕様 11.8.5）
        if await self.svc.has_active(user_id):
            active = await self.session_repo.get_by_user(user_id)
            await interaction.response.send_message(
                embed=error_embed(
                    f"既に進行中のセッションがあります（{active['keta']} 第{active['layer_num']}層）。\n"
                    "先に `/layer end` で終了してください。"),
                ephemeral=True)
            return

        started = await self.svc.start(user_id, keta.value, layer_num)
        embed = success_embed(
            "積層開始を記録しました",
            f"桁名: **{keta.value}**\n層番号: **第{layer_num}層**\n開始: {fmt_jp(started)}",
            executor=interaction.user.display_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- end ----------
    @group.command(name="end", description="進行中の積層を終了し、対応シートへ記録します。")
    @require(Level.L1)
    async def end(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if not await self.svc.has_active(user_id):
            await interaction.response.send_message(
                embed=error_embed("進行中のセッションがありません。先に `/layer start` を実行してください。"),
                ephemeral=True)
            return

        # 処理中状態を返してから Sheets 書き込み（仕様 11.8.5）
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.svc.end(user_id, interaction.user.display_name)
        except SheetsError as e:
            await self.bot.log_to_channel(f"[Layer] Sheets 書き込み失敗 user={user_id}: {e}")
            await interaction.followup.send(
                embed=error_embed(
                    "Sheets への書き込みに失敗しました。セッションは保持されています。"
                    "時間をおいて再度 `/layer end` を実行してください。"),
                ephemeral=True)
            return

        embed = success_embed(
            "積層を記録しました",
            f"桁名: **{result['keta']}**\n層番号: **第{result['layer_num']}層**\n"
            f"作業時間: **{result['minutes']} 分**",
            executor=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- status ----------
    @group.command(name="status", description="現在進行中の作業一覧を表示します。")
    @require(Level.L1)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sessions = await self.svc.list_active()
        if not sessions:
            await interaction.followup.send(
                embed=info_embed("進行中の積層作業", "現在、進行中の作業はありません。"),
                ephemeral=True)
            return
        embed = info_embed("進行中の積層作業")
        guild = interaction.guild
        for s in sessions:
            name = s["user_id"]
            if guild:
                m = guild.get_member(int(s["user_id"]))
                if m:
                    name = m.display_name
            embed.add_field(
                name=f"{name}",
                value=f"桁: {s['keta']} / 第{s['layer_num']}層 / 経過 {s['elapsed_min']} 分",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LayerTracking(bot))
