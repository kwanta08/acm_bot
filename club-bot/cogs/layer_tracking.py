"""
LayerTracking モジュール（仕様 11.8）。

桁巻き積層作業を /layer start / end の2コマンドで記録する。
桁名はコマンドで登録管理し、/layer start では登録済みの桁名から
autocomplete で選択する。進行中セッションと完了記録は SQLite（正本）に
永続化され、参照は DB（NocoDB）から行う（Google Sheets 連携は廃止）。

マルチテナント版: セッション・桁名・記録を interaction.guild.id で
スコープする。services/layer_tracking_service.py には
guild 固定プロキシ repo.for_guild(guild_id) を渡して利用する。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.layer_keta_repository import LayerKetaRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.name_cache_repository import ENTITY_USER, NameCacheRepository
from repositories.progress_repository import ProgressRepository
from services.layer_stats_service import (
    PERIOD_ALL,
    PERIOD_LABELS,
    PERIOD_MONTH,
    PERIOD_WEEK,
    LayerStats,
    aggregate_layer_stats,
    period_start,
)
from services.layer_tracking_service import LayerTrackingService
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    error_embed,
    info_embed,
    success_embed,
)
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso, now, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("layer")

PERIOD_CHOICES = [
    app_commands.Choice(name=PERIOD_LABELS[PERIOD_WEEK], value=PERIOD_WEEK),
    app_commands.Choice(name=PERIOD_LABELS[PERIOD_MONTH], value=PERIOD_MONTH),
    app_commands.Choice(name=PERIOD_LABELS[PERIOD_ALL], value=PERIOD_ALL),
]

#: 桁別・人別それぞれで Embed に並べる最大件数（合計が MAX_EMBED_FIELDS を
#: 超えないようにする。超えると送信そのものが HTTPException で落ちる）
STATS_SECTION_LIMIT = 10


def _fmt_minutes(minutes: int) -> str:
    """分を「N分」「N時間M分」に整える。"""
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


class LayerTracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session_repo = LayerSessionRepository(bot.db)
        self.keta_repo = LayerKetaRepository(bot.db)
        self.progress_repo = ProgressRepository(bot.db)
        self.name_repo = NameCacheRepository(bot.db)

    def _svc_for(self, guild_id: int) -> LayerTrackingService:
        """ギルド固定スコープのリポジトリでサービスを構成する。"""
        return LayerTrackingService(self.session_repo.for_guild(guild_id))

    group = app_commands.Group(name="layer", description="桁巻き積層作業の記録")

    # ---------- 桁名 autocomplete ----------
    async def _keta_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        names = await self.keta_repo.list_active(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()
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
            embed=success_embed(
                "桁名を登録しました", f"桁名: **{name}**", executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

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
                embed=error_embed(f"桁名「{name}」は登録されていません。"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=success_embed(
                "桁名を無効化しました", f"桁名: **{name}**", executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

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
                embed=empty_state_embed(
                    "桁名一覧", "登録済みの桁名はありません。", "/layer keta-add"
                ),
                ephemeral=True,
            )
            return
        lines = [f"{'✅' if r['active_flag'] else '⛔'} {r['keta_name']}" for r in rows]
        await interaction.followup.send(
            embed=info_embed("桁名一覧", "\n".join(lines)), ephemeral=True
        )

    # ---------- start ----------
    @group.command(name="start", description="桁名と層番号を指定して積層開始を記録します。")
    @app_commands.describe(
        keta="桁名（登録済みから選択）",
        layer_num="層番号（数字または「シュリンク」などのテキスト）",
    )
    @app_commands.autocomplete(keta=_keta_autocomplete)
    @require(Level.L1)
    async def start(self, interaction: discord.Interaction, keta: str, layer_num: str):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self.keta_repo.exists_active(guild_id, keta):
            await interaction.response.send_message(
                embed=error_embed(
                    f"桁名「{keta}」は登録されていません。`/layer keta-add` で登録してください。"
                ),
                ephemeral=True,
            )
            return

        svc = self._svc_for(guild_id)
        user_id = str(interaction.user.id)
        # 二重開始チェック（仕様 11.8.5）
        if await svc.has_active(user_id):
            active = await self.session_repo.get_by_user(guild_id, user_id)
            await interaction.response.send_message(
                embed=error_embed(
                    f"既に進行中のセッションがあります（{active['keta']} {active['layer_num']}）。\n"
                    "先に `/layer end` で終了してください。"
                ),
                ephemeral=True,
            )
            return

        started = await svc.start(user_id, keta, layer_num)
        embed = success_embed(
            "積層開始を記録しました",
            f"桁名: **{keta}**\n層番号: **{layer_num}**\n開始: {fmt_jp(started)}",
            executor=interaction.user.display_name,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------- end ----------
    @group.command(name="end", description="進行中の積層を終了し、記録を保存します。")
    @require(Level.L1)
    async def end(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = self._svc_for(guild_id)
        user_id = str(interaction.user.id)
        if not await svc.has_active(user_id):
            await interaction.response.send_message(
                embed=error_embed(
                    "進行中のセッションがありません。先に `/layer start` を実行してください。"
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await svc.end(user_id, interaction.user.display_name)

        embed = success_embed(
            "積層を記録しました",
            f"桁名: **{result['keta']}**\n層番号: **{result['layer_num']}**\n"
            f"作業時間: **{result['minutes']} 分**",
            executor=interaction.user.display_name,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- cancel ----------
    @group.command(
        name="cancel", description="進行中の積層を、記録を残さずに取り消します。"
    )
    @require(Level.L1)
    async def cancel(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = self._svc_for(guild_id)
        cancelled = await svc.cancel(str(interaction.user.id))
        if cancelled is None:
            await interaction.response.send_message(
                embed=empty_state_embed(
                    "取り消せる積層がありません",
                    "進行中のセッションがありません。",
                    "/layer start",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed(
                "積層を取り消しました",
                f"桁名: **{cancelled['keta']}**\n層番号: **{cancelled['layer_num']}**\n"
                "作業記録は残していません。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

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
                ephemeral=True,
            )
            return
        embed = info_embed("進行中の積層作業")
        guild = interaction.guild
        for s in sessions[:MAX_EMBED_FIELDS]:
            name = s["user_id"]
            if guild:
                m = guild.get_member(int(s["user_id"]))
                if m:
                    name = m.display_name
            embed.add_field(
                name=f"{name}",
                value=f"桁: {s['keta']} / {s['layer_num']} / 経過 {s['elapsed_min']} 分",
                inline=False,
            )
        add_truncation_note(embed, len(sessions), MAX_EMBED_FIELDS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- stats ----------
    def _resolve_name(
        self, guild: discord.Guild | None, cached: dict[str, str], user_id: str
    ) -> str:
        if guild is not None:
            try:
                member = guild.get_member(int(user_id))
            except (TypeError, ValueError):
                member = None
            if member is not None:
                return member.display_name
        return cached.get(str(user_id), str(user_id))

    def _stats_embed(
        self,
        stats: LayerStats,
        period: str,
        keta: str | None,
        guild: discord.Guild | None,
        cached_names: dict[str, str],
    ) -> discord.Embed:
        label = PERIOD_LABELS.get(period, PERIOD_LABELS[PERIOD_ALL])
        title = f"積層記録の集計（{label}）"
        if keta:
            title += f" — {keta}"
        embed = info_embed(
            title,
            f"記録 {stats.records} 件 / 合計 {_fmt_minutes(stats.total_minutes)}",
        )

        for k in stats.ketas[:STATS_SECTION_LIMIT]:
            # 目標が無い桁に分母を作らない（ADR 0021）
            if k.target:
                head = f"{k.layers} / {k.target} 層（残り {k.remaining} 層）"
            else:
                head = f"{k.layers} 層（目標未設定）"
            average = (
                f"1層あたり {k.average_minutes}分" if k.average_minutes is not None else "1層あたり —"
            )
            last = "—"
            if k.last_worked_at:
                try:
                    last = fmt_jp(from_iso(k.last_worked_at))
                except ValueError:
                    last = k.last_worked_at
            embed.add_field(
                name=f"桁: {k.keta}",
                value=f"{head}\n合計 {_fmt_minutes(k.minutes)} / {average}\n最終作業 {last}",
                inline=False,
            )
        if len(stats.ketas) > STATS_SECTION_LIMIT:
            embed.add_field(
                name="（桁の続き）",
                value=f"ほか {len(stats.ketas) - STATS_SECTION_LIMIT} 本"
                "（`keta` を指定すると絞り込めます）",
                inline=False,
            )

        if stats.members:
            lines = [
                f"{self._resolve_name(guild, cached_names, m.user_id)}: "
                f"{m.layers} 層 / {_fmt_minutes(m.minutes)}"
                for m in stats.members[:STATS_SECTION_LIMIT]
            ]
            if len(stats.members) > STATS_SECTION_LIMIT:
                lines.append(f"…ほか {len(stats.members) - STATS_SECTION_LIMIT} 人")
            embed.add_field(name="作業者別", value="\n".join(lines), inline=False)
        return embed

    @group.command(name="stats", description="積層記録を桁別・作業者別に集計します。")
    @app_commands.describe(keta="桁名（省略時はすべての桁）", period="集計期間（既定は全期間）")
    @app_commands.autocomplete(keta=_keta_autocomplete)
    @app_commands.choices(period=PERIOD_CHOICES)
    @require(Level.L1)
    async def stats(
        self,
        interaction: discord.Interaction,
        keta: str | None = None,
        period: str = PERIOD_ALL,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        records = await self.session_repo.list_records(guild_id, keta=keta)
        links = await self.progress_repo.list_spar_links(guild_id)
        targets = {str(link["keta_name"]): int(link["target_layers"]) for link in links}
        stats = aggregate_layer_stats(records, targets, since=period_start(period, now()))

        if stats.records == 0:
            situation = (
                f"「{keta}」の積層記録はまだありません。"
                if keta
                else "集計できる積層記録がまだありません。"
            )
            if period != PERIOD_ALL:
                situation += f"（期間: {PERIOD_LABELS.get(period, period)}）"
            await interaction.followup.send(
                embed=empty_state_embed("積層記録の集計", situation, "/layer start"),
                ephemeral=True,
            )
            return

        cached_names = await self.name_repo.names(guild_id, ENTITY_USER)
        embed = self._stats_embed(stats, period, keta, interaction.guild, cached_names)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LayerTracking(bot))
