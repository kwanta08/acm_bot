"""
Reports モジュール（仕様 11.6）。

週次サマリー、CSV エクスポート、通知ログ（reminders_log）と
操作ログ（audit_log）の閲覧。
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
from repositories.audit_log_repository import AuditLogRepository
from repositories.name_cache_repository import ENTITY_USER, NameCacheRepository
from repositories.progress_repository import ProgressRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from services import team_service
from services.milestone_service import days_until_competition, evaluate_all
from services.progress_tree import load_tree
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    info_embed,
    success_embed,
)
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
        self.audit_repo = AuditLogRepository(bot.db)
        self.name_repo = NameCacheRepository(bot.db)

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

        if not open_tasks and not overdue and not schedules:
            # 全部0件は「健全に運用できている」ではなく「まだ始まっていない」。
            # 0/0/0 のサマリーは両者を見分けられないので空状態として出す
            await interaction.followup.send(
                embed=empty_state_embed(
                    f"{gconf.club_name_or_default} 週次サマリー",
                    "まだデータがありません。`/task add` でタスクを、"
                    "`/schedule create` で日程調整を作成できます。",
                    "/task add",
                ),
                ephemeral=True,
            )
            return

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

    # ---------- notifications (通知ログ) ----------
    #
    # **旧 `/report audit` はこれ。** 読んでいるのは reminders_log
    # （bot が送った通知の記録）で、管理者操作の証跡である audit_log とは
    # 別物だった。証跡側は `/report changes` が読む（G4-3）。
    @group.command(name="notifications", description="直近の通知ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）")
    @require(Level.L3)
    async def notifications(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.log_repo.list_recent(guild_id, limit)
        if not rows:
            # ログは通知（リマインダー等）が動いて初めて溜まる。
            # 何も無い＝まだ通知が発生する運用が始まっていない
            await interaction.followup.send(
                embed=empty_state_embed(
                    "通知ログ",
                    "通知はまだ記録されていません。日程調整やタスクを作ると、"
                    "締切前のリマインドがここに記録されます。",
                    "/schedule create",
                ),
                ephemeral=True,
            )
            return
        embed = info_embed("通知ログ")
        for r in rows:
            d = dict(r)
            embed.add_field(
                name=f"{d['reminder_type']} [{d['status']}]",
                value=f"対象: {d['target_id']} / {fmt_jp(from_iso(d['sent_at']))}"
                + (f"\nエラー: {d['error_message']}" if d.get("error_message") else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- changes (操作ログ / audit_log) ----------
    async def _actor_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """操作ログに実際に出てくる実行者だけを候補に出す。

        ギルドの全メンバーを並べても、そのほとんどは1度も操作していない。
        """
        if interaction.guild is None:
            return []
        actors = await self.audit_repo.list_actors(interaction.guild.id, limit=25)
        names = await self.name_repo.names(interaction.guild.id, ENTITY_USER)
        out: list[app_commands.Choice[str]] = []
        for actor_id in actors:
            label = self._resolve_actor(interaction.guild, names, actor_id)
            if current and current.lower() not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=actor_id))
        return out[:25]

    @staticmethod
    def _resolve_actor(guild, names: dict[str, str], actor_id: str | None) -> str:
        """ID を表示名へ。ギルドキャッシュ → discord_name_cache → ID の順。

        退部した人・bot 再起動直後でも生の ID が並ばないようにする。
        解決できないものは ID のまま出す（伏せると追跡できなくなる）。
        """
        raw = str(actor_id or "")
        if not raw:
            return "（不明）"
        if guild is not None and raw.isdigit():
            member = guild.get_member(int(raw))
            if member is not None:
                return member.display_name
        return names.get(raw, raw)

    @group.command(name="changes", description="設定・マスタ変更の操作ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）", actor="実行者で絞り込む")
    @app_commands.autocomplete(actor=_actor_autocomplete)
    @require(Level.L3)
    async def changes(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
        actor: str | None = None,
    ):
        """audit_log を読む。/report notifications が読む reminders_log とは別物。"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.audit_repo.list_recent(guild_id, limit, actor_id=actor)
        names = await self.name_repo.names(guild_id, ENTITY_USER)
        guild = interaction.guild
        if not rows:
            situation = (
                "この実行者の操作ログはありません。"
                if actor
                else "設定やマスタの変更はまだ記録されていません。"
            )
            await interaction.followup.send(
                embed=empty_state_embed("操作ログ", situation, "/setup"),
                ephemeral=True,
            )
            return
        title = "操作ログ"
        if actor:
            title += f" — {self._resolve_actor(guild, names, actor)}"
        embed = info_embed(title)
        for r in rows:
            d = dict(r)
            parts = [f"実行者: {self._resolve_actor(guild, names, d.get('actor_id'))}"]
            if d.get("target"):
                # target は ID が入ることがある（ロール ID・表#行 ID など）
                parts.append(f"対象: {self._resolve_actor(guild, names, d['target'])}")
            if d.get("detail"):
                parts.append(str(d["detail"])[:300])
            try:
                parts.append(fmt_jp(from_iso(str(d["created_at"]))))
            except (TypeError, ValueError):
                parts.append(str(d.get("created_at")))
            embed.add_field(
                name=str(d["action"])[:250], value="\n".join(parts)[:1024], inline=False
            )
        add_truncation_note(embed, len(rows), MAX_EMBED_FIELDS)
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
            await interaction.followup.send(
                embed=empty_state_embed(
                    "出欠率一覧", "集計対象の投票がありません。", "/schedule create"
                ),
                ephemeral=True,
            )
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
