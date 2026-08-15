"""ProgressRepository（機体進捗ツリーの CRUD）の単体テスト。

- ノードの upsert / 部分更新 / 削除（サブツリー削除を含む）
- Todoist 紐付け・桁巻き紐付けの upsert と削除
- layer_records からの完了層数集計
- **すべての操作が guild_id で分離されること**（他ギルドのデータを
  読まない・壊さない）
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from repositories.progress_repository import ProgressRepository
from utils.db import Database

G1 = 100000000000000001
G2 = 200000000000000002
NOW = "2026-08-11 10:00"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


async def _repo() -> tuple[Database, ProgressRepository]:
    db = Database(_tmp_db_path())
    await db.connect()
    return db, ProgressRepository(db)


async def _seed_tree(repo: ProgressRepository, guild_id: int) -> None:
    """機体 → パーツ → 部品 の3階層を作る。"""
    await repo.upsert_node(guild_id, "airframe", name="1号機", now_text=NOW)
    await repo.upsert_node(
        guild_id, "wing", parent_id="airframe", name="主翼", sort_order=1, now_text=NOW
    )
    await repo.upsert_node(
        guild_id,
        "spar",
        parent_id="wing",
        name="主桁",
        manual_progress=0.5,
        sort_order=1,
        now_text=NOW,
    )


# ---------------------------------------------------------------------
# ノード
# ---------------------------------------------------------------------
def test_upsert_and_list_nodes():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            nodes = await repo.list_nodes(G1)
            assert {n["node_id"] for n in nodes} == {"airframe", "wing", "spar"}
            spar = next(n for n in nodes if n["node_id"] == "spar")
            assert spar["parent_id"] == "wing"
            assert spar["manual_progress"] == 0.5
            assert spar["source"] == "manual"
            assert spar["weight"] == 1.0
        finally:
            await db.close()

    run(_main())


def test_upsert_replaces_existing_row():
    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.upsert_node(
                G1,
                "wing",
                name="主翼（改）",
                parent_id="airframe",
                manual_progress=0.25,
                now_text="2026-08-12",
            )
            assert await repo.count_nodes(G1) == 1
            node = await repo.get_node(G1, "wing")
            assert node["name"] == "主翼（改）"
            assert node["parent_id"] == "airframe"
            assert node["manual_progress"] == 0.25
            # created_at は初回のまま、updated_at だけ進む
            assert node["created_at"] == NOW
            assert node["updated_at"] == "2026-08-12"
        finally:
            await db.close()

    run(_main())


def test_update_node_partial():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            assert await repo.update_node(
                G1, "spar", "2026-08-12", assignee="山田", status="製作中"
            )
            node = await repo.get_node(G1, "spar")
            assert node["assignee"] == "山田"
            assert node["status"] == "製作中"
            # 触っていない列は保持される
            assert node["name"] == "主桁"
            assert node["manual_progress"] == 0.5
        finally:
            await db.close()

    run(_main())


def test_update_node_rejects_unknown_column():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            # シート時代の列名（row_index）・主キー・created_at は更新できない
            with pytest.raises(ValueError):
                await repo.update_node(G1, "spar", NOW, row_index=3)
            with pytest.raises(ValueError):
                await repo.update_node(G1, "spar", NOW, progress_node_id=1)
            with pytest.raises(ValueError):
                await repo.update_node(G1, "spar", NOW, created_at="2020-01-01")
        finally:
            await db.close()

    run(_main())


def test_update_node_returns_false_for_missing():
    async def _main():
        db, repo = await _repo()
        try:
            assert await repo.update_node(G1, "nope", NOW, name="x") is False
        finally:
            await db.close()

    run(_main())


def test_set_progress():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            await repo.set_progress(G1, "spar", 1.0, NOW, status="完了", source="spar_winding")
            node = await repo.get_node(G1, "spar")
            assert node["manual_progress"] == 1.0
            assert node["status"] == "完了"
            assert node["source"] == "spar_winding"
        finally:
            await db.close()

    run(_main())


def test_list_children():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            roots = await repo.list_children(G1, None)
            assert [n["node_id"] for n in roots] == ["airframe"]
            assert [n["node_id"] for n in await repo.list_children(G1, "airframe")] == ["wing"]
            assert await repo.list_children(G1, "spar") == []
        finally:
            await db.close()

    run(_main())


def test_delete_subtree():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            await repo.upsert_node(G1, "rib", parent_id="wing", name="リブ", now_text=NOW)
            deleted = await repo.delete_subtree(G1, "wing")
            assert deleted == 3  # wing + spar + rib
            assert {n["node_id"] for n in await repo.list_nodes(G1)} == {"airframe"}
        finally:
            await db.close()

    run(_main())


def test_delete_subtree_survives_cycle():
    """循環参照があっても停止すること（不正データへの耐性）。"""

    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_node(G1, "a", parent_id="b", now_text=NOW)
            await repo.upsert_node(G1, "b", parent_id="a", now_text=NOW)
            assert await repo.delete_subtree(G1, "a") == 2
            assert await repo.count_nodes(G1) == 0
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 紐付け
# ---------------------------------------------------------------------
def test_todoist_link_upsert_and_delete():
    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_todoist_link(
                G1, "主翼班", "wing", NOW, notify_channel_id="123", created_by="42"
            )
            links = await repo.list_todoist_links(G1)
            assert len(links) == 1
            assert links[0]["node_id"] == "wing"
            assert links[0]["notify_channel_id"] == "123"

            # 同じプロジェクト名は上書き（重複行を作らない）
            await repo.upsert_todoist_link(G1, "主翼班", "spar", "2026-08-12")
            links = await repo.list_todoist_links(G1)
            assert len(links) == 1
            assert links[0]["node_id"] == "spar"

            assert await repo.delete_todoist_link(G1, "主翼班") is True
            assert await repo.list_todoist_links(G1) == []
        finally:
            await db.close()

    run(_main())


def test_spar_link_upsert_and_validation():
    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_spar_link(G1, "主桁1", "spar", 12, NOW)
            links = await repo.list_spar_links(G1)
            assert links[0]["target_layers"] == 12

            await repo.upsert_spar_link(G1, "主桁1", "spar", 16, "2026-08-12")
            assert (await repo.list_spar_links(G1))[0]["target_layers"] == 16

            with pytest.raises(ValueError):
                await repo.upsert_spar_link(G1, "主桁2", "spar2", 0, NOW)
        finally:
            await db.close()

    run(_main())


def test_count_completed_layers_from_layer_records():
    async def _main():
        db, repo = await _repo()
        try:
            for keta, layer in (("主桁1", "1"), ("主桁1", "2"), ("主桁1", "2"), ("主桁2", "1")):
                await db.execute(
                    "INSERT INTO layer_records"
                    " (guild_id, user_id, keta, layer_num, started_at,"
                    "  ended_at, minutes)"
                    " VALUES (?, '1', ?, ?, '2026-08-01 10:00',"
                    "  '2026-08-01 11:00', 60)",
                    (G1, keta, layer),
                )
            counts = await repo.count_completed_layers(G1)
            # 同じ層の巻き直しは1層として数える
            assert counts == {"主桁1": 2, "主桁2": 1}
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# マルチテナント分離
# ---------------------------------------------------------------------
def test_nodes_are_isolated_per_guild():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            await repo.upsert_node(G2, "airframe", name="別大学の機体", now_text=NOW)

            assert await repo.count_nodes(G1) == 3
            assert await repo.count_nodes(G2) == 1
            assert (await repo.get_node(G2, "airframe"))["name"] == "別大学の機体"
            # 他ギルドのノードは見えない
            assert await repo.get_node(G2, "wing") is None
            assert await repo.exists(G2, "spar") is False
        finally:
            await db.close()

    run(_main())


def test_updates_do_not_leak_across_guilds():
    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_node(G1, "airframe", name="1号機", now_text=NOW)
            await repo.upsert_node(G2, "airframe", name="別大学の機体", now_text=NOW)

            await repo.update_node(G1, "airframe", NOW, name="1号機（改）")
            assert (await repo.get_node(G2, "airframe"))["name"] == "別大学の機体"

            assert await repo.delete_node(G2, "airframe") is True
            assert await repo.get_node(G1, "airframe") is not None
        finally:
            await db.close()

    run(_main())


def test_delete_all_nodes_only_affects_one_guild():
    async def _main():
        db, repo = await _repo()
        try:
            await _seed_tree(repo, G1)
            await _seed_tree(repo, G2)
            assert await repo.delete_all_nodes(G1) == 3
            assert await repo.count_nodes(G1) == 0
            assert await repo.count_nodes(G2) == 3
        finally:
            await db.close()

    run(_main())


def test_links_and_layer_counts_are_isolated():
    async def _main():
        db, repo = await _repo()
        try:
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)
            await repo.upsert_spar_link(G1, "主桁1", "spar", 12, NOW)
            await db.execute(
                "INSERT INTO layer_records"
                " (guild_id, user_id, keta, layer_num, started_at,"
                "  ended_at, minutes)"
                " VALUES (?, '1', '主桁1', '1', '2026-08-01 10:00',"
                "  '2026-08-01 11:00', 60)",
                (G1,),
            )

            assert await repo.list_todoist_links(G2) == []
            assert await repo.list_spar_links(G2) == []
            assert await repo.count_completed_layers(G2) == {}
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# for_guild プロキシ（services へ渡す形）
# ---------------------------------------------------------------------
def test_for_guild_proxy_binds_guild_id():
    async def _main():
        db, repo = await _repo()
        try:
            bound = repo.for_guild(G1)
            await bound.upsert_node("airframe", name="1号機", now_text=NOW)
            assert bound.guild_id == G1
            assert len(await bound.list_nodes()) == 1
            assert await repo.count_nodes(G2) == 0
        finally:
            await db.close()

    run(_main())
