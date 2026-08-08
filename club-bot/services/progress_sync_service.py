"""Todoist → 進捗管理シートの同期サービス。

Todoist の親子構造（task.id / task.parent_id）を、シートの
ID（`td_` プレフィックス付き）/ 親ID にそのままマッピングして upsert する。

同期の流れ（sync_guild）:
  1. Todoist対応表シートから「プロジェクト名 → 紐付け先ノードID」を取得
  2. 各プロジェクトのアクティブタスクを取得し、`td_<id>` で upsert
     （トップレベルタスクは対応ノードへ、サブタスクは親タスクへぶら下げる）
  3. 前回シートに存在したのに今回アクティブに無い td_ 行は完了扱い
     （進捗率 100%・状態=完了）
  4. ツリーを再構築し、深さ・集計進捗率・更新日時を書き戻す

安全策:
- `ソース=manual` の行は一切変更しない
- 完了マーク付けは「現在対応表に載っているアンカー配下の td_ 行」に限定する
  （対応表から外したプロジェクトの行を誤って完了扱いしない）
- いずれかのプロジェクトの取得に失敗した場合は同期全体を中断する
  （部分的なアクティブ一覧で誤完了させない）
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from repositories.settings_repository import SettingsRepository
from services import progress_sheet_service as pss
from services.progress_tree import (
    ProgressNode,
    ProgressTree,
    build_and_aggregate,
    build_tree,
)
from utils.db import Database
from utils.logger import get_logger
from utils.parser import now

log = get_logger("progress_sync")

STATUS_DONE = "完了"


async def get_spreadsheet_id(db: Database, guild_id: int) -> str | None:
    """ギルド別 settings から進捗シートの ID を取得する。未設定なら None。"""
    value = await SettingsRepository(db).get(guild_id, pss.SETTINGS_KEY)
    value = (value or "").strip()
    return value or None


@dataclass
class UpsertPlan:
    """Todoist 取得結果をシートへ反映するための更新計画（純粋データ）。"""
    cell_ranges: list[dict[str, Any]] = field(default_factory=list)
    new_rows: list[list[Any]] = field(default_factory=list)
    added: int = 0
    updated: int = 0
    completed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """1ギルド分の同期結果（通知・ログ用）。"""
    spreadsheet_id: str
    projects: int = 0
    added: int = 0
    updated: int = 0
    completed: int = 0
    errors: list[str] = field(default_factory=list)


def _descendant_todoist_rows(tree: ProgressTree,
                             anchor_ids: list[str]) -> dict[str, ProgressNode]:
    """アンカー配下（自身を除く）の source=todoist 行を td_ ID で返す。"""
    out: dict[str, ProgressNode] = {}
    for anchor_id in anchor_ids:
        anchor = tree.by_id.get(anchor_id)
        if anchor is None:
            continue
        stack = list(anchor.children)
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if node.source == pss.SOURCE_TODOIST:
                out[node.node_id] = node
    return out


def plan_todoist_upsert(nodes: list[ProgressNode],
                        anchors: list[tuple[str, list[Any]]],
                        now_text: str) -> UpsertPlan:
    """アクティブタスク一覧からシート更新計画を組み立てる（純粋関数）。

    anchors: [(紐付け先ノードID, そのプロジェクトのアクティブタスク一覧)]
    タスクは todoist_api_python の Task 互換（id / content / parent_id）であればよい。
    """
    plan = UpsertPlan()
    tree = build_tree(nodes)

    valid_anchor_ids: list[str] = []
    for anchor_id, _tasks in anchors:
        if anchor_id in tree.by_id:
            valid_anchor_ids.append(anchor_id)
        else:
            plan.errors.append(
                f"対応表の紐付け先ノード `{anchor_id}` がシートに見つかりません")

    # 現在アクティブな td_ ID の集合（全アンカー横断）
    active_ids: set[str] = set()
    for anchor_id, tasks in anchors:
        for task in tasks:
            active_ids.add(f"{pss.TODOIST_ID_PREFIX}{task.id}")

    for anchor_id, tasks in anchors:
        if anchor_id not in tree.by_id:
            continue
        for seq, task in enumerate(tasks, start=1):
            node_id = f"{pss.TODOIST_ID_PREFIX}{task.id}"
            raw_parent = getattr(task, "parent_id", None)
            parent_td = (f"{pss.TODOIST_ID_PREFIX}{raw_parent}"
                         if raw_parent else None)
            # 親タスクがアクティブ一覧かシートに存在すればそこへ、
            # 無ければ（親が先に完了した等）アンカー直下へぶら下げる
            if parent_td and (parent_td in active_ids
                              or parent_td in tree.by_id):
                parent_id = parent_td
            else:
                parent_id = anchor_id

            existing = tree.by_id.get(node_id)
            if existing is None or existing.row_index is None:
                plan.new_rows.append([
                    node_id, parent_id, seq, "", str(task.content),
                    "", "", 0, "", "", pss.SOURCE_TODOIST,
                    str(task.id), now_text,
                ])
                plan.added += 1
                continue
            if existing.source != pss.SOURCE_TODOIST:
                continue  # manual 行は絶対に上書きしない
            r = existing.row_index
            changed = False
            if (existing.parent_id or None) != parent_id:
                plan.cell_ranges.append({
                    "range": f"'{pss.PROGRESS_SHEET}'!B{r}",
                    "values": [[parent_id]]})
                changed = True
            if existing.name != str(task.content):
                plan.cell_ranges.append({
                    "range": f"'{pss.PROGRESS_SHEET}'!E{r}",
                    "values": [[str(task.content)]]})
                changed = True
            if changed:
                plan.updated += 1

    # 完了検知: 現在のアンカー配下の td_ 行のうちアクティブに無いもの
    for node_id, node in _descendant_todoist_rows(tree, valid_anchor_ids).items():
        if node_id in active_ids or node.row_index is None:
            continue
        if node.manual_progress == 1.0 and node.status == STATUS_DONE:
            continue  # 反映済み
        r = node.row_index
        plan.cell_ranges.append({
            "range": f"'{pss.PROGRESS_SHEET}'!G{r}:H{r}",
            "values": [[STATUS_DONE, 1]]})
        plan.completed += 1

    return plan


def _now_text() -> str:
    return now().strftime("%Y-%m-%d %H:%M")


async def recalculate(client: pss.ProgressSheetClient,
                      spreadsheet_id: str) -> ProgressTree:
    """シートを読み込み、集計・深さを計算して書き戻す。

    戻り値のツリーには循環参照等のエラー一覧（tree.errors）が含まれる。
    """
    grid = await asyncio.to_thread(client.read_progress_grid, spreadsheet_id)
    tree = build_and_aggregate(pss.grid_to_nodes(grid))
    ranges = pss.build_writeback_ranges(grid, tree, _now_text())
    await asyncio.to_thread(client.apply_value_ranges, spreadsheet_id, ranges)
    return tree


async def sync_guild(db: Database, guild_id: int, todoist_svc: Any,
                     client: pss.ProgressSheetClient) -> SyncResult | None:
    """1ギルド分の Todoist 同期＋再集計を行う。

    進捗シート未設定のギルドは None を返して自動スキップする（例外にしない）。
    Todoist 未設定のギルドは再集計のみ行う。
    Todoist API の失敗（TodoistError）・Sheets の失敗は呼び出し側へ伝播する。
    """
    spreadsheet_id = await get_spreadsheet_id(db, guild_id)
    if spreadsheet_id is None:
        return None

    result = SyncResult(spreadsheet_id=spreadsheet_id)

    if todoist_svc is not None and getattr(todoist_svc, "enabled", False):
        mapping_grid = await asyncio.to_thread(
            client.read_mapping_grid, spreadsheet_id)
        mappings = pss.parse_mapping_grid(mapping_grid)
        if mappings:
            projects = await todoist_svc.get_projects()
            project_ids = {getattr(p, "name", ""): str(p.id) for p in projects}
            anchors: list[tuple[str, list[Any]]] = []
            for m in mappings:
                project_id = project_ids.get(m["project_name"])
                if project_id is None:
                    result.errors.append(
                        f"Todoist プロジェクト「{m['project_name']}」が"
                        "見つかりません")
                    continue
                tasks = await todoist_svc.get_tasks(project_id=project_id)
                anchors.append((m["node_id"], tasks))
            result.projects = len(anchors)

            if anchors:
                grid = await asyncio.to_thread(
                    client.read_progress_grid, spreadsheet_id)
                plan = plan_todoist_upsert(
                    pss.grid_to_nodes(grid), anchors, _now_text())
                result.errors.extend(plan.errors)
                result.added = plan.added
                result.updated = plan.updated
                result.completed = plan.completed
                await asyncio.to_thread(
                    client.apply_value_ranges, spreadsheet_id,
                    plan.cell_ranges)
                await asyncio.to_thread(
                    client.append_progress_rows, spreadsheet_id,
                    plan.new_rows)

    tree = await recalculate(client, spreadsheet_id)
    result.errors.extend(
        f"行スキップ: {e.node_id or '(ID なし)'} — {e.reason}"
        for e in tree.errors)
    log.info("進捗同期完了 (guild=%s): +%d行 / 更新%d / 完了%d / エラー%d",
             guild_id, result.added, result.updated, result.completed,
             len(result.errors))
    return result
