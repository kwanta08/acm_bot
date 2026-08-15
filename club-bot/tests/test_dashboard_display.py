"""表示整形ヘルパー（dashboard/display.py）のテスト。

- JST フォーマッタ: UTC 入力 → JST 秒単位出力（日付跨ぎを含む）。分で丸めない
- naive な既存値は保存規約（utils.parser.TZ）の壁時計として解釈する
- 人物・チャンネル・候補の解決と、解決できないときの ID 付きフォールバック
- シートタブ項目の降順ソート（開催日時が無い桁タブは入力順を保つ）

純関数のみを対象とするため FastAPI（dashboard/requirements.txt）は不要。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.display import (
    NameMaps,
    attach_display,
    attach_display_row,
    build_sheets,
    channel_label,
    export_rows,
    fmt_jst,
    user_label,
)
from repositories.table_repository import TABLES

UTC = timezone.utc


# ---------------------------------------------------------------------
# fmt_jst（UTC → JST・秒単位）
# ---------------------------------------------------------------------
def test_fmt_jst_converts_aware_utc_to_jst_with_seconds():
    dt = datetime(2026, 8, 15, 12, 3, 47, tzinfo=UTC)
    assert fmt_jst(dt) == "2026-08-15 21:03:47"


def test_fmt_jst_crosses_date_boundary():
    """UTC 15:00 以降は JST では翌日になる。"""
    dt = datetime(2026, 8, 15, 15, 0, 0, tzinfo=UTC)
    assert fmt_jst(dt) == "2026-08-16 00:00:00"
    late = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert fmt_jst(late) == "2027-01-01 08:59:59"


def test_fmt_jst_does_not_round_to_minutes():
    dt = datetime(2026, 8, 15, 12, 3, 47, tzinfo=UTC)
    assert fmt_jst(dt).endswith(":47")


def test_fmt_jst_parses_aware_iso_string():
    assert fmt_jst("2026-08-15T21:03:47+09:00") == "2026-08-15 21:03:47"
    # UTC 表記の文字列も JST へ変換される
    assert fmt_jst("2026-08-15T12:03:47+00:00") == "2026-08-15 21:03:47"


def test_fmt_jst_treats_naive_text_as_local_convention():
    """既存 DB の naive 文字列（分精度）は保存規約の壁時計として扱う。

    既定の TZ は Asia/Tokyo なので、そのまま秒 00 を補って表示される。
    """
    assert fmt_jst("2026-08-11 10:00") == "2026-08-11 10:00:00"
    assert fmt_jst("2026-08-11 10:00:05") == "2026-08-11 10:00:05"


def test_fmt_jst_handles_empty_and_garbage():
    assert fmt_jst(None) is None
    assert fmt_jst("") is None
    # 解釈できない文字列は例外にせずそのまま返す
    assert fmt_jst("未定") == "未定"


# ---------------------------------------------------------------------
# 人物・チャンネルの解決
# ---------------------------------------------------------------------
def test_user_label_resolves_and_falls_back_with_id():
    users = {"42": "山田太郎"}
    assert user_label("42", users) == "山田太郎"
    # 解決不能（退会済み等）は ID 付きフォールバック
    assert user_label("999", users) == "不明なユーザー (999)"
    assert user_label(None, users) is None
    assert user_label("", users) is None


def test_channel_label_prefixes_hash_and_falls_back():
    channels = {"555": "general"}
    assert channel_label("555", channels) == "#general"
    assert channel_label("777", channels) == "不明なチャンネル (777)"
    assert channel_label(None, channels) is None


# ---------------------------------------------------------------------
# 行への _display 付与と CSV 用変換
# ---------------------------------------------------------------------
def _vote_row() -> dict:
    return {
        "vote_id": 1,
        "option_id": "opt-1",
        "user_id": "42",
        "status": "ok",
        "updated_at": "2026-08-15T21:03:47+09:00",
    }


def test_attach_display_adds_names_and_keeps_raw_values():
    maps = NameMaps(users={"42": "山田太郎"}, options={"opt-1": "9/1(月) 19:00"})
    rows = attach_display(TABLES["schedule_votes"], [_vote_row()], maps)
    row = rows[0]
    # 生の値はそのまま（DB は ID 保持。編集入力にも使う）
    assert row["user_id"] == "42"
    assert row["updated_at"] == "2026-08-15T21:03:47+09:00"
    # 表示は解決済み
    assert row["_display"]["user_id"] == "山田太郎"
    assert row["_display"]["option_id"] == "9/1(月) 19:00"
    assert row["_display"]["updated_at"] == "2026-08-15 21:03:47"


def test_attach_display_row_single():
    maps = NameMaps(users={"42": "山田太郎"})
    row = attach_display_row(TABLES["schedule_votes"], _vote_row(), maps)
    assert row["_display"]["user_id"] == "山田太郎"


def test_export_rows_replaces_ids_with_names():
    maps = NameMaps(users={"42": "山田太郎"}, options={"opt-1": "9/1(月) 19:00"})
    rows = export_rows(TABLES["schedule_votes"], [_vote_row()], maps)
    assert rows[0]["user_id"] == "山田太郎"
    assert rows[0]["option_id"] == "9/1(月) 19:00"
    assert rows[0]["updated_at"] == "2026-08-15 21:03:47"
    # 変換対象外の列は生のまま
    assert rows[0]["status"] == "ok"


# ---------------------------------------------------------------------
# シートタブ項目
# ---------------------------------------------------------------------
def test_build_sheets_sorts_by_datetime_desc():
    raw = [
        {"id": "old", "label": "旧い予定", "at": "2026-08-01T19:00:00+09:00"},
        {"id": "new", "label": "新しい予定", "at": "2026-09-01T19:00:00+09:00"},
        {"id": "undated", "label": "日時なし", "at": None},
    ]
    items = build_sheets(raw)
    assert [s["id"] for s in items] == ["new", "old", "undated"]
    assert items[0]["at"] == "2026-09-01 19:00:00"
    assert items[2]["at"] is None


def test_build_sheets_keeps_input_order_when_no_dates():
    """桁タブ（日時なし）はリポジトリの並び順（桁名順）を保つ。"""
    raw = [{"id": k, "label": k, "at": None} for k in ("主桁", "尾桁", "翼桁")]
    assert [s["id"] for s in build_sheets(raw)] == ["主桁", "尾桁", "翼桁"]
