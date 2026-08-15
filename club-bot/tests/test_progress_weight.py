"""機体重量（スキーマ v12 / migrations/011）のテスト。

人力飛行機は重量が競技成績に直結する。機体 → パーツ → 部品の木は進捗と
同一なので、progress_nodes に列を足して既存の再帰集計へ相乗りさせる。

- 新規 DB に target_weight_g / actual_weight_g があること
- v11 相当の既存 DB からマイグレーションで列が増え、既存ノードが壊れないこと
- PostgreSQL 用 DDL では DOUBLE PRECISION になること
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.progress import Progress, build_level_embed, format_grams, weight_line
from repositories.progress_repository import ProgressRepository
from services.progress_tree import (
    ProgressNode,
    build_and_aggregate,
    load_tree,
    nodes_over_target,
    weight_summary,
)
from utils.db import SCHEMA_VERSION, TABLE_DDL_PG, Database
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
NOW = "2026-08-12 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


async def _columns(db: Database, table: str) -> set[str]:
    return {r["name"] for r in await db.fetchall(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_schema_version_is_at_least_12():
    assert SCHEMA_VERSION >= 12


def test_fresh_schema_has_weight_columns():
    async def _main():
        db = await _connected_db()
        try:
            cols = await _columns(db, "progress_nodes")
            assert {"target_weight_g", "actual_weight_g"} <= cols
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_pg_ddl_uses_double_precision_for_weights():
    """PostgreSQL の REAL は 4 バイトなので DOUBLE PRECISION へ広げる。"""
    ddl = TABLE_DDL_PG["progress_nodes"]
    assert "target_weight_g DOUBLE PRECISION" in ddl
    assert "actual_weight_g DOUBLE PRECISION" in ddl
    assert "REAL" not in ddl


# ---------------------------------------------------------------------
# v11 → v12 マイグレーション
# ---------------------------------------------------------------------
def _make_v11_db() -> str:
    """重量列を持たない progress_nodes を持つ DB（v11 相当）。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE progress_nodes (
            progress_node_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        INTEGER NOT NULL,
            node_id         TEXT NOT NULL,
            parent_id       TEXT,
            sort_order      REAL NOT NULL DEFAULT 0,
            name            TEXT NOT NULL DEFAULT '',
            assignee        TEXT,
            status          TEXT,
            manual_progress REAL,
            source          TEXT NOT NULL DEFAULT 'manual',
            todoist_task_id TEXT,
            weight          REAL NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE (guild_id, node_id)
        );
        PRAGMA user_version = 11;
        """
    )
    conn.execute(
        "INSERT INTO progress_nodes (guild_id, node_id, name, manual_progress,"
        " created_at, updated_at) VALUES (?, 'wing', '主翼', 0.5, ?, ?)",
        (G1, NOW, NOW),
    )
    conn.commit()
    conn.close()
    return path


def test_v11_db_gains_columns_and_keeps_nodes():
    async def _main():
        db = Database(_make_v11_db())
        await db.connect()
        try:
            cols = await _columns(db, "progress_nodes")
            assert {"target_weight_g", "actual_weight_g"} <= cols

            row = await db.fetchone(
                "SELECT * FROM progress_nodes WHERE guild_id = ? AND node_id = ?", (G1, "wing")
            )
            assert row is not None, "既存ノードが失われた"
            assert row["name"] == "主翼"
            assert row["manual_progress"] == 0.5
            # 既存ノードは重量未入力として扱う
            assert row["target_weight_g"] is None
            assert row["actual_weight_g"] is None
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_weight_migration_is_idempotent():
    async def _main():
        path = _make_v11_db()
        for _ in range(2):
            db = Database(path)
            await db.connect()
            assert {"target_weight_g", "actual_weight_g"} <= await _columns(db, "progress_nodes")
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# リポジトリからの読み書き
# ---------------------------------------------------------------------
def test_weight_columns_are_updatable():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)

            await repo.update_node(
                G1, "wing", now_text=NOW, actual_weight_g=1240.5, target_weight_g=1100.0
            )

            row = await db.fetchone(
                "SELECT * FROM progress_nodes WHERE guild_id = ? AND node_id = ?", (G1, "wing")
            )
            assert row["actual_weight_g"] == 1240.5
            assert row["target_weight_g"] == 1100.0
        finally:
            await db.close()

    run(_main())


def test_weight_survives_round_trip_through_tree():
    """DB → ProgressNode → 集計まで重量が流れること。"""

    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.update_node(
                G1, "wing", now_text=NOW, actual_weight_g=1240.0, target_weight_g=1100.0
            )

            tree = await load_tree(repo, G1)
            node = tree.by_id["wing"]
            assert node.actual_weight_g == 1240.0
            assert node.target_weight_g == 1100.0
            assert node.aggregated_actual_weight_g == 1240.0
        finally:
            await db.close()

    run(_main())


def test_weight_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
            await repo.upsert_node(G2, "wing", name="別大学の主翼", now_text=NOW)

            await repo.update_node(G1, "wing", now_text=NOW, actual_weight_g=1240.0)

            other = await db.fetchone(
                "SELECT * FROM progress_nodes WHERE guild_id = ? AND node_id = ?", (G2, "wing")
            )
            assert other["actual_weight_g"] is None, "他サーバーの同名ノードを巻き込んで更新した"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 集計規則（F3-2）
# ---------------------------------------------------------------------
def _node(node_id, parent=None, *, actual=None, target=None, order=0.0):
    return ProgressNode(
        node_id=node_id,
        parent_id=parent,
        order=order,
        name=node_id,
        actual_weight_g=actual,
        target_weight_g=target,
    )


def test_leaf_weights_roll_up_to_parent():
    """葉だけに実測があるとき、親は子の合計になる。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("tail", "airframe", actual=200.0),
        ]
    )
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 1000.0


