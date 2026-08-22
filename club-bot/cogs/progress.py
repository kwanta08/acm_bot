"""
Progress コグ（機体進捗管理・DB 正本）

機体 → パーツ → 部品 → サブタスク …と深さ無制限のツリーで管理される
進捗を、Discord 上でドリルダウン表示する。データの正本は DB の
progress_nodes テーブル（ギルドごとに独立）。Google Sheets は不要。

- /progress view       : ドリルダウン表示（全員。進捗バーは Embed 内テキスト）
- /progress add        : 機体・パーツ・部品を追加（班長以上）
- /progress edit       : 名前・担当・状態・進捗率を変更（班長以上）
- /progress remove     : ノードを配下ごと削除（班長以上）
- /progress spar-link  : 桁巻き（/layer）の記録を進捗へ反映する紐付け（班長以上）
- /progress setup      : Todoist プロジェクトを進捗ツリーに紐付ける
                         セルフサービス登録ウィザード（班長以上）
- /progress sync       : Todoist 同期＋桁巻き反映＋再集計を即時実行（管理者）
- 定期同期             : 20分ごとに全ギルドを同期。エラーは #bot-log へ通知
"""

from __future__ import annotations

import uuid
from datetime import time as dtime
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

# タスク一覧の整形は既存の Reminders 通知とスタイルを揃える
from cogs.reminders import _build_grouped_description
from config import config
from repositories.audit_log_repository import AuditLogRepository
from repositories.progress_repository import ProgressRepository
from services import progress_sync_service
from services import progress_tree as pt
from services.milestone_service import (
    VERDICT_BEHIND,
    VERDICT_DONE,
    VERDICT_ON_TRACK,
    VERDICT_OVERDUE,
    VERDICT_UNKNOWN,
    MilestoneStatus,
    Pace,
    days_until_competition,
    evaluate_all,
    parse_date,
    spar_pace,
)
from services.progress_tree import (
    ProgressNode,
    ProgressTree,
    nodes_over_target,
    weight_summary,
)
from services.todoist_service import TodoistError
from utils import progress_bar
from utils.embeds import (
    add_truncation_note,
    error_embed,
    info_embed,
    success_embed,
    task_embed,
)
from utils.logger import get_logger
from utils.parser import TZ, now, parse_deadline
from utils.permissions import (
    Level,
    ensure_guild,
    is_admin,
    require,
    require_manage_guild_or,
)
from utils.views import ConfirmView, TimeoutAwareView

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("progress")

SYNC_INTERVAL_MINUTES = 20
# Embed 内テキストバーの幅（詳細なグラフは Web ダッシュボード側で描画する）
BAR_WIDTH = 12

# 手入力ノードの ID プレフィックス（Todoist 由来 td_ / プロジェクト pj_ と区別）
MANUAL_ID_PREFIX = "n_"

_EMPTY_TREE_DESC = (
    "まだ機体が登録されていません。\n"
    "`/progress add` で機体（親を指定しない一番上のノード）を追加してください。"
)


def _todoist_task_url(task_id: str) -> str:
    return f"https://app.todoist.com/app/task/{task_id}"


def new_node_id() -> str:
    """手入力ノードの ID を採番する（ギルド内で衝突しない十分な長さ）。"""
    return f"{MANUAL_ID_PREFIX}{uuid.uuid4().hex[:10]}"


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
        current = tree.by_id.get(current.parent_id) if current.parent_id else None
    return " > ".join(reversed(names))


def child_nodes(tree: ProgressTree, node_id: str | None) -> list[ProgressNode]:
    """指定ノードの子一覧（node_id=None ならルート一覧）を返す。"""
    if node_id is None:
        return tree.roots
    node = tree.by_id.get(node_id)
    return node.children if node else []


def percent(node: ProgressNode) -> str:
    return f"{(node.aggregated or 0.0) * 100:.0f}%"


def source_label(source: str) -> str:
    """ソース列の表示名。"""
    if source == pt.SOURCE_TODOIST:
        return "Todoist"
    if source == pt.SOURCE_SPAR_WINDING:
        return "桁巻き（/layer）"
    return "手入力"


def format_grams(value: float) -> str:
    return f"{value:,.0f}g"


def weight_line(tree: ProgressTree, node_id: str | None) -> str:
    """「重量: 実測 1,240g / 目標 1,100g（+140g）」の1行。

    実測も目標も入っていなければ空文字を返す。重量を使っていない
    サーバーで /progress view の表示を変えないため、呼び出し側は
    空文字なら行ごと出さない。
    """
    summary = weight_summary(tree, node_id)
    if summary.actual_g is None and summary.target_g is None:
        return ""
    parts = []
    if summary.actual_g is not None:
        parts.append(f"実測 {format_grams(summary.actual_g)}")
    if summary.target_g is not None:
        parts.append(f"目標 {format_grams(summary.target_g)}")
    text = " / ".join(parts)
    diff = summary.diff_g
    if diff is not None:
        text += f"（{diff:+,.0f}g）"
    return f"重量: {text}"


def _with_weight(desc: str, tree: ProgressTree, node_id: str | None) -> str:
    line = weight_line(tree, node_id)
    if not line:
        return desc
    return f"{desc}\n{line}" if desc else line


_VERDICT_MARK = {
    VERDICT_DONE: "✅ 完了",
    VERDICT_ON_TRACK: "✅ 間に合う",
    VERDICT_BEHIND: "⚠️ 遅延",
    VERDICT_OVERDUE: "🚨 期限超過",
    VERDICT_UNKNOWN: "❓ 判定不能",
}

COMPETITION_DATE_HELP = (
    "大会日が設定されていません。\n"
    "`/settings_set` で `COMPETITION_DATE` に `YYYY-MM-DD` 形式の日付を"
    "登録してください（例: `2026-07-25`）。"
)


