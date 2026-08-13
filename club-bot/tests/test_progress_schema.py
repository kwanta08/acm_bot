"""機体進捗テーブル（スキーマ v10 / migrations/009）の単体テスト。

- 新規 DB に progress_nodes / progress_todoist_links / progress_spar_links が
  作成され、期待するカラムを持つこと
- v9 相当の既存 DB から自動マイグレーションでテーブルが追加され、
  既存データが壊れないこと
- (guild_id, node_id) の一意制約が効き、ギルドをまたぐと同じ node_id を
  独立に使えること（マルチテナント分離）
- PostgreSQL 用 DDL への機械変換が正しいこと（REAL → DOUBLE PRECISION 等）
"""
import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database

G1 = 100000000000000001
G2 = 200000000000000002

PROGRESS_TABLES = ("progress_nodes", "progress_todoist_links",
                   "progress_spar_links")

# v9 相当（機体進捗テーブル導入前）のテーブル群
V9_TABLES = [t for t in TABLE_DDL if t not in PROGRESS_TABLES]


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # connect() に作成させる
    return path


def run(coro):
    return asyncio.run(coro)


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


async def _columns(db: Database, table: str) -> set[str]:
    return {r["name"] for r in await db.fetchall(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------
# 新規 DB
# ---------------------------------------------------------------------
def test_fresh_schema_creates_progress_tables():
    async def _main():
        db = await _connected_db()
        try:
            cols = await _columns(db, "progress_nodes")
            assert {"progress_node_id", "guild_id", "node_id", "parent_id",
                    "sort_order", "name", "assignee", "status",
                    "manual_progress", "source", "todoist_task_id", "weight",
                    "created_at", "updated_at"} <= cols

            cols = await _columns(db, "progress_todoist_links")
            assert {"link_id", "guild_id", "project_name", "node_id",
                    "notify_channel_id", "created_by", "created_at",
                    "updated_at"} <= cols

            cols = await _columns(db, "progress_spar_links")
            assert {"spar_link_id", "guild_id", "keta_name", "node_id",
                    "target_layers", "created_at", "updated_at"} <= cols
        finally:
            await db.close()

    run(_main())


def test_fresh_schema_version_is_current():
    async def _main():
        db = await _connected_db()
        try:
            assert await db._user_version() == SCHEMA_VERSION
            assert SCHEMA_VERSION >= 10
        finally:
            await db.close()

    run(_main())


def test_progress_indexes_exist():
    async def _main():
        db = await _connected_db()
        try:
            rows = await db.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index'")
            names = {r["name"] for r in rows}
            assert "idx_progress_nodes_guild_parent" in names
            assert "idx_progress_nodes_guild_source" in names
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# guild_id スコープ
# ---------------------------------------------------------------------
async def _insert_node(db: Database, guild_id: int, node_id: str,
                       parent_id: str | None = None, name: str = "主翼"):
    await db.execute(
        "INSERT INTO progress_nodes"
        " (guild_id, node_id, parent_id, sort_order, name, source,"
        "  created_at, updated_at)"
        " VALUES (?, ?, ?, 0, ?, 'manual', '2026-01-01', '2026-01-01')",
        (guild_id, node_id, parent_id, name))


def test_same_node_id_allowed_in_different_guilds():
    """同じ node_id を別ギルドで独立に持てる（テナント分離）。"""
    async def _main():
        db = await _connected_db()
        try:
            await _insert_node(db, G1, "airframe", name="1号機")
            await _insert_node(db, G2, "airframe", name="別大学の機体")

            rows = await db.fetchall(
                "SELECT name FROM progress_nodes WHERE guild_id = ?", (G1,))
            assert [r["name"] for r in rows] == ["1号機"]
            rows = await db.fetchall(
                "SELECT name FROM progress_nodes WHERE guild_id = ?", (G2,))
            assert [r["name"] for r in rows] == ["別大学の機体"]
        finally:
            await db.close()

    run(_main())


def test_node_id_is_unique_within_guild():
    async def _main():
        db = await _connected_db()
        try:
            await _insert_node(db, G1, "dup")
            try:
                await _insert_node(db, G1, "dup")
            except sqlite3.IntegrityError as e:
                assert "UNIQUE" in str(e).upper()
            else:
                raise AssertionError("同一ギルド内の node_id 重複が許可された")
        finally:
            await db.close()

    run(_main())


def test_links_are_unique_per_guild():
    async def _main():
        db = await _connected_db()
        try:
            for guild_id in (G1, G2):
                await db.execute(
                    "INSERT INTO progress_todoist_links"
                    " (guild_id, project_name, node_id, created_at, updated_at)"
                    " VALUES (?, '主翼班', 'wing', '2026-01-01', '2026-01-01')",
                    (guild_id,))
                await db.execute(
                    "INSERT INTO progress_spar_links"
                    " (guild_id, keta_name, node_id, target_layers,"
                    "  created_at, updated_at)"
                    " VALUES (?, '主桁1', 'spar1', 12, '2026-01-01', '2026-01-01')",
                    (guild_id,))
            rows = await db.fetchall("SELECT guild_id FROM progress_todoist_links")
            assert len(rows) == 2  # ギルドごとに1行ずつ共存できる

            try:
                await db.execute(
                    "INSERT INTO progress_todoist_links"
                    " (guild_id, project_name, node_id, created_at, updated_at)"
                    " VALUES (?, '主翼班', 'other', '2026-01-01', '2026-01-01')",
                    (G1,))
            except sqlite3.IntegrityError as e:
                assert "UNIQUE" in str(e).upper()
            else:
                raise AssertionError("同一ギルド内のプロジェクト名重複が許可された")
        finally:
            await db.close()

    run(_main())


def test_spar_link_rejects_non_positive_target():
    async def _main():
        db = await _connected_db()
        try:
            try:
                await db.execute(
                    "INSERT INTO progress_spar_links"
                    " (guild_id, keta_name, node_id, target_layers,"
                    "  created_at, updated_at)"
                    " VALUES (?, '主桁1', 'spar1', 0, '2026-01-01', '2026-01-01')",
                    (G1,))
            except sqlite3.IntegrityError as e:
                assert "CHECK" in str(e).upper()
            else:
                raise AssertionError("目標層数 0 が許可された")
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 既存 DB からのマイグレーション
# ---------------------------------------------------------------------
def test_migrates_v9_database():
    """進捗テーブルが無い v9 相当 DB に、テーブルが追加されること。"""
    async def _main():
        path = _tmp_db_path()
        # v9 相当の DB を手で作る（進捗テーブルなし・user_version=9）
        async with aiosqlite.connect(path) as conn:
            for name in V9_TABLES:
                await conn.executescript(TABLE_DDL[name])
            await conn.execute(
                "INSERT INTO guilds (guild_id, guild_name, joined_at)"
                " VALUES (?, '既存サークル', '2026-01-01')", (G1,))
            await conn.execute("PRAGMA user_version = 9")
            await conn.commit()

        db = Database(path)
        await db.connect()
        try:
            assert await db._user_version() == SCHEMA_VERSION
            for table in PROGRESS_TABLES:
                assert await _columns(db, table), f"{table} が作成されていない"
            # 既存データは保持される
            row = await db.fetchone(
                "SELECT guild_name FROM guilds WHERE guild_id = ?", (G1,))
            assert row["guild_name"] == "既存サークル"
        finally:
            await db.close()

    run(_main())


def test_migration_is_idempotent():
    """2回接続しても失敗せず、行が消えないこと。"""
    async def _main():
        path = _tmp_db_path()
        db = Database(path)
        await db.connect()
        await _insert_node(db, G1, "airframe", name="1号機")
        await db.close()

        db = Database(path)
        await db.connect()
        try:
            rows = await db.fetchall(
                "SELECT node_id FROM progress_nodes WHERE guild_id = ?", (G1,))
            assert [r["node_id"] for r in rows] == ["airframe"]
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# PostgreSQL 用 DDL への変換
# ---------------------------------------------------------------------
def test_pg_ddl_conversion():
    ddl = TABLE_DDL_PG["progress_nodes"]
    # SQLite 固有の記法が残っていない
    assert "AUTOINCREMENT" not in ddl
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl
    # Discord ID は BIGINT
    assert "guild_id BIGINT" in ddl
    # REAL（4バイト float）ではなく倍精度で保持する
    assert "REAL" not in ddl
    # sort_order / manual_progress / weight / target_weight_g / actual_weight_g
    assert ddl.count("DOUBLE PRECISION") == 5

    spar = TABLE_DDL_PG["progress_spar_links"]
    assert "guild_id BIGINT" in spar
    assert "CHECK (target_layers > 0)" in spar
