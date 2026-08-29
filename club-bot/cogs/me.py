"""
`/me` — 部員視点の個人サマリー（G4-4）。

部員から見た入口が無かった。未回答の投票・積層実績・担当ノードは
それぞれ別コマンドに散らばっている。
**新入生が「今日自分は何をすればいいか」を1コマンドで確認できない。**

タスクはここに出ない。タスクの正本は Todoist で、Todoist には
「どの Discord ユーザーが担当か」という概念が無い（スキーマ v22）。
自分のタスクは Todoist 側で確認する。

**新しいテーブルは作らない。** 既存のクエリを合成するだけの読み取り専用
コマンドなので、マイグレーションも新しい設定も無い。

マルチテナント版: すべての取得は interaction.guild.id でスコープする。
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.schedule_repository import ScheduleRepository
from services.layer_stats_service import PERIOD_MONTH, aggregate_layer_stats, period_start
from services.spar_winding_service import STATUS_DONE
from utils.embeds import empty_state_embed, error_embed, info_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso, now
from utils.permissions import Level, ensure_guild, is_self_or_level, require

log = get_logger("me")

#: 各セクションに並べる最大件数（1コマンドで俯瞰させるための上限）
SECTION_LIMIT = 5

#: 他人のサマリーを見るのに必要なレベル。
#: ここが下がると、一般部員が他人の担当タスクと出欠回答状況を引けるようになる。
VIEW_OTHERS_LEVEL = Level.L2


def _fmt_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}分"
    hours, rest = divmod(minutes, 60)
    return f"{hours}時間{rest}分" if rest else f"{hours}時間"


class Me(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.schedule_repo = ScheduleRepository(bot.db)
        self.session_repo = LayerSessionRepository(bot.db)
        self.progress_repo = ProgressRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)

    async def may_view(self, interaction: discord.Interaction, user_id: str) -> bool:
        """そのユーザーのサマリーを見てよいか。

        自分の分は誰でも、他人の分は `VIEW_OTHERS_LEVEL` 以上。
        **メソッドとして切り出してある**のは、コマンドのテストが
        モジュール変数の差し替えに頼らずに済むようにするため
        （`unload_extension` を通ると `sys.modules` の実体が入れ替わり、
        モジュール変数への `mock.patch` が別のモジュールに当たる）。
        """
        if str(user_id) == str(interaction.user.id):
            return True
        return await is_self_or_level(interaction, str(user_id), VIEW_OTHERS_LEVEL)

    # ------------------------------------------------------------------
    # 集計（コマンドから切り離してテストできるようにする）
    # ------------------------------------------------------------------
    async def unanswered_schedules(self, guild_id: int, user_id: str) -> list[dict[str, Any]]:
        """開催中の投票のうち、その人が1票も入れていないものを締切順に返す。

        「未回答」の定義は投票**単位**（G3-2 と同じ）。候補単位で数えると
        「3件中2件だけ答えた」人が未回答として出てしまう。
        """
        out: list[dict[str, Any]] = []
        for row in await self.schedule_repo.list_open_schedules(guild_id):
            voters = await self.schedule_repo.list_voters_for_schedule(
                guild_id, row["schedule_id"]
            )
            if str(user_id) not in {str(v) for v in voters}:
                out.append(row)
        out.sort(key=lambda r: str(r.get("deadline") or ""))
        return out

    async def assigned_nodes(self, guild_id: int, names: set[str]) -> list[dict[str, Any]]:
        """担当中（完了以外）の進捗ノード。

        `progress_nodes.assignee` は **Discord ユーザー ID ではなく自由記述の
        名前**（`/progress add assignee:` で入る）。ID で引けないので、
        表示名・台帳の登録名のいずれかと一致する行を拾う。
        名前を変えた人の分が落ちるが、**ID を持たない列を ID で引いたことに
        して 0 件を返すより、一致した分だけ出す方が正しい**。
        """
        wanted = {n.strip() for n in names if n and n.strip()}
        if not wanted:
            return []
        out = []
        for node in await self.progress_repo.list_nodes(guild_id):
            assignee = str(node.get("assignee") or "").strip()
            if assignee and assignee in wanted and str(node.get("status") or "") != STATUS_DONE:
                out.append(node)
        return out

    async def layer_summary(self, guild_id: int, user_id: str) -> tuple[int, int]:
        """今月の (層数, 分)。記録が無ければ (0, 0)。"""
        records = await self.session_repo.list_records(guild_id)
        stats = aggregate_layer_stats(
            [r for r in records if str(r["user_id"]) == str(user_id)],
            {},
            since=period_start(PERIOD_MONTH, now()),
        )
        if not stats.members:
            return 0, 0
        member = stats.members[0]
        return member.layers, member.minutes

    # ------------------------------------------------------------------
    # コマンド
    # ------------------------------------------------------------------
    @app_commands.command(name="me", description="自分のタスク・投票・積層・担当をまとめて表示します。")
    @app_commands.describe(user="他の人の分を見る（班長以上のみ）")
    @require(Level.L1)
    async def me(self, interaction: discord.Interaction, user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        target = user or interaction.user
        if not await self.may_view(interaction, str(target.id)):
            await interaction.followup.send(
                embed=error_embed(
                    "他の人のサマリーは**班長以上**が見られます。\n"
                    "`user` を付けずに実行すると自分の分を表示します。"
                ),
                ephemeral=True,
            )
            return

        user_id = str(target.id)
        unanswered = await self.unanswered_schedules(guild_id, user_id)
        layers, minutes = await self.layer_summary(guild_id, user_id)

        member = await self.member_repo.get_member(guild_id, user_id)
        names = {target.display_name}
        if member and member.get("display_name"):
            names.add(str(member["display_name"]))
        nodes = await self.assigned_nodes(guild_id, names)

        if not unanswered and not nodes and layers == 0:
            await interaction.followup.send(
                embed=empty_state_embed(
                    f"{target.display_name} さんのサマリー",
                    "未回答の投票・今月の積層記録・担当ノードのいずれもありません。",
                    "/schedule create",
                ),
                ephemeral=True,
            )
            return

        embed = info_embed(f"{target.display_name} さんのサマリー")

        if unanswered:
            lines = []
            for s in unanswered[:SECTION_LIMIT]:
                try:
                    when = fmt_jp(from_iso(str(s["deadline"])))
                except (TypeError, ValueError):
                    when = str(s.get("deadline") or "—")
                lines.append(f"・{s['title']}（締切 {when}）")
            if len(unanswered) > SECTION_LIMIT:
                lines.append(f"…ほか {len(unanswered) - SECTION_LIMIT} 件")
            embed.add_field(
                name=f"未回答の投票（{len(unanswered)}）", value="\n".join(lines), inline=False
            )
        else:
            embed.add_field(name="未回答の投票", value="なし", inline=False)

        embed.add_field(
            name="今月の積層",
            value=f"{layers} 層 / {_fmt_minutes(minutes)}" if layers else "記録なし",
            inline=False,
        )

        if nodes:
            lines = [
                f"・{n['name']}（{n.get('status') or '—'}）" for n in nodes[:SECTION_LIMIT]
            ]
            if len(nodes) > SECTION_LIMIT:
                lines.append(f"…ほか {len(nodes) - SECTION_LIMIT} 件（`/progress view`）")
            embed.add_field(
                name=f"担当中の進捗ノード（{len(nodes)}）", value="\n".join(lines), inline=False
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Me(bot))
