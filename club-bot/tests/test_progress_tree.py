"""progress_tree（進捗ツリー構築・集計）のユニットテスト。"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.progress_tree import (
    ProgressNode,
    build_and_aggregate,
    build_tree,
    descendant_ids,
    load_tree,
    node_from_row,
    parse_progress,
)


def _node(
    node_id: str,
    parent_id: str | None = None,
    *,
    progress: float | None = None,
    order: float = 0.0,
    weight: float = 1.0,
) -> ProgressNode:
    return ProgressNode(
        node_id=node_id,
        parent_id=parent_id,
        name=node_id,
        manual_progress=progress,
        order=order,
        weight=weight,
    )


# ---------------------------------------------------------------------
# parse_progress
# ---------------------------------------------------------------------
def test_parse_progress_variants():
    assert parse_progress(0.5) == 0.5
    assert parse_progress("0.5") == 0.5
    assert parse_progress("50%") == 0.5
    assert parse_progress("50％") == 0.5  # 全角パーセント
    assert parse_progress("50") == 0.5  # 1 より大きい数値は % とみなす
    assert parse_progress(1) == 1.0
    assert parse_progress(0) == 0.0
    assert parse_progress("") is None
    assert parse_progress(None) is None
    assert parse_progress("abc") is None
    assert parse_progress("150%") == 1.0  # クランプ
    assert parse_progress(-0.2) == 0.0  # クランプ


# ---------------------------------------------------------------------
# B1: ツリー構築と集計
# ---------------------------------------------------------------------
def test_single_leaf_root():
    tree = build_and_aggregate([_node("a", progress=0.3)])
    assert not tree.errors
    assert [r.node_id for r in tree.roots] == ["a"]
    assert tree.by_id["a"].aggregated == 0.3


def test_leaf_without_progress_counts_as_zero():
    tree = build_and_aggregate([_node("a")])
    assert tree.by_id["a"].aggregated == 0.0


def test_parent_is_average_of_children():
    tree = build_and_aggregate(
        [
            _node("root"),
            _node("c1", "root", progress=1.0),
            _node("c2", "root", progress=0.0),
            _node("c3", "root", progress=0.5),
        ]
    )
    assert not tree.errors
    assert tree.by_id["root"].aggregated == (1.0 + 0.0 + 0.5) / 3


def test_deep_tree_recursive_average():
    # root -> a -> b -> c(0.8), root -> d(0.2)
    tree = build_and_aggregate(
        [
            _node("root"),
            _node("a", "root"),
            _node("b", "a"),
            _node("c", "b", progress=0.8),
            _node("d", "root", progress=0.2),
        ]
    )
    assert not tree.errors
    assert tree.by_id["b"].aggregated == 0.8
    assert tree.by_id["a"].aggregated == 0.8
    assert tree.by_id["root"].aggregated == (0.8 + 0.2) / 2


def test_weighted_average_hook():
    # 重み列を将来足した場合の拡張フック（weight 3:1）
    tree = build_and_aggregate(
        [
            _node("root"),
            _node("c1", "root", progress=1.0, weight=3.0),
            _node("c2", "root", progress=0.0, weight=1.0),
        ]
    )
    assert tree.by_id["root"].aggregated == 0.75


def test_manual_progress_on_parent_is_ignored():
    # 親ノードの手入力値は使わず、常に子の集計を優先する
    parent = _node("root", progress=0.1)
    tree = build_and_aggregate([parent, _node("c", "root", progress=0.9)])
    assert tree.by_id["root"].aggregated == 0.9


def test_siblings_sorted_by_order():
    tree = build_and_aggregate(
        [
            _node("root"),
            _node("b", "root", order=2),
            _node("a", "root", order=1),
            _node("r2", order=1),
        ]
    )
    assert [c.node_id for c in tree.by_id["root"].children] == ["a", "b"]
    # ルートも表示順でソート（同順は ID 順）
    assert [r.node_id for r in tree.roots] == ["root", "r2"]


def test_multiple_roots():
    tree = build_and_aggregate(
        [
            _node("m1", progress=0.4),
            _node("m2", progress=0.6),
        ]
    )
    assert len(tree.roots) == 2


def test_very_deep_tree_does_not_hit_recursion_limit():
    # 明示スタック実装のため Python の再帰上限（既定 1000）を超えても動く
    nodes = [_node("n0")]
    for i in range(1, 3000):
        nodes.append(_node(f"n{i}", f"n{i - 1}"))
    nodes[-1].manual_progress = 1.0
    tree = build_and_aggregate(nodes)
    assert not tree.errors
    assert tree.by_id["n0"].aggregated == 1.0


# ---------------------------------------------------------------------
# B2: 循環参照・孤児・重複ガード
# ---------------------------------------------------------------------
def test_self_cycle_is_skipped_and_reported():
    tree = build_and_aggregate(
        [
            _node("ok", progress=0.5),
            _node("loop", "loop"),
        ]
    )
    assert "loop" not in tree.by_id
    assert any(e.node_id == "loop" and "循環" in e.reason for e in tree.errors)
    assert tree.by_id["ok"].aggregated == 0.5  # 正常系は影響を受けない


def test_two_node_cycle_with_descendant():
    tree = build_and_aggregate(
        [
            _node("a", "b"),
            _node("b", "a"),
            _node("child", "a", progress=1.0),  # 循環ノードの子孫も除外される
            _node("ok", progress=0.2),
        ]
    )
    assert "a" not in tree.by_id
    assert "b" not in tree.by_id
    assert "child" not in tree.by_id
    reported = {e.node_id for e in tree.errors}
    assert {"a", "b", "child"} <= reported
    assert [r.node_id for r in tree.roots] == ["ok"]


def test_orphan_parent_is_reported():
    tree = build_and_aggregate(
        [
            _node("x", "ghost"),
            _node("ok"),
        ]
    )
    assert "x" not in tree.by_id
    assert any(e.node_id == "x" and "ghost" in e.reason for e in tree.errors)


def test_duplicate_id_keeps_first_row():
    first = _node("dup", progress=0.1)
    second = _node("dup", progress=0.9)
    tree = build_and_aggregate([first, second])
    assert tree.by_id["dup"] is first
    assert any(e.node_id == "dup" and "重複" in e.reason for e in tree.errors)


def test_empty_id_reported():
    tree = build_tree([_node("")])
    assert any("ID が空" in e.reason for e in tree.errors)
    assert not tree.by_id


# ---------------------------------------------------------------------
# B3: 深さの自動計算
# ---------------------------------------------------------------------
def test_depth_computed_from_root():
    tree = build_and_aggregate(
        [
            _node("root"),
            _node("a", "root"),
            _node("b", "a"),
            _node("c", "b"),
        ]
    )
    assert tree.by_id["root"].depth == 0
    assert tree.by_id["a"].depth == 1
    assert tree.by_id["b"].depth == 2
    assert tree.by_id["c"].depth == 3


# ---------------------------------------------------------------------
# B4: DB（progress_nodes）からのツリー読み込み
# ---------------------------------------------------------------------
def test_node_from_row_normalizes_nulls():
    """NULL 許容列は空文字・既定値へ正規化される。"""
    node = node_from_row(
        {
            "node_id": "wing",
            "parent_id": None,
            "sort_order": None,
            "name": None,
            "assignee": None,
            "status": None,
            "manual_progress": None,
            "source": None,
            "todoist_task_id": None,
            "weight": None,
        }
    )
    assert node.node_id == "wing"
    assert node.parent_id is None
    assert node.order == 0.0
    assert node.name == ""
    assert node.assignee == ""
    assert node.status == ""
    assert node.manual_progress is None
    assert node.source == "manual"
    assert node.todoist_task_id == ""
    assert node.weight == 1.0
    assert node.row_index is None  # DB 経路ではシート行番号を持たない


def test_node_from_row_maps_columns():
    node = node_from_row(
        {
            "node_id": "spar",
            "parent_id": "wing",
            "sort_order": 2.5,
            "name": "主桁",
            "assignee": "山田",
            "status": "製作中",
            "manual_progress": 0.25,
            "source": "spar_winding",
            "todoist_task_id": "999",
            "weight": 2.0,
        }
    )
    assert (node.parent_id, node.order, node.name) == ("wing", 2.5, "主桁")
    assert (node.assignee, node.status) == ("山田", "製作中")
    assert node.manual_progress == 0.25
    assert node.source == "spar_winding"
    assert node.todoist_task_id == "999"
    assert node.weight == 2.0


class _FakeRepo:
    """list_nodes だけを持つ最小のリポジトリ代役。"""

    def __init__(self, rows_by_guild: dict[int, list[dict]]):
        self._rows = rows_by_guild

    async def list_nodes(self, guild_id: int) -> list[dict]:
        return self._rows.get(guild_id, [])


def _row(node_id: str, parent_id: str | None = None, progress=None, order: float = 0.0) -> dict:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "sort_order": order,
        "name": node_id,
        "assignee": None,
        "status": None,
        "manual_progress": progress,
        "source": "manual",
        "todoist_task_id": None,
        "weight": 1.0,
    }


def test_load_tree_builds_and_aggregates():
    repo = _FakeRepo(
        {
            1: [
                _row("airframe"),
                _row("wing", "airframe"),
                _row("spar", "wing", progress=0.5),
                _row("rib", "wing", progress=1.0),
            ]
        }
    )
    tree = asyncio.run(load_tree(repo, 1))
    assert [n.node_id for n in tree.roots] == ["airframe"]
    assert tree.by_id["wing"].aggregated == 0.75  # (0.5 + 1.0) / 2
    assert tree.by_id["airframe"].aggregated == 0.75
    assert tree.by_id["spar"].depth == 2


def test_load_tree_returns_empty_tree_for_unknown_guild():
    tree = asyncio.run(load_tree(_FakeRepo({}), 999))
    assert tree.roots == []
    assert tree.by_id == {}
    assert tree.errors == []


# ---------------------------------------------------------------------
# descendant_ids: 親の付け替えで循環参照を作らせないためのガード
# ---------------------------------------------------------------------
def test_descendant_ids_collects_whole_subtree():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe"),
            _node("spar", "wing"),
            _node("rib", "wing"),
            _node("tail", "airframe"),
        ]
    )
    assert descendant_ids(tree, "wing") == {"spar", "rib"}
    assert descendant_ids(tree, "airframe") == {"wing", "spar", "rib", "tail"}


def test_descendant_ids_excludes_self_and_leaves():
    tree = build_and_aggregate([_node("airframe"), _node("wing", "airframe")])
    assert descendant_ids(tree, "wing") == set()
    assert "airframe" not in descendant_ids(tree, "airframe")


def test_descendant_ids_for_unknown_node_is_empty():
    tree = build_and_aggregate([_node("airframe")])
    assert descendant_ids(tree, "missing") == set()
