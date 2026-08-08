"""
Progress コグ（機体進捗管理・Google Sheets 正本）

機体 → パーツ → 部品 → サブタスク …と深さ無制限のツリーで管理される
進捗を、Discord 上でドリルダウン表示する。データの正本は
ギルドごとに設定された Google Sheets（中央スプレッドシート）。

- /progress view  : ドリルダウン表示（全員）
- /progress setup : Todoist プロジェクトを進捗ツリーに紐付ける
                    セルフサービス登録ウィザード（班長以上。
                    .env 編集・bot 再起動なしで新チームを追加できる）
- /progress sync  : Todoist 同期＋再集計を即時実行（管理者）
- /progress init  : スプレッドシート ID の登録とシートの初期化（管理者）
- 定期同期        : 20分ごとに全ギルドの Todoist 同期＋再集計。
                    エラーは各ギルドの #bot-log へ通知する
"""
from __future__ import annotations

import asyncio
import io
from datetime import time as dtime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

# タスク一覧の整形は既存の Reminders 通知とスタイルを揃える
from cogs.reminders import _build_grouped_description

from services import progress_sheet_service as pss
from services import progress_sync_service
from services.progress_sheet_service import ProgressSheetUnavailable
from services.progress_tree import ProgressNode, ProgressTree
from services.todoist_service import TodoistError
from config import config
from utils import progress_chart
from utils.embeds import error_embed, info_embed, success_embed, task_embed
from utils.logger import get_logger
from utils.parser import TZ, now
from utils.permissions import Level, ensure_guild, is_admin, require

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("progress")

SYNC_INTERVAL_MINUTES = 20
CHART_FILENAME = "progress.png"

_NOT_CONFIGURED_DESC = (
    "このサーバーでは進捗管理シートが未設定です。\n"
    "管理者が `/progress init` でスプレッドシート ID を登録してください。"
)


def _todoist_task_url(task_id: str) -> str:
    return f"https://app.todoist.com/app/task/{task_id}"


# ---------------------------------------------------------------------
# 表示用ヘルパー（純粋関数。テスト対象）
# ---------------------------------------------------------------------
def breadcrumb(tree: ProgressTree, node_id: str | None) -> str:
    """`本機 > 主翼 > リブ` 形式のパンくずリストを返す。ルート一覧は「機体一覧」。"""
    if node_id is None:
        return "機体一覧"
    names: list[str] = []
    seen: set[str] = set()
    current = tree.by_id.get(node_id)
    while current is not None and current.node_id not in seen:
        seen.add(current.node_id)
        names.append(current.name or current.node_id)
        current = (tree.by_id.get(current.parent_id)
                   if current.parent_id else None)
    return " > ".join(reversed(names))


def child_nodes(tree: ProgressTree, node_id: str | None) -> list[ProgressNode]:
    """指定ノードの子一覧（node_id=None ならルート一覧）を返す。"""
    if node_id is None:
        return tree.roots
    node = tree.by_id.get(node_id)
    return node.children if node else []


def percent(node: ProgressNode) -> str:
    return f"{(node.aggregated or 0.0) * 100:.0f}%"


def build_level_embed(tree: ProgressTree,
                      node_id: str | None) -> discord.Embed:
    """現在の階層（子ノード一覧 or 葉の詳細）の Embed を組み立てる。"""
    title = f"📊 {breadcrumb(tree, node_id)}"
    children = child_nodes(tree, node_id)
    node = tree.by_id.get(node_id) if node_id else None

    if node is not None and not children:
        # 葉ノード: 詳細表示
        embed = info_embed(title, f"進捗率: **{percent(node)}**")
        embed.add_field(name="担当者", value=node.assignee or "—", inline=True)
        embed.add_field(name="状態", value=node.status or "—", inline=True)
        embed.add_field(
            name="ソース",
            value=("Todoist" if node.source == pss.SOURCE_TODOIST else "手入力"),
            inline=True)
        if node.todoist_task_id:
            embed.add_field(
                name="Todoist",
                value=f"[タスクを開く]({_todoist_task_url(node.todoist_task_id)})",
                inline=False)
        return embed

    desc = f"全体進捗: **{percent(node)}**" if node is not None else ""
    embed = info_embed(title, desc)
    for child in children[:25]:
        detail = []
        if child.assignee:
            detail.append(f"担当: {child.assignee}")
        if child.status:
            detail.append(f"状態: {child.status}")
        if child.children:
            detail.append(f"内訳 {len(child.children)} 件")
        embed.add_field(
            name=f"{child.name or child.node_id} — {percent(child)}",
            value=" / ".join(detail) or "​",  # 空はゼロ幅スペース
            inline=False)
    if len(children) > 25:
        embed.set_footer(text=f"他 {len(children) - 25} 件（シートを参照）")
    if children:
        embed.set_image(url=f"attachment://{CHART_FILENAME}")
    return embed


