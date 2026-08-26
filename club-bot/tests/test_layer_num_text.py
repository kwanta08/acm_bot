"""layer_sessions.layer_num の TEXT 化（スキーマ v16）のテスト。

/layer start の層番号は数字のほか「シュリンク」等のテキストを受け付ける仕様
（コマンド定義の describe に明記。layer_records.layer_num は当初から TEXT）
なのに、進行中セッションの layer_sessions.layer_num だけ INTEGER で
作られていた。

SQLite は動的型付けでテキストも保存できてしまうため開発環境では顕在化せず、
本番（PostgreSQL / asyncpg）だけが

    DataError: invalid input for query argument $4: 'test'
    ('str' object cannot be interpreted as an integer)

で /layer start に失敗していた（G1-0 / G1-9 と同型の「SQLite では再現しない」
不具合）。PostgreSQL 実機での再現・修正確認は
tests/test_db_postgres.py::test_pg_live_layer_start_accepts_text_layer_num。
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from repositories.layer_session_repository import LayerSessionRepository
from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database

G1 = 100000000000000001
NOW = "2026-08-26 10:00:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _declared_type(db: Database, table: str, column: str) -> str:
    rows = await db.fetchall(f"PRAGMA table_info({table})")
    return next(r["type"].upper() for r in rows if r["name"] == column)


# ---------------------------------------------------------------------
# スキーマ宣言
# ---------------------------------------------------------------------
def test_layer_num_is_text_in_both_ddls():
    """進行中セッションと完了記録の layer_num がどちらも TEXT であること。"""
    for ddl in (TABLE_DDL["layer_sessions"], TABLE_DDL_PG["layer_sessions"]):
        assert re.search(r"layer_num\s+TEXT", ddl), "layer_sessions.layer_num が TEXT でない"
        assert not re.search(r"layer_num\s+INTEGER", ddl)
    assert re.search(r"layer_num\s+TEXT", TABLE_DDL["layer_records"])


def test_schema_version_is_at_least_16():
    assert SCHEMA_VERSION >= 16


# ---------------------------------------------------------------------
# 新規 DB
# ---------------------------------------------------------------------
def test_fresh_db_stores_text_layer_num():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            assert await _declared_type(db, "layer_sessions", "layer_num") == "TEXT"

            repo = LayerSessionRepository(db)
            await repo.start(G1, "u1", "主桁1", "シュリンク", NOW)
            row = await repo.get_by_user(G1, "u1")
            assert row is not None and row["layer_num"] == "シュリンク"

            # 数字入力も文字列のまま保持される（INTEGER 親和性で 3 に
            # 丸められない。'05' のような入力が '5' に化けない）
            await repo.start(G1, "u2", "主桁1", "05", NOW)
            assert (await repo.get_by_user(G1, "u2"))["layer_num"] == "05"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# v15 → v16 マイグレーション
# ---------------------------------------------------------------------
def _make_v15_db() -> str:
    """layer_num が INTEGER の layer_sessions を持つ DB（v15 相当）。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE layer_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL CHECK (guild_id >= 0),
            user_id    TEXT NOT NULL,
            keta       TEXT NOT NULL,
            layer_num  INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            UNIQUE (guild_id, user_id)
        );
        INSERT INTO layer_sessions (guild_id, user_id, keta, layer_num, started_at)
        VALUES ({G1}, 'u1', '主桁1', 3, '{NOW}');
        PRAGMA user_version = 15;
        """
    )
    conn.commit()
    conn.close()
    return path


def test_v15_db_migrates_layer_num_to_text_and_keeps_sessions():
    async def _main():
        db = Database(_make_v15_db())
        await db.connect()
        try:
            assert await _declared_type(db, "layer_sessions", "layer_num") == "TEXT"
            assert await db._user_version() == SCHEMA_VERSION

            # 既存の進行中セッションが失われていない（数値は文字列になる）
            repo = LayerSessionRepository(db)
            row = await repo.get_by_user(G1, "u1")
            assert row is not None, "既存セッションが失われた"
            assert row["keta"] == "主桁1"
            assert row["layer_num"] == "3"

            # 移行後はテキストの層番号で /layer start できる
            await repo.start(G1, "u2", "主桁1", "シュリンク", NOW)
            assert (await repo.get_by_user(G1, "u2"))["layer_num"] == "シュリンク"

            # 再作成後も 1 人 1 セッションの UNIQUE (guild_id, user_id) が生きている
            with pytest.raises(sqlite3.IntegrityError):
                await repo.start(G1, "u1", "主桁2", "1", NOW)
        finally:
            await db.close()

    run(_main())


def test_v16_migration_is_idempotent():
    """既に TEXT の DB へもう一度かけても何も起きないこと。"""

    async def _main():
        path = _tmp_db_path()
        db = Database(path)
        await db.connect()
        try:
            repo = LayerSessionRepository(db)
            await repo.start(G1, "u1", "主桁1", "シュリンク", NOW)
            await db._migrate_v16_layer_num_text()
            assert await _declared_type(db, "layer_sessions", "layer_num") == "TEXT"
            assert (await repo.get_by_user(G1, "u1"))["layer_num"] == "シュリンク"
        finally:
            await db.close()

    run(_main())
