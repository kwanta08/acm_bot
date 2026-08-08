"""桁巻き（スパーワインディング）データ → 進捗ツリーの反映サービス。

桁巻きデータは中央スプレッドシートとは別ファイル（桁巻きブック）で管理する。
ブック ID は中央シート「設定」タブの `桁巻きスプレッドシートID` に保持し、
中央シート「桁巻き対応表」（桁巻きファイル内の識別子 → 紐付け先ノードID）を
介して進捗ツリーの葉ノードへ橋渡しする。

桁巻きブックのスキーマ（旧・桁巻き Sheets 構成
（scripts/migrate_sheets_to_db.py の LAYER_COLS）と整合）:
- 「桁マスタ」シート : [桁名, 目標層数]。桁名は桁別シートのシート名と一致させる
- 桁別シート（シート名=桁名）: [層番号, 作業者, 開始, 終了, 作業時間(分)]

進捗率 = 完了層数（終了が記入された行の層番号の種類数）÷ 目標層数。
対応する葉ノードの `進捗率(手入力)` に書き戻し、`ソース` を
`spar_winding` に設定する。この行は Todoist 同期（ソース=todoist のみ更新）
から保護され、人手入力も毎同期で上書きされるため実質無効となる。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from services import progress_sheet_service as pss
from services.progress_tree import ProgressNode

SPAR_MASTER_SHEET = "桁マスタ"
SPAR_MASTER_HEADER = ["桁名", "目標層数"]
SPAR_LAYER_HEADER = ["層番号", "作業者", "開始", "終了", "作業時間(分)"]

STATUS_NOT_STARTED = "未着手"
STATUS_IN_PROGRESS = "製作中"
STATUS_DONE = "完了"


def _cell(row: list, index: int) -> str:
    if index < len(row) and row[index] is not None:
        return str(row[index]).strip()
    return ""


# ---------------------------------------------------------------------
# 純粋関数: 桁巻きブックの解釈
# ---------------------------------------------------------------------
def parse_master_grid(grid: list[list]) -> dict[str, int]:
    """「桁マスタ」を {桁名: 目標層数} へ変換する。

    目標層数が正の整数でない行はスキップする。
    """
    out: dict[str, int] = {}
    for row in grid[1:]:
        name = _cell(row, 0)
        target = _cell(row, 1)
        if name and target.isdigit() and int(target) > 0:
            out[name] = int(target)
    return out


def count_completed_layers(grid: list[list]) -> int:
    """桁別シートから完了層数（終了が記入された層番号の種類数）を数える。

    同じ層番号の行が複数あっても1層として数える（巻き直し等）。
    """
    seen: set[str] = set()
    for row in grid[1:]:
        layer = _cell(row, 0)
        ended = _cell(row, 3)
        if layer and ended:
            seen.add(layer)
    return len(seen)


def progress_status(progress: float) -> str:
    """進捗率から `状態` 列の値を導出する（ダッシュボードの円グラフ用）。"""
    if progress >= 1.0:
        return STATUS_DONE
    if progress > 0.0:
        return STATUS_IN_PROGRESS
    return STATUS_NOT_STARTED


@dataclass
class SparPlan:
    """桁巻き進捗をシートへ反映するための更新計画。"""
    cell_ranges: list[dict[str, Any]] = field(default_factory=list)
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def plan_spar_updates(nodes: list[ProgressNode],
                      mappings: list[dict[str, str]],
                      progress_by_key: dict[str, float]) -> SparPlan:
    """桁巻き対応表と進捗率から進捗管理シートの更新計画を組み立てる。

    対象葉ノードの `進捗率(手入力)`（H）・`状態`（G）を更新し、
    `ソース`（K）を spar_winding に設定する。
    """
    plan = SparPlan()
    by_id = {n.node_id: n for n in nodes}

    for m in mappings:
        node = by_id.get(m["node_id"])
        if node is None or node.row_index is None:
            plan.errors.append(
                f"桁巻き対応表の紐付け先ノード `{m['node_id']}` が"
                "シートに見つかりません")
            continue
        progress = progress_by_key.get(m["spar_key"])
        if progress is None:
            continue  # 取得段階のエラーは collect 側で記録済み
        r = node.row_index
        progress = min(max(progress, 0.0), 1.0)
        plan.cell_ranges.append({
            "range": f"'{pss.PROGRESS_SHEET}'!G{r}:H{r}",
            "values": [[progress_status(progress), round(progress, 4)]]})
        if node.source != pss.SOURCE_SPAR_WINDING:
            plan.cell_ranges.append({
                "range": f"'{pss.PROGRESS_SHEET}'!K{r}",
                "values": [[pss.SOURCE_SPAR_WINDING]]})
        plan.updated += 1
    return plan


# ---------------------------------------------------------------------
# オーケストレーション
# ---------------------------------------------------------------------
async def collect_spar_progress(
        client: pss.ProgressSheetClient, spar_book_id: str,
        spar_keys: list[str]) -> tuple[dict[str, float], list[str]]:
    """桁巻きブックから各桁の進捗率（完了層数÷目標層数）を集める。

    戻り値: ({桁名: 進捗率}, エラー一覧)
    """
    errors: list[str] = []
    try:
        master_grid = await asyncio.to_thread(
            client.read_grid, spar_book_id, SPAR_MASTER_SHEET)
    except Exception:  # noqa: BLE001  (シート不在・権限エラー等)
        return {}, [f"桁巻きブックの「{SPAR_MASTER_SHEET}」シートを"
                    "読み込めません（シート名・共有設定を確認してください）"]
    master = parse_master_grid(master_grid)

    out: dict[str, float] = {}
    for key in spar_keys:
        target = master.get(key)
        if target is None:
            errors.append(f"「{SPAR_MASTER_SHEET}」に桁 `{key}` の"
                          "目標層数がありません")
            continue
        try:
            layer_grid = await asyncio.to_thread(
                client.read_grid, spar_book_id, key)
        except Exception:  # noqa: BLE001
            errors.append(f"桁巻きブックに桁別シート `{key}` が見つかりません")
            continue
        out[key] = min(count_completed_layers(layer_grid) / target, 1.0)
    return out, errors


async def sync_spar_winding(client: pss.ProgressSheetClient,
                            spreadsheet_id: str,
                            sheet_settings: dict[str, str]) -> SparPlan:
    """桁巻きブックの進捗を中央シートの該当葉ノードへ反映する。

    桁巻きスプレッドシートID 未設定・対応表が空なら何もしない
    （エラーにはしない）。
    """
    plan = SparPlan()
    spar_book_id = (sheet_settings.get(pss.SHEET_KEY_SPAR_BOOK) or "").strip()
    if not spar_book_id:
        return plan
    mappings = pss.parse_spar_mapping_grid(await asyncio.to_thread(
        client.read_spar_mapping_grid, spreadsheet_id))
    if not mappings:
        return plan

    progress_by_key, errors = await collect_spar_progress(
        client, spar_book_id, [m["spar_key"] for m in mappings])
    plan.errors.extend(errors)

    grid = await asyncio.to_thread(client.read_progress_grid, spreadsheet_id)
    computed = plan_spar_updates(
        pss.grid_to_nodes(grid), mappings, progress_by_key)
    plan.errors.extend(computed.errors)
    plan.cell_ranges = computed.cell_ranges
    plan.updated = computed.updated

    await asyncio.to_thread(
        client.apply_value_ranges, spreadsheet_id, plan.cell_ranges)
    return plan