def chart_items(tree: ProgressTree,
                node_id: str | None) -> list[tuple[str, float]]:
    """グラフ描画用の (名前, 進捗率) 一覧を返す。"""
    return [(c.name or c.node_id, c.aggregated or 0.0)
            for c in child_nodes(tree, node_id)[:25]]


# ---------------------------------------------------------------------
# /progress setup ウィザード用ヘルパー（純粋関数。テスト対象）
# ---------------------------------------------------------------------
def unmapped_projects(projects: list, mappings: list[dict]) -> list:
    """まだ対応表に登録されていない Todoist プロジェクトの一覧を返す。"""
    mapped_names = {m["project_name"] for m in mappings}
    return [p for p in projects
            if getattr(p, "name", "") not in mapped_names]


def anchor_candidates(tree: ProgressTree,
                      max_depth: int = 1) -> list[ProgressNode]:
    """紐付け先候補ノード（深さ max_depth 以下）を表示順で返す。

    機体（深さ0）とパーツ（深さ1）を候補にする。表示順はツリーの
    行きがけ順（機体 → その配下のパーツ → 次の機体 → …）。
    """
    out: list[ProgressNode] = []

    def walk(node: ProgressNode):
        if (node.depth or 0) > max_depth:
            return
        out.append(node)
        for child in node.children:
            walk(child)

    for root in tree.roots:
        walk(root)
    return out


def new_part_row(project_id: str, project_name: str, root_id: str,
                 now_text: str) -> list:
    """「新規パーツとして追加」時に進捗管理シートへ追加する行。

    ID は `pj_<TodoistプロジェクトID>`（一意・安定）。パーツ名は
    プロジェクト名をそのまま使う（後からシートで自由に変更できる）。
    """
    return [f"pj_{project_id}", root_id, "", "", project_name,
            "", "", "", "", "", pss.SOURCE_MANUAL, "", now_text]


def due_items(tasks: list, until, category: str) -> list[dict]:
    """通知対象タスク（期限が until 以前。超過含む）を整形して返す。

    _build_grouped_description が受け取る item 形式に合わせる。
    期限なし・until より先のタスクは除外する。
    """
    items = []
    for t in tasks:
        due = getattr(t, "due", None)
        raw = getattr(due, "date", None) if due else None
        if raw is None:
            continue
        due_date = raw.date() if hasattr(raw, "date") else raw
        if due_date > until:
            continue
        raw_pr = getattr(t, "priority", None)
        pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
        items.append({
            "due_date": due_date,
            "title": t.content,
            "priority": pr_int,
            "url": _todoist_task_url(t.id),
            "category": category,
        })
    return items


