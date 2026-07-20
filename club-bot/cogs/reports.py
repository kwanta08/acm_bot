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

from config import INITIAL_TEAMS
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso, now
from utils.permissions import Level, ensure_guild, require

log = get_logger("reports")

TEAM_NAME = {key: name for key, name in INITIAL_TEAMS}


class Reports(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.task_repo = TaskRepository(bot.db)
        self.schedule_repo = ScheduleRepository(bot.db)

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

        embed = info_embed("週次サマリー")
        embed.add_field(name="未完了タスク", value=str(len(open_tasks)), inline=True)
        embed.add_field(name="期限超過", value=str(len(overdue)), inline=True)
        embed.add_field(name="開催中の投票", value=str(len(schedules)), inline=True)

        # 班別タスク集計
        by_team: dict[str, int] = {}
        for t in open_tasks:
            key = t.get("team_key") or "未分類"
            by_team[key] = by_team.get(key, 0) + 1
        if by_team:
            lines = [f"{TEAM_NAME.get(k, k)}: {v}" for k, v in sorted(by_team.items())]
            embed.add_field(name="班別未完了タスク", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- export tasks (CSV) ----------
    @group.command(name="export-tasks", description="タスク一覧を CSV で出力します。")
    @require(Level.L2)
    async def export_tasks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tasks = await self.task_repo.list_all_for_export(guild_id)
        guild = interaction.guild
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["local_task_id", "todoist_task_id", "title", "assignee", "team",
                         "due_date", "priority", "status", "created_by", "created_at",
                         "completed_at"])
        for t in tasks:
            assignee = ""
            if t.get("assignee_id") and guild:
                m = guild.get_member(int(t["assignee_id"]))
                assignee = m.display_name if m else t["assignee_id"]
            writer.writerow([
                t["local_task_id"], t.get("todoist_task_id") or "", t["title"], assignee,
                TEAM_NAME.get(t.get("team_key"), t.get("team_key") or ""),
                t.get("due_date") or "", t.get("priority") or "", t["status"],
                t["created_by"], t["created_at"], t.get("completed_at") or "",
            ])
        data = buf.getvalue().encode("utf-8-sig")
        file = discord.File(io.BytesIO(data), filename=f"tasks_{now().strftime('%Y%m%d')}.csv")
        await interaction.followup.send(
            embed=success_embed("タスク CSV を出力しました", f"{len(tasks)} 件"),
            file=file, ephemeral=True)

    # ---------- audit (監査ログ) ----------
    @group.command(name="audit", description="直近の通知・監査ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）")
    @require(Level.L3)
    async def audit(self, interaction: discord.Interaction,
                    limit: app_commands.Range[int, 1, 25] = 10):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM reminders_log WHERE guild_id = ?"
            " ORDER BY reminder_id DESC LIMIT ?",
            (guild_id, limit))
        embed = info_embed("監査・通知ログ")
        if not rows:
            embed.description = "ログがありません。"
        for r in rows:
            d = dict(r)
            embed.add_field(
                name=f"{d['reminder_type']} [{d['status']}]",
                value=f"対象: {d['target_id']} / {fmt_jp(from_iso(d['sent_at']))}"
                      + (f"\nエラー: {d['error_message']}" if d.get("error_message") else ""),
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- attendance rate ----------
    @group.command(name="attendance-rate", description="出欠率一覧を表示します。")
    @require(Level.L2)
    async def attendance_rate(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        all_sched = await self.bot.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ?", (guild_id,))
        embed = info_embed("出欠率一覧")
        if not all_sched:
            embed.description = "集計対象の投票がありません。"
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        for r in all_sched[:25]:
            s = dict(r)
            options = await self.schedule_repo.list_options(guild_id, s["schedule_id"])
            total_yes = 0
            total_votes = 0
            for opt in options:
                votes = await self.schedule_repo.list_votes(guild_id, opt["option_id"])
                total_votes += len(votes)
                total_yes += sum(1 for v in votes if v["status"] == "yes")
            rate = f"{(total_yes / total_votes * 100):.0f}%" if total_votes else "—"
            embed.add_field(
                name=s["title"],
                value=f"参加率(yes/総票): {rate}（yes {total_yes} / 票 {total_votes}）",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reports(bot))