def test_parent_actual_beats_children_sum():
    """親に実測があれば、子の合計ではなく親の実測が勝つ。"""
    tree = build_and_aggregate(
        [
            _node("airframe", actual=1500.0),
            _node("wing", "airframe", actual=800.0),
            _node("tail", "airframe", actual=200.0),
        ]
    )
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 1500.0
    # 子側は自分の実測のまま
    assert tree.by_id["wing"].aggregated_actual_weight_g == 800.0


def test_target_weight_uses_same_rule():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", target=700.0),
            _node("tail", "airframe", target=150.0),
        ]
    )
    assert tree.by_id["airframe"].aggregated_target_weight_g == 850.0

    tree = build_and_aggregate(
        [
            _node("airframe", target=900.0),
            _node("wing", "airframe", target=700.0),
        ]
    )
    assert tree.by_id["airframe"].aggregated_target_weight_g == 900.0


def test_unmeasured_subtree_is_none_not_zero():
    """未計測は 0 g ではなく None（見積もりに混ぜない）。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe"),
        ]
    )
    assert tree.by_id["airframe"].aggregated_actual_weight_g is None
    assert tree.by_id["wing"].aggregated_actual_weight_g is None


def test_partially_measured_parent_sums_only_measured_children():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("tail", "airframe"),  # 未計測
        ]
    )
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 800.0


def test_deep_tree_rolls_up_through_all_levels():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe"),
            _node("spar", "wing", actual=300.0),
            _node("rib", "wing", actual=120.0),
            _node("tail", "airframe", actual=200.0),
        ]
    )
    assert tree.by_id["wing"].aggregated_actual_weight_g == 420.0
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 620.0


def test_cycle_does_not_hang_and_is_excluded():
    """循環データでも停止し、循環ノードは集計から外れる。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("a", "b", actual=999.0),  # 循環
            _node("b", "a", actual=999.0),
        ]
    )
    assert "a" not in tree.by_id
    assert "b" not in tree.by_id
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 800.0
    assert tree.errors


def test_orphan_is_excluded_from_weight():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("lost", "no-such-parent", actual=500.0),
        ]
    )
    assert "lost" not in tree.by_id
    assert tree.by_id["airframe"].aggregated_actual_weight_g == 800.0


# ---------------------------------------------------------------------
# サマリ（実測入力率・目標との差）
# ---------------------------------------------------------------------
def test_summary_reports_totals_and_fill_rate():
    tree = build_and_aggregate(
        [
            _node("airframe", target=1000.0),
            _node("wing", "airframe", actual=800.0),
            _node("tail", "airframe"),
        ]
    )
    summary = weight_summary(tree)
    assert summary.actual_g == 800.0
    assert summary.target_g == 1000.0
    assert summary.total_nodes == 3
    assert summary.measured_nodes == 1
    assert abs(summary.fill_rate - 1 / 3) < 1e-9
    assert summary.diff_g == -200.0
    assert summary.is_over_target is False


def test_summary_flags_over_target():
    tree = build_and_aggregate(
        [
            _node("airframe", target=1100.0, actual=1240.0),
        ]
    )
    summary = weight_summary(tree)
    assert summary.diff_g == 140.0
    assert summary.is_over_target is True


def test_summary_of_subtree():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("spar", "wing", actual=300.0),
            _node("tail", "airframe", actual=200.0),
        ]
    )
    summary = weight_summary(tree, "wing")
    assert summary.actual_g == 800.0  # 親の実測が勝つ
    assert summary.total_nodes == 2  # wing と spar
    assert summary.measured_nodes == 2