# ---------------------------------------------------------------------
# ドリルダウン View
# ---------------------------------------------------------------------
class ProgressView(discord.ui.View):
    """階層ドリルダウン用の View。

    「選択ノードの子を取得して表示」を再帰的に繰り返すだけの実装で、
    階層数のハードコードは無い。ツリーはコマンド実行時に読み込んだものを
    View 内に保持する（🔄 ボタンで再読込できる）。
    """

    def __init__(self, cog: Progress, tree: ProgressTree,
                 node_id: str | None, owner_id: int, guild_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.tree = tree
        self.node_id = node_id
        self.owner_id = owner_id
        self.guild_id = guild_id
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        children = child_nodes(self.tree, self.node_id)
        if children:
            options = [
                discord.SelectOption(
                    label=(c.name or c.node_id)[:100],
                    value=c.node_id,
                    description=f"進捗 {percent(c)}"[:100],
                )
                for c in children[:25]
            ]
            select = discord.ui.Select(
                placeholder="ノードを選択して詳細を表示", options=options)
            select.callback = self._on_select
            self.add_item(select)

        back = discord.ui.Button(
            label="戻る", emoji="⬆️", style=discord.ButtonStyle.secondary,
            disabled=self.node_id is None)
        back.callback = self._on_back
        self.add_item(back)

        reload_btn = discord.ui.Button(
            label="再読込", emoji="🔄", style=discord.ButtonStyle.secondary)
        reload_btn.callback = self._on_reload
        self.add_item(reload_btn)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この表示はコマンドを実行した本人のみ操作できます。"
                "`/progress view` で自分の表示を開いてください。", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.node_id = interaction.data["values"][0]
        await self._render(interaction)

    async def _on_back(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        node = self.tree.by_id.get(self.node_id) if self.node_id else None
        self.node_id = node.parent_id if node else None
        await self._render(interaction)

    async def _on_reload(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await interaction.response.defer()
        try:
            self.tree = await self.cog.load_tree(self.guild_id)
        except Exception as e:  # noqa: BLE001
            log.warning("進捗ツリー再読込失敗 (guild=%s): %s",
                        self.guild_id, type(e).__name__)
            await interaction.followup.send(
                embed=error_embed("シートの再読込に失敗しました。"), ephemeral=True)
            return
        if self.node_id and self.node_id not in self.tree.by_id:
            self.node_id = None  # 再読込でノードが消えていたらルートへ
        await self._render(interaction, deferred=True)

    async def _render(self, interaction: discord.Interaction,
                      deferred: bool = False) -> None:
        self._rebuild_items()
        embed = build_level_embed(self.tree, self.node_id)
        attachments = []
        items = chart_items(self.tree, self.node_id)
        if items:
            png = await asyncio.to_thread(
                progress_chart.render_progress_bars, items)
            attachments.append(discord.File(
                io.BytesIO(png), filename=CHART_FILENAME))
        if deferred:
            await interaction.edit_original_response(
                embed=embed, attachments=attachments, view=self)
        else:
            await interaction.response.edit_message(
                embed=embed, attachments=attachments, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


# ---------------------------------------------------------------------
# /progress setup ウィザード View
# ---------------------------------------------------------------------
class ProjectSetupWizard(discord.ui.View):
    """Todoist プロジェクトを進捗ツリーへ紐付けるセルフサービスウィザード。

    ステップ: ①プロジェクト選択 → ②紐付け先ノード選択
    （「新規パーツとして追加」を選ぶと ②b で機体を選択）→
    ③通知先選択（専用チャンネル or 共通）→ 対応表へ append。
    すべて ephemeral メッセージ内で完結し、.env 編集・再起動は発生しない。
    """

    NEW_PART = "__new_part__"

    def __init__(self, cog: Progress, guild_id: int, owner_id: int,
                 spreadsheet_id: str, projects: list, tree: ProgressTree):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.spreadsheet_id = spreadsheet_id
        self.projects = projects          # 未登録プロジェクトのみ
        self.tree = tree
        # 選択状態
        self.project_id: str | None = None
        self.project_name: str | None = None
        self.anchor_id: str | None = None
        self.new_part_root_id: str | None = None
        self.step = "project"
        self._build()

    # ---------- ステップごとの UI 構築 ----------
    def _build(self) -> None:
        self.clear_items()
        if self.step == "project":
            options = [
                discord.SelectOption(
                    label=str(getattr(p, "name", p.id))[:100],
                    value=str(p.id))
                for p in self.projects[:25]
            ]
            select = discord.ui.Select(
                placeholder="紐付ける Todoist プロジェクトを選択",
                options=options)
            select.callback = self._on_project
            self.add_item(select)
        elif self.step == "anchor":
            options = [discord.SelectOption(
                label="➕ 新規パーツとして追加", value=self.NEW_PART,
                description="プロジェクト名のパーツを機体の下に作成")]
            for node in anchor_candidates(self.tree)[:24]:
                indent = "└ " if (node.depth or 0) > 0 else ""
                options.append(discord.SelectOption(
                    label=f"{indent}{node.name or node.node_id}"[:100],
                    value=node.node_id,
                    description=f"ID: {node.node_id}"[:100]))
            select = discord.ui.Select(
                placeholder="紐付け先ノード（機体 or パーツ）を選択",
                options=options)
            select.callback = self._on_anchor
            self.add_item(select)
        elif self.step == "root":
            options = [
                discord.SelectOption(
                    label=(r.name or r.node_id)[:100], value=r.node_id)
                for r in self.tree.roots[:25]
            ]
            select = discord.ui.Select(
                placeholder="新規パーツを追加する機体を選択", options=options)
            select.callback = self._on_root
            self.add_item(select)
        elif self.step == "notify":
            channel_select = discord.ui.ChannelSelect(
                placeholder="①このプロジェクト専用の通知チャンネルを選択",
                channel_types=[discord.ChannelType.text])
            channel_select.callback = self._on_channel
            self.add_item(channel_select)
            common = discord.ui.Button(
                label="②共通の通知チャンネルにまとめる",
                style=discord.ButtonStyle.primary)
            common.callback = self._on_common
            self.add_item(common)

    # ---------- コールバック ----------
    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このウィザードはコマンドを実行した本人のみ操作できます。",
                ephemeral=True)
            return False
        return True

    async def _on_project(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.project_id = interaction.data["values"][0]
        self.project_name = next(
            (str(getattr(p, "name", ""))
             for p in self.projects if str(p.id) == self.project_id),
            self.project_id)
        self.step = "anchor"
        self._build()
        await interaction.response.edit_message(
            embed=info_embed(
                "紐付け先の選択",
                f"プロジェクト「{self.project_name}」のタスクを"
                "どのノードの下にぶら下げますか？"),
            view=self)

    async def _on_anchor(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        value = interaction.data["values"][0]
        if value == self.NEW_PART:
            if len(self.tree.roots) == 1:
                # 機体が1つだけなら選択ステップを省略
                self.new_part_root_id = self.tree.roots[0].node_id
                self.anchor_id = f"pj_{self.project_id}"
                self.step = "notify"
            else:
                self.step = "root"
        else:
            self.anchor_id = value
            self.step = "notify"
        self._build()
        await interaction.response.edit_message(
            embed=self._step_embed(), view=self)

    async def _on_root(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.new_part_root_id = interaction.data["values"][0]
        self.anchor_id = f"pj_{self.project_id}"
        self.step = "notify"
        self._build()
        await interaction.response.edit_message(
            embed=self._step_embed(), view=self)

    def _step_embed(self) -> discord.Embed:
        if self.step == "root":
            return info_embed("機体の選択",
                              "新規パーツをどの機体の下に追加しますか？")
        return info_embed(
            "通知先の選択",
            f"プロジェクト「{self.project_name}」のタスク通知を"
            "どこへ送りますか？\n"
            "① 専用チャンネル: 下のメニューから選択\n"
            "② 共通チャンネル: ボタンを押す（「設定」タブの"
            "デフォルト通知チャンネルへ送られます）")

    async def _on_channel(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        channel_id = str(interaction.data["values"][0])
        await self._finish(interaction, channel_id)

    async def _on_common(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        await self._finish(interaction, "")

    # ---------- 完了処理 ----------
    async def _finish(self, interaction: discord.Interaction,
                      notify_channel_id: str) -> None:
        await interaction.response.defer()
        client = self.cog._client()
        now_text = progress_sync_service._now_text()
        try:
            if self.new_part_root_id is not None:
                await asyncio.to_thread(
                    client.append_progress_rows, self.spreadsheet_id,
                    [new_part_row(self.project_id, self.project_name,
                                  self.new_part_root_id, now_text)])
            await asyncio.to_thread(
                client.append_mapping_row, self.spreadsheet_id,
                self.project_name, self.anchor_id, notify_channel_id)
        except Exception as e:  # noqa: BLE001  (gspread の API エラー等)
            log.warning("対応表への登録失敗 (guild=%s): %s",
                        self.guild_id, type(e).__name__)
            await interaction.edit_original_response(
                embed=error_embed("シートへの登録に失敗しました。"
                                  "時間をおいて再試行してください。"),
                view=None)
            return

        notify_disp = (f"<#{notify_channel_id}>" if notify_channel_id
                       else "共通チャンネル（設定タブのデフォルト）")
        desc = (f"プロジェクト: {self.project_name}\n"
                f"紐付け先ノード: `{self.anchor_id}`"
                + ("（新規パーツとして追加）" if self.new_part_root_id else "")
                + f"\n通知先: {notify_disp}\n\n"
                f"{SYNC_INTERVAL_MINUTES} 分ごとの自動同期でタスクが"
                "取り込まれます（`/progress sync` で即時実行）。")
        await interaction.edit_original_response(
            embed=success_embed("プロジェクトを登録しました", desc),
            view=None)
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


# ---------------------------------------------------------------------
# コグ本体
# ---------------------------------------------------------------------
class Progress(commands.Cog):
    """機体進捗管理コグ。client_factory 注入でテスト可能。"""

    group = app_commands.Group(
        name="progress", description="機体進捗管理（Google Sheets 正本）")

    def __init__(self, bot: commands.Bot, client_factory=None):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self._client_factory = client_factory or pss.ProgressSheetClient

    def _client(self) -> pss.ProgressSheetClient:
        return self._client_factory()

    async def cog_load(self):
        self.periodic_sync.start()
        self.daily_project_notify.start()

    async def cog_unload(self):
        self.periodic_sync.cancel()
        self.daily_project_notify.cancel()

    # ---------- 共通処理 ----------
    async def load_tree(self, guild_id: int) -> ProgressTree:
        """シートを読み込んでツリーを構築する（書き戻しはしない）。"""
        spreadsheet_id = await progress_sync_service.get_spreadsheet_id(
            self.db, guild_id)
        if spreadsheet_id is None:
            raise ProgressSheetUnavailable(_NOT_CONFIGURED_DESC)
        client = self._client()
        grid = await asyncio.to_thread(
            client.read_progress_grid, spreadsheet_id)
        from services.progress_tree import build_and_aggregate
        return build_and_aggregate(pss.grid_to_nodes(grid))

    async def _run_sync(self, guild_id: int):
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        return await progress_sync_service.sync_guild(
            self.db, guild_id, svc, self._client())

    # ---------- 定期同期（20分ごと） ----------
    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def periodic_sync(self):
        for guild in list(self.bot.guilds):
            try:
                result = await self._run_sync(guild.id)
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("進捗定期同期失敗 (guild=%s): %s",
                            guild.id, type(e).__name__)
                continue
            if result and result.errors:
                lines = "\n".join(f"- {e}" for e in result.errors[:10])
                await self.bot.log_to_channel(
                    f"[進捗同期] シートに問題があります:\n{lines}",
                    guild_id=guild.id)

    @periodic_sync.before_loop
    async def _before_sync(self):
        await self.bot.wait_until_ready()

    # ---------- 毎朝 08:30: プロジェクト別タスク通知 ----------
    @tasks.loop(time=dtime(hour=8, minute=30, tzinfo=TZ))
    async def daily_project_notify(self):
        for guild in list(self.bot.guilds):
            try:
                await self.push_project_tasks(guild.id)
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("プロジェクト別通知失敗 (guild=%s): %s",
                            guild.id, type(e).__name__)

    @daily_project_notify.before_loop
    async def _before_notify(self):
        await self.bot.wait_until_ready()

    async def push_project_tasks(self, guild_id: int) -> int:
        """対応表の各プロジェクトの期限タスク（7日以内・超過）を通知する。

        送信先は対応表の通知チャンネルID →「設定」タブのデフォルト →
        ギルドのタスクチャンネルの順で解決する。送信件数を返す。
        """
        spreadsheet_id = await progress_sync_service.get_spreadsheet_id(
            self.db, guild_id)
        if spreadsheet_id is None:
            return 0
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            return 0

        client = self._client()
        mappings = pss.parse_mapping_grid(await asyncio.to_thread(
            client.read_mapping_grid, spreadsheet_id))
        if not mappings:
            return 0
        sheet_settings = pss.parse_settings_grid(await asyncio.to_thread(
            client.read_settings_grid, spreadsheet_id))

        projects = await svc.get_projects()
        project_ids = {getattr(p, "name", ""): str(p.id) for p in projects}

        gconf = await config.for_guild(guild_id)
        today = now().date()
        until = today + timedelta(days=7)
        sent = 0

        for m in mappings:
            project_id = project_ids.get(m["project_name"])
            if project_id is None:
                continue
            try:
                proj_tasks = await svc.get_tasks(project_id=project_id)
            except TodoistError:
                log.warning("プロジェクト %s のタスク取得失敗 (guild=%s)",
                            m["project_name"], guild_id)
                continue
            items = due_items(proj_tasks, until, m["project_name"])
            if not items:
                continue

            channel_id = progress_sync_service.resolve_notify_channel_id(
                m, sheet_settings) or gconf.default_task_channel_id
            channel = (self.bot.get_channel(channel_id)
                       if channel_id else None)
            if channel is None:
                await self.bot.log_to_channel(
                    f"[進捗通知] 送信先チャンネルがありません"
                    f"（{m['project_name']}）。対応表の通知チャンネルID か"
                    "「設定」タブのデフォルト通知チャンネルID を設定してください。",
                    guild_id=guild_id)
                continue

            desc = _build_grouped_description(
                today, until, "今日から7日以内", items)
            embed = task_embed(f"【進捗・{m['project_name']}】期限タスク")
            embed.description = desc[:4096]
            try:
                await channel.send(embed=embed)
                sent += 1
            except discord.HTTPException as e:
                await self.bot.log_to_channel(
                    f"[進捗通知] 送信失敗（{m['project_name']}）: {e}",
                    guild_id=guild_id)
        return sent

    # ---------- /progress init ----------
    @group.command(
        name="init",
        description="進捗管理シートを登録・初期化します（管理者）。")
    @app_commands.describe(
        spreadsheet_id="スプレッドシートの ID（URL の /d/ と /edit の間の文字列）")
    @app_commands.check(is_admin)
    async def progress_init(self, interaction: discord.Interaction,
                            spreadsheet_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        spreadsheet_id = spreadsheet_id.strip()
        if not spreadsheet_id:
            await interaction.followup.send(
                embed=error_embed("スプレッドシート ID を指定してください。"),
                ephemeral=True)
            return
        client = self._client()
        try:
            created = await asyncio.to_thread(
                client.setup_book, spreadsheet_id)
        except ProgressSheetUnavailable as e:
            await interaction.followup.send(
                embed=error_embed(f"Sheets 連携を実行できません: {e}"),
                ephemeral=True)
            return
        except Exception as e:  # noqa: BLE001  (gspread の API エラー等)
            log.warning("進捗シート初期化失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed(
                    "シートの初期化に失敗しました。\n"
                    "サービスアカウントを「編集者」として共有しているか、"
                    "シート ID が正しいかを確認してください。"),
                ephemeral=True)
            return

        from repositories.settings_repository import SettingsRepository
        await SettingsRepository(self.db).set(
            guild_id, pss.SETTINGS_KEY, spreadsheet_id)

        new_sheets = [name for name, is_new in created.items() if is_new]
        desc = (f"ID: `{spreadsheet_id}`\n"
                + (f"作成したシート: {', '.join(new_sheets)}\n" if new_sheets
                   else "既存のシート構成を確認しました。\n")
                + "`進捗管理` シートに機体・パーツの行を追加し、"
                  "Todoist 連携する場合は `Todoist対応表` に"
                  "プロジェクト名と紐付け先ノード ID を記入してください。\n"
                  f"同期は {SYNC_INTERVAL_MINUTES} 分ごとに自動実行されます"
                  "（`/progress sync` で即時実行）。")
        await interaction.followup.send(
            embed=success_embed("進捗管理シートを登録しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- /progress sync ----------
    @group.command(
        name="sync",
        description="Todoist 同期と進捗の再集計を今すぐ実行します（管理者）。")
    @app_commands.check(is_admin)
    async def progress_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            result = await self._run_sync(guild_id)
        except ProgressSheetUnavailable as e:
            await interaction.followup.send(
                embed=error_embed(f"Sheets 連携を実行できません: {e}"),
                ephemeral=True)
            return
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("Todoist の取得に失敗しました。",
                                  code="TODOIST_API_FAILED"),
                ephemeral=True)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("進捗手動同期失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed("シートの読み書きに失敗しました。"),
                ephemeral=True)
            return

        if result is None:
            await interaction.followup.send(
                embed=info_embed("進捗管理は未設定です", _NOT_CONFIGURED_DESC),
                ephemeral=True)
            return
        desc = (f"対象プロジェクト: {result.projects} 件\n"
                f"追加 {result.added} 行 / 更新 {result.updated} 行 / "
                f"完了 {result.completed} 行")
        if result.errors:
            desc += "\n\n⚠️ 警告:\n" + "\n".join(
                f"- {e}" for e in result.errors[:10])
        await interaction.followup.send(
            embed=success_embed("進捗同期を実行しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- /progress setup ----------
    @group.command(
        name="setup",
        description="Todoist プロジェクトを進捗ツリーに紐付けます（班長以上）。")
    @require(Level.L2)
    async def progress_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        spreadsheet_id = await progress_sync_service.get_spreadsheet_id(
            self.db, guild_id)
        if spreadsheet_id is None:
            await interaction.followup.send(
                embed=info_embed("進捗管理は未設定です", _NOT_CONFIGURED_DESC),
                ephemeral=True)
            return

        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            await interaction.followup.send(
                embed=info_embed(
                    "Todoist 未設定",
                    "このサーバーでは Todoist が未設定です。\n"
                    "管理者が `/todoist-setup` で登録してください。"),
                ephemeral=True)
            return

        client = self._client()
        try:
            projects = await svc.get_projects()
            mapping_grid = await asyncio.to_thread(
                client.read_mapping_grid, spreadsheet_id)
            tree = await self.load_tree(guild_id)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("Todoist プロジェクトの取得に失敗しました。",
                                  code="TODOIST_API_FAILED"),
                ephemeral=True)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("setup ウィザード準備失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed("シートの読み込みに失敗しました。"),
                ephemeral=True)
            return

        candidates = unmapped_projects(
            projects, pss.parse_mapping_grid(mapping_grid))
        if not candidates:
            await interaction.followup.send(
                embed=info_embed(
                    "登録できるプロジェクトがありません",
                    "すべての Todoist プロジェクトが登録済みか、"
                    "プロジェクトが存在しません。\n"
                    "別ワークスペースのプロジェクトを使う場合は、"
                    "bot 用 Todoist アカウントへの共有を依頼してください。"),
                ephemeral=True)
            return
        if not tree.roots:
            await interaction.followup.send(
                embed=info_embed(
                    "機体が未登録です",
                    "`進捗管理` シートに機体（親 ID 空欄）の行を"
                    "先に追加してください。"),
                ephemeral=True)
            return

        view = ProjectSetupWizard(self, guild_id, interaction.user.id,
                                  spreadsheet_id, candidates, tree)
        await interaction.followup.send(
            embed=info_embed(
                "プロジェクト登録ウィザード",
                "紐付ける Todoist プロジェクトを選択してください。\n"
                "（登録済みのプロジェクトは表示されません）"),
            view=view, ephemeral=True)

    # ---------- /progress view ----------
    @group.command(
        name="view",
        description="機体製作の進捗をドリルダウン表示します。")
    @require(Level.L1)
    async def progress_view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            tree = await self.load_tree(guild_id)
        except ProgressSheetUnavailable as e:
            await interaction.followup.send(
                embed=info_embed("進捗管理は未設定です", str(e)),
                ephemeral=True)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("進捗ツリー読込失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed(
                    "進捗シートの読み込みに失敗しました。\n"
                    "サービスアカウントの共有設定とシート ID を確認してください。"),
                ephemeral=True)
            return

        if not tree.roots:
            await interaction.followup.send(
                embed=info_embed(
                    "進捗データがありません",
                    "`進捗管理` シートに機体（親 ID 空欄）の行を追加してください。"),
                ephemeral=True)
            return

        embed = build_level_embed(tree, None)
        files = []
        items = chart_items(tree, None)
        if items:
            png = await asyncio.to_thread(
                progress_chart.render_progress_bars, items)
            files.append(discord.File(io.BytesIO(png),
                                      filename=CHART_FILENAME))
        view = ProgressView(self, tree, None, interaction.user.id, guild_id)
        await interaction.followup.send(embed=embed, files=files, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Progress(bot))