def _pace_text(status: MilestoneStatus) -> str:
    """必要ペースと実績ペースを %/日 で示す。"""
    if status.required_per_day is None:
        return ""
    need = f"必要 {status.required_per_day * 100:.1f}%/日"
    if status.actual_per_day is None:
        return f"{need} / 実績 —"
    return f"{need} / 実績 {status.actual_per_day * 100:.1f}%/日"


def build_countdown_embed(
    competition_date: str | None, statuses: list[MilestoneStatus], today
) -> discord.Embed:
    """/countdown の Embed。大会までの残り日数とマイルストーンの判定。"""
    left = days_until_competition(competition_date, today)
    if left is None:
        head = "大会日: 未設定"
    elif left > 0:
        head = f"大会（{competition_date}）まで **残り {left} 日**"
    elif left == 0:
        head = f"**本日が大会日です**（{competition_date}）"
    else:
        head = f"大会（{competition_date}）から {-left} 日が経過しています"

    if not statuses:
        return info_embed(
            "🏁 大会カウントダウン",
            f"{head}\n\nマイルストーンが登録されていません。\n"
            "`/milestone add` で節目の期限を登録すると、"
            "必要なペースと実績を突き合わせて遅延をお知らせします。",
        )

    behind = [s for s in statuses if s.is_behind]
    unknown = [s for s in statuses if s.verdict == VERDICT_UNKNOWN]
    summary = f"{head}\n遅延 **{len(behind)} 件** / 全 {len(statuses)} 件"
    if unknown:
        summary += f"（判定不能 {len(unknown)} 件）"

    embed = info_embed("🏁 大会カウントダウン", summary)
    for status in statuses[:25]:
        when = (
            f"期限まで {status.days_left} 日"
            if status.days_left > 0
            else "本日が期限"
            if status.days_left == 0
            else f"{-status.days_left} 日超過"
        )
        lines = [f"{when} / 進捗 {status.progress * 100:.0f}%"]
        pace = _pace_text(status)
        if pace:
            lines.append(pace)
        if status.verdict == VERDICT_UNKNOWN and status.reason:
            lines.append(f"※ {status.reason}ため判定できません")
        embed.add_field(
            name=f"{_VERDICT_MARK[status.verdict]}　{status.node_name}: {status.name}",
            value="\n".join(lines),
            inline=False,
        )
    if len(statuses) > 25:
        embed.description += f"\n…ほか {len(statuses) - 25} 件"
    return embed


def build_level_embed(tree: ProgressTree, node_id: str | None) -> discord.Embed:
    """現在の階層（子ノード一覧 or 葉の詳細）の Embed を組み立てる。"""
    title = f"📊 {breadcrumb(tree, node_id)}"
    children = child_nodes(tree, node_id)
    node = tree.by_id.get(node_id) if node_id else None

    if node is not None and not children:
        # 葉ノード: 詳細表示
        embed = info_embed(title, _with_weight(f"進捗率: **{percent(node)}**", tree, node_id))
        embed.add_field(name="担当者", value=node.assignee or "—", inline=True)
        embed.add_field(name="状態", value=node.status or "—", inline=True)
        embed.add_field(name="ソース", value=source_label(node.source), inline=True)
        if node.todoist_task_id:
            embed.add_field(
                name="Todoist",
                value=f"[タスクを開く]({_todoist_task_url(node.todoist_task_id)})",
                inline=False,
            )
        embed.set_footer(text=f"ID: {node.node_id}")
        return embed

    desc = f"全体進捗: **{percent(node)}**" if node is not None else ""
    embed = info_embed(title, _with_weight(desc, tree, node_id))
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
            value=" / ".join(detail) or "\u200b",  # 空はゼロ幅スペース
            inline=False,
        )
    if len(children) > 25:
        embed.set_footer(text=f"他 {len(children) - 25} 件")
    block = progress_bar.render_block(chart_items(tree, node_id), width=BAR_WIDTH)
    if block:
        head = embed.description or ""
        embed.description = f"{head}\n{block}".strip()
    return embed


def chart_items(tree: ProgressTree, node_id: str | None) -> list[tuple[str, float]]:
    """グラフ描画用の (名前, 進捗率) 一覧を返す。"""
    return [(c.name or c.node_id, c.aggregated or 0.0) for c in child_nodes(tree, node_id)[:25]]


def node_choices(tree: ProgressTree, current: str, limit: int = 25) -> list[tuple[str, str]]:
    """オートコンプリート用の (表示名, node_id) 一覧を返す。

    ツリーの行きがけ順（機体 → 配下のパーツ → …）で、階層を全角スペースで
    字下げする。current による絞り込みは名前・ID の部分一致。
    """
    out: list[tuple[str, str]] = []
    needle = current.lower()

    def walk(node: ProgressNode) -> None:
        label = f"{'　' * (node.depth or 0)}{node.name or node.node_id}"
        if needle in label.lower() or needle in node.node_id.lower():
            out.append((label[:100], node.node_id))
        for child in node.children:
            walk(child)

    for root in tree.roots:
        walk(root)
    return out[:limit]


# ---------------------------------------------------------------------
# /progress setup ウィザード用ヘルパー（純粋関数。テスト対象）
# ---------------------------------------------------------------------
def unmapped_projects(projects: list, links: list[dict]) -> list:
    """まだ紐付けられていない Todoist プロジェクトの一覧を返す。"""
    mapped_names = {link["project_name"] for link in links}
    return [p for p in projects if getattr(p, "name", "") not in mapped_names]


def anchor_candidates(tree: ProgressTree, max_depth: int = 1) -> list[ProgressNode]:
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


