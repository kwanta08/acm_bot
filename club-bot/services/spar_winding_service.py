"""桁巻き（スパーワインディング）→ 進捗ツリーの反映サービス。

完了層数は `/layer end` が書き込む layer_records（DB）から数え、目標層数は
progress_spar_links.target_layers が持つ。旧構成で必要だった
「桁巻きスプレッドシート（別ブック）」と Google Sheets 連携は不要になった。

進捗率 = 完了層数 ÷ 目標層数（1.0 でクランプ）。
紐付け先ノードの `manual_progress` へ書き込み、`source` を `spar_winding`
に設定する。このノードは Todoist 同期（source=todoist のみ更新）から
保護され、`/progress edit` で進捗を入れ直すと手入力へ戻る。

旧・桁巻きブックの「桁マスタ」を読む純粋関数（parse_master_grid）は、
移行スクリプト scripts/migrate_progress_sheet_to_db.py が目標層数を
取り込むために残している。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.progress_tree import SOURCE_SPAR_WINDING

SPAR_MASTER_SHEET = "桁マスタ"
SPAR_MASTER_HEADER = ["桁名", "目標層数"]

STATUS_NOT_STARTED = "未着手"
STATUS_IN_PROGRESS = "製作中"
STATUS_DONE = "完了"


def _cell(row: list, index: int) -> str:
    if index < len(row) and row[index] is not None:
        return str(row[index]).strip()
    return ""


def parse_master_grid(grid: list[list]) -> dict[str, int]:
    """旧・桁巻きブックの「桁マスタ」を {桁名: 目標層数} へ変換する。

    目標層数が正の整数でない行はスキップする。移行時のみ使用する。
    """
    out: dict[str, int] = {}
    for row in grid[1:]:
        name = _cell(row, 0)
        target = _cell(row, 1)
        if name and target.isdigit() and int(target) > 0:
            out[name] = int(target)
    return out


def progress_status(progress: float) -> str:
    """進捗率から `状態` の値を導出する。"""
    if progress >= 1.0:
        return STATUS_DONE
    if progress > 0.0:
        return STATUS_IN_PROGRESS
    return STATUS_NOT_STARTED


# ---------------------------------------------------------------------
# DB ベース（正本 = progress_nodes / layer_records）
# ---------------------------------------------------------------------
@dataclass
class SparSyncPlan:
    """桁巻き進捗を progress_nodes へ反映するための更新計画。"""
    # (node_id, 進捗率 0.0〜1.0)
    updates: list[tuple[str, float]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def updated(self) -> int:
        return len(self.updates)


def plan_spar_sync(node_ids: set[str], links: list[dict[str, Any]],
                   completed_by_keta: dict[str, int]) -> SparSyncPlan:
    """桁の紐付けと完了層数から進捗更新計画を組み立てる（純粋関数）。

    進捗率 = 完了層数 ÷ 目標層数（1.0 でクランプ）。
    紐付け先ノードが存在しない桁はエラーとして記録しスキップする。
    まだ1層も記録が無い桁は進捗 0 として更新する（未着手の明示）。
    """
    plan = SparSyncPlan()
    for link in links:
        node_id = str(link["node_id"])
        if node_id not in node_ids:
            plan.errors.append(
                f"桁「{link['keta_name']}」の紐付け先ノード `{node_id}` が"
                "見つかりません")
            continue
        target = int(link["target_layers"])
        if target <= 0:
            plan.errors.append(
                f"桁「{link['keta_name']}」の目標層数が不正です（{target}）")
            continue
        done = int(completed_by_keta.get(link["keta_name"], 0))
        plan.updates.append((node_id, min(done / target, 1.0)))
    return plan


async def sync_spar_winding_db(repo: Any, guild_id: int,
                               now_text: str) -> SparSyncPlan:
    """桁巻きの進捗を該当ノードへ反映する。

    桁の紐付けが1件も無いギルドでは何もしない（エラーにはしない）。
    """
    links = await repo.list_spar_links(guild_id)
    if not links:
        return SparSyncPlan()
    node_ids = {row["node_id"] for row in await repo.list_nodes(guild_id)}
    plan = plan_spar_sync(node_ids, links,
                          await repo.count_completed_layers(guild_id))
    for node_id, progress in plan.updates:
        await repo.set_progress(
            guild_id, node_id, progress, now_text,
            status=progress_status(progress),
            source=SOURCE_SPAR_WINDING)
    return plan
