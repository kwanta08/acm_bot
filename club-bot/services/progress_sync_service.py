"""Todoist → 機体進捗ツリー（DB）の同期サービス。

Todoist の親子構造（task.id / task.parent_id）を progress_nodes の
node_id（`td_` プレフィックス付き）/ parent_id にそのままマッピングして
upsert する。正本は DB であり、Google Sheets には一切依存しない。

同期の流れ（sync_guild_db）:
  1. progress_todoist_links から「プロジェクト名 → 紐付け先ノードID」を取得
  2. 各プロジェクトのアクティブタスクを取得し、`td_<id>` で upsert
     （トップレベルタスクは対応ノードへ、サブタスクは親タスクへぶら下げる）
  3. 前回 DB に存在したのに今回アクティブに無い td_ ノードは完了扱い
     （進捗率 100%・状態=完了）
  4. 桁巻き（layer_records）の完了層数から該当ノードの進捗率を更新
  5. ツリーを再構築して集計結果を返す（表示用）

安全策:
- `source=manual` / `source=spar_winding` のノードは一切変更しない
- 完了マーク付けは「現在紐付いているアンカー配下の todoist ノード」に限定する
  （紐付けを外したプロジェクトのノードを誤って完了扱いしない）
- いずれかのプロジェクトの取得に失敗した場合は同期全体を中断する
  （部分的なアクティブ一覧で誤完了させない）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repositories.progress_repository import ProgressRepository
from repositories.settings_repository import SettingsRepository
from services import progress_tree as pt
from services import spar_winding_service
from services.progress_tree import ProgressNode, ProgressTree, build_tree
from utils.db import Database
from utils.logger import get_logger
from utils.parser import now

log = get_logger("progress_sync")

STATUS_DONE = "完了"

# Todoist 由来ノードの ID プレフィックス（旧シート版と同一。移行後も
# 既存ノードの ID が変わらないようにするため値を変えないこと）
TODOIST_ID_PREFIX = "td_"


# =====================================================================
# DB ベースの同期（正本 = progress_nodes）
# =====================================================================
# 旧「設定」タブのデフォルト通知チャンネルに相当する settings キー。
SETTINGS_DEFAULT_CHANNEL_KEY = "PROGRESS_DEFAULT_CHANNEL_ID"

# 進捗の通知先を解決する順序。
#
# 綴りの違う2つのキーが並んでいるのは歴史的な事情による。移行スクリプトと
# ダッシュボードは PROGRESS_DEFAULT_CHANNEL_ID を書き、/setup ウィザードと
# /settings は DEFAULT_PROGRESS_CHANNEL_ID を書く。片方しか見ていなかったため、
# /setup しかしていないサーバーには週次のマイルストーン警告が一度も届かず、
# しかもログにも残らなかった。両方を見てからタスクチャンネルへ落とす。
DEFAULT_CHANNEL_KEYS = (
    SETTINGS_DEFAULT_CHANNEL_KEY,
    "DEFAULT_PROGRESS_CHANNEL_ID",
    "DEFAULT_TASK_CHANNEL_ID",
)


def _now_text() -> str:
    """progress_nodes の created_at / updated_at に入れる時刻文字列。"""
    return now().strftime("%Y-%m-%d %H:%M")


@dataclass
class DbSyncPlan:
    """Todoist 取得結果を progress_nodes へ反映するための更新計画（純粋データ）。"""

    creates: list[dict[str, Any]] = field(default_factory=list)
    updates: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    completions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return len(self.creates)

    @property
    def updated(self) -> int:
        return len(self.updates)

    @property
    def completed(self) -> int:
        return len(self.completions)


@dataclass
class GuildSyncResult:
    """1ギルド分の同期結果（通知・ログ用）。"""

    guild_id: int
    projects: int = 0
    added: int = 0
    updated: int = 0
    completed: int = 0
    spar_updated: int = 0
    errors: list[str] = field(default_factory=list)
    tree: ProgressTree | None = None


def _descendant_todoist_nodes(tree: ProgressTree, anchor_ids: list[str]) -> dict[str, ProgressNode]:
    """アンカー配下（自身を除く）の source=todoist ノードを返す。"""
    out: dict[str, ProgressNode] = {}
    for anchor_id in anchor_ids:
        anchor = tree.by_id.get(anchor_id)
        if anchor is None:
            continue
        stack = list(anchor.children)
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.source == pt.SOURCE_TODOIST:
                out[node.node_id] = node
    return out


def plan_todoist_sync(
    nodes: list[ProgressNode], anchors: list[tuple[str, list[Any]]]
) -> DbSyncPlan:
    """アクティブタスク一覧から progress_nodes の更新計画を組み立てる（純粋関数）。

    anchors: [(紐付け先ノードID, そのプロジェクトのアクティブタスク一覧)]
    タスクは todoist_api_python の Task 互換（id / content / parent_id）でよい。

    安全策（シート版から引き継ぐ）:
    - source=manual / spar_winding のノードは絶対に上書きしない
    - 完了マーク付けは「現在紐付いているアンカー配下の todoist ノード」に限定する
    """
    plan = DbSyncPlan()
    tree = build_tree(nodes)

    valid_anchor_ids: list[str] = []
    for anchor_id, _tasks in anchors:
        if anchor_id in tree.by_id:
            valid_anchor_ids.append(anchor_id)
        else:
            plan.errors.append(f"紐付け先ノード `{anchor_id}` が進捗ツリーに見つかりません")

    active_ids: set[str] = {
        f"{TODOIST_ID_PREFIX}{task.id}" for _anchor_id, tasks in anchors for task in tasks
    }

    for anchor_id, tasks in anchors:
        if anchor_id not in tree.by_id:
            continue
        for seq, task in enumerate(tasks, start=1):
            node_id = f"{TODOIST_ID_PREFIX}{task.id}"
            raw_parent = getattr(task, "parent_id", None)
            parent_td = f"{TODOIST_ID_PREFIX}{raw_parent}" if raw_parent else None
            # 親タスクがアクティブかツリーに在ればそこへ、
            # 無ければ（親が先に完了した等）アンカー直下へぶら下げる
            if parent_td and (parent_td in active_ids or parent_td in tree.by_id):
                parent_id = parent_td
            else:
                parent_id = anchor_id

            existing = tree.by_id.get(node_id)
            if existing is None:
                plan.creates.append(
                    {
                        "node_id": node_id,
                        "parent_id": parent_id,
                        "sort_order": float(seq),
                        "name": str(task.content),
                        "manual_progress": 0.0,
                        "source": pt.SOURCE_TODOIST,
                        "todoist_task_id": str(task.id),
                    }
                )
                continue
            if existing.source != pt.SOURCE_TODOIST:
                continue  # 手入力・桁巻き由来の行は上書きしない
            fields: dict[str, Any] = {}
            if (existing.parent_id or None) != parent_id:
                fields["parent_id"] = parent_id
            if existing.name != str(task.content):
                fields["name"] = str(task.content)
            if fields:
                plan.updates.append((node_id, fields))

    for node_id, node in _descendant_todoist_nodes(tree, valid_anchor_ids).items():
        if node_id in active_ids:
            continue
        if node.manual_progress == 1.0 and node.status == STATUS_DONE:
            continue  # 反映済み
        plan.completions.append(node_id)

    return plan


async def apply_todoist_plan(repo: Any, guild_id: int, plan: DbSyncPlan, now_text: str) -> None:
    """更新計画を DB へ適用する。"""
    for kwargs in plan.creates:
        await repo.upsert_node(guild_id, now_text=now_text, **kwargs)
    for node_id, fields in plan.updates:
        await repo.update_node(guild_id, node_id, now_text, **fields)
    for node_id in plan.completions:
        await repo.set_progress(guild_id, node_id, 1.0, now_text, status=STATUS_DONE)


async def resolve_default_channel_id(db: Database, guild_id: int) -> int | None:
    """ギルドの既定通知チャンネル（settings）を返す。どれも未設定なら None。

    DEFAULT_CHANNEL_KEYS の順に見て、最初に見つかった数値を採用する。
    """
    repo = SettingsRepository(db)
    for key in DEFAULT_CHANNEL_KEYS:
        raw = (await repo.get(guild_id, key) or "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def resolve_link_channel_id(link: dict[str, Any], default_channel_id: int | None) -> int | None:
    """タスク通知の送信先を解決する。

    優先順: 紐付け行の通知チャンネル（プロジェクト専用）> ギルドの既定。
    どちらも無ければ None（呼び出し側でギルドのタスクチャンネルへ）。
    """
    raw = str(link.get("notify_channel_id") or "").strip()
    if raw.isdigit():
        return int(raw)
    return default_channel_id


async def sync_guild_db(db: Database, guild_id: int, todoist_svc: Any) -> GuildSyncResult:
    """1ギルド分の Todoist 同期＋桁巻き反映＋再集計（DB 版）。

    - Todoist 未設定・紐付け0件のギルドは桁巻き反映と再集計だけ行う
    - いずれかのプロジェクト取得に失敗した場合は例外を呼び出し側へ伝播する
      （部分的なアクティブ一覧で誤完了させない）
    """
    repo = ProgressRepository(db)
    result = GuildSyncResult(guild_id=guild_id)
    now_text = _now_text()

    links = await repo.list_todoist_links(guild_id)
    if links and todoist_svc is not None and getattr(todoist_svc, "enabled", False):
        projects = await todoist_svc.get_projects()
        project_ids = {getattr(p, "name", ""): str(p.id) for p in projects}
        anchors: list[tuple[str, list[Any]]] = []
        for link in links:
            project_id = project_ids.get(link["project_name"])
            if project_id is None:
                result.errors.append(
                    f"Todoist プロジェクト「{link['project_name']}」が見つかりません"
                )
                continue
            anchors.append((link["node_id"], await todoist_svc.get_tasks(project_id=project_id)))
        result.projects = len(anchors)

        if anchors:
            nodes = pt.nodes_from_rows(await repo.list_nodes(guild_id))
            plan = plan_todoist_sync(nodes, anchors)
            await apply_todoist_plan(repo, guild_id, plan, now_text)
            result.added = plan.added
            result.updated = plan.updated
            result.completed = plan.completed
            result.errors.extend(plan.errors)

    spar_plan = await spar_winding_service.sync_spar_winding_db(repo, guild_id, now_text)
    result.spar_updated = spar_plan.updated
    result.errors.extend(spar_plan.errors)

    tree = await pt.load_tree(repo, guild_id)
    result.tree = tree
    result.errors.extend(
        f"ノードをスキップ: {e.node_id or '(ID なし)'} — {e.reason}" for e in tree.errors
    )
    log.info(
        "進捗同期完了 (guild=%s): +%d / 更新%d / 完了%d / 桁巻き%d / エラー%d",
        guild_id,
        result.added,
        result.updated,
        result.completed,
        result.spar_updated,
        len(result.errors),
    )
    return result


async def sync_all_guilds(
    db: Database, guild_ids: list[int], todoist_manager: Any
) -> list[GuildSyncResult]:
    """参加中の全ギルドを順に同期する（1ギルドの失敗は他を止めない）。"""
    results: list[GuildSyncResult] = []
    for guild_id in guild_ids:
        try:
            svc = await todoist_manager.for_guild(guild_id)
        except Exception as e:  # noqa: BLE001  (トークン復号失敗等)
            log.warning("Todoist サービス取得失敗 (guild=%s): %s", guild_id, type(e).__name__)
            svc = None
        try:
            results.append(await sync_guild_db(db, guild_id, svc))
        except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
            log.warning("進捗同期失敗 (guild=%s): %s", guild_id, type(e).__name__)
            results.append(
                GuildSyncResult(
                    guild_id=guild_id, errors=[f"同期に失敗しました（{type(e).__name__}）"]
                )
            )
    return results