def new_part_node_id(project_id: str) -> str:
    """「新規パーツとして追加」時のノード ID（一意・安定）。

    プロジェクト ID から導くため、同じプロジェクトを消して再登録しても
    同じノードに戻る。
    """
    return f"pj_{project_id}"


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
        items.append(
            {
                "due_date": due_date,
                "title": t.content,
                "priority": pr_int,
                "url": _todoist_task_url(t.id),
                "category": category,
            }
        )
    return items


# ---------------------------------------------------------------------
# ドリルダウン View
# ---------------------------------------------------------------------
class ProgressView(TimeoutAwareView):
    """階層ドリルダウン用の View。

    「選択ノードの子を取得して表示」を再帰的に繰り返すだけの実装で、
    階層数のハードコードは無い。ツリーはコマンド実行時に読み込んだものを
    View 内に保持する（🔄 ボタンで DB から読み直せる）。
    """

    def __init__(
        self, cog: Progress, tree: ProgressTree, node_id: str | None, owner_id: int, guild_id: int
    ):
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
            select = discord.ui.Select(placeholder="ノードを選択して詳細を表示", options=options)
            select.callback = self._on_select
            self.add_item(select)

        back = discord.ui.Button(
            label="戻る",
            emoji="⬆️",
            style=discord.ButtonStyle.secondary,
            disabled=self.node_id is None,
        )
        back.callback = self._on_back
        self.add_item(back)

        reload_btn = discord.ui.Button(
            label="再読込", emoji="🔄", style=discord.ButtonStyle.secondary
        )
        reload_btn.callback = self._on_reload
        self.add_item(reload_btn)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この表示はコマンドを実行した本人のみ操作できます。"
                "`/progress view` で自分の表示を開いてください。",
                ephemeral=True,
            )
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
            log.warning("進捗ツリー再読込失敗 (guild=%s): %s", self.guild_id, type(e).__name__)
            await interaction.followup.send(
                embed=error_embed("進捗データの再読込に失敗しました。"), ephemeral=True
            )
            return
        if self.node_id and self.node_id not in self.tree.by_id:
            self.node_id = None  # 再読込でノードが消えていたらルートへ
        await self._render(interaction, deferred=True)

    async def _render(self, interaction: discord.Interaction, deferred: bool = False) -> None:
        self._rebuild_items()
        embed = build_level_embed(self.tree, self.node_id)
        # 進捗バーは Embed 内のテキスト。画像の生成・添付は行わない
        if deferred:
            await interaction.edit_original_response(embed=embed, attachments=[], view=self)
        else:
            await interaction.response.edit_message(embed=embed, attachments=[], view=self)



