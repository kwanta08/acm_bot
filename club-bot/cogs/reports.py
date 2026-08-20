"""
Reports モジュール（仕様 11.6）。

週次サマリー、CSV エクスポート、監査ログ閲覧。
出力例: 期限超過タスク一覧、月次出欠率、支援依頼頻度（11.6.2）。
マルチテナント版: 全集計を interaction.guild.id でスコープする。
"""

from __future__ import annotations

import csv
import io

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.progress_repository import ProgressRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from services import team_service
from services.milestone_service import days_until_competition, evaluate_all
from services.progress_tree import load_tree
from utils.embeds import MAX_EMBED_FIELDS, add_truncation_note, info_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso, now
from utils.permissions import Level, ensure_guild, require

log = get_logger("reports")


class Reports(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.task_repo = TaskRepository(bot.db)
        self.schedule_repo = ScheduleRepository(bot.db)
        self.log_repo = RemindersLogRepository(bot.db)

    group = app_commands.Group(name="report", description="集計・エクスポート・監査")

    # ---------- weekly ----------
    @group.command(name="weekly", description="週次サマリーを表示します。")
    @require(Level.L2)
    async def weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        today = now().date().isoformat()
        overdue = await self.task_repo.list_overdue(guild_id, today)
        open_tasks = await self.task_repo.list_tasks(guild_id, status="open")
        schedules = await self.schedule_repo.list_open_schedules(guild_id)

        gconf = await config.for_guild(guild_id, db=self.bot.db)
        embed = info_embed(f"{gconf.club_name_or_default} 週次サマリー")
        embed.add_field(name="未完了タスク", value=str(len(open_tasks)), inline=True)
        embed.add_field(name="期限超過", value=str(len(overdue)), inline=True)
        embed.add_field(name="開催中の投票", value=str(len(schedules)), inline=True)

        # 班別タスク集計
        team_names = await team_service.team_name_map(self.bot.db, guild_id)
        by_team: dict[str, int] = {}
        for t in open_tasks:
            key = t.get("team_key") or "未分類"
            by_team[key] = by_team.get(key, 0) + 1
        if by_team:
            lines = [f"{team_names.get(k, k)}: {v}" for k, v in sorted(by_team.items())]
            embed.add_field(name="班別未完了タスク", value="\n".join(lines), inline=False)

        countdown = await self.countdown_summary(guild_id, gconf)
        if countdown:
            embed.add_field(name="大会まで", value=countdown, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def countdown_summary(self, guild_id: int, gconf) -> str:
        """「大会まで N 日 / 遅延 M 件」の1行。

        大会日が未設定でマイルストーンも無いサーバーでは空文字を返し、
        週次サマリーの表示を変えない。
        """
        today = now().date()
        left = days_until_competition(gconf.competition_date, today)
        repo = ProgressRepository(self.bot.db)
        try:
            rows = await repo.list_milestones(guild_id)
            statuses = (
                evaluate_all(await load_tree(repo, guild_id), rows, today=today) if rows else []
            )
        except Exception as e:  # noqa: BLE001  (週次サマリー全体は止めない)
            log.warning("マイルストーンの集計に失敗 (guild=%s): %s", guild_id, type(e).__name__)
            return ""

        if left is None and not statuses:
            return ""
        parts = []
        if left is None:
            parts.append("大会日: 未設定")
        elif left > 0:
            parts.append(f"残り {left} 日")
        elif left == 0:
            parts.append("本日が大会日")
        else:
            parts.append(f"{-left} 日経過")
        if statuses:
            behind = sum(1 for s in statuses if s.is_behind)
            parts.append(f"遅延 {behind} 件 / 全 {len(statuses)} 件")
        return " / ".join(parts)

    # ---------- export tasks (CSV) ----------
    @group.command(name="export-tasks", description="タスク一覧を CSV で出力します。")
    @require(Level.L2)
    async def export_tasks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tasks = await self.task_repo.list_all_for_export(guild_id)
        team_names = await team_service.team_name_map(self.bot.db, guild_id)
        guild = interaction.guild
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "local_task_id",
                "todoist_task_id",
                "title",
                "assignee",
                "team",
                "due_date",
                "priority",
                "status",
                "created_by",
                "created_at",
                "completed_at",
            ]
        )
        for t in tasks:
            assignee = ""
            if t.get("assignee_id") and guild:
                m = guild.get_member(int(t["assignee_id"]))
                assignee = m.display_name if m else t["assignee_id"]
            writer.writerow(
                [
                    t["local_task_id"],
                    t.get("todoist_task_id") or "",
                    t["title"],
                    assignee,
                    team_names.get(t.get("team_key"), t.get("team_key") or ""),
                    t.get("due_date") or "",
                    t.get("priority") or "",
                    t["status"],
                    t["created_by"],
                    t["created_at"],
                    t.get("completed_at") or "",
                ]
            )
        data = buf.getvalue().encode("utf-8-sig")
        file = discord.File(io.BytesIO(data), filename=f"tasks_{now().strftime('%Y%m%d')}.csv")
        await interaction.followup.send(
            embed=success_embed("タスク CSV を出力しました", f"{len(tasks)} 件"),
            file=file,
            ephemeral=True,
        )

    # ---------- audit (監査ログ) ----------
    @group.command(name="audit", description="直近の通知・監査ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）")
    @require(Level.L3)
    async def audit(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.log_repo.list_recent(guild_id, limit)
        embed = info_embed("監査・通知ログ")
        if not rows:
            embed.description = "ログがありません。"
        for r in rows:
            d = dict(r)
            embed.add_field(
                name=f"{d['reminder_type']} [{d['status']}]",
                value=f"対象: {d['target_id']} / {fmt_jp(from_iso(d['sent_at']))}"
                + (f"\nエラー: {d['error_message']}" if d.get("error_message") else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- attendance rate ----------
    @group.command(name="attendance-rate", description="出欠率一覧を表示します。")
    @require(Level.L2)
    async def attendance_rate(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        all_sched = await self.schedule_repo.list_all(guild_id)
        embed = info_embed("出欠率一覧")
        if not all_sched:
            embed.description = "集計対象の投票がありません。"
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        # 集計は全件で行い、表示だけ Embed の上限に合わせて絞る。
        # 26 件目以降を集計から落とすと、表示されないだけでなく
        # 全体の参加率そのものが誤った数字になる。
        grand_ok = 0
        grand_votes = 0
        for i, s in enumerate(all_sched):
            options = await self.schedule_repo.list_options(guild_id, s["schedule_id"])
            total_ok = 0
            total_votes = 0
            for opt in options:
                votes = await self.schedule_repo.list_votes(guild_id, opt["option_id"])
                total_votes += len(votes)
                total_ok += sum(1 for v in votes if v["status"] == "ok")
            grand_ok += total_ok
            grand_votes += total_votes
            if i >= MAX_EMBED_FIELDS:
                continue
            rate = f"{(total_ok / total_votes * 100):.0f}%" if total_votes else "—"
            embed.add_field(
                name=s["title"],
                value=f"参加率(ok/総票): {rate}（ok {total_ok} / 票 {total_votes}）",
                inline=False,
            )
        overall = f"{(grand_ok / grand_votes * 100):.0f}%" if grand_votes else "—"
        embed.description = (
            f"全 {len(all_sched)} 件の通算参加率: **{overall}**（ok {grand_ok} / 票 {grand_votes}）"
        )
        add_truncation_note(embed, len(all_sched), MAX_EMBED_FIELDS, "通算は全件で計算しています")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reports(bot))