def test_summary_of_unknown_node_is_empty():
    tree = build_and_aggregate([_node("airframe", actual=100.0)])
    summary = weight_summary(tree, "no-such-node")
    assert summary.actual_g is None
    assert summary.total_nodes == 0
    assert summary.fill_rate == 0.0


def test_summary_of_empty_tree():
    summary = weight_summary(build_and_aggregate([]))
    assert summary.actual_g is None
    assert summary.target_g is None
    assert summary.fill_rate == 0.0


# ---------------------------------------------------------------------
# 超過ランキング（減量の着手先）
# ---------------------------------------------------------------------
def test_over_target_nodes_sorted_by_excess():
    """超過量の大きい順。親は子から積み上がった合計で判定される。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=900.0, target=800.0),  # +100
            _node("tail", "airframe", actual=260.0, target=200.0),  # +60
            _node("fuse", "airframe", actual=100.0, target=150.0),  # -50
        ]
    )
    ranked = nodes_over_target(tree)
    # airframe は 1260 / 1150 で +110（機体全体としても超過している）
    assert [n.node_id for n, _ in ranked] == ["airframe", "wing", "tail"]
    assert [round(over) for _, over in ranked] == [110, 100, 60]
    assert "fuse" not in [n.node_id for n, _ in ranked]


def test_over_target_excludes_nodes_within_target():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=700.0, target=800.0),
            _node("tail", "airframe", actual=150.0, target=200.0),
        ]
    )
    assert nodes_over_target(tree) == []


def test_over_target_skips_nodes_without_target():
    tree = build_and_aggregate(
        [
            _node("wing", actual=900.0),  # 目標が無いので判定できない
        ]
    )
    assert nodes_over_target(tree) == []


# ---------------------------------------------------------------------
# /weight コマンドと /progress view への表示（F3-3）
# ---------------------------------------------------------------------
def _weight_command(name: str):
    for cmd in Progress(_FakeProgressBot()).walk_app_commands():
        if cmd.qualified_name == f"weight {name}":
            return cmd
    raise AssertionError(f"/weight {name} が見つからない")


class _FakeProgressBot:
    db = None
    todoist_manager = None
    guilds = ()


def test_weight_set_requires_leader_level():
    """記録は班長（L2）以上。閲覧は誰でもできる。"""
    assert command_required_level(_weight_command("set")) == Level.L2
    assert command_required_level(_weight_command("view")) == Level.L1
    assert command_required_level(_weight_command("top")) == Level.L1


def test_weight_commands_are_registered():
    names = {c.qualified_name for c in Progress(_FakeProgressBot()).walk_app_commands()}
    assert {"weight set", "weight view", "weight top"} <= names


# ---- 表示 ----------------------------------------------------------
def test_weight_line_formats_actual_target_and_diff():
    tree = build_and_aggregate(
        [
            _node("airframe", actual=1240.0, target=1100.0),
        ]
    )
    assert weight_line(tree, "airframe") == "重量: 実測 1,240g / 目標 1,100g（+140g）"


def test_weight_line_shows_under_target_as_negative():
    tree = build_and_aggregate([_node("wing", actual=900.0, target=1000.0)])
    assert "（-100g）" in weight_line(tree, "wing")


def test_weight_line_is_empty_when_unset():
    """重量を使っていないサーバーでは行ごと出さない。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe"),
        ]
    )
    assert weight_line(tree, "airframe") == ""
    assert weight_line(tree, None) == ""


def test_weight_line_with_only_actual():
    tree = build_and_aggregate([_node("wing", actual=900.0)])
    line = weight_line(tree, "wing")
    assert "実測 900g" in line
    assert "目標" not in line
    assert "（" not in line  # 差分は出せない


def test_progress_view_embed_unchanged_without_weights():
    """重量未設定なら /progress view の説明文は従来どおり。"""
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe"),
        ]
    )
    embed = build_level_embed(tree, "airframe")
    assert "重量" not in (embed.description or "")


def test_progress_view_embed_shows_weight_when_set():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0, target=700.0),
        ]
    )
    embed = build_level_embed(tree, "airframe")
    assert "重量: 実測 800g / 目標 700g（+100g）" in (embed.description or "")


def test_leaf_embed_shows_weight():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0, target=700.0),
        ]
    )
    embed = build_level_embed(tree, "wing")
    assert "進捗率" in (embed.description or "")
    assert "重量: 実測 800g" in (embed.description or "")


def test_root_level_embed_shows_total_weight():
    tree = build_and_aggregate(
        [
            _node("airframe"),
            _node("wing", "airframe", actual=800.0),
            _node("tail", "airframe", actual=200.0),
        ]
    )
    embed = build_level_embed(tree, None)
    assert "実測 1,000g" in (embed.description or "")


def test_format_grams_uses_thousands_separator():
    assert format_grams(1240) == "1,240g"
    assert format_grams(0) == "0g"
