"""機体進捗ツリーの構築・集計ロジック。

正本は DB の progress_nodes テーブル（隣接リスト形式）。その行データから
メモリ上にツリーを構築し、集計進捗率・深さを計算する。

- 構築・集計は純粋関数（DB / Discord に依存しない）。DB からの読み込みは
  load_tree() が ProgressRepository 経由で行う
- 深さは無制限（機体 → パーツ → 部品 → サブタスク → …）
- 葉ノード: 進捗率(手入力) をそのまま集計進捗率に採用
- 親ノード: 子の集計進捗率の加重平均（現状は全ノード weight=1.0 の単純平均。
  progress_nodes.weight に 1 以外を入れれば重み付け平均になる）
- 進捗率は内部的に 0.0〜1.0 の小数で扱う
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 進捗ノードの由来（progress_nodes.source）
SOURCE_MANUAL = "manual"
SOURCE_TODOIST = "todoist"
SOURCE_SPAR_WINDING = "spar_winding"


@dataclass
class ProgressNode:
    """progress_nodes の1行に対応するノード。"""
    node_id: str
    parent_id: str | None = None
    order: float = 0.0            # 表示順（兄弟間の並び。DB の sort_order）
    name: str = ""
    assignee: str = ""
    status: str = ""
    manual_progress: float | None = None  # 進捗率(手入力)。0.0〜1.0
    source: str = SOURCE_MANUAL   # manual / todoist / spar_winding
    todoist_task_id: str = ""
    # 旧・中央スプレッドシート上の行番号（1始まり）。
    # 移行スクリプトがシートを読むときだけ設定する。DB 経路では常に None
    row_index: int | None = None
    weight: float = 1.0           # 集計時の重み
    # 重量（g）。None は未入力。人力飛行機では重量が競技成績に直結するため、
    # 進捗と同じ木で積み上げる
    target_weight_g: float | None = None
    actual_weight_g: float | None = None
    # 進捗ペースの推定に使う（大会からの逆算）。DB の同名列をそのまま持つ
    created_at: str = ""
    updated_at: str = ""

    # 計算結果（compute_tree が設定する）
    depth: int | None = None
    aggregated: float | None = None
    # 集計重量（g）。実測が入っていればそれ、無ければ子の合計。
    # すべて未入力の部分木では None のまま（0.0 と区別する）
    aggregated_actual_weight_g: float | None = None
    aggregated_target_weight_g: float | None = None
    children: list[ProgressNode] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class TreeError:
    """ツリー構築・集計中に検出した異常（該当ノードはスキップされる）。"""
    node_id: str
    reason: str


@dataclass
class ProgressTree:
    """構築済みツリー。roots は表示順でソート済み。"""
    roots: list[ProgressNode] = field(default_factory=list)
    by_id: dict[str, ProgressNode] = field(default_factory=dict)
    errors: list[TreeError] = field(default_factory=list)


def parse_progress(value: object) -> float | None:
    """入力値を 0.0〜1.0 の進捗率に正規化する。

    スラッシュコマンドの引数と、移行時に読む旧シートのセル値の両方を扱う。
    受け付ける形式: 0.5 / "0.5" / "50%" / "50"（1 より大きい数値は % とみなす）。
    空・解釈不能は None（未入力扱い）。範囲外は 0.0〜1.0 にクランプする。
    """
    if value is None:
        return None
    is_percent = False
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        text = str(value).strip().replace("％", "%")
        if not text:
            return None
        is_percent = text.endswith("%")
        if is_percent:
            text = text[:-1].strip()
        try:
            num = float(text)
        except ValueError:
            return None
    if is_percent or num > 1.0:
        num /= 100.0
    return min(max(num, 0.0), 1.0)


def build_tree(nodes: list[ProgressNode]) -> ProgressTree:
    """ノード一覧（隣接リスト）からツリーを構築する。

    - ID 重複行はエラーとして記録し、後勝ちにせず最初の行のみ採用する
    - 親 ID が存在しない行（孤児）・循環参照はエラーとして記録し、
      ツリーから除外する（集計・表示の対象にしない）
    """
    tree = ProgressTree()

    for node in nodes:
        node.children = []
        if not node.node_id:
            tree.errors.append(TreeError("", "ID が空の行があります"))
            continue
        if node.node_id in tree.by_id:
            tree.errors.append(TreeError(
                node.node_id, "ID が重複しています（最初の行のみ採用）"))
            continue
        tree.by_id[node.node_id] = node

    excluded = _find_invalid_ids(tree)

    for node_id in excluded:
        tree.by_id.pop(node_id, None)

    for node in tree.by_id.values():
        if node.parent_id:
            tree.by_id[node.parent_id].children.append(node)
        else:
            tree.roots.append(node)

    tree.roots.sort(key=lambda n: (n.order, n.node_id))
    for node in tree.by_id.values():
        node.children.sort(key=lambda n: (n.order, n.node_id))
    return tree


def _find_invalid_ids(tree: ProgressTree) -> set[str]:
    """孤児（親 ID 不明）・循環参照に該当するノード ID の集合を返す。

    各ノードから親を辿り、同一 ID を2回訪れたら循環と判定する
    （無限ループに陥らない）。孤児・循環ノードの子孫も道連れで除外される
    （ルートに到達できないため）。判定結果はノードごとに tree.errors へ
    記録する。
    """
    invalid: set[str] = set()
    valid: set[str] = set()

    for start_id in tree.by_id:
        if start_id in invalid or start_id in valid:
            continue
        path: list[str] = []
        seen: set[str] = set()
        current: str | None = start_id
        reason = None
        while current is not None:
            if current in valid:
                break
            if current in invalid:
                reason = "親が異常ノード（循環参照・孤児）に該当します"
                break
            if current in seen:
                reason = "循環参照を検出しました"
                break
            seen.add(current)
            path.append(current)
            parent_id = tree.by_id[current].parent_id or None
            if parent_id is None:
                break  # ルートに到達
            if parent_id not in tree.by_id:
                reason = f"親 ID `{parent_id}` が見つかりません"
                break
            current = parent_id

        if reason is None:
            valid.update(path)
        else:
            for node_id in path:
                invalid.add(node_id)
                tree.errors.append(TreeError(node_id, reason))
    return invalid


def _resolve_weight(own: float | None,
                    child_values: list[float | None]) -> float | None:
    """重量の集計規則。

    **実測値（またはそのノードに入力された値）が入っていればそれを採用**し、
    無ければ子の合計を積み上げる。機体側で実測した値のほうが、部品の
    見積もりを足し上げた値より信用できるため。

    子がすべて未入力なら None を返す（「0 g」と「未計測」を区別する）。
    """
    if own is not None:
        return float(own)
    present = [v for v in child_values if v is not None]
    if not present:
        return None
    return sum(present)


def aggregate(tree: ProgressTree) -> None:
    """集計進捗率・集計重量と深さを末端から根に向けて計算し、各ノードに書き込む。

    - 葉: manual_progress（未入力は 0.0）
    - 親: 子の aggregated の加重平均（weight は現状すべて 1.0）
    - depth: ルート = 0
    - 重量: 自ノードの入力値が優先、無ければ子の合計（_resolve_weight）
    build_tree 済みのツリーは非循環が保証されているため再帰は停止する。
    Python の再帰上限を避けるため明示的なスタックで後行順に処理する。
    """
    for root in tree.roots:
        # 深さ: 行きがけに設定
        stack: list[ProgressNode] = [root]
        root.depth = 0
        order: list[ProgressNode] = []
        while stack:
            node = stack.pop()
            order.append(node)
            for child in node.children:
                child.depth = (node.depth or 0) + 1
                stack.append(child)
        # 集計: 帰りがけ（子 → 親の順）に計算
        for node in reversed(order):
            if node.is_leaf:
                node.aggregated = node.manual_progress or 0.0
            else:
                total_weight = sum(c.weight for c in node.children)
                if total_weight <= 0:
                    node.aggregated = 0.0
                else:
                    node.aggregated = sum(
                        (c.aggregated or 0.0) * c.weight
                        for c in node.children) / total_weight
            # 重量は進捗率と同じ後行順の中で積み上げる
            node.aggregated_actual_weight_g = _resolve_weight(
                node.actual_weight_g,
                [c.aggregated_actual_weight_g for c in node.children])
            node.aggregated_target_weight_g = _resolve_weight(
                node.target_weight_g,
                [c.aggregated_target_weight_g for c in node.children])


def build_and_aggregate(nodes: list[ProgressNode]) -> ProgressTree:
    """build_tree + aggregate を一括で行うヘルパー。"""
    tree = build_tree(nodes)
    aggregate(tree)
    return tree


# ---------------------------------------------------------------------
# 重量サマリ
# ---------------------------------------------------------------------
@dataclass
class WeightSummary:
    """ツリー（または部分木）の重量集計。

    measured / total は「実測がどれだけ埋まっているか」を示す。
    実測が少ないほど集計値は見積もりに近く、確度が低い。
    """
    actual_g: float | None = None
    target_g: float | None = None
    measured_nodes: int = 0
    total_nodes: int = 0

    @property
    def fill_rate(self) -> float:
        """実測が入っているノードの割合（0.0〜1.0）。"""
        if self.total_nodes <= 0:
            return 0.0
        return self.measured_nodes / self.total_nodes

    @property
    def diff_g(self) -> float | None:
        """目標との差分（実測 − 目標）。どちらか欠けていれば None。"""
        if self.actual_g is None or self.target_g is None:
            return None
        return self.actual_g - self.target_g

    @property
    def is_over_target(self) -> bool:
        diff = self.diff_g
        return diff is not None and diff > 0


def iter_subtree(node: ProgressNode):
    """ノードとその子孫を順に返す（非循環が保証済みなので停止する）。"""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def weight_summary(tree: ProgressTree,
                   node_id: str | None = None) -> WeightSummary:
    """ツリー全体、または指定ノードの部分木の重量サマリを返す。

    node_id を省略すると全ルートの合計。存在しない node_id では
    空のサマリを返す（例外にしない）。
    """
    if node_id is None:
        roots = list(tree.roots)
    else:
        node = tree.by_id.get(node_id)
        roots = [node] if node is not None else []

    actual_parts = [r.aggregated_actual_weight_g for r in roots]
    target_parts = [r.aggregated_target_weight_g for r in roots]
    measured = total = 0
    for root in roots:
        for node in iter_subtree(root):
            total += 1
            if node.actual_weight_g is not None:
                measured += 1

    return WeightSummary(
        actual_g=_resolve_weight(None, actual_parts),
        target_g=_resolve_weight(None, target_parts),
        measured_nodes=measured,
        total_nodes=total,
    )


def nodes_over_target(tree: ProgressTree) -> list[tuple[ProgressNode, float]]:
    """目標超過のノードを超過量の大きい順に返す（減量の着手先）。

    目標と集計実測の両方が分かるノードだけが対象。
    """
    rows: list[tuple[ProgressNode, float]] = []
    for node in tree.by_id.values():
        actual = node.aggregated_actual_weight_g
        target = node.aggregated_target_weight_g
        if actual is None or target is None:
            continue
        over = actual - target
        if over > 0:
            rows.append((node, over))
    rows.sort(key=lambda pair: (-pair[1], pair[0].node_id))
    return rows


# ---------------------------------------------------------------------
# DB（progress_nodes）との橋渡し
# ---------------------------------------------------------------------
def node_from_row(row: dict[str, Any]) -> ProgressNode:
    """progress_nodes の1行を ProgressNode へ変換する。

    NULL 許容列（assignee / status / todoist_task_id）は空文字に正規化し、
    表示側で `or "—"` の判定をそのまま使えるようにする。
    """
    return ProgressNode(
        node_id=str(row["node_id"]),
        parent_id=(row.get("parent_id") or None),
        order=float(row.get("sort_order") or 0.0),
        name=row.get("name") or "",
        assignee=row.get("assignee") or "",
        status=row.get("status") or "",
        manual_progress=(None if row.get("manual_progress") is None
                         else float(row["manual_progress"])),
        source=row.get("source") or SOURCE_MANUAL,
        todoist_task_id=row.get("todoist_task_id") or "",
        weight=float(row["weight"]) if row.get("weight") is not None else 1.0,
        target_weight_g=(None if row.get("target_weight_g") is None
                         else float(row["target_weight_g"])),
        actual_weight_g=(None if row.get("actual_weight_g") is None
                         else float(row["actual_weight_g"])),
        created_at=row.get("created_at") or "",
        updated_at=row.get("updated_at") or "",
    )


def nodes_from_rows(rows: list[dict[str, Any]]) -> list[ProgressNode]:
    """progress_nodes の行一覧を ProgressNode 一覧へ変換する。"""
    return [node_from_row(row) for row in rows]


async def load_tree(repo: Any, guild_id: int) -> ProgressTree:
    """DB からギルドの進捗ツリーを読み込み、集計まで済ませて返す。

    repo は ProgressRepository（または for_guild プロキシではない生の
    リポジトリ）。ノードが1件も無いギルドでは空のツリーを返す
    （例外にはしない。/progress view 側で案内する）。
    """
    return build_and_aggregate(nodes_from_rows(await repo.list_nodes(guild_id)))
