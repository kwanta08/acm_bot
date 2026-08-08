"""progress コグの表示ヘルパー・グラフ生成のユニットテスト。

Discord への接続は行わず、Embed 組み立て・パンくず・グラフ PNG 生成のみ検証する。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.progress import (  # noqa: E402
    CHART_FILENAME,
    anchor_candidates,
    breadcrumb,
    build_level_embed,
    chart_items,
    child_nodes,
    new_part_row,
    unmapped_projects,
)
from services.progress_tree import ProgressNode, build_and_aggregate  # noqa: E402
from utils.progress_chart import render_progress_bars  # noqa: E402


def _node(node_id, parent=None, *, name=None, progress=None, order=0.0,
          assignee="", status="", source="manual", td_id=""):
    return ProgressNode(node_id=node_id, parent_id=parent,
                        name=name or node_id, manual_progress=progress,
                        order=order, assignee=assignee, status=status,
                        source=source, todoist_task_id=td_id)


def _tree():
    return build_and_aggregate([
        _node("m1", name="本機", order=1),
        _node("wing", "m1", name="主翼", order=1),
        _node("rib", "wing", name="リブ", progress=0.5,
              assignee="山田", status="製作中"),
        _node("spar", "wing", name="桁", progress=1.0,
              source="todoist", td_id="42"),
        _node("tail", "m1", name="尾翼", order=2, progress=0.0),
    ])


# ---------------------------------------------------------------------
# パンくず・子取得
# ---------------------------------------------------------------------
def test_breadcrumb_walks_to_root():
    tree = _tree()
    assert breadcrumb(tree, "rib") == "本機 > 主翼 > リブ"
    assert breadcrumb(tree, "m1") == "本機"
    assert breadcrumb(tree, None) == "機体一覧"


def test_child_nodes_root_and_nested():
    tree = _tree()
    assert [n.node_id for n in child_nodes(tree, None)] == ["m1"]
    assert [n.node_id for n in child_nodes(tree, "wing")] == ["rib", "spar"]
    assert child_nodes(tree, "rib") == []          # 葉
    assert child_nodes(tree, "ghost") == []        # 不在 ID


# ---------------------------------------------------------------------
# Embed 組み立て
# ---------------------------------------------------------------------
def test_level_embed_lists_children_with_percent():
    tree = _tree()
    embed = build_level_embed(tree, "wing")
    assert "主翼" in embed.title
    names = [f.name for f in embed.fields]
    assert any("リブ" in n and "50%" in n for n in names)
    assert any("桁" in n and "100%" in n for n in names)
    assert embed.image.url == f"attachment://{CHART_FILENAME}"


def test_level_embed_leaf_details():
    tree = _tree()
    embed = build_level_embed(tree, "spar")
    assert "桁" in embed.title
    fields = {f.name: f.value for f in embed.fields}
    assert fields["ソース"] == "Todoist"
    assert "42" in fields["Todoist"]
    assert embed.image.url is None  # 葉はグラフなし


def test_level_embed_root_listing():
    tree = _tree()
    embed = build_level_embed(tree, None)
    assert "機体一覧" in embed.title
    assert any("本機" in f.name for f in embed.fields)


def test_chart_items_order_and_values():
    tree = _tree()
    items = chart_items(tree, "wing")
    assert items == [("リブ", 0.5), ("桁", 1.0)]


# ---------------------------------------------------------------------
# /progress setup ウィザードヘルパー
# ---------------------------------------------------------------------
class _Proj:
    def __init__(self, pid, name):
        self.id = pid
        self.name = name


def test_unmapped_projects_excludes_registered():
    projects = [_Proj("1", "主翼班"), _Proj("2", "尾翼班"), _Proj("3", "電装班")]
    mappings = [{"project_name": "主翼班", "node_id": "wing",
                 "notify_channel_id": ""}]
    result = unmapped_projects(projects, mappings)
    assert [p.name for p in result] == ["尾翼班", "電装班"]


def test_anchor_candidates_depth_limited_and_ordered():
    tree = _tree()
    # 機体（深さ0）→ その配下のパーツ（深さ1）の行きがけ順。深さ2は含まない
    ids = [n.node_id for n in anchor_candidates(tree)]
    assert ids == ["m1", "wing", "tail"]


def test_new_part_row_layout():
    row = new_part_row("P1", "主翼班", "m1", "2026-08-08 12:00")
    assert row[0] == "pj_P1"       # ID
    assert row[1] == "m1"          # 親ID = 選択した機体
    assert row[4] == "主翼班"      # 名前 = プロジェクト名
    assert row[10] == "manual"     # ソース（同期処理の上書き対象にしない）
    assert row[12] == "2026-08-08 12:00"
    assert len(row) == 13          # 進捗管理シートの列数と一致


# ---------------------------------------------------------------------
# グラフ PNG 生成
# ---------------------------------------------------------------------
def test_render_progress_bars_returns_png():
    png = render_progress_bars([("主翼", 0.75), ("尾翼", 0.0), ("胴体", 1.0)])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_render_progress_bars_clamps_and_truncates():
    png = render_progress_bars([("と" * 40, 1.5), ("x", -0.5)])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_progress_bars_rejects_empty():
    try:
        render_progress_bars([])
        raise AssertionError("ValueError が送出されるべき")
    except ValueError:
        pass
