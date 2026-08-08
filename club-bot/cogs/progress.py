"""
Progress コグ（機体進捗管理・Google Sheets 正本）

機体 → パーツ → 部品 → サブタスク …と深さ無制限のツリーで管理される
進捗を、Discord 上でドリルダウン表示する。データの正本は
ギルドごとに設定された Google Sheets（進捗管理シート）。

- /progress-setup : スプレッドシート ID の登録とシートの初期化（管理者）
- /progress-sync  : Todoist 同期＋再集計を即時実行（管理者）
- /progress       : ドリルダウン表示（全員）
- 定期同期        : 20分ごとに全ギルドの Todoist 同期＋再集計。
                    エラーは各ギルドの #bot-log へ通知する
"""
from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services import progress_sheet_service as pss
from services import progress_sync_service
from services.progress_sheet_service import ProgressSheetUnavailable
from services.progress_tree import ProgressNode, ProgressTree
from services.todoist_service import TodoistError
from utils import progress_chart
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import Level, ensure_guild, is_admin, require

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("progress")

SYNC_INTERVAL_MINUTES = 20
CHART_FILENAME = "progress.png"

_NOT_CONFIGURED_DESC = (
    "このサーバーでは進捗管理シートが未設定です。\n"
    "管理者が `/progress-setup` でスプレッドシート ID を登録してください。"
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
                "`/progress` で自分の表示を開いてください。", ephemeral=True)
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
# コグ本体
# ---------------------------------------------------------------------
class Progress(commands.Cog):
    """機体進捗管理コグ。client_factory 注入でテスト可能。"""

    def __init__(self, bot: commands.Bot, client_factory=None):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self._client_factory = client_factory or pss.ProgressSheetClient

    def _client(self) -> pss.ProgressSheetClient:
        return self._client_factory()

    async def cog_load(self):
        self.periodic_sync.start()

    async def cog_unload(self):
        self.periodic_sync.cancel()

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

    # ---------- /progress-setup ----------
    @app_commands.command(
        name="progress-setup",
        description="進捗管理シートを登録・初期化します（管理者）。")
    @app_commands.describe(
        spreadsheet_id="スプレッドシートの ID（URL の /d/ と /edit の間の文字列）")
    @app_commands.check(is_admin)
    async def progress_setup(self, interaction: discord.Interaction,
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
                  "（`/progress-sync` で即時実行）。")
        await interaction.followup.send(
            embed=success_embed("進捗管理シートを登録しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- /progress-sync ----------
    @app_commands.command(
        name="progress-sync",
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

    # ---------- /progress ----------
    @app_commands.command(
        name="progress",
        description="機体製作の進捗をドリルダウン表示します。")
    @require(Level.L1)
    async def progress(self, interaction: discord.Interaction):
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
