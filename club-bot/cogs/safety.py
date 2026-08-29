"""
Safety コグ（ヒヤリハット・事故報告。G4-10）

工房での切削・溶剤・高所作業・機体運搬・テストフライトと危険度が高く、
大学から安全管理体制の提示を求められることもある。
今は「危なかった」が雑談チャンネルに流れて消えている。

- /incident report : Modal で報告（全員。`anonymous:true` で匿名）
- /incident list   : 直近の報告を一覧（幹部以上）

**匿名の約束は構造で守る。**
報告者 ID は匿名でも DB に保存する（悪用・虚偽報告への対処に要る）が、
表示に使うのは `reporter_name` だけで、匿名報告ではそれが NULL になる。
リポジトリの取得系は `reporter_id` を返さないので、
表示層に「うっかり出す」経路そのものが無い。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.incident_repository import IncidentRepository
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    error_embed,
    info_embed,
    success_embed,
)
from utils.logger import get_logger
from utils.notify import guild_channel, resolve_notice_channel_id
from utils.parser import now, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("safety")

#: 匿名報告の表示名（一覧・通知の両方でこれを使う）
ANONYMOUS_LABEL = "匿名"

#: 一覧に並べる最大件数
MAX_INCIDENT_FIELDS = MAX_EMBED_FIELDS - 1


def reporter_label(row: dict) -> str:
    """報告者の表示。匿名、または名前が残っていなければ「匿名」。

    **`reporter_id` は参照しない。** 参照しないことがこの関数の要点で、
    リポジトリ側も取得列に含めていない。
    """
    if row.get("anonymous_flag"):
        return ANONYMOUS_LABEL
    return str(row.get("reporter_name") or ANONYMOUS_LABEL)


def build_incident_embed(row: dict, *, title: str) -> discord.Embed:
    """報告1件の Embed。匿名なら報告者欄に名前を出さない。"""
    embed = info_embed(title, str(row.get("description") or ""))
    embed.add_field(name="発生日時", value=str(row.get("occurred_at") or "—"), inline=True)
    embed.add_field(name="場所", value=str(row.get("place") or "—"), inline=True)
    embed.add_field(name="けが", value=str(row.get("injury") or "記載なし"), inline=True)
    if row.get("prevention"):
        embed.add_field(name="再発防止案", value=str(row["prevention"]), inline=False)
    embed.add_field(name="報告者", value=reporter_label(row), inline=False)
    return embed


class IncidentModal(discord.ui.Modal, title="ヒヤリハット・事故の報告"):
    """5項目の入力。Modal の上限がちょうど5つなので、匿名指定はコマンド引数側。"""

    occurred_input = discord.ui.TextInput(
        label="発生日時",
        placeholder="2026-08-29 15:30 ごろ",
        required=True,
        max_length=100,
    )
    place_input = discord.ui.TextInput(
        label="場所",
        placeholder="工房のボール盤の前",
        required=True,
        max_length=100,
    )
    description_input = discord.ui.TextInput(
        label="何が起きたか",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    injury_input = discord.ui.TextInput(
        label="けがの有無",
        placeholder="無し / 軽い擦り傷 など",
        required=False,
        max_length=200,
    )
    prevention_input = discord.ui.TextInput(
        label="再発防止案（任意）",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: Safety, guild_id: int, owner_id: int, anonymous: bool):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.anonymous = anonymous

    async def on_submit(self, interaction: discord.Interaction):
        # コマンド実行者と同一であることを検証（todoist_admin と同じ作法）
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.save_report(
            interaction,
            guild_id=self.guild_id,
            occurred_at=(self.occurred_input.value or "").strip(),
            place=(self.place_input.value or "").strip(),
            description=(self.description_input.value or "").strip(),
            injury=(self.injury_input.value or "").strip() or None,
            prevention=(self.prevention_input.value or "").strip() or None,
            anonymous=self.anonymous,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # **報告の中身をログに出さない**（匿名報告の内容が運用ログへ漏れる）
        log.warning(
            "ヒヤリハット報告の Modal でエラー (guild=%s): %s",
            self.guild_id,
            type(error).__name__,
        )
        try:
            message = error_embed("報告の保存に失敗しました。時間をおいて再試行してください。")
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
        except Exception:  # noqa: BLE001, S110
            pass


class Safety(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = IncidentRepository(bot.db)

    group = app_commands.Group(name="incident", description="ヒヤリハット・事故の報告")

    @group.command(name="report", description="ヒヤリハット・事故を報告します。")
    @app_commands.describe(anonymous="匿名で報告する（名前を表示しません）")
    @require(Level.L1)
    async def incident_report(self, interaction: discord.Interaction, anonymous: bool = False):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        # Modal は defer より前に送る必要があるため ensure_guild だけで進む
        await interaction.response.send_modal(
            IncidentModal(self, guild_id, interaction.user.id, anonymous)
        )

    async def save_report(
        self,
        interaction: discord.Interaction,
        *,
        guild_id: int,
        occurred_at: str,
        place: str,
        description: str,
        injury: str | None,
        prevention: str | None,
        anonymous: bool,
    ) -> None:
        incident_id = await self.repo.report(
            guild_id,
            occurred_at=occurred_at,
            place=place,
            description=description,
            injury=injury,
            prevention=prevention,
            reporter_id=str(interaction.user.id),
            reporter_name=interaction.user.display_name,
            anonymous=anonymous,
            created_at=to_iso(now()),
        )
        # **保存した行を読み直して通知する。** 入力値をそのまま流すと、
        # 匿名フラグの適用漏れが通知にだけ現れる形が作れてしまう
        row = await self.repo.get(guild_id, incident_id)
        await self.notify_exec(interaction.guild, guild_id, row or {})
        await interaction.followup.send(
            embed=success_embed(
                "報告を受け付けました",
                "幹部へ共有しました。ありがとうございます。"
                + ("\n（匿名で記録しています）" if anonymous else ""),
            ),
            ephemeral=True,
        )

    async def notify_exec(self, guild: discord.Guild | None, guild_id: int, row: dict) -> None:
        """幹部ロールへ通知する。送信先が無ければ運用ログに残す。"""
        channel_id = await resolve_notice_channel_id(self.bot.db, guild_id)
        channel = guild_channel(guild, channel_id)
        if channel is None:
            log.info("ヒヤリハット報告の通知先が無い (guild=%s)", guild_id)
            await self.bot.log_to_channel(
                "[安全] ヒヤリハットの報告がありましたが、通知先チャンネルが"
                "設定されていないため共有できませんでした。`/incident list` で確認できます。",
                guild_id=guild_id,
            )
            return
        gconf = await config.for_guild(guild_id, db=self.bot.db)
        content = None
        if guild is not None and gconf.exec_role_id:
            role = guild.get_role(int(gconf.exec_role_id))
            if role is not None:
                content = role.mention
        try:
            await channel.send(
                content=content,
                embed=build_incident_embed(row, title="⚠️ ヒヤリハットの報告がありました"),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("ヒヤリハット報告の通知に失敗 (guild=%s): %s", guild_id, e)
            await self.bot.log_to_channel(
                f"[安全] ヒヤリハット報告の共有に失敗しました: {e}", guild_id=guild_id
            )

    @group.command(name="list", description="直近のヒヤリハット報告を表示します（幹部以上）。")
    @app_commands.describe(limit="表示件数（最大24）")
    @require(Level.L3)
    async def incident_list(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 24] = 10
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.repo.list_recent(guild_id, limit)
        if not rows:
            await interaction.followup.send(
                embed=empty_state_embed(
                    "ヒヤリハット報告",
                    "まだ報告がありません。",
                    "/incident report",
                ),
                ephemeral=True,
            )
            return
        total = await self.repo.count(guild_id)
        embed = info_embed("ヒヤリハット報告", f"直近 {len(rows)} 件 / 全 {total} 件")
        for row in rows[:MAX_INCIDENT_FIELDS]:
            value = str(row.get("description") or "")[:400]
            value += f"\nけが: {row.get('injury') or '記載なし'}"
            if row.get("prevention"):
                value += f"\n再発防止: {str(row['prevention'])[:200]}"
            value += f"\n報告者: {reporter_label(row)}"
            embed.add_field(
                name=f"#{row['incident_id']} {row.get('occurred_at')} / {row.get('place')}"[:250],
                value=value[:1024],
                inline=False,
            )
        add_truncation_note(embed, total, len(rows))
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Safety(bot))
