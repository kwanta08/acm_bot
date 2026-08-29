"""ダッシュボードの行 ID を主キーの型へ正規化することのテスト（G1-0）。

ルータは URL の `row_id` を `str` で受け取り、`utils/db.py` の `_prepare()` は
`?` → `$n` の書き換えしかしない。そのため PostgreSQL では **str が bigint
パラメータとして asyncpg へ渡り** DataError になる。

    asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
    ('str' object cannot be interpreted as an integer)

**SQLite では型親和性が '5' を 5 に読み替えるため再現しない。**
「テストが緑」を根拠にできない典型なので、ここでは
「一致するか」ではなく **ドライバへ渡る値そのもの** を検査する。

本番は PostgreSQL（ADR 0006）なので、これは本番だけが壊れる不具合。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from repositories.table_repository import (
    TABLES,
    TableRepository,
    UnknownRowError,
    coerce_row_id,
    get_spec,
)
from utils.db import TABLE_DDL, Database

G1 = 100000000000000001

# 主キーの型 → DDL の宣言型
DDL_TYPE_FOR = {"int": "INTEGER", "text": "TEXT"}


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


# ---------------------------------------------------------------------
# 宣言と DDL がずれないこと
#
# 型をルータではなく TableSpec に持たせる以上、その宣言が DDL と
# 食い違えば同じ不具合が別の表で再発する。宣言漏れも検出する。
# ---------------------------------------------------------------------
def test_every_table_declares_a_known_pk_type():
    for key, spec in TABLES.items():
        assert spec.pk_type in DDL_TYPE_FOR, f"{key}: 未知の pk_type {spec.pk_type!r}"


def test_declared_pk_type_matches_the_ddl():
    """新しい表を足したとき、宣言と実際の列型がずれたら落とす。"""
    for key, spec in TABLES.items():
        ddl = TABLE_DDL[spec.table]
        m = re.search(r"^\s*" + re.escape(spec.pk) + r"\s+(\w+)", ddl, re.MULTILINE)
        assert m, f"{key}: DDL に主キー {spec.pk} が見つからない"
        assert m.group(1).upper() == DDL_TYPE_FOR[spec.pk_type], (
            f"{key}: 宣言 {spec.pk_type} だが DDL は {m.group(1)}"
        )


def test_integer_primary_keys_are_the_majority_case():
    """回帰の範囲を固定する（G0-3 で PG 実機が落ちたのは先頭の6表）。

    G4-3 で読み取り専用の表を足した。**編集しない表でも `get_row` は通る**
    （PATCH の 400 を返す前に行の存在を見る）ので、型宣言の対象からは外さない。
    """
    int_tables = {k for k, s in TABLES.items() if s.pk_type == "int"}
    assert int_tables == {
        "tasks",
        "members",
        "teams",
        "schedule_votes",
        "layer_records",
        "progress",
        # 読み取り専用（G4-3）
        "audit_log",
        "seasons",
        "progress_milestones",
        "layer_keta",
        "skill_tags",
        # 進捗の日次履歴（G4-7）
        "progress_snapshots",
        # 資材・消耗品の在庫（G4-8）
        "stock_items",
        "stock_movements",
        # 工具・機材の貸出（G4-9）
        "tools",
        "tool_loans",
        # ヒヤリハット・事故報告（G4-10）
        "incidents",
    }
    text_tables = {k for k, s in TABLES.items() if s.pk_type == "text"}
    assert text_tables == {"schedules", "settings"}


# ---------------------------------------------------------------------
# coerce_row_id（純粋関数）
# ---------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "expected"), [("5", 5), (5, 5), (" 7 ", 7), ("0", 0)])
def test_int_pk_is_normalised_to_int(raw, expected):
    got = coerce_row_id(get_spec("tasks"), raw)
    assert got == expected
    assert isinstance(got, int)


@pytest.mark.parametrize("raw", ["abc", "", "  ", None, "5.5", "5,000", "٥"])
def test_int_pk_rejects_unconvertible_values(raw):
    """変換できない値は 404 になる例外にする（500 にしない）。"""
    with pytest.raises(UnknownRowError):
        coerce_row_id(get_spec("tasks"), raw)


def test_int_pk_rejects_bool():
    """True が 1 行目として通ってしまわないこと。"""
    with pytest.raises(UnknownRowError):
        coerce_row_id(get_spec("tasks"), True)


def test_text_pk_is_kept_as_string():
    spec = get_spec("schedules")
    assert coerce_row_id(spec, "sch_001") == "sch_001"
    # 数字だけの ID でも int にしない（TEXT 列なので str のまま渡す）
    got = coerce_row_id(spec, "12345")
    assert got == "12345"
    assert isinstance(got, str)


def test_text_pk_rejects_none():
    with pytest.raises(UnknownRowError):
        coerce_row_id(get_spec("schedules"), None)


# ---------------------------------------------------------------------
# リポジトリ経路
# ---------------------------------------------------------------------
class _SpyDatabase(Database):
    """SELECT に渡ったバインド値を記録する Database。

    SQLite は型親和性で '5' を 5 として扱うため、「行が引けたか」では
    修正を検証できない。**ドライバへ渡る値そのもの**を見る。
    """

    def __init__(self, path: str):
        super().__init__(path)
        self.seen_params: list[tuple] = []

    async def fetchone(self, sql: str, params: tuple = ()):
        self.seen_params.append(params)
        return await super().fetchone(sql, params)


async def _seed_task(db: Database) -> int:
    cur = await db.execute(
        "INSERT INTO tasks (guild_id, title, status, created_by, created_at)"
        " VALUES (?, ?, 'open', 'tester', '2026-01-01')",
        (G1, "主桁の積層"),
    )
    return cur.lastrowid


def test_get_row_passes_an_int_to_the_driver():
    """PostgreSQL の asyncpg は bigint 引数に str を渡すと DataError になる。"""

    async def _main():
        db = _SpyDatabase(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            db.seen_params.clear()

            row = await repo.get_row(G1, "tasks", str(task_id))

            assert row is not None and row["title"] == "主桁の積層"
            assert db.seen_params, "SELECT が走っていない"
            bound_row_id = db.seen_params[-1][-1]
            assert bound_row_id == task_id
            assert isinstance(bound_row_id, int), (
                f"str のままドライバへ渡っている: {bound_row_id!r}"
            )
        finally:
            await db.close()

    run(_main())


def test_get_row_rejects_unconvertible_row_id():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            with pytest.raises(UnknownRowError):
                await repo.get_row(G1, "tasks", "abc")
        finally:
            await db.close()

    run(_main())


def test_update_row_rejects_before_writing_anything():
    """変換の失敗は書き込みより前に起きる（部分書き込みを作らない）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            with pytest.raises(UnknownRowError):
                await repo.update_row(G1, "tasks", "abc", {"title": "書き換わってはいけない"})

            row = await repo.get_row(G1, "tasks", task_id)
            assert row["title"] == "主桁の積層"
        finally:
            await db.close()

    run(_main())


def test_update_row_accepts_string_row_id():
    """正しい ID なら str で渡されても従来どおり更新できる。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            assert await repo.update_row(G1, "tasks", str(task_id), {"title": "リブの積層"}) is True
            assert (await repo.get_row(G1, "tasks", task_id))["title"] == "リブの積層"
        finally:
            await db.close()

    run(_main())
