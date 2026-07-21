"""
Core モジュール（仕様 11.1）。

/ping と /health を提供する。共通ユーティリティ（DB・サービス参照）は
bot インスタンス経由で各 Cog から利用する。
"""
from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.todoist_config_repository import TodoistConfigRepository
from utils import crypto
from utils.embeds import info_embed, success_embed
from utils.logger import get_logger

log = get_logger("core")


class Core(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Bot の応答確認をします。")
    async def ping(self, interaction: discord.Interaction):
        start = time.perf_counter()
        await interaction.response.defer(ephemeral=True)
        latency_ms = round(self.bot.latency * 1000)
        rtt_ms = round((time.perf_counter() - start) * 1000)
        embed = success_embed(
            "Pong",
            f"WebSocket 遅延: **{latency_ms} ms**\n往復: **{rtt_ms} ms**",
            executor=interaction.user.display_name,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="health", description="Bot と各連携サービスの状態を表示します。")
    async def health(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bot = self.bot

        db_ok = "✅" if await bot.db.is_healthy() else "❌"
        # Todoist はギルド別設定。当該ギルドの登録有無と暗号鍵の状態を示す
        if interaction.guild is not None:
            cfg = await TodoistConfigRepository(bot.db).get(interaction.guild.id)
            todoist_ok = "✅ 登録済み" if cfg else "⚪ 未登録（/todoist-setup）"
        else:
            todoist_ok = "⚪ ギルド外のため不明"
        enc_ok = "✅" if crypto.is_encryption_ready() else "❌ 未設定/不正"

        desc = (
            f"**DB（{bot.db.driver_name}）**: {db_ok}\n"
            f"**Todoist（このサーバー）**: {todoist_ok}\n"
            f"**暗号鍵（ENCRYPTION_KEY）**: {enc_ok}\n"
            f"**WebSocket 遅延**: {round(bot.latency * 1000)} ms\n"
            f"**タイムゾーン**: {config.tz}\n"
            f"**参加ギルド数**: {len(bot.guilds)}\n"
            f"**読み込み済み Cog**: {len(bot.cogs)}"
        )
        embed = info_embed("ヘルスチェック", desc, executor=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
