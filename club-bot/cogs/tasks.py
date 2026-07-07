"""
Tasks モジュール（仕様 11.3）。

Discord を操作面、Todoist を実タスク管理基盤とする。ローカル DB に
local_task_id ↔ todoist_task_id を対応付ける。/today で「今日やること」
ラベルを完全一致検索で付与する。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import INITIAL_TEAMS, config
from repositories.task_repository import TaskRepository
from services.todoist_service import TodoistError
from utils.embeds import error_embed, info_embed, success_embed, task_embed
from utils.logger import get_logger
from utils.parser import InvalidDatetimeError, fmt_jp, parse_datetime, to_iso
from utils.permissions import Level, require

log = get_logger("tasks")

TEAM_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in INITIAL_TEAMS]
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}


class Tasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = TaskRepository(bot.db)

    group = app_commands.Group(name="task", description="タスク管理（Todoist 連携）")

    # ---------- add ----------
    @group.command(name="add", description="新規タスクを作成します。")
    @app_commands.describe(
        title="タスク名", due="期限（例: 2026-07-05 18:00）", assignee="担当者",
        team="関連班", priority="優先度 1〜4", location="作業拠点", note="補足")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L1)
    async def add(self, interaction: discord.Interaction, title: str,
                  due: str | None = None, assignee: discord.Member | None = None,
                  team: app_commands.Choice[str] | None = None,
                  priority: app_commands.Range[int, 1, 4] | None = None,
                  location: str | None = None, note: str | None = None):
        await interaction.response.defer(ephemeral=True)

        due_iso = None
        due_string = None
        if due:
            due_dt = parse_datetime(due)  # 失敗時 INVALID_DATETIME
            due_iso = to_iso(due_dt)
            due_string = due_dt.strftime("%Y-%m-%d %H:%M")

        team_key = team.value if team else None

        # Todoist 反映（仕様 11.3.3）
        todoist_id = None
        if self.bot.todoist.enabled:
            try:
                content = title
                if team_key:
                    content = f"[{team.name}] {title}"
                todoist_id = await self.bot.todoist.add_task(
                    content=content, due_string=due_string, priority=priority, description=note)
            except TodoistError:
                await interaction.followup.send(
                    embed=error_embed("Todoist への登録に失敗しました。時間をおいて再試行してください。",
                                      code="TODOIST_API_FAILED"),
                    ephemeral=True)
                return

        local_id = await self.repo.create_task(
            title=title, created_by=str(interaction.user.id), todoist_task_id=todoist_id,
            assignee_id=str(assignee.id) if assignee else None, team_key=team_key,
            due_date=due_iso, priority=priority, location_key=location)

        desc = f"ローカル ID: `{local_id}`"
        if todoist_id:
            desc += f"\nTodoist: 連携済み"
        if assignee:
            desc += f"\n担当: {assignee.display_name}"
        if due_iso:
            desc += f"\n期限: {fmt_jp(parse_datetime(due))}"
        embed = success_embed(f"タスクを作成しました: {title}", desc,
                              executor=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)

        await self._sync_tasks_sheet()

    # ---------- list ----------
    @group.command(name="list", description="タスク一覧を表示します。")
    @app_commands.describe(mine="自分の担当のみ表示")
    @require(Level.L1)
    async def list_cmd(self, interaction: discord.Interaction, mine: bool = False):
        await interaction.response.defer(ephemeral=True)
        assignee = str(interaction.user.id) if mine else None
        tasks = await self.repo.list_tasks(status="open", assignee_id=assignee)
        embed = self._build_task_list_embed("タスク一覧", tasks, interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- done ----------
    @group.command(name="done", description="タスクを完了にします。")
    @app_commands.describe(task_id="ローカルタスク ID")
    @require(Level.L1)
    async def done(self, interaction: discord.Interaction, task_id: int):
        await interaction.response.defer(ephemeral=True)
        task = await self.repo.get_task(task_id)
        if not task or task["status"] != "open":
            await interaction.followup.send(
                embed=error_embed("対象の未完了タスクが見つかりません。"), ephemeral=True)
            return
        if task.get("todoist_task_id") and self.bot.todoist.enabled:
            try:
                await self.bot.todoist.close_task(task["todoist_task_id"])
            except TodoistError:
                pass  # ローカルは完了扱いにする
        await self.repo.complete_task(task_id)
        await interaction.followup.send(
            embed=success_embed("完了にしました", f"`{task_id}` {task['title']}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_tasks_sheet()

    # ---------- delete ----------
    @group.command(name="delete", description="タスクを削除します。")
    @app_commands.describe(task_id="ローカルタスク ID")
    @require(Level.L2)
    async def delete(self, interaction: discord.Interaction, task_id: int):
        await interaction.response.defer(ephemeral=True)
        task = await self.repo.get_task(task_id)
        if not task:
            await interaction.followup.send(
                embed=error_embed("対象タスクが見つかりません。"), ephemeral=True)
            return
        if task.get("todoist_task_id") and self.bot.todoist.enabled:
            try:
                await self.bot.todoist.delete_task(task["todoist_task_id"])
            except TodoistError:
                pass
        await self.repo.delete_task(task_id)
        await interaction.followup.send(
            embed=success_embed("削除しました", f"`{task_id}` {task['title']}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_tasks_sheet()

    # ---------- assign ----------
    @group.command(name="assign", description="担当者を変更します。")
    @app_commands.describe(task_id="ローカルタスク ID", assignee="担当者")
    @require(Level.L2)
    async def assign(self, interaction: discord.Interaction, task_id: int,
                     assignee: discord.Member):
        await interaction.response.defer(ephemeral=True)
        task = await self.repo.get_task(task_id)
        if not task:
            await interaction.followup.send(
                embed=error_embed("対象タスクが見つかりません。"), ephemeral=True)
            return
        await self.repo.set_assignee(task_id, str(assignee.id))
        await interaction.followup.send(
            embed=success_embed("担当者を変更しました",
                                f"`{task_id}` → {assignee.display_name}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_tasks_sheet()

    # ---------- priority ----------
    @group.command(name="priority", description="優先度を変更します。")
    @app_commands.describe(task_id="ローカルタスク ID", priority="優先度 1〜4")
    @require(Level.L1)
    async def priority(self, interaction: discord.Interaction, task_id: int,
                       priority: app_commands.Range[int, 1, 4]):
        await interaction.response.defer(ephemeral=True)
        task = await self.repo.get_task(task_id)
        if not task:
            await interaction.followup.send(
                embed=error_embed("対象タスクが見つかりません。"), ephemeral=True)
            return
        await self.repo.set_priority(task_id, priority)
        await interaction.followup.send(
            embed=success_embed("優先度を変更しました",
                                f"`{task_id}` → {PRIORITY_LABELS.get(priority, priority)}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_tasks_sheet()

    # ---------- overdue ----------
    @group.command(name="overdue", description="期限超過タスク一覧を表示します。")
    @require(Level.L1)
    async def overdue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from utils.parser import now
        today = now().date().isoformat()
        tasks = await self.repo.list_overdue(today)
        embed = self._build_task_list_embed("期限超過タスク", tasks, interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- team ----------
    @group.command(name="team", description="班別のタスク一覧を表示します。")
    @app_commands.describe(team="班")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L1)
    async def team(self, interaction: discord.Interaction, team: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        tasks = await self.repo.list_tasks(status="open", team_key=team.value)
        embed = self._build_task_list_embed(f"班別タスク: {team.name}", tasks, interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- sync（L4）----------
    @group.command(name="sync", description="Todoist 同期を再実行します（管理者）。")
    @require(Level.L4)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.todoist.enabled:
            await interaction.followup.send(
                embed=info_embed("Todoist 無効", "TODOIST_API_TOKEN が未設定です。"),
                ephemeral=True)
            return
        try:
            await self.bot.todoist.ensure_label()
            await self._sync_tasks_sheet()
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("同期を実行しました", executor=interaction.user.display_name),
            ephemeral=True)

    # ====================================================================
    # /today コマンド群（仕様 11.3.1, 11.3.3）
    # ====================================================================
    today_group = app_commands.Group(name="today", description="「今日やること」ラベル付与")

    @today_group.command(name="task", description="タスク名で「今日やること」ラベルを付与します。")
    @app_commands.describe(name="完全一致するタスク名")
    @require(Level.L1)
    async def today_task(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.todoist.enabled:
            await interaction.followup.send(
                embed=info_embed("Todoist 無効", "この機能には Todoist 連携が必要です。"),
                ephemeral=True)
            return
        try:
            matches = await self.bot.todoist.find_open_tasks_by_name(name)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("Todoist 検索に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True)
            return

        if not matches:
            await interaction.followup.send(
                embed=error_embed(f"未完了タスク「{name}」が見つかりません。"), ephemeral=True)
            return

        if len(matches) == 1:
            ok = await self.bot.todoist.add_today_label(str(matches[0].id))
            await interaction.followup.send(
                embed=success_embed("ラベルを付与しました",
                                    f"「{name}」に「{config.today_label_name}」を付与",
                                    executor=interaction.user.display_name),
                ephemeral=True)
            return

        # 複数候補 → 候補一覧を実行者にのみ表示（仕様 11.3.3）
        embed = task_embed(f"同名タスクが {len(matches)} 件あります",
                           "`/today id:<ID>` で対象を確定してください。")
        for t in matches:
            section = getattr(t, "section_id", None) or "—"
            due = getattr(getattr(t, "due", None), "string", None) or "期限なし"
            embed.add_field(
                name=f"ID: {t.id}",
                value=f"プロジェクト: {getattr(t, 'project_id', '—')}\n"
                      f"セクション: {section}\n期限: {due}",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @today_group.command(name="id", description="重複候補からID指定でラベルを付与します。")
    @app_commands.describe(task_id="Todoist タスク ID")
    @require(Level.L1)
    async def today_id(self, interaction: discord.Interaction, task_id: str):
        await interaction.response.defer(ephemeral=True)
        if not self.bot.todoist.enabled:
            await interaction.followup.send(
                embed=info_embed("Todoist 無効", "この機能には Todoist 連携が必要です。"),
                ephemeral=True)
            return
        try:
            ok = await self.bot.todoist.add_today_label(task_id)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("ラベル付与に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True)
            return
        if not ok:
            await interaction.followup.send(
                embed=error_embed(f"タスク ID `{task_id}` が見つかりません。"), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("ラベルを付与しました",
                                f"ID `{task_id}` に「{config.today_label_name}」を付与",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ====================================================================
    # 内部ヘルパー
    # ====================================================================
    def _build_task_list_embed(self, title: str, tasks: list[dict],
                               guild: discord.Guild | None) -> discord.Embed:
        if not tasks:
            return info_embed(title, "該当するタスクはありません。")
        embed = task_embed(title)
        for t in tasks[:25]:
            assignee = "未割当"
            if t.get("assignee_id") and guild:
                m = guild.get_member(int(t["assignee_id"]))
                assignee = m.display_name if m else "不明"
            due = fmt_jp(parse_datetime(t["due_date"])) if t.get("due_date") else "期限なし"
            pr = PRIORITY_LABELS.get(t.get("priority"), "—")
            embed.add_field(
                name=f"`{t['local_task_id']}` {t['title']}",
                value=f"担当: {assignee} / 期限: {due} / 優先: {pr}",
                inline=False)
        if len(tasks) > 25:
            embed.set_footer(text=f"他 {len(tasks) - 25} 件")
        return embed

    async def _sync_tasks_sheet(self):
        """タスク変更時に Sheets を更新（有効時のみ・失敗は握りつぶす）。"""
        sheets_cog = self.bot.get_cog("Sheets")
        if sheets_cog:
            try:
                await sheets_cog.sync_tasks()
            except Exception as e:  # noqa: BLE001
                log.warning("タスク Sheets 同期失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tasks(bot))
