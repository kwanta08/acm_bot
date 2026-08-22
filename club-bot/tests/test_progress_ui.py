"""progress コグの表示ヘルパー・進捗バーのユニットテスト。

Discord への接続は行わず、Embed 組み立て・パンくず・テキスト進捗バーのみ検証する。
（matplotlib による PNG 生成は撤去され、詳細なグラフはブラウザ側で描画する）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.progress import (
    BAR_WIDTH,
    anchor_candidates,
    breadcrumb,
    build_level_embed,
    chart_items,
    child_nodes,
    new_part_node_id,
    node_choices,
    unmapped_projects,
)
from services.progress_tree import ProgressNode, build_and_aggregate
from utils import progress_bar


def _node(
    node_id,
    parent=None,
    *,
    name=None,
    progress=None,
    order=0.0,
    assignee="",
    status="",
    source="manual",
    td_id="",
):
    return ProgressNode(
        node_id=node_id,
        parent_id=parent,
        name=name or node_id,
        manual_progress=progress,
        order=order,
        assignee=assignee,
        status=status,
        source=source,
        todoist_task_id=td_id,
    )


def _tree():
    return build_and_aggregate(
        [
            _node("m1", name="本機", order=1),
            _node("wing", "m1", name="主翼", order=1),
            _node("rib", "wing", name="リブ", progress=0.5, assignee="山田", status="製作中"),
            _node("spar", "wing", name="桁", progress=1.0, source="todoist", td_id="42"),
            _node("tail", "m1", name="尾翼", order=2, progress=0.0),
        ]
    )


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
    assert child_nodes(tree, "rib") == []  # 葉
    assert child_nodes(tree, "ghost") == []  # 不在 ID


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
    assert progress_bar.FILLED in (embed.description or "")


def test_level_embed_leaf_details():
    tree = _tree()
    embed = build_level_embed(tree, "spar")
    assert "桁" in embed.title
    fields = {f.name: f.value for f in embed.fields}
    assert fields["ソース"] == "Todoist"
    assert "42" in fields["Todoist"]
    assert progress_bar.FILLED not in (embed.description or "")  # 葉はバーなし


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
    links = [{"project_name": "主翼班", "node_id": "wing", "notify_channel_id": ""}]
    result = unmapped_projects(projects, links)
    assert [p.name for p in result] == ["尾翼班", "電装班"]


def test_anchor_candidates_depth_limited_and_ordered():
    tree = _tree()
    # 機体（深さ0）→ その配下のパーツ（深さ1）の行きがけ順。深さ2は含まない
    ids = [n.node_id for n in anchor_candidates(tree)]
    assert ids == ["m1", "wing", "tail"]


def test_new_part_node_id_is_stable():
    """プロジェクト ID から導くため、消して再登録しても同じノードに戻る。"""
    assert new_part_node_id("P1") == "pj_P1"
    assert new_part_node_id("P1") == new_part_node_id("P1")


# ---------------------------------------------------------------------
# ノード指定のオートコンプリート
# ---------------------------------------------------------------------
def test_node_choices_walks_tree_in_order():
    choices = node_choices(_tree(), "")
    ids = [node_id for _, node_id in choices]
    labels = [label for label, _ in choices]
    # ツリーの行きがけ順（機体 → 配下のパーツ → その配下 → 次のパーツ）
    assert ids == ["m1", "wing", "rib", "spar", "tail"]
    # 深さぶん全角スペースで字下げされる
    assert labels[0] == "本機"
    assert labels[1] == "　主翼"
    assert labels[2] == "　　リブ"


def test_node_choices_filters_by_input():
    ids = [node_id for _, node_id in node_choices(_tree(), "spar")]
    assert ids == ["spar"]


# ---------------------------------------------------------------------
# プロジェクト別タスク通知
# ---------------------------------------------------------------------
def test_due_items_filters_and_formats():
    from datetime import date

    from cogs.progress import due_items

    class Due:
        def __init__(self, d):
            self.date = d

    class Task:
        def __init__(self, tid, content, due=None, priority=1):
            self.id = tid
            self.content = content
            self.due = due
            self.priority = priority

    today = date(2026, 8, 8)
    until = date(2026, 8, 15)
    tasks = [
        Task("1", "超過タスク", Due(date(2026, 8, 1)), priority=4),
        Task("2", "今週タスク", Due(date(2026, 8, 10))),
        Task("3", "来月タスク", Due(date(2026, 9, 1))),  # 期間外
        Task("4", "期限なし"),  # 除外
    ]
    items = due_items(tasks, until, "主翼班")
    assert [i["title"] for i in items] == ["超過タスク", "今週タスク"]
    assert items[0]["priority"] == 4
    assert items[0]["category"] == "主翼班"
    assert "todoist.com" in items[0]["url"]
    assert today < items[1]["due_date"] <= until


# ---------------------------------------------------------------------
# テキスト進捗バー
# ---------------------------------------------------------------------
def test_render_block_contains_bars_and_percent():
    block = progress_bar.render_block([("主翼", 0.75), ("尾翼", 0.0)])
    assert block.startswith("```") and block.endswith("```")
    assert "主翼" in block and "75%" in block
    assert progress_bar.FILLED in block and progress_bar.EMPTY in block


def test_render_block_is_empty_for_no_items():
    assert progress_bar.render_block([]) == ""


def test_bar_clamps_and_keeps_width():
    assert progress_bar.bar(0.0, 10) == progress_bar.EMPTY * 10
    assert progress_bar.bar(1.0, 10) == progress_bar.FILLED * 10
    assert progress_bar.bar(1.5, 10) == progress_bar.FILLED * 10  # クランプ
    assert progress_bar.bar(-0.5, 10) == progress_bar.EMPTY * 10
    assert len(progress_bar.bar(0.37, 12)) == 12
    assert len(progress_bar.bar(0.37, 0)) == 1  # 最低1文字


def test_render_lines_align_bars():
    lines = progress_bar.render_lines([("主翼", 0.5), ("胴体フレーム", 0.25)])
    # 名前の長さが違ってもバーの開始位置が揃う
    assert len(lines) == 2
    assert lines[0].index(progress_bar.FILLED) == lines[1].index(progress_bar.FILLED)


def test_render_block_truncates_long_lists():
    items = [(f"部品{i}", 0.5) for i in range(30)]
    block = progress_bar.render_block(items, max_rows=25)
    assert "他 5 件" in block


def test_embed_contains_text_bar_instead_of_image():
    """/progress view の Embed は画像添付ではなくテキストバーを持つ。"""
    embed = build_level_embed(_tree(), "m1")
    assert embed.image.url is None
    assert progress_bar.FILLED in (embed.description or "")
    assert BAR_WIDTH >= 8

# ---------------------------------------------------------------------
# /progress edit・add の進捗率検証（G2-6）
#
# parse_progress は「解釈不能なら None」を返す（移行スクリプト用の仕様。
# 変えない）。コマンド側がそれを素通しすると、
# `/progress edit node:主桁 progress:半分` で**既存の進捗率が消えて**
# 緑の成功 Embed が出ていた。コマンド側で None を弾く。
# 解釈規則はダッシュボード側（repositories/table_repository.py の
# progress 列検証。G0-2 の 8b9c0f4）と同じ: 0.5 / 50% / 50 を受け、
# 空はクリア、解釈不能はエラー。
# ---------------------------------------------------------------------
import asyncio
import tempfile
from types import SimpleNamespace

from utils.db import Database

_G1 = 111


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _Interaction:
    def __init__(self):
        self.guild = SimpleNamespace(id=_G1)
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return f"{embed.title or ''} {embed.description or ''}"


def _progress_cog(db):
    from cogs.progress import Progress

    return Progress(SimpleNamespace(db=db, guilds=[]))


def _run_edit(db, **kwargs):
    from cogs.progress import Progress

    interaction = _Interaction()
    asyncio.run(Progress.progress_edit.callback(_progress_cog(db), interaction, **kwargs))
    return interaction


def test_edit_with_unparsable_progress_is_an_error_and_keeps_the_value():
    """`progress:半分` で既存の進捗率が消えないこと。"""

    async def _seed(db):
        from repositories.progress_repository import ProgressRepository

        repo = ProgressRepository(db)
        await repo.upsert_node(_G1, "spar", name="主桁", manual_progress=0.4)
        return repo

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed(db)
            interaction = _Interaction()
            from cogs.progress import Progress

            await Progress.progress_edit.callback(
                _progress_cog(db), interaction, node="spar", progress="半分"
            )
            text = interaction.text()
            assert "0.5" in text and "50%" in text and "形式" in text, text
            assert "変更しました" not in text, "解釈不能なのに成功と表示している"

            row = await repo.get_node(_G1, "spar")
            assert row["manual_progress"] == 0.4, "既存の進捗率が消えている"
        finally:
            await db.close()

    asyncio.run(_main())


def test_edit_accepts_the_three_documented_forms():
    """0.5 / 50% / 50 はダッシュボード側と同じ解釈で受けること。"""

    async def _main():
        from cogs.progress import Progress
        from repositories.progress_repository import ProgressRepository

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = ProgressRepository(db)
            for raw, expected in (("0.5", 0.5), ("50%", 0.5), ("50", 0.5), ("１００％", 1.0)):
                await repo.upsert_node(_G1, "spar", name="主桁", manual_progress=0.1)
                interaction = _Interaction()
                await Progress.progress_edit.callback(
                    _progress_cog(db), interaction, node="spar", progress=raw
                )
                row = await repo.get_node(_G1, "spar")
                assert row["manual_progress"] == expected, (raw, row["manual_progress"])
        finally:
            await db.close()

    asyncio.run(_main())


def test_add_with_unparsable_progress_is_an_error_not_a_silent_none():
    """/progress add も同じ穴（進捗が黙って未入力になる）を塞ぐこと。"""

    async def _main():
        from cogs.progress import Progress
        from repositories.progress_repository import ProgressRepository

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            interaction = _Interaction()
            await Progress.progress_add.callback(
                _progress_cog(db), interaction, name="主桁", parent=None, progress="半分"
            )
            text = interaction.text()
            assert "形式" in text, text
            assert not await ProgressRepository(db).list_nodes(_G1), (
                "エラーなのにノードが作成されている"
            )
        finally:
            await db.close()

    asyncio.run(_main())

