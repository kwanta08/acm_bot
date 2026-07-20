"""
LayerTracking モジュール（仕様 11.8）。

桁巻き積層作業を /layer start / end の2コマンドで記録し、桁ごとの
Google Sheets シートへ追記する。桁名はコマンドで登録管理し、
/layer start では登録済みの桁名から autocomplete で選択する。
進行中セッションは SQLite に永続化し、Bot 再起動後も復元できる。

マルチテナント版: セッション・桁名・記録を interaction.guild.id で
スコープする。services/layer_tracking_service.py は変更禁止のため、
guild 固定プロキシ repo.for_guild(guild_id) を渡して利用する。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.layer_keta_repository import LayerKetaRepository
from repositories.layer_session_repository import LayerSessionRepository
from services.layer_tracking_service import LayerTrackingService
from services.sheets_service import SheetsError
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, now, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("layer")


class LayerTracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session_repo = LayerSessionRepository(bot.db)
        self.keta_repo = LayerKetaRepository(bot.db)

    def _svc_for(self, guild_id: int) -> LayerTrackingService:
        """ギルド固定スコープのリポジトリでサービスを構成する。"""
        return LayerTrackingService(self.session_repo.for_guild(guild_id), self.bot.sheets)

    group = app_commands.Group(name="layer", description="桁巻き積層作業の記録")

    # ---------- 桁名 autocomplete ----------
    async def _keta_autocomplete(self, interaction: discord.Interaction,
                                 current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        names = await self.keta_repo.list_active(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in names if current.lower() in n.lower()
        ][:25]

    # ---------- keta-add ----------
    @group.command(name="keta-add", description="桁名を登録します。")
    @app_commands.describe(name="登録する桁名")
    @require(Level.L2)
    async def keta_add(self, interaction: discord.Interaction, name: str):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        await self.keta_repo.add(guild_id, name, str(interaction.user.id), to_iso(now()))
        await interaction.response.send_message(
            embed=success_embed("桁名を登録しました", f"桁名: **{name}**",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- keta-remove ----------
    @group.command(name="keta-remove", description="桁名を無効化します。")
    @app_commands.describe(name="無効化する桁名")
    @app_commands.autocomplete(name=_keta_autocomplete)
    @require(Level.L2)
    async def keta_remove(self, interaction: discord.Interaction, name: str):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        ok = await self.keta_repo.deactivate(guild_id, name)
        if not ok:
            await interaction.response.send_message(
                embed=error_embed(f"桁名「{name}」は登録されていません。"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("桁名を無効化しました", f"桁名: **{name}**",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- keta-list ----------
    @group.command(name="keta-list", description="登録済みの桁名一覧を表示します。")
    @require(Level.L1)
    async def keta_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.keta_repo.list_all(guild_id)
        if not rows:
            await interaction.followup.send(
                embed=info_embed("桁名一覧", "登録済みの桁名はありません。"), ephemeral=True)
            return
        lines = [f"{'✅' if r['active_flag'] else '⛔'} {r['keta_name']}" for r in rows]
        await interaction.followup.send(
            embed=info_embed("桁名一覧", "\n".join(lines)), ephemeral=True)

    # ---------- start ----------
    @group.command(name="start", description="桁名と層番号を指定して積層開始を記録します。")
    @app_commands.describe(keta="桁名（登録済みから選択）",
                           layer_num="層番号（数字または「シュリンク」などのテキスト）")
    @app_commands.autocomplete(keta=_keta_autocomplete)
    @require(Level.L1)
    async def start(self, interaction: discord.Interaction, keta: str, layer_num: str):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self.keta_repo.exists_active(guild_id, keta):
            await interaction.response.send_message(
                embed=error_embed(
                    f"桁名「{keta}」は登録されていません。`/layer keta-add` で登録してください。"),
                ephemeral=True)
            return

        svc = self._svc_for(guild_id)
        user_id = str(interaction.user.id)
        # 二重開始チェック（仕様 11.8.5）
        if await svc.has_active(user_id):
            active = await self.session_repo.get_by_user(guild_id, user_id)
            await interaction.response.send_message(
                embed=error_embed(
                    f"既に進行中のセッションがあります（{active['keta']} {active['layer_num']}）。\n"
                    "先に `/layer end` で終了してください。"),
                ephemeral=True)
            return

        started = await svc.start(user_id, keta, layer_num)
        embed = success_embed(
            "積層開始を記録しました",
            f"桁名: **{keta}**\n層番号: **{layer_num}**\n開始: {fmt_jp(started)}",
            executor=interaction.user.display_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- end ----------
    @group.command(name="end", description="進行中の積層を終了し、対応シートへ記録します。")
    @require(Level.L1)
    async def end(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = self._svc_for(guild_id)
        user_id = str(interaction.user.id)
        if not await svc.has_active(user_id):
            await interaction.response.send_message(
                embed=error_embed("進行中のセッションがありません。先に `/layer start` を実行してください。"),
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            result = await svc.end(user_id, interaction.user.display_name)
        except SheetsError as e:
            await self.bot.log_to_channel(
                f"[Layer] Sheets 書き込み失敗 user={user_id}: {e}", guild_id=guild_id)
            await interaction.followup.send(
                embed=error_embed(
                    "Sheets への書き込みに失敗しました。記録は保存済みです。"
                    "`/layer sync` で後から再送信できます。"),
                ephemeral=True)
            return

        embed = success_embed(
            "積層を記録しました",
            f"桁名: **{result['keta']}**\n層番号: **{result['layer_num']}**\n"
            f"作業時間: **{result['minutes']} 分**",
            executor=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- status ----------
    @group.command(name="status", description="現在進行中の作業一覧を表示します。")
    @require(Level.L1)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = self._svc_for(guild_id)
        sessions = await svc.list_active()
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
                value=f"桁: {s['keta']} / {s['layer_num']} / 経過 {s['elapsed_min']} 分",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- sync ----------
    @group.command(name="sync", description="未反映の桁巻き記録をシートへ再送信します。")
    @require(Level.L2)
    async def sync_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not self.bot.sheets.enabled:
            await interaction.followup.send(
                embed=info_embed("Google Sheets 無効",
                                 "credentials.json と SPREADSHEET_ID を設定すると有効化されます。"),
                ephemeral=True)
            return
        svc = self._svc_for(guild_id)
        try:
            n = await svc.sync_unsynced_records()
        except SheetsError as e:
            await self.bot.log_to_channel(f"[Layer] sync 失敗: {e}", guild_id=guild_id)
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。時間をおいて再試行してください。"),
                ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("桁巻き記録シート同期完了", f"{n} 件を再送信しました",
                                executor=interaction.user.display_name),
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LayerTracking(bot))
