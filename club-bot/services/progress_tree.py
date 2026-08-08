"""機体進捗ツリーの構築・集計ロジック（純粋関数群）。

Google Sheets 上の進捗管理シート（隣接リスト形式）を読み込んだ行データから
メモリ上にツリーを構築し、集計進捗率・深さを計算する。

- Sheets / Discord には依存しない（シート I/O は progress_sheet_service が担当）
- 深さは無制限（機体 → パーツ → 部品 → サブタスク → …）
- 葉ノード: 進捗率(手入力) をそのまま集計進捗率に採用
- 親ノード: 子の集計進捗率の加重平均（現状は全ノード weight=1.0 の単純平均。
  将来シートに重み列を足す場合は ProgressNode.weight に読み込むだけで
  重み付け平均へ拡張できる）
- 進捗率は内部的に 0.0〜1.0 の小数で扱う
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProgressNode:
    """進捗管理シートの1行に対応するノード。"""
    node_id: str
    parent_id: str | None = None
    order: float = 0.0            # 表示順（兄弟間の並び）
    name: str = ""
    assignee: str = ""
    status: str = ""
    manual_progress: float | None = None  # 進捗率(手入力)。0.0〜1.0
    source: str = "manual"        # "manual" | "todoist"
    todoist_task_id: str = ""
    row_index: int | None = None  # シート上の行番号（書き戻し用。1始まり）
    weight: float = 1.0           # 集計時の重み（将来の重み列拡張用フック）

    # 計算結果（compute_tree が設定する）
    depth: int | None = None
    aggregated: float | None = None
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
    """シートのセル値を 0.0〜1.0 の進捗率に正規化する。

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


def aggregate(tree: ProgressTree) -> None:
    """集計進捗率と深さを末端から根に向けて計算し、各ノードに書き込む。

    - 葉: manual_progress（未入力は 0.0）
    - 親: 子の aggregated の加重平均（weight は現状すべて 1.0）
    - depth: ルート = 0
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


def build_and_aggregate(nodes: list[ProgressNode]) -> ProgressTree:
    """build_tree + aggregate を一括で行うヘルパー。"""
    tree = build_tree(nodes)
    aggregate(tree)
    return tree
