"""number 列を DDL の型（INTEGER / REAL）どおりに正規化することのテスト（G1-9）。

G1-0（`row_id` の型変換）とまったく同じ失敗の**書き込み側**。

`_coerce()` の number 分岐は `int()` → 失敗したら `float()` の順に試すため、
INTEGER 列にも float が入る。さらに `isinstance(value, (int, float))` の
早期 return により、JSON ボディの `{"priority": 2.7}` は変換すら経ずに素通りする。

asyncpg は int8 の引数に float を渡しても DataError を投げるので、
**本番（PostgreSQL）だけが 500 になる**。SQLite は動的型付けなので保存できてしまい、
そのあと bot 側の読み取りが壊れる（gotcha `progress-stops-after-dashboard-edit` と同型）。

**number の既定を int にはできない。** 編集できる number 列は整数と実数が混在する:
INTEGER が `tasks.priority` / `layer_records.minutes`、
REAL が `progress.sort_order` / `target_weight_g` / `actual_weight_g`。
どちらを既定にしても他方が壊れるので、列ごとに宣言させる。
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from repositories.table_repository import (
    TABLES,
    Column,
    InvalidValueError,
    TableRepository,
    _coerce,
    get_spec,
)
from utils.db import TABLE_DDL, TABLE_DDL_PG, Database

G1 = 100000000000000001

# 列の数値型 → DDL の宣言型
DDL_TYPE_FOR = {"int": "INTEGER", "real": "REAL"}


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _column(table_key: str, name: str) -> Column:
    return next(c for c in get_spec(table_key).columns if c.name == name)


PRIORITY = _column("tasks", "priority")  # INTEGER
MINUTES = _column("layer_records", "minutes")  # INTEGER
SORT_ORDER = _column("progress", "sort_order")  # REAL
TARGET_WEIGHT = _column("progress", "target_weight_g")  # REAL


# ---------------------------------------------------------------------
# 宣言と DDL がずれないこと（G1-0 の pk_type と同じ形）
# ---------------------------------------------------------------------
def test_every_number_column_declares_a_number_type():
    for key, spec in TABLES.items():
        for column in spec.columns:
            if column.type != "number":
                continue
            assert column.number_type in DDL_TYPE_FOR, (
                f"{key}.{column.name}: 未宣言または未知の number_type {column.number_type!r}"
            )


def test_declared_number_type_matches_the_ddl():
    for key, spec in TABLES.items():
        ddl = TABLE_DDL[spec.table]
        for column in spec.columns:
            if column.type != "number":
                continue
            m = re.search(r"^\s*" + re.escape(column.name) + r"\s+(\w+)", ddl, re.MULTILINE)
            assert m, f"{key}.{column.name}: DDL に列が見つからない"
            assert m.group(1).upper() == DDL_TYPE_FOR[column.number_type], (
                f"{key}.{column.name}: 宣言 {column.number_type} だが DDL は {m.group(1)}"
            )


def test_editable_number_columns_are_a_mix_of_int_and_real():
    """既定値を1つに決められない理由そのものを固定する。"""
    editable = {
        (key, c.name): c.number_type
        for key, spec in TABLES.items()
        for c in spec.columns
        if c.type == "number" and c.editable
    }
    assert editable == {
        ("tasks", "priority"): "int",
        ("layer_records", "minutes"): "int",
        ("progress", "sort_order"): "real",
        ("progress", "target_weight_g"): "real",
        ("progress", "actual_weight_g"): "real",
    }


# ---------------------------------------------------------------------
# 宣言し忘れを構造で防ぐ
# ---------------------------------------------------------------------
def test_number_column_cannot_be_declared_without_a_number_type():
    """既定値を置かず、決めないと Column を作れないようにする。"""
    with pytest.raises(ValueError):
        Column(name="x", label="X", type="number")


def test_number_type_on_a_non_number_column_is_rejected():
    """宣言が効かない場所に書かれていたら気付けるようにする。"""
    with pytest.raises(ValueError):
        Column(name="x", label="X", type="text", number_type="int")


# ---------------------------------------------------------------------
# INTEGER 列
# ---------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "expected"), [("3", 3), (3, 3), (" 4 ", 4), ("2.0", 2), (2.0, 2)])
def test_integer_column_normalises_to_int(raw, expected):
    got = _coerce(PRIORITY, raw)
    assert got == expected
    assert isinstance(got, int)


@pytest.mark.parametrize("raw", ["2.7", 2.7, "-0.5", 1.0000001])
def test_integer_column_rejects_fractions(raw):
    """丸めない。400 にして利用者に入れ直してもらう。"""
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(PRIORITY, raw)
    assert "整数" in str(excinfo.value)


def test_integer_column_rejects_float_from_json_body():
    """`{"priority": 2.7}` は文字列を経ないので、素通りの穴になっていた。"""
    with pytest.raises(InvalidValueError):
        _coerce(PRIORITY, 2.7)
    with pytest.raises(InvalidValueError):
        _coerce(MINUTES, 30.5)


def test_integer_column_still_rejects_text():
    with pytest.raises(InvalidValueError):
        _coerce(PRIORITY, "いちばん上")


# ---------------------------------------------------------------------
# REAL 列
# ---------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "expected"), [("2.5", 2.5), (2.5, 2.5), ("3", 3.0), (7, 7.0)])
def test_real_column_normalises_to_float(raw, expected):
    got = _coerce(SORT_ORDER, raw)
    assert got == expected
    assert isinstance(got, float), f"REAL 列に {type(got).__name__} が入っている"


def test_real_column_accepts_fractions():
    """重量は小数を持つ（ADR 0021: 未計測を 0 に丸めない、とは別の話）。"""
    assert _coerce(TARGET_WEIGHT, "1234.5") == 1234.5


def test_real_column_rejects_text():
    with pytest.raises(InvalidValueError):
        _coerce(SORT_ORDER, "おもい")


# ---------------------------------------------------------------------
# 既存の契約は変えない
# ---------------------------------------------------------------------
def test_empty_stays_none_for_both_kinds():
    assert _coerce(PRIORITY, "") is None
    assert _coerce(SORT_ORDER, "") is None
    assert _coerce(PRIORITY, None) is None


def test_bool_is_still_accepted_as_0_or_1():
    assert _coerce(PRIORITY, True) == 1
    assert _coerce(PRIORITY, False) == 0


# ---------------------------------------------------------------------
# リポジトリ経路
# ---------------------------------------------------------------------
class _SpyDatabase(Database):
    """UPDATE に渡ったバインド値を記録する Database。

    SQLite は REAL 宣言の列にも int を、INTEGER 宣言の列にも float を
    保存できてしまうため、「保存できたか」では検証にならない。
    """

    def __init__(self, path: str):
        super().__init__(path)
        self.seen_params: list[tuple] = []

    async def execute(self, sql: str, params: tuple = ()):
        self.seen_params.append(params)
        return await super().execute(sql, params)


async def _seed_task(db: Database) -> int:
    cur = await db.execute(
        "INSERT INTO tasks (guild_id, title, status, created_by, created_at, priority)"
        " VALUES (?, ?, 'open', 'tester', '2026-01-01', 2)",
        (G1, "主桁の積層"),
    )
    return cur.lastrowid


def test_update_row_passes_an_int_to_the_driver_for_integer_columns():
    """asyncpg は int8 引数に float を渡すと DataError になる。"""

    async def _main():
        db = _SpyDatabase(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            db.seen_params.clear()

            assert await repo.update_row(G1, "tasks", task_id, {"priority": "3"}) is True

            bound = db.seen_params[-1][0]
            assert bound == 3
            assert isinstance(bound, int), f"float のままドライバへ渡っている: {bound!r}"
        finally:
            await db.close()

    run(_main())


def test_update_row_rejects_a_fraction_for_an_integer_column():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            with pytest.raises(InvalidValueError):
                await repo.update_row(G1, "tasks", task_id, {"priority": 2.7})

            # 元の値のまま（部分書き込みが起きていない）
            assert (await repo.get_row(G1, "tasks", task_id))["priority"] == 2
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 範囲チェック（G1-9 の追撃）
#
# G1-0 / G1-9 とまったく同じ形。変換は通るが **DB の型に収まらない**ので、
# 本番（PostgreSQL）だけが 500 になる。入口は3つある:
#
#   "1e20"    → int() 失敗 → float() = 1e20 → is_integer() True → int() = 10^20
#   "9" * 30  → int() が**直接成功**する（Python は任意精度整数）
#   10**30    → JSON ボディの数値。文字列を経ないので変換すら通らない
#
# priority / minutes に CHECK 制約は無いので DB 側では止まらない。
# 丸めない・切り詰めない。範囲外は 400 にして入れ直してもらう。
# ---------------------------------------------------------------------
# 編集できる INTEGER 列は PostgreSQL では int4（BIGINT は主キーだけ）。
# int8 で判定すると 3000000000 が素通りして本番だけ落ちる。
INT32_MAX = 2**31 - 1
INT32_MIN = -(2**31)


def test_editable_integer_columns_are_int4_in_postgres():
    """範囲判定の前提（int4）を DDL と突き合わせて固定する。

    編集できる INTEGER 列を BIGINT へ広げたら、この検査が落ちて
    _coerce_number の範囲判定も見直すことになる（片方だけ直す事故を防ぐ）。
    """
    for key, spec in TABLES.items():
        for column in spec.columns:
            if column.type != "number" or column.number_type != "int" or not column.editable:
                continue
            m = re.search(
                r"^\s*" + re.escape(column.name) + r"\s+(\w+)",
                TABLE_DDL_PG[spec.table],
                re.MULTILINE,
            )
            assert m, f"{key}.{column.name}: PG の DDL に列が見つからない"
            assert m.group(1).upper() == "INTEGER", (
                f"{key}.{column.name}: PG では {m.group(1)}。"
                "int4 前提の範囲判定（_INT32_MAX）を見直すこと"
            )


@pytest.mark.parametrize(
    "raw",
    [
        "1e20",  # 入口1: float 経由で整数になる
        "9" * 30,  # 入口2: int() が直接成功する
        10**30,  # 入口3: JSON ボディの int
        1e20,  # JSON ボディの float
        str(INT32_MAX + 1),
        str(INT32_MIN - 1),
        "-1e20",
        3000000000,  # int8 なら通るが int4 では溢れる（この差が本番の 500）
    ],
)
def test_integer_column_rejects_values_outside_bigint(raw):
    """BIGINT に収まらない値は 400。asyncpg は int8 の範囲外で DataError を投げる。"""
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(PRIORITY, raw)
    assert "範囲を超えています" in str(excinfo.value)


@pytest.mark.parametrize("raw", [INT32_MAX, INT32_MIN, str(INT32_MAX), str(INT32_MIN)])
def test_integer_column_accepts_the_bigint_boundary(raw):
    """境界そのものは通す（1つ内側で切らない）。"""
    got = _coerce(PRIORITY, raw)
    assert got == int(raw)
    assert isinstance(got, int)


def test_integer_range_check_does_not_break_the_equivalent_conversion():
    """G1-9 の「2.0 は 2 として受ける」を壊していないこと。"""
    assert _coerce(PRIORITY, "2.0") == 2
    assert _coerce(PRIORITY, 2.0) == 2
    # 2**30 は int4 に収まり float64 でも厳密なので、往復しても値が変わらない
    assert _coerce(MINUTES, float(2**30)) == 2**30


def test_float_just_outside_the_boundary_is_rejected_not_truncated():
    """範囲外の float は切り詰めずに 400。

    2147483648.0 は float64 で厳密に表せて is_integer() も True なので、
    G1-9 の等価変換をそのまま通ると int4 を 1 だけ超えた値が
    ドライバへ渡る。ここで INT32_MAX に丸めると利用者が入れた値と
    違う値が保存されるので、拒否して入れ直してもらう。
    """
    just_outside = float(INT32_MAX + 1)
    assert just_outside.is_integer(), "前提: 等価変換は通ってしまう形"
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(PRIORITY, just_outside)
    assert "範囲を超えています" in str(excinfo.value)


def test_integer_column_rejects_infinity():
    """inf は「小数」ではなく「範囲外」として説明する。"""
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(PRIORITY, "inf")
    assert "範囲を超えています" in str(excinfo.value)


# ---------------------------------------------------------------------
# REAL 列の inf / NaN
#
# PostgreSQL の float8 は Infinity / NaN を**格納できてしまう**ので、
# int 側と違って 500 にすらならず静かに入る。target_weight_g /
# actual_weight_g に NaN が入ると services/progress_tree.py の
# _resolve_weight() が子の合計を取る際に伝播し、重量ツリー全体が壊れる。
#
# ADR 0021 は「未計測を 0.0 に丸めず None のまま扱う」と決めている。
# 未計測は None で表す、と決めた以上 NaN という第3の「値でない値」は通さない。
# ---------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["1e400", "-1e400", "inf", "-inf", "Infinity", float("inf")])
def test_real_column_rejects_infinity(raw):
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(TARGET_WEIGHT, raw)
    assert "範囲を超えています" in str(excinfo.value)


@pytest.mark.parametrize("raw", ["nan", "NaN", float("nan")])
def test_real_column_rejects_nan(raw):
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(TARGET_WEIGHT, raw)
    assert "範囲を超えています" in str(excinfo.value)


def test_real_column_rejects_an_int_too_large_for_float8():
    """任意精度の int は float() で OverflowError になる（= 500 の穴）。"""
    with pytest.raises(InvalidValueError) as excinfo:
        _coerce(SORT_ORDER, "9" * 400)
    assert "範囲を超えています" in str(excinfo.value)
    with pytest.raises(InvalidValueError):
        _coerce(SORT_ORDER, 10**400)


def test_real_column_still_accepts_large_finite_values():
    """float8 に収まる大きさは通す（重量に上限を新設しない）。"""
    assert _coerce(TARGET_WEIGHT, "1e308") == 1e308
    assert _coerce(TARGET_WEIGHT, "1234.5") == 1234.5


def test_nan_never_reaches_the_weight_tree():
    """NaN を弾く理由を固定する。

    _resolve_weight() は子の合計を取るので、NaN が1つ混ざると
    その祖先すべてが NaN になる。エラーにならないぶん発見が遅れる。
    """
    from services.progress_tree import _resolve_weight

    # 弾かなかった場合に何が起きるか（この前提が崩れたら弾く理由も変わる）
    poisoned = _resolve_weight(None, [1.0, float("nan"), 2.0])
    assert math.isnan(poisoned), "NaN は合計へ伝播する（だから入口で弾く）"

    # 入口で止まるので、ツリーには到達しない
    for column in (TARGET_WEIGHT, _column("progress", "actual_weight_g")):
        with pytest.raises(InvalidValueError):
            _coerce(column, "nan")


def test_update_row_rejects_out_of_range_without_partial_write():
    """範囲外は UPDATE へ到達させない（部分書き込みが起きない）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_task(db)
            repo = TableRepository(db)
            with pytest.raises(InvalidValueError):
                await repo.update_row(G1, "tasks", task_id, {"priority": "9" * 30})

            assert (await repo.get_row(G1, "tasks", task_id))["priority"] == 2
        finally:
            await db.close()

    run(_main())