# ---------------------------------------------------------------------
# /progress setup ウィザード View
# ---------------------------------------------------------------------
class ProjectSetupWizard(TimeoutAwareView):
    """Todoist プロジェクトを進捗ツリーへ紐付けるセルフサービスウィザード。

    ステップ: ①プロジェクト選択 → ②紐付け先ノード選択
    （「新規パーツとして追加」を選ぶと ②b で機体を選択）→
    ③通知先選択（専用チャンネル or 共通）→ DB へ登録。
    すべて ephemeral メッセージ内で完結し、.env 編集・再起動は発生しない。
    """

    NEW_PART = "__new_part__"

    def __init__(
        self, cog: Progress, guild_id: int, owner_id: int, projects: list, tree: ProgressTree
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.projects = projects  # 未登録プロジェクトのみ
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
                discord.SelectOption(label=str(getattr(p, "name", p.id))[:100], value=str(p.id))
                for p in self.projects[:25]
            ]
            select = discord.ui.Select(
                placeholder="紐付ける Todoist プロジェクトを選択", options=options
            )
            select.callback = self._on_project
            self.add_item(select)
        elif self.step == "anchor":
            options = [
                discord.SelectOption(
                    label="➕ 新規パーツとして追加",
                    value=self.NEW_PART,
                    description="プロジェクト名のパーツを機体の下に作成",
                )
            ]
            for node in anchor_candidates(self.tree)[:24]:
                indent = "└ " if (node.depth or 0) > 0 else ""
                options.append(
                    discord.SelectOption(
                        label=f"{indent}{node.name or node.node_id}"[:100],
                        value=node.node_id,
                        description=f"ID: {node.node_id}"[:100],
                    )
                )
            select = discord.ui.Select(
                placeholder="紐付け先ノード（機体 or パーツ）を選択", options=options
            )
            select.callback = self._on_anchor
            self.add_item(select)
        elif self.step == "root":
            options = [
                discord.SelectOption(label=(r.name or r.node_id)[:100], value=r.node_id)
                for r in self.tree.roots[:25]
            ]
            select = discord.ui.Select(
                placeholder="新規パーツを追加する機体を選択", options=options
            )
            select.callback = self._on_root
            self.add_item(select)
        elif self.step == "notify":
            channel_select = discord.ui.ChannelSelect(
                placeholder="①このプロジェクト専用の通知チャンネルを選択",
                channel_types=[discord.ChannelType.text],
            )
            channel_select.callback = self._on_channel
            self.add_item(channel_select)
            common = discord.ui.Button(
                label="②共通の通知チャンネルにまとめる", style=discord.ButtonStyle.primary
            )
            common.callback = self._on_common
            self.add_item(common)

    # ---------- コールバック ----------
    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このウィザードはコマンドを実行した本人のみ操作できます。", ephemeral=True
            )
            return False
        return True

    async def _on_project(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.project_id = interaction.data["values"][0]
        self.project_name = next(
            (str(getattr(p, "name", "")) for p in self.projects if str(p.id) == self.project_id),
            self.project_id,
        )
        self.step = "anchor"
        self._build()
        await interaction.response.edit_message(
            embed=info_embed(
                "紐付け先の選択",
                f"プロジェクト「{self.project_name}」のタスクをどのノードの下にぶら下げますか？",
            ),
            view=self,
        )

    async def _on_anchor(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        value = interaction.data["values"][0]
        if value == self.NEW_PART:
            if len(self.tree.roots) == 1:
                # 機体が1つだけなら選択ステップを省略
                self.new_part_root_id = self.tree.roots[0].node_id
                self.anchor_id = new_part_node_id(self.project_id)
                self.step = "notify"
            else:
                self.step = "root"
        else:
            self.anchor_id = value
            self.step = "notify"
        self._build()
        await interaction.response.edit_message(embed=self._step_embed(), view=self)

    async def _on_root(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.new_part_root_id = interaction.data["values"][0]
        self.anchor_id = new_part_node_id(self.project_id)
        self.step = "notify"
        self._build()
        await interaction.response.edit_message(embed=self._step_embed(), view=self)

    def _step_embed(self) -> discord.Embed:
        if self.step == "root":
            return info_embed("機体の選択", "新規パーツをどの機体の下に追加しますか？")
        return info_embed(
            "通知先の選択",
            f"プロジェクト「{self.project_name}」のタスク通知を"
            "どこへ送りますか？\n"
            "① 専用チャンネル: 下のメニューから選択\n"
            "② 共通チャンネル: ボタンを押す（このサーバーの既定の"
            "通知チャンネルへ送られます）",
        )

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
    async def _finish(self, interaction: discord.Interaction, notify_channel_id: str) -> None:
        await interaction.response.defer()
        repo = self.cog.repo
        now_text = progress_sync_service._now_text()
        try:
            if self.new_part_root_id is not None:
                await repo.upsert_node(
                    self.guild_id,
                    self.anchor_id,
                    parent_id=self.new_part_root_id,
                    name=self.project_name or self.anchor_id,
                    now_text=now_text,
                )
            await repo.upsert_todoist_link(
                self.guild_id,
                self.project_name,
                self.anchor_id,
                now_text,
                notify_channel_id=notify_channel_id,
                created_by=str(interaction.user.id),
            )
        except Exception as e:  # noqa: BLE001  (DB エラー)
            log.warning("Todoist 紐付けの登録失敗 (guild=%s): %s", self.guild_id, type(e).__name__)
            await interaction.edit_original_response(
                embed=error_embed("登録に失敗しました。時間をおいて再試行してください。"), view=None
            )
            return

        notify_disp = (
            f"<#{notify_channel_id}>"
            if notify_channel_id
            else "共通チャンネル（このサーバーの既定）"
        )
        desc = (
            f"プロジェクト: {self.project_name}\n"
            f"紐付け先ノード: `{self.anchor_id}`"
            + ("（新規パーツとして追加）" if self.new_part_root_id else "")
            + f"\n通知先: {notify_disp}\n\n"
            f"{SYNC_INTERVAL_MINUTES} 分ごとの自動同期でタスクが"
            "取り込まれます（`/progress sync` で即時実行）。"
        )
        await interaction.edit_original_response(
            embed=success_embed("プロジェクトを登録しました", desc), view=None
        )
        self.stop()



# ---------------------------------------------------------------------
# コグ本体
# ---------------------------------------------------------------------
class Progress(commands.Cog):
    """機体進捗管理コグ（DB 正本）。"""

    group = app_commands.Group(name="progress", description="機体進捗管理")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.repo = ProgressRepository(self.db)

    async def cog_load(self):
        self.periodic_sync.start()
        self.daily_project_notify.start()

    async def cog_unload(self):
        self.periodic_sync.cancel()
        self.daily_project_notify.cancel()

    # ---------- 共通処理 ----------
    async def load_tree(self, guild_id: int) -> ProgressTree:
        """DB から進捗ツリーを読み込み、集計まで済ませて返す。

        DB 読み取りは軽いため、コマンド・ボタン操作のたびに読み直す
        （シート時代のようなメモリキャッシュは持たない = 常に最新）。
        """
        return await pt.load_tree(self.repo, guild_id)

    async def _run_sync(self, guild_id: int):
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        return await progress_sync_service.sync_guild_db(self.db, guild_id, svc)

    async def _resolve_node(
        self, interaction: discord.Interaction, guild_id: int, node_id: str
    ) -> bool:
        """ノードの存在確認。無ければ ephemeral で案内して False。"""
        if await self.repo.exists(guild_id, node_id):
            return True
        await interaction.followup.send(
            embed=error_embed(
                f"ノード `{node_id}` が見つかりません。オートコンプリートの候補から選んでください。"
            ),
            ephemeral=True,
        )
        return False

    async def _node_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        try:
            tree = await self.load_tree(interaction.guild.id)
        except Exception:  # noqa: BLE001  (補完は失敗しても致命的でない)
            return []
        return [
            app_commands.Choice(name=label, value=node_id)
            for label, node_id in node_choices(tree, current)
        ]

    async def _keta_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        from repositories.layer_keta_repository import LayerKetaRepository

        names = await LayerKetaRepository(self.db).list_active(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()
        ][:25]

    # ---------- 定期同期（20分ごと・bot全体で単一ジョブ） ----------
    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def periodic_sync(self):
        """全ギルドの進捗を同期する（Todoist 取り込み・桁巻き反映・再集計）。

        1ギルドの失敗は他ギルドを止めない。エラーはそのギルドの #bot-log へ。
        """
        guild_ids = [g.id for g in self.bot.guilds]
        try:
            results = await progress_sync_service.sync_all_guilds(
                self.db, guild_ids, self.bot.todoist_manager
            )
        except Exception as e:  # noqa: BLE001  (次周期でリトライ)
            log.warning("進捗定期同期失敗: %s", type(e).__name__)
            return
        for result in results:
            if not result.errors:
                continue
            lines = "\n".join(f"- {e}" for e in result.errors[:10])
            await self.bot.log_to_channel(
                f"[進捗同期] 問題があります:\n{lines}", guild_id=result.guild_id
            )

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
                log.warning("プロジェクト別通知失敗 (guild=%s): %s", guild.id, type(e).__name__)

    async def push_project_tasks(self, guild_id: int) -> int:
        """紐付け済み各プロジェクトの期限タスク（7日以内・超過）を通知する。

        送信先は紐付け行の通知チャンネル → ギルドの既定通知チャンネル
        （settings の PROGRESS_DEFAULT_CHANNEL_ID）→ タスクチャンネルの順で
        解決する。送信件数を返す。
        """
        links = await self.repo.list_todoist_links(guild_id)
        if not links:
            return 0
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            return 0

        projects = await svc.get_projects()
        project_ids = {getattr(p, "name", ""): str(p.id) for p in projects}
        default_channel_id = await progress_sync_service.resolve_default_channel_id(
            self.db, guild_id
        )

        gconf = await config.for_guild(guild_id)
        today = now().date()
        until = today + timedelta(days=7)
        sent = 0

        for link in links:
            project_id = project_ids.get(link["project_name"])
            if project_id is None:
                continue
            try:
                proj_tasks = await svc.get_tasks(project_id=project_id)
            except TodoistError:
                log.warning(
                    "プロジェクト %s のタスク取得失敗 (guild=%s)", link["project_name"], guild_id
                )
                continue
            items = due_items(proj_tasks, until, link["project_name"])
            if not items:
                continue

            channel_id = (
                progress_sync_service.resolve_link_channel_id(link, default_channel_id)
                or gconf.default_task_channel_id
            )
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if channel is None:
                await self.bot.log_to_channel(
                    f"[進捗通知] 送信先チャンネルがありません"
                    f"（{link['project_name']}）。`/progress setup` で"
                    "通知チャンネルを設定し直してください。",
                    guild_id=guild_id,
                )
                continue

            desc = _build_grouped_description(today, until, "今日から7日以内", items)
            embed = task_embed(f"【進捗・{link['project_name']}】期限タスク")
            embed.description = desc[:4096]
            try:
                await channel.send(embed=embed)
                sent += 1
            except discord.HTTPException as e:
                await self.bot.log_to_channel(
                    f"[進捗通知] 送信失敗（{link['project_name']}）: {e}", guild_id=guild_id
                )
        return sent

    @daily_project_notify.before_loop
    async def _before_notify(self):
        await self.bot.wait_until_ready()

    # ---------- /progress add ----------
    @group.command(name="add", description="機体・パーツ・部品を追加します（班長以上）。")
    @app_commands.describe(
        name="表示名（例: 1号機 / 主翼 / 主桁）",
        parent="親ノード（省略すると機体＝一番上のノードになります）",
        assignee="担当者名（任意）",
        status="状態（未着手／製作中／完了 など・任意）",
        progress="進捗率（0.5 / 50% どちらでも可・任意。葉ノードのみ有効）",
    )
    @require(Level.L2)
    async def progress_add(
        self,
        interaction: discord.Interaction,
        name: str,
        parent: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        progress: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if parent and not await self._resolve_node(interaction, guild_id, parent):
            return

        node_id = new_node_id()
        siblings = await self.repo.list_children(guild_id, parent or None)
        await self.repo.upsert_node(
            guild_id,
            node_id,
            parent_id=parent or None,
            sort_order=float(len(siblings) + 1),
            name=name,
            assignee=assignee,
            status=status,
            manual_progress=pt.parse_progress(progress),
            now_text=progress_sync_service._now_text(),
        )

        where = f"`{parent}` の下" if parent else "機体（最上位）として"
        await interaction.followup.send(
            embed=success_embed(
                "進捗ノードを追加しました",
                f"名前: **{name}**\n位置: {where}\nID: `{node_id}`\n\n"
                "`/progress view` で確認できます。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- /progress edit ----------
    @group.command(
        name="edit", description="ノードの名前・担当・状態・進捗率を変更します（班長以上）。"
    )
    @app_commands.describe(
        node="対象ノード",
        name="新しい表示名（任意）",
        assignee="担当者名（任意）",
        status="状態（任意）",
        progress="進捗率（0.5 / 50% どちらでも可・任意）",
        parent="親ノードを変更する場合に指定（任意）",
    )
    @require(Level.L2)
    async def progress_edit(
        self,
        interaction: discord.Interaction,
        node: str,
        name: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        progress: str | None = None,
        parent: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self._resolve_node(interaction, guild_id, node):
            return
        if parent:
            if parent == node:
                await interaction.followup.send(
                    embed=error_embed("自分自身を親にはできません。"), ephemeral=True
                )
                return
            if not await self._resolve_node(interaction, guild_id, parent):
                return
            # 自分の配下を親にすると循環参照になり、その部分木がツリーから
            # 丸ごと除外される（利用者からは進捗が消えたように見える）
            tree = await self.load_tree(guild_id)
            if parent in pt.descendant_ids(tree, node):
                await interaction.followup.send(
                    embed=error_embed(
                        f"`{parent}` は `{node}` の配下にあるため親にできません"
                        "（循環参照になります）。\n"
                        "先に移動先を配下の外へ出してください。"
                    ),
                    ephemeral=True,
                )
                return

        fields: dict[str, object] = {}
        if name is not None:
            fields["name"] = name
        if assignee is not None:
            fields["assignee"] = assignee
        if status is not None:
            fields["status"] = status
        if progress is not None:
            fields["manual_progress"] = pt.parse_progress(progress)
            fields["source"] = pt.SOURCE_MANUAL  # 手入力に戻す
        if parent is not None:
            fields["parent_id"] = parent
        if not fields:
            await interaction.followup.send(
                embed=info_embed(
                    "変更内容がありません", "変更したい項目を1つ以上指定してください。"
                ),
                ephemeral=True,
            )
            return

        await self.repo.update_node(guild_id, node, progress_sync_service._now_text(), **fields)
        changed = "\n".join(f"- {k}: {v}" for k, v in fields.items())
        await interaction.followup.send(
            embed=success_embed(
                f"`{node}` を更新しました", changed, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    # ---------- /progress remove ----------
    @group.command(name="remove", description="ノードを配下ごと削除します（班長以上）。")
    @app_commands.describe(node="削除するノード（配下も一緒に削除されます）")
    @require(Level.L2)
    async def progress_remove(self, interaction: discord.Interaction, node: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self._resolve_node(interaction, guild_id, node):
            return

        # 消える件数を**消す前に**見せる。配下ごと消えることは説明文にしか
        # 書かれておらず、実行後に件数を報告するだけでは手遅れだった
        total = await self.repo.count_subtree(guild_id, node)
        descendants = max(total - 1, 0)
        body = (
            f"`{node}` を削除します。\n"
            f"配下のノード: **{descendants}** 件（自身を含めて合計 **{total}** 件）\n\n"
            "削除したノードの進捗・重量・マイルストーンは戻せません。"
        )

        async def _do_remove(confirm_interaction: discord.Interaction) -> None:
            deleted = await self.repo.delete_subtree(guild_id, node)
            await confirm_interaction.followup.send(
                embed=success_embed(
                    "進捗ノードを削除しました",
                    f"`{node}` とその配下 合計 **{deleted}** 件を削除しました。",
                    executor=confirm_interaction.user.display_name,
                ),
                ephemeral=True,
            )

        view = ConfirmView(
            interaction.user.id,
            info_embed("進捗ノードの削除を確認してください", body),
            _do_remove,
            cancel_message="進捗ノードは削除していません。",
        )
        view.message = await interaction.followup.send(
            embed=view.preview_embed, view=view, ephemeral=True
        )

    # ---------- /progress spar-link ----------
    @group.command(
        name="spar-link",
        description="桁巻き（/layer）の記録を進捗ノードへ反映する紐付け（班長以上）。",
    )
    @app_commands.describe(
        keta="桁名（/layer keta-add で登録済みのもの）",
        node="進捗を反映する葉ノード",
        target_layers="目標層数（この層数を巻き終えたら 100%）",
    )
    @require(Level.L2)
    async def progress_spar_link(
        self, interaction: discord.Interaction, keta: str, node: str, target_layers: int
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if target_layers <= 0:
            await interaction.followup.send(
                embed=error_embed("目標層数は 1 以上を指定してください。"), ephemeral=True
            )
            return
        if not await self._resolve_node(interaction, guild_id, node):
            return

        await self.repo.upsert_spar_link(
            guild_id, keta, node, target_layers, progress_sync_service._now_text()
        )
        await interaction.followup.send(
            embed=success_embed(
                "桁巻きの紐付けを登録しました",
                f"桁: **{keta}**\n反映先: `{node}`\n"
                f"目標層数: **{target_layers}**\n\n"
                "`/layer end` で積層を記録するたびに進捗率が更新されます"
                f"（{SYNC_INTERVAL_MINUTES} 分ごとの自動同期。"
                "`/progress sync` で即時実行）。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- /progress sync ----------
    @group.command(
        name="sync", description="Todoist 同期と進捗の再集計を今すぐ実行します（管理者）。"
    )
    @app_commands.check(is_admin)
    async def progress_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            result = await self._run_sync(guild_id)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("Todoist の取得に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True,
            )
            return
        except Exception as e:  # noqa: BLE001
            log.warning("進捗手動同期失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed("進捗の同期に失敗しました。"), ephemeral=True
            )
            return

        desc = (
            f"対象プロジェクト: {result.projects} 件\n"
            f"追加 {result.added} / 更新 {result.updated} / "
            f"完了 {result.completed} / 桁巻き反映 {result.spar_updated}"
        )
        if result.errors:
            desc += "\n\n⚠️ 警告:\n" + "\n".join(f"- {e}" for e in result.errors[:10])
        await interaction.followup.send(
            embed=success_embed(
                "進捗同期を実行しました", desc, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    # ---------- /progress setup ----------
    # 導入直後のサーバーは班長ロールが未設定のことが多いため、
    # 「サーバー管理（Manage Server）」権限でも実行できるようにする
    # （班長ロールを設定済みのサーバーとの後方互換は L2 判定で維持）。
    @group.command(
        name="setup",
        description="Todoist プロジェクトを進捗ツリーに紐付けます（サーバー管理権限または班長以上）。",
    )
    @require_manage_guild_or(Level.L2)
    async def progress_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            await interaction.followup.send(
                embed=info_embed(
                    "Todoist 未設定",
                    "このサーバーでは Todoist が未設定です。\n"
                    "管理者が `/todoist-setup` で登録してください。",
                ),
                ephemeral=True,
            )
            return

        try:
            projects = await svc.get_projects()
            links = await self.repo.list_todoist_links(guild_id)
            tree = await self.load_tree(guild_id)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed(
                    "Todoist プロジェクトの取得に失敗しました。", code="TODOIST_API_FAILED"
                ),
                ephemeral=True,
            )
            return
        except Exception as e:  # noqa: BLE001
            log.warning("setup ウィザード準備失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed("進捗データの読み込みに失敗しました。"), ephemeral=True
            )
            return

        candidates = unmapped_projects(projects, links)
        if not candidates:
            await interaction.followup.send(
                embed=info_embed(
                    "登録できるプロジェクトがありません",
                    "すべての Todoist プロジェクトが登録済みか、"
                    "プロジェクトが存在しません。\n"
                    "別ワークスペースのプロジェクトを使う場合は、"
                    "bot 用 Todoist アカウントへの共有を依頼してください。",
                ),
                ephemeral=True,
            )
            return
        if not tree.roots:
            await interaction.followup.send(
                embed=info_embed("機体が未登録です", _EMPTY_TREE_DESC), ephemeral=True
            )
            return

        view = ProjectSetupWizard(self, guild_id, interaction.user.id, candidates, tree)
        view.message = await interaction.followup.send(
            embed=info_embed(
                "プロジェクト登録ウィザード",
                "紐付ける Todoist プロジェクトを選択してください。\n"
                "（登録済みのプロジェクトは表示されません）",
            ),
            view=view,
            ephemeral=True,
        )

    # ---------- /progress view ----------
    @group.command(name="view", description="機体製作の進捗をドリルダウン表示します。")
    @require(Level.L1)
    async def progress_view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            tree = await self.load_tree(guild_id)
        except Exception as e:  # noqa: BLE001
            log.warning("進捗ツリー読込失敗 (guild=%s): %s", guild_id, e)
            await interaction.followup.send(
                embed=error_embed("進捗データの読み込みに失敗しました。"), ephemeral=True
            )
            return

        if not tree.roots:
            await interaction.followup.send(
                embed=info_embed("進捗データがありません", _EMPTY_TREE_DESC), ephemeral=True
            )
            return

        embed = build_level_embed(tree, None)
        view = ProgressView(self, tree, None, interaction.user.id, guild_id)
        view.message = await interaction.followup.send(embed=embed, view=view)

    # ---------- /weight ----------
    weight_group = app_commands.Group(name="weight", description="機体重量の記録と集計")

    @weight_group.command(name="set", description="ノードの重量（g）を記録します。")
    @app_commands.describe(
        node="対象ノード", actual="実測重量（g）", target="目標重量（g。省略時は変更しない）"
    )
    @require(Level.L2)
    async def weight_set(
        self,
        interaction: discord.Interaction,
        node: str,
        actual: float,
        target: float | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if actual < 0 or (target is not None and target < 0):
            await interaction.followup.send(
                embed=error_embed("重量に負の値は指定できません。"), ephemeral=True
            )
            return
        if not await self._resolve_node(interaction, guild_id, node):
            return

        fields: dict[str, float] = {"actual_weight_g": actual}
        if target is not None:
            fields["target_weight_g"] = target
        await self.repo.update_node(
            guild_id, node, now_text=now().strftime("%Y-%m-%d %H:%M"), **fields
        )
        await self._audit(
            guild_id,
            interaction,
            "weight.set",
            node,
            f"実測 {format_grams(actual)}"
            + (f" / 目標 {format_grams(target)}" if target is not None else ""),
        )

        tree = await self.load_tree(guild_id)
        detail = weight_line(tree, node) or "重量: —"
        await interaction.followup.send(
            embed=success_embed(
                "重量を記録しました", f"`{node}`\n{detail}", executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    @weight_group.command(
        name="view", description="重量の集計・目標との差・実測入力率を表示します。"
    )
    @app_commands.describe(node="対象ノード（省略時は機体全体）")
    @require(Level.L1)
    async def weight_view(self, interaction: discord.Interaction, node: str | None = None):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tree = await self.load_tree(guild_id)
        if not tree.roots:
            await interaction.followup.send(
                embed=info_embed("進捗データがありません", _EMPTY_TREE_DESC), ephemeral=True
            )
            return

        summary = weight_summary(tree, node)
        if summary.total_nodes == 0:
            await interaction.followup.send(
                embed=error_embed(f"ノード `{node}` が見つかりません。"), ephemeral=True
            )
            return

        target_name = (tree.by_id[node].name or node) if node else "機体全体"
        lines = [
            f"実測合計: **{format_grams(summary.actual_g)}**"
            if summary.actual_g is not None
            else "実測合計: **未計測**",
            f"目標合計: **{format_grams(summary.target_g)}**"
            if summary.target_g is not None
            else "目標合計: **未設定**",
        ]
        diff = summary.diff_g
        if diff is not None:
            mark = "⚠️ 超過" if diff > 0 else "✅ 目標内"
            lines.append(f"差分: **{diff:+,.0f}g** {mark}")
        lines.append(
            f"実測入力率: **{summary.fill_rate * 100:.0f}%**"
            f"（{summary.measured_nodes} / {summary.total_nodes} ノード）"
        )
        if summary.fill_rate < 1.0:
            lines.append("※ 未計測のノードがあるため、合計は見積もりを含みます。")

        await interaction.followup.send(
            embed=info_embed(f"⚖️ 重量: {target_name}", "\n".join(lines))
        )

    @weight_group.command(
        name="top", description="目標を超過しているノードを超過量の大きい順に表示します。"
    )
    @require(Level.L1)
    async def weight_top(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tree = await self.load_tree(guild_id)
        ranked = nodes_over_target(tree)
        if not ranked:
            await interaction.followup.send(
                embed=info_embed(
                    "⚖️ 目標超過のノードはありません",
                    "目標と実測の両方が入っているノードのうち、超過しているものは"
                    "ありませんでした。\n"
                    "重量を登録するには `/weight set` を使います。",
                )
            )
            return

        lines = []
        for rank, (node, over) in enumerate(ranked[:20], start=1):
            actual = node.aggregated_actual_weight_g
            target = node.aggregated_target_weight_g
            lines.append(
                f"{rank}. **{node.name or node.node_id}** "
                f"+{format_grams(over)}"
                f"（実測 {format_grams(actual)} / 目標 {format_grams(target)}）"
            )
        if len(ranked) > 20:
            lines.append(f"…ほか {len(ranked) - 20} 件")

        await interaction.followup.send(embed=info_embed("⚖️ 目標超過ランキング", "\n".join(lines)))

    # ---------- /milestone ----------
    milestone_group = app_commands.Group(
        name="milestone", description="大会に向けたマイルストーン（期限）の管理"
    )

    @milestone_group.command(name="add", description="ノードに期限（マイルストーン）を設定します。")
    @app_commands.describe(node="対象ノード", name="マイルストーン名", due="期限（YYYY-MM-DD）")
    @require(Level.L2)
    async def milestone_add(self, interaction: discord.Interaction, node: str, name: str, due: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self._resolve_node(interaction, guild_id, node):
            return
        # 日付の解釈は既存パーサに任せる（失敗は INVALID_DATETIME として
        # グローバルハンドラが案内する）
        due_date = parse_deadline(due).strftime("%Y-%m-%d")

        await self.repo.add_milestone(
            guild_id, node, name, due_date, now().strftime("%Y-%m-%d %H:%M")
        )
        await self._audit(guild_id, interaction, "milestone.add", node, f"{name} / {due_date}")
        await interaction.followup.send(
            embed=success_embed(
                "マイルストーンを設定しました",
                f"`{node}` — **{name}**\n期限: {due_date}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @milestone_group.command(name="remove", description="マイルストーンを削除します。")
    @app_commands.describe(node="対象ノード", name="マイルストーン名")
    @require(Level.L2)
    async def milestone_remove(self, interaction: discord.Interaction, node: str, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if not await self.repo.remove_milestone(guild_id, node, name):
            await interaction.followup.send(
                embed=error_embed(f"`{node}` に「{name}」というマイルストーンはありません。"),
                ephemeral=True,
            )
            return
        await self._audit(guild_id, interaction, "milestone.remove", node, name)
        await interaction.followup.send(
            embed=success_embed(
                "マイルストーンを削除しました",
                f"`{node}` — {name}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @milestone_group.command(
        name="list", description="登録済みのマイルストーンを期限順に表示します。"
    )
    @require(Level.L1)
    async def milestone_list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.repo.list_milestones(guild_id)
        if not rows:
            await interaction.followup.send(
                embed=info_embed(
                    "マイルストーンがありません", "`/milestone add` で節目の期限を登録できます。"
                )
            )
            return
        tree = await self.load_tree(guild_id)
        lines = []
        for row in rows:
            node = tree.by_id.get(row["node_id"])
            label = node.name if node is not None else f"{row['node_id']}（削除済み）"
            lines.append(f"**{row['due_date']}** — {label}: {row['name']}")
        shown = 50
        embed = info_embed("🏁 マイルストーン一覧", "\n".join(lines[:shown]))
        add_truncation_note(embed, len(lines), shown, "期限が近い順に表示しています")
        await interaction.followup.send(embed=embed)

    # ---------- /countdown ----------
    async def pace_overrides(self, guild_id: int) -> dict[str, Pace]:
        """桁巻きに紐付いたノードのペースを layer_records から作る。

        積層は作業日の履歴が残っているので、created_at / updated_at から
        推定するより実際の作業ペースに近い。
        """
        links = await self.repo.list_spar_links(guild_id)
        if not links:
            return {}
        dates_by_keta = await self.repo.list_layer_dates(guild_id)
        overrides: dict[str, Pace] = {}
        for link in links:
            raw = dates_by_keta.get(link["keta_name"], [])
            days = [d for d in (parse_date(x) for x in raw) if d is not None]
            pace = spar_pace(days, int(link["target_layers"] or 0))
            if pace.per_day is not None:
                overrides[link["node_id"]] = pace
        return overrides

    @app_commands.command(
        name="countdown", description="大会までの残り日数と、遅れているマイルストーンを表示します。"
    )
    @require(Level.L1)
    async def countdown(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        gconf = await config.for_guild(guild_id)
        if not gconf.competition_date:
            await interaction.followup.send(
                embed=info_embed("大会日が未設定です", COMPETITION_DATE_HELP), ephemeral=True
            )
            return

        today = now().date()
        tree = await self.load_tree(guild_id)
        rows = await self.repo.list_milestones(guild_id)
        statuses = evaluate_all(
            tree, rows, today=today, pace_by_node=await self.pace_overrides(guild_id)
        )
        await interaction.followup.send(
            embed=build_countdown_embed(gconf.competition_date, statuses, today)
        )

    async def _audit(
        self, guild_id: int, interaction: discord.Interaction, action: str, target: str, detail: str
    ) -> None:
        """監査ログへ記録する（記録の失敗で操作自体は止めない）。"""
        try:
            await AuditLogRepository(self.db).record(
                guild_id,
                actor_id=str(interaction.user.id),
                action=action,
                target=target,
                detail=detail,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("監査ログの記録に失敗 (guild=%s): %s", guild_id, type(e).__name__)


# ノード指定を受け取るコマンドへオートコンプリートを紐付ける
Progress.progress_add.autocomplete("parent")(Progress._node_autocomplete)
Progress.progress_edit.autocomplete("node")(Progress._node_autocomplete)
Progress.progress_edit.autocomplete("parent")(Progress._node_autocomplete)
Progress.progress_remove.autocomplete("node")(Progress._node_autocomplete)
Progress.progress_spar_link.autocomplete("node")(Progress._node_autocomplete)
Progress.progress_spar_link.autocomplete("keta")(Progress._keta_autocomplete)
Progress.weight_set.autocomplete("node")(Progress._node_autocomplete)
Progress.weight_view.autocomplete("node")(Progress._node_autocomplete)
Progress.milestone_add.autocomplete("node")(Progress._node_autocomplete)
Progress.milestone_remove.autocomplete("node")(Progress._node_autocomplete)


async def setup(bot: commands.Bot):
    await bot.add_cog(Progress(bot))
