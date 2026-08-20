"""ダッシュボード編集の値バリデーションと CSV 出力の安全性テスト。

SQLite は動的型付けのため、REAL 列にも文字列がそのまま保存できてしまう。
進捗率に数値でない値が入ると bot 側の float() 変換が落ち、そのサーバーの
/progress view と定期同期がまとめて止まる（Web からの書き込みが bot を
壊す経路）。書き込み口で列の型へ正規化することを検証する。

CSV 出力は、タスク名・メモなど Discord 利用者が自由に入力できる値を
そのまま出すと表計算ソフトで数式として実行される（CSV インジェクション）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")

from repositories.table_repository import (
    InvalidValueError,
    _coerce,
    csv_safe,
    get_spec,
)


def _column(table_key: str, name: str):
    return next(c for c in get_spec(table_key).columns if c.name == name)


PROGRESS_COL = _column("progress", "manual_progress")
SORT_ORDER_COL = _column("progress", "sort_order")
IS_LEADER_COL = _column("members", "is_leader")
NAME_COL = _column("progress", "name")


# ---------------------------------------------------------------------
# progress 列: bot 側の /progress edit と同じ解釈にそろえる
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.5, 0.5),
        ("0.5", 0.5),
        ("50%", 0.5),
        ("50", 0.5),  # 1 より大きい数値は % とみなす
        ("150%", 1.0),  # クランプ
        (-1, 0.0),  # クランプ
        ("", None),  # 空欄は未入力
    ],
)
def test_progress_column_is_normalised(raw, expected):
    assert _coerce(PROGRESS_COL, raw) == expected


def test_progress_column_rejects_non_numeric():
    """bot 側の float() を落とす値は書き込ませない。"""
    with pytest.raises(InvalidValueError):
        _coerce(PROGRESS_COL, "だいたい終わった")


# ---------------------------------------------------------------------
# number / bool 列
# ---------------------------------------------------------------------
def test_number_column_accepts_numeric_strings():
    assert _coerce(SORT_ORDER_COL, "3") == 3
    assert _coerce(SORT_ORDER_COL, "2.5") == 2.5
    assert _coerce(SORT_ORDER_COL, 7) == 7
    assert _coerce(SORT_ORDER_COL, "") is None


def test_number_column_rejects_text():
    with pytest.raises(InvalidValueError):
        _coerce(SORT_ORDER_COL, "いちばん上")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, 1),
        (False, 0),
        (1, 1),
        (0, 0),
        ("1", 1),
        ("0", 0),
        ("true", 1),
        ("FALSE", 0),
        ("yes", 1),
        ("no", 0),
        ("", 0),
    ],
)
def test_bool_column_is_normalised(raw, expected):
    assert _coerce(IS_LEADER_COL, raw) == expected


def test_bool_column_rejects_arbitrary_text():
    with pytest.raises(InvalidValueError):
        _coerce(IS_LEADER_COL, "たぶん")


# ---------------------------------------------------------------------
# text 列は素通し（None も保てる）
# ---------------------------------------------------------------------
def test_text_column_passes_through():
    assert _coerce(NAME_COL, "主桁") == "主桁"
    assert _coerce(NAME_COL, None) is None


# ---------------------------------------------------------------------
# CSV インジェクション
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        '=HYPERLINK("http://evil.example","領収書")',
        "+1+1",
        "-1+1",
        "@SUM(A1:A9)",
        "\tX",
        "\rX",
    ],
)
def test_formula_like_cells_are_escaped(raw):
    escaped = csv_safe(raw)
    assert escaped.startswith("'")
    assert escaped[1:] == raw


@pytest.mark.parametrize("raw", ["主桁", "2026-08-11", "50%", "", "a=b"])
def test_ordinary_cells_are_unchanged(raw):
    assert csv_safe(raw) == raw


def test_non_string_cells_are_stringified():
    """csv_safe は必ず str を返す（csv.writer へそのまま渡すため）。

    None は空文字にする。「未設定」を "None" と書き出さないため。
    """
    assert csv_safe(42) == "42"
    assert csv_safe(0.5) == "0.5"
    assert csv_safe(None) == ""
