"""DB ベースの進捗同期（progress_sync_service.sync_guild_db）のテスト。

- Todoist のタスク・サブタスク階層が progress_nodes へ取り込まれること
- アクティブでなくなったタスクが完了扱いになること
- source=manual / spar_winding のノードを同期が上書きしないこと
- 桁巻き（layer_records）の完了層数から進捗率が計算されること
- **ギルドをまたいで同期結果が混ざらないこと**
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repositories.progress_repository import ProgressRepository
from services import progress_sync_service as pss_db
from services import spar_winding_service
from services.progress_tree import nodes_from_rows
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
class FakeTask:
    id: str
    content: str
    parent_id: str | None = None


@dataclass
class FakeProject:
    id: str
    name: str


class FakeTodoist:
    enabled = True

    def __init__(self, projects, tasks_by_project):
        self._projects = projects
        self._tasks = tasks_by_project

    async def get_projects(self):
        return self._projects

    async def get_tasks(self, project_id=None):
        return self._tasks.get(project_id, [])


async def _db() -> tuple[Database, ProgressRepository]:
    db = Database(_tmp_db_path())
    await db.connect()
    return db, ProgressRepository(db)


# ---------------------------------------------------------------------
# 更新計画（純粋関数）
# ---------------------------------------------------------------------
def test_plan_creates_nodes_for_new_tasks():
    nodes = nodes_from_rows(
        [
            {
                "node_id": "wing",
                "parent_id": None,
                "sort_order": 0,
                "name": "主翼",
                "assignee": None,
                "status": None,
                "manual_progress": None,
                "source": "manual",
                "todoist_task_id": None,
                "weight": 1,
            },
        ]
    )
    plan = pss_db.plan_todoist_sync(
        nodes, [("wing", [FakeTask("1", "リブ切り出し"), FakeTask("2", "接着", parent_id="1")])]
    )
    assert plan.added == 2
    created = {c["node_id"]: c for c in plan.creates}
    assert created["td_1"]["parent_id"] == "wing"
    # サブタスクは親タスクの下へ
    assert created["td_2"]["parent_id"] == "td_1"
    assert created["td_1"]["source"] == "todoist"


def test_plan_reports_missing_anchor():
    plan = pss_db.plan_todoist_sync([], [("nope", [FakeTask("1", "x")])])
    assert plan.creates == []
    assert any("nope" in e for e in plan.errors)


def test_plan_does_not_touch_manual_nodes():
    """手入力・桁巻き由来のノードは同期で上書きしない。"""
    nodes = nodes_from_rows(
        [
            {
                "node_id": "wing",
                "parent_id": None,
                "sort_order": 0,
                "name": "主翼",
                "assignee": None,
                "status": None,
                "manual_progress": None,
                "source": "manual",
                "todoist_task_id": None,
                "weight": 1,
            },
            {
                "node_id": "td_1",
                "parent_id": "wing",
                "sort_order": 1,
                "name": "古い名前",
                "assignee": None,
                "status": None,
                "manual_progress": 0.3,
                "source": "manual",
                "todoist_task_id": "1",
                "weight": 1,
            },
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [FakeTask("1", "新しい名前")])])
    assert plan.updates == []
    assert plan.creates == []


def _node_row(
    node_id: str,
    parent_id: str | None = None,
    *,
    name: str = "",
    source: str = "manual",
    progress=None,
    status=None,
    td_id=None,
) -> dict:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "sort_order": 0,
        "name": name or node_id,
        "assignee": None,
        "status": status,
        "manual_progress": progress,
        "source": source,
        "todoist_task_id": td_id,
        "weight": 1,
    }


def test_plan_orphan_subtask_falls_back_to_anchor():
    """親タスクがアクティブにもツリーにも無ければアンカー直下へぶら下げる。"""
    nodes = nodes_from_rows([_node_row("wing", name="主翼")])
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [FakeTask("5", "子タスク", parent_id="999")])])
    assert plan.creates[0]["parent_id"] == "wing"


def test_plan_already_completed_nodes_not_rewritten():
    nodes = nodes_from_rows(
        [
            _node_row("wing", name="主翼"),
            _node_row(
                "td_1",
                "wing",
                name="リブ",
                source="todoist",
                progress=1.0,
                status="完了",
                td_id="1",
            ),
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [])])
    assert plan.completions == []


def test_plan_never_touches_spar_winding_nodes():
    """アンカー配下に桁巻きノードがあっても完了扱い・更新の対象にしない。"""
    nodes = nodes_from_rows(
        [
            _node_row("wing", name="主翼"),
            _node_row("spar1", "wing", name="主桁", source="spar_winding", progress=0.5),
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [])])
    assert plan.completions == []
    assert plan.updates == []


def test_plan_completion_limited_to_anchored_subtrees():
    """紐付けから外れた別サブツリーの td_ ノードは完了扱いしない。"""
    nodes = nodes_from_rows(
        [
            _node_row("wing", name="主翼"),
            _node_row("tail", name="尾翼"),
            _node_row("td_9", "tail", name="別プロジェクトのタスク", source="todoist", td_id="9"),
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [])])
    assert plan.completions == []


def test_plan_updates_renamed_and_reparented_nodes():
    nodes = nodes_from_rows(
        [
            _node_row("wing", name="主翼"),
            _node_row("tail", name="尾翼"),
            _node_row("td_1", "tail", name="古い名前", source="todoist", td_id="1"),
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [FakeTask("1", "新しい名前")])])
    assert plan.updates == [("td_1", {"parent_id": "wing", "name": "新しい名前"})]


def test_plan_marks_completed_tasks():
    nodes = nodes_from_rows(
        [
            {
                "node_id": "wing",
                "parent_id": None,
                "sort_order": 0,
                "name": "主翼",
                "assignee": None,
                "status": None,
                "manual_progress": None,
                "source": "manual",
                "todoist_task_id": None,
                "weight": 1,
            },
            {
                "node_id": "td_9",
                "parent_id": "wing",
                "sort_order": 1,
                "name": "終わったタスク",
                "assignee": None,
                "status": None,
                "manual_progress": 0.0,
                "source": "todoist",
                "todoist_task_id": "9",
                "weight": 1,
            },
        ]
    )
    plan = pss_db.plan_todoist_sync(nodes, [("wing", [])])
    assert plan.completions == ["td_9"]


# ---------------------------------------------------------------------
# 同期の適用
# ---------------------------------------------------------------------
def test_sync_guild_db_imports_tasks():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)
            svc = FakeTodoist(
                [FakeProject("P1", "主翼班")], {"P1": [FakeTask("1", "リブ切り出し")]}
            )

            result = await pss_db.sync_guild_db(db, G1, svc)
            assert result.projects == 1
            assert result.added == 1
            node = await repo.get_node(G1, "td_1")
            assert node["name"] == "リブ切り出し"
            assert node["source"] == "todoist"
            # 集計済みツリーが返る
            assert result.tree.by_id["wing"].aggregated == 0.0
        finally:
            await db.close()

    run(_main())


def test_sync_guild_db_completes_missing_tasks():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.upsert_node(
                G1,
                "td_9",
                parent_id="wing",
                name="済",
                manual_progress=0.0,
                source="todoist",
                todoist_task_id="9",
                now_text=NOW,
            )
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)
            svc = FakeTodoist([FakeProject("P1", "主翼班")], {"P1": []})

            result = await pss_db.sync_guild_db(db, G1, svc)
            assert result.completed == 1
            node = await repo.get_node(G1, "td_9")
            assert node["manual_progress"] == 1.0
            assert node["status"] == "完了"
        finally:
            await db.close()

    run(_main())


def test_sync_reports_unknown_project():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.upsert_todoist_link(G1, "消えた班", "wing", NOW)
            svc = FakeTodoist([], {})
            result = await pss_db.sync_guild_db(db, G1, svc)
            assert any("消えた班" in e for e in result.errors)
        finally:
            await db.close()

    run(_main())


def test_sync_without_todoist_still_aggregates():
    """Todoist 未設定でも桁巻き反映と再集計は行われる。"""

    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "m1", name="本機", now_text=NOW)
            await repo.upsert_node(
                G1, "spar", parent_id="m1", name="主桁", manual_progress=0.5, now_text=NOW
            )
            result = await pss_db.sync_guild_db(db, G1, None)
            assert result.projects == 0
            assert result.tree.by_id["m1"].aggregated == 0.5
        finally:
            await db.close()

    run(_main())


def test_sync_is_isolated_per_guild():
    async def _main():
        db, repo = await _db()
        try:
            for guild_id in (G1, G2):
                await repo.upsert_node(guild_id, "wing", name="主翼", now_text=NOW)
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)
            svc = FakeTodoist(
                [FakeProject("P1", "主翼班")], {"P1": [FakeTask("1", "リブ切り出し")]}
            )

            await pss_db.sync_guild_db(db, G1, svc)
            assert await repo.get_node(G1, "td_1") is not None
            # 紐付けの無いギルドには取り込まれない
            assert await repo.get_node(G2, "td_1") is None
            assert await repo.count_nodes(G2) == 1
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 桁巻き（layer_records → 進捗率）
# ---------------------------------------------------------------------
async def _record_layer(db: Database, guild_id: int, keta: str, layer: str):
    await db.execute(
        "INSERT INTO layer_records"
        " (guild_id, user_id, keta, layer_num, started_at, ended_at, minutes)"
        " VALUES (?, '1', ?, ?, '2026-08-01 10:00', '2026-08-01 11:00', 60)",
        (guild_id, keta, layer),
    )


def test_spar_progress_from_layer_records():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "spar", name="主桁", now_text=NOW)
            await repo.upsert_spar_link(G1, "主桁1", "spar", 4, NOW)
            for layer in ("1", "2", "2"):  # 巻き直しは1層と数える
                await _record_layer(db, G1, "主桁1", layer)

            plan = await spar_winding_service.sync_spar_winding_db(repo, G1, NOW)
            assert plan.updated == 1
            node = await repo.get_node(G1, "spar")
            assert node["manual_progress"] == 0.5  # 2 / 4
            assert node["status"] == "製作中"
            assert node["source"] == "spar_winding"
        finally:
            await db.close()

    run(_main())


def test_spar_progress_clamps_and_marks_done():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G1, "spar", name="主桁", now_text=NOW)
            await repo.upsert_spar_link(G1, "主桁1", "spar", 2, NOW)
            for layer in ("1", "2", "3"):  # 目標を超えて巻いた
                await _record_layer(db, G1, "主桁1", layer)

            await spar_winding_service.sync_spar_winding_db(repo, G1, NOW)
            node = await repo.get_node(G1, "spar")
            assert node["manual_progress"] == 1.0
            assert node["status"] == "完了"
        finally:
            await db.close()

    run(_main())


def test_spar_link_to_missing_node_is_reported():
    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_spar_link(G1, "主桁1", "nope", 4, NOW)
            plan = await spar_winding_service.sync_spar_winding_db(repo, G1, NOW)
            assert plan.updated == 0
            assert any("nope" in e for e in plan.errors)
        finally:
            await db.close()

    run(_main())


def test_spar_progress_is_isolated_per_guild():
    async def _main():
        db, repo = await _db()
        try:
            for guild_id in (G1, G2):
                await repo.upsert_node(guild_id, "spar", name="主桁", now_text=NOW)
                await repo.upsert_spar_link(guild_id, "主桁1", "spar", 4, NOW)
            # G1 でだけ積層を記録する
            for layer in ("1", "2"):
                await _record_layer(db, G1, "主桁1", layer)

            await spar_winding_service.sync_spar_winding_db(repo, G1, NOW)
            await spar_winding_service.sync_spar_winding_db(repo, G2, NOW)
            assert (await repo.get_node(G1, "spar"))["manual_progress"] == 0.5
            assert (await repo.get_node(G2, "spar"))["manual_progress"] == 0.0
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 通知先の解決
# ---------------------------------------------------------------------
def test_resolve_link_channel_prefers_link_over_default():
    link = {"notify_channel_id": "100"}
    assert pss_db.resolve_link_channel_id(link, 200) == 100
    assert pss_db.resolve_link_channel_id({"notify_channel_id": ""}, 200) == 200
    assert pss_db.resolve_link_channel_id({}, None) is None


def test_sync_all_guilds_isolates_failures():
    """1ギルドの同期失敗が他ギルドを止めない。"""

    async def _main():
        db, repo = await _db()
        try:
            await repo.upsert_node(G2, "m1", name="本機", now_text=NOW)
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)

            class BrokenManager:
                async def for_guild(self, guild_id):
                    if guild_id == G1:
                        raise RuntimeError("token broken")
                    return SimpleNamespace(enabled=False)

            results = await pss_db.sync_all_guilds(db, [G1, G2], BrokenManager())
            assert [r.guild_id for r in results] == [G1, G2]
            assert results[1].tree is not None  # G2 は正常に同期される
        finally:
            await db.close()

    run(_main())
