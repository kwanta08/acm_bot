"""
Sheets モジュール（仕様 11.7）。

Google Sheets への同期。全行置換が基本、監査ログのみ append。
同期中フラグで二重書き込みを防止する。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import INITIAL_TEAMS, config
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from services.sheets_service import SheetsError
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, fmt_sheet, from_iso, now
from utils.permissions import Level, require

log = get_logger("sheets")

TEAM_NAME = {key: name for key, name in INITIAL_TEAMS}
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}

TASK_HEADER = ["ローカルID", "TodoistID", "タスク名", "担当者", "関連班", "期限",
               "優先度", "ステータス", "作成者", "作成日時", "完了日時"]
ATT_HEADER = ["schedule_id", "イベント", "候補日時", "ユーザー", "結果", "締切", "集計時刻"]
MEM_HEADER = ["ユーザーID", "表示名", "主所属班", "副所属班", "班長", "技能", "入部日", "在籍"]


class Sheets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.task_repo = TaskRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)
        self.schedule_repo = ScheduleRepository(bot.db)

    group = app_commands.Group(name="sheets", description="Google Sheets 同期")

    def _disabled_embed(self):
        return info_embed("Google Sheets 無効",
                          "credentials.json と SPREADSHEET_ID を設定すると有効化されます。")

    # ---------- sync all ----------
    @group.command(name="sync-all", description="全シートを一括同期します。")
    @require(Level.L3)
    async def sync_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        if not self.bot.sheets.begin_sync():
            await interaction.followup.send(
                embed=info_embed("同期中", "別の同期が実行中です。"), ephemeral=True)
            return
        try:
            t = await self.sync_tasks()
            m = await self.sync_members()
            a = await self.sync_all_attendance()
            s = await self.sync_all_schedule_sheets()   # ★ 追加
        except SheetsError as e:
            await self.bot.log_to_channel(f"[Sheets] sync-all 失敗: {e}")
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。時間をおいて再試行してください。"),
                ephemeral=True)
            return
        finally:
            self.bot.sheets.end_sync()
        await interaction.followup.send(
            embed=success_embed("全シート同期完了",
                                f"タスク: {t} 行 / メンバー: {m} 行 / 出欠: {a} 行 / "
                                f"日程調整シート: {s} 件",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @group.command(name="sync-tasks", description="タスクシートのみ同期します。")
    @require(Level.L2)
    async def sync_tasks_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        try:
            n = await self.sync_tasks()
        except SheetsError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("タスクシート同期完了", f"{n} 行",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @group.command(name="sync-members", description="メンバーシートのみ同期します。")
    @require(Level.L2)
    async def sync_members_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        try:
            n = await self.sync_members()
        except SheetsError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("メンバーシート同期完了", f"{n} 行",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @group.command(name="sync-attendance", description="出欠シートのみ同期します。")
    @require(Level.L2)
    async def sync_attendance_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        try:
            n = await self.sync_all_attendance()
        except SheetsError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("出欠シート同期完了", f"{n} 行",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @group.command(name="sync-schedules", description="日程調整シートを一括同期します。")
    @require(Level.L2)
    async def sync_schedules_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled or not config.schedule_sheets_enabled():
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        try:
            n = await self.sync_all_schedule_sheets()
        except SheetsError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("日程調整シート同期完了", f"{n} 件のシートを更新しました",
                                executor=interaction.user.display_name),
            ephemeral=True)
    
    @group.command(name="sync-layers", description="未反映の桁巻き記録をシートへ再送信します。")
    @require(Level.L2)
    async def sync_layers_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.sheets.enabled:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        try:
            n = await self.sync_layer_records()
        except SheetsError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("桁巻き記録シート同期完了", f"{n} 件を再送信しました",
                                executor=interaction.user.display_name),
            ephemeral=True)


    async def sync_layer_records(self) -> int:
        """synced_flag=0 の記録をすべてシートへ再送信する。"""
        from repositories.layer_session_repository import LayerSessionRepository
        repo = LayerSessionRepository(self.bot.db)
        guild = self.bot.get_guild(config.guild_id) if config.guild_id else None

        unsynced = await repo.list_unsynced()
        count = 0
        for rec in unsynced:
            name = rec["user_id"]
            if guild:
                m = guild.get_member(int(rec["user_id"]))
                if m:
                    name = m.display_name
            row = [rec["layer_num"], name,
                fmt_jp(from_iso(rec["started_at"])), fmt_jp(from_iso(rec["ended_at"])),
                rec["minutes"]]
            await self.bot.sheets.append_layer_row(rec["keta"], row)
            await repo.mark_synced(rec["record_id"])
            count += 1
        return count

    @group.command(name="status", description="最終同期日時と件数を表示します。")
    @require(Level.L1)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        state = "有効" if self.bot.sheets.enabled else "無効"
        await interaction.followup.send(
            embed=info_embed("Sheets 連携状態",
                             f"状態: {state}\nブック ID: `{config.spreadsheet_id or '未設定'}`"),
            ephemeral=True)

    @group.command(name="url", description="スプレッドシートの URL を返します。")
    @require(Level.L1)
    async def url(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not config.spreadsheet_id:
            await interaction.followup.send(embed=self._disabled_embed(), ephemeral=True)
            return
        link = f"https://docs.google.com/spreadsheets/d/{config.spreadsheet_id}"
        await interaction.followup.send(
            embed=info_embed("スプレッドシート", link), ephemeral=True)

    # ====================================================================
    # 同期ロジック（他 Cog / Reminders から呼ばれる）
    # ====================================================================
    async def sync_tasks(self) -> int:
        if not self.bot.sheets.enabled:
            return 0
        tasks = await self.task_repo.list_all_for_export()
        guild = self.bot.get_guild(config.guild_id) if config.guild_id else None
        rows = []
        for t in tasks:
            assignee = ""
            if t.get("assignee_id") and guild:
                m = guild.get_member(int(t["assignee_id"]))
                assignee = m.display_name if m else t["assignee_id"]
            rows.append([
                t["local_task_id"], t.get("todoist_task_id") or "", t["title"], assignee,
                TEAM_NAME.get(t.get("team_key"), t.get("team_key") or ""),
                fmt_jp(from_iso(t["due_date"])) if t.get("due_date") else "",
                t.get("priority") or "", t["status"],
                t["created_by"], fmt_jp(from_iso(t["created_at"])),
                fmt_jp(from_iso(t["completed_at"])) if t.get("completed_at") else "",
            ])
        return await self.bot.sheets.replace_all(config.sheet_tasks, TASK_HEADER, rows)

    async def sync_members(self) -> int:
        if not self.bot.sheets.enabled:
            return 0
        members = await self.member_repo.list_members()
        rows = []
        for m in members:
            rows.append([
                m["user_id"], m["display_name"],
                TEAM_NAME.get(m.get("primary_team"), m.get("primary_team") or ""),
                "、".join(TEAM_NAME.get(t, t) for t in m["secondary_teams"]),
                "○" if m["is_leader"] else "",
                "、".join(m["skills"]),
                fmt_jp(from_iso(m["joined_at"])),
                "在籍" if m["active_flag"] else "退会",
            ])
        return await self.bot.sheets.replace_all(config.sheet_members, MEM_HEADER, rows)

    async def sync_all_attendance(self) -> int:
        """全 schedule の出欠結果を1シートに展開する。"""
        if not self.bot.sheets.enabled:
            return 0
        rows = await self._build_attendance_rows(None)
        return await self.bot.sheets.replace_all(config.sheet_attendance, ATT_HEADER, rows)

    async def sync_attendance_for(self, schedule_id: str) -> int:
        """締切時に呼ばれる。全件再構築（全行置換のため）。"""
        return await self.sync_all_attendance()

    async def _build_attendance_rows(self, only_schedule_id: str | None) -> list[list]:
        guild = self.bot.get_guild(config.guild_id) if config.guild_id else None
        schedules = await self.schedule_repo.list_open_schedules()
        # クローズ済みも含めるため全件取得
        closed = await self.bot.db.fetchall("SELECT * FROM schedules")
        all_sched = {dict(r)["schedule_id"]: dict(r) for r in closed}
        rows = []
        agg_time = fmt_sheet(now())
        for sid, s in all_sched.items():
            if only_schedule_id and sid != only_schedule_id:
                continue
            options = await self.schedule_repo.list_options(sid)
            for opt in options:
                votes = await self.schedule_repo.list_votes(opt["option_id"])
                for v in votes:
                    name = v["user_id"]
                    if guild:
                        mem = guild.get_member(int(v["user_id"]))
                        if mem:
                            name = mem.display_name
                    rows.append([
                        sid, s["title"], opt["label"], name, v["status"],
                        fmt_jp(from_iso(s["deadline"])), agg_time,
                    ])
        return rows
    
    async def sync_all_schedule_sheets(self) -> int:
        """sheet_title が設定済みの全スケジュールを最新状態でシートへ反映する。"""
        if not self.bot.sheets.enabled or not config.schedule_sheets_enabled():
            return 0
        rows = await self.bot.db.fetchall(
            "SELECT * FROM schedules WHERE sheet_title IS NOT NULL")
        guild = self.bot.get_guild(config.guild_id) if config.guild_id else None
        count = 0
        for row in rows:
            schedule = dict(row)
            options = await self.schedule_repo.list_options(schedule["schedule_id"])
            votes_map = {}
            for opt in options:
                votes = await self.schedule_repo.list_votes(opt["option_id"])
                votes_map[opt["option_id"]] = {
                    "ok": [self._display_name(guild, v["user_id"])
                        for v in votes if v["status"] == "ok"],
                    "maybe": [self._display_name(guild, v["user_id"])
                            for v in votes if v["status"] == "maybe"],
                    "ng": [self._display_name(guild, v["user_id"])
                        for v in votes if v["status"] == "ng"],
                    "unanswered": [],
                }
            await self.bot.sheets.update_schedule_sheet(
                schedule["sheet_title"], options, votes_map)
            count += 1
        return count


    def _display_name(self, guild: discord.Guild | None, user_id: str) -> str:
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                return member.display_name
        return f"<@{user_id}>"


async def setup(bot: commands.Bot):
    await bot.add_cog(Sheets(bot))
