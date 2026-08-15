"""Progress コグの DB 経路のテスト（正本 = progress_nodes）。

Discord へは接続せず、実際の SQLite DB とフェイクの bot で検証する。

- load_tree が DB から読み、直前の更新が即座に反映されること
  （シート時代のメモリキャッシュを廃止したため、常に最新を返す）
- /progress setup ウィザードの完了処理が DB へ紐付けと新規パーツを書くこと
- 同期（sync_guild_db）が Todoist タスクをノードとして取り込むこと
- **ギルドをまたいでツリーが混ざらないこと**
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.progress import Progress, ProjectSetupWizard, new_part_node_id
from repositories.progress_repository import ProgressRepository
from utils.db import Database

G1 = 111
G2 = 222
NOW = "2026-08-11 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


@dataclass
class FakeProject:
    id: str
    name: str


class DisabledTodoistManager:
    async def for_guild(self, guild_id):
        return SimpleNamespace(enabled=False)


async def _make_cog() -> tuple[Progress, Database, ProgressRepository]:
    db = Database(_tmp_db_path())
    await db.connect()
    bot = SimpleNamespace(db=db, guilds=[], todoist_manager=DisabledTodoistManager())
    cog = Progress(bot)
    return cog, db, ProgressRepository(db)


# ---------------------------------------------------------------------
# load_tree
# ---------------------------------------------------------------------
def test_load_tree_reads_from_db():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_node(G1, "m1", name="本機", now_text=NOW)
            await repo.upsert_node(
                G1, "wing", parent_id="m1", name="主翼", manual_progress=0.5, now_text=NOW
            )
            tree = await cog.load_tree(G1)
            assert [n.node_id for n in tree.roots] == ["m1"]
            assert tree.by_id["m1"].aggregated == 0.5
        finally:
            await db.close()

    run(_main())


def test_load_tree_always_returns_fresh_data():
    """キャッシュを持たないため、直前の更新がそのまま見える。"""

    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_node(G1, "m1", name="本機", now_text=NOW)
            first = await cog.load_tree(G1)
            assert "wing" not in first.by_id

            await repo.upsert_node(G1, "wing", parent_id="m1", name="主翼", now_text=NOW)
            second = await cog.load_tree(G1)
            assert "wing" in second.by_id
            assert first is not second
        finally:
            await db.close()

    run(_main())


def test_load_tree_is_isolated_per_guild():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_node(G1, "m1", name="1号機", now_text=NOW)
            await repo.upsert_node(G2, "m1", name="別大学の機体", now_text=NOW)

            tree1 = await cog.load_tree(G1)
            tree2 = await cog.load_tree(G2)
            assert tree1.by_id["m1"].name == "1号機"
            assert tree2.by_id["m1"].name == "別大学の機体"
            assert len(tree1.by_id) == 1
        finally:
            await db.close()

    run(_main())


def test_load_tree_empty_guild():
    async def _main():
        cog, db, _repo = await _make_cog()
        try:
            tree = await cog.load_tree(G1)
            assert tree.roots == []
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /progress setup ウィザードの完了処理
# ---------------------------------------------------------------------
class _FakeInteraction:
    def __init__(self):
        self.user = SimpleNamespace(id=42, display_name="山田")
        self.edited: list = []
        self.response = SimpleNamespace(defer=self._defer)

    async def _defer(self, *args, **kwargs):
        return None

    async def edit_original_response(self, **kwargs):
        self.edited.append(kwargs)


def test_setup_wizard_writes_link_to_db():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_node(G1, "m1", name="本機", now_text=NOW)
            tree = await cog.load_tree(G1)

            wizard = ProjectSetupWizard(cog, G1, 42, [FakeProject("P1", "主翼班")], tree)
            wizard.project_id = "P1"
            wizard.project_name = "主翼班"
            wizard.anchor_id = "m1"
            interaction = _FakeInteraction()
            await wizard._finish(interaction, "12345")

            links = await repo.list_todoist_links(G1)
            assert len(links) == 1
            assert links[0]["project_name"] == "主翼班"
            assert links[0]["node_id"] == "m1"
            assert links[0]["notify_channel_id"] == "12345"
            assert links[0]["created_by"] == "42"
            # 他ギルドには漏れない
            assert await repo.list_todoist_links(G2) == []
        finally:
            await db.close()

    run(_main())


def test_setup_wizard_creates_new_part_node():
    """「新規パーツとして追加」で機体の下にノードが作られる。"""

    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_node(G1, "m1", name="本機", now_text=NOW)
            tree = await cog.load_tree(G1)

            wizard = ProjectSetupWizard(cog, G1, 42, [FakeProject("P1", "電装班")], tree)
            wizard.project_id = "P1"
            wizard.project_name = "電装班"
            wizard.anchor_id = new_part_node_id("P1")
            wizard.new_part_root_id = "m1"
            await wizard._finish(_FakeInteraction(), "")

            node = await repo.get_node(G1, "pj_P1")
            assert node is not None
            assert node["parent_id"] == "m1"
            assert node["name"] == "電装班"
            assert node["source"] == "manual"  # 同期の上書き対象にしない
        finally:
            await db.close()

    run(_main())
