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
    RESOLVED_TYPES,
    NameMaps,
    attach_display,
    attach_display_row,
    build_attendance_pivot,
    build_sheets,
    channel_label,
    export_rows,
    fmt_jst,
    fmt_jst_date,
    fmt_option_at,
    has_time_hint,
    team_label,
    team_list_label,
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
# 候補日時: 時刻未指定の候補は日付だけを出す（00:00:00 を出さない）
#
# schedule_options.start_at は "%Y-%m-%d" 入力でも 00:00:00 付きの ISO に
# なるため、時刻の有無はユーザーが打った生文字列（label）で判定する
# ---------------------------------------------------------------------
def test_has_time_hint_detects_hh_mm_in_label():
    assert has_time_hint("2026-09-01 19:00") is True
    assert has_time_hint("9/1 19:00") is True
    assert has_time_hint("2026-09-01T19:00") is True
    assert has_time_hint("2026-09-01 19:00:05") is True
    # 0:00 を明示した場合も「時刻あり」
    assert has_time_hint("2026-09-01 0:00") is True
    # 日付だけの入力
    assert has_time_hint("2026-09-01") is False
    assert has_time_hint("9/1") is False
    assert has_time_hint("2026/09/01") is False
    # 空・None・数字だけはクラッシュせず False
    assert has_time_hint("") is False
    assert has_time_hint(None) is False
    assert has_time_hint("未定") is False


def test_fmt_jst_date_returns_date_only_in_jst():
    assert fmt_jst_date("2026-09-01T00:00:00+09:00") == "2026-09-01"
    # UTC 表記でも JST の日付になる（UTC 15:00 = JST 翌日 0:00）
    assert fmt_jst_date("2026-08-31T15:00:00+00:00") == "2026-09-01"
    assert fmt_jst_date(datetime(2026, 8, 31, 15, 0, 0, tzinfo=UTC)) == "2026-09-01"
    # naive な既存値は保存規約の壁時計として扱う
    assert fmt_jst_date("2026-09-01 00:00") == "2026-09-01"
    # fmt_jst と同じ入力規約: 空は None、解釈できない文字列はそのまま
    assert fmt_jst_date(None) is None
    assert fmt_jst_date("") is None
    assert fmt_jst_date("未定") == "未定"


def test_fmt_option_at_uses_label_to_decide_date_only():
    start = "2026-09-01T00:00:00+09:00"
    # 日付だけの label → 日付だけ（00:00:00 を出さない。セルを空にはしない）
    assert fmt_option_at(start, "2026-09-01") == "2026-09-01"
    assert fmt_option_at(start, "9/1") == "2026-09-01"
    # 時刻付きの label → 従来どおり秒まで
    evening = "2026-09-01T19:00:00+09:00"
    assert fmt_option_at(evening, "2026-09-01 19:00") == "2026-09-01 19:00:00"
    assert fmt_option_at(evening, "9/1 19:00") == "2026-09-01 19:00:00"
    # 0:00 を明示指定した候補は 00:00:00 を出す（日付だけの候補と区別できる）
    assert fmt_option_at(start, "2026-09-01 0:00") == "2026-09-01 00:00:00"


def test_fmt_option_at_keeps_current_behaviour_for_garbage():
    # start_at が解釈できない文字列はそのまま返す（label の有無によらず）
    assert fmt_option_at("未定", "未定") == "未定"
    assert fmt_option_at("未定", "9/1 19:00") == "未定"
    # start_at が空なら None（呼び出し側が label へフォールバックする）
    assert fmt_option_at(None, "9/1") is None
    assert fmt_option_at("", None) is None


def test_build_attendance_pivot_date_only_option_shows_date_only():
    options = [
        {"option_id": "o1", "label": "2026-09-01", "start_at": "2026-09-01T00:00:00+09:00"},
        {"option_id": "o2", "label": "9/1 19:00", "start_at": "2026-09-01T19:00:00+09:00"},
        # 解釈できない start_at はそのまま。start_at が無ければ label を出す
        {"option_id": "o3", "label": "未定", "start_at": "未定"},
        {"option_id": "o4", "label": "9/2", "start_at": None},
    ]
    pivot = build_attendance_pivot(options, [], [], {})
    assert [r["at"] for r in pivot["rows"]] == [
        "2026-09-01",
        "2026-09-01 19:00:00",
        "未定",
        "9/2",
    ]
    # ツールチップ用の生ラベルはそのまま
    assert [r["label"] for r in pivot["rows"]] == ["2026-09-01", "9/1 19:00", "未定", "9/2"]


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
# 班の解決（members の主所属班 / 副所属班。slug → /team-add の班名）
# ---------------------------------------------------------------------
TEAMS = {"kouzou": "構造班", "denki": "電気班"}


def test_team_label_resolves_slug_and_keeps_unknown_slug():
    assert team_label("kouzou", TEAMS) == "構造班"
    # teams に無いキーは slug のまま（勝手に空にしない）
    assert team_label("ghost", TEAMS) == "ghost"
    # 空・None は None（フロントは「—」を出す）
    assert team_label(None, TEAMS) is None
    assert team_label("", TEAMS) is None


def test_team_list_label_joins_names_with_japanese_comma():
    # DB の生値は JSON 配列の文字列
    assert team_list_label('["kouzou","denki"]', TEAMS) == "構造班、電気班"
    assert team_list_label('["kouzou"]', TEAMS) == "構造班"
    # リポジトリ経由で既に list になっている値も受け付ける
    assert team_list_label(["denki", "kouzou"], TEAMS) == "電気班、構造班"
    # 無いキーは slug のまま並べる
    assert team_list_label('["kouzou","ghost"]', TEAMS) == "構造班、ghost"


def test_team_list_label_handles_empty_and_garbage():
    # 空配列は空表示（画面は「—」、CSV は空欄）
    assert team_list_label("[]", TEAMS) == ""
    assert team_list_label([], TEAMS) == ""
    # 空・None は None（従来どおり「—」）
    assert team_list_label(None, TEAMS) is None
    assert team_list_label("", TEAMS) is None
    # JSON として解釈できない生値・配列でない JSON は表示変換しない（生値のまま）
    assert team_list_label("kouzou, denki", TEAMS) is None
    assert team_list_label('"kouzou"', TEAMS) is None
    assert team_list_label('{"a": 1}', TEAMS) is None


def test_team_types_are_resolved_types():
    assert "team" in RESOLVED_TYPES
    assert "team_list" in RESOLVED_TYPES


def _member_row() -> dict:
    return {
        "member_id": 1,
        "user_id": "42",
        "display_name": "山田太郎",
        "primary_team": "kouzou",
        "secondary_teams": '["kouzou","denki"]',
        "is_leader": 0,
        "skills": "[]",
        "notes": None,
        "joined_at": "2026-08-15T21:03:47+09:00",
        "active_flag": 1,
    }


def test_attach_display_resolves_member_teams_and_keeps_raw_values():
    maps = NameMaps(users={"42": "山田太郎"}, teams=TEAMS)
    row = attach_display(TABLES["members"], [_member_row()], maps)[0]
    # 生の値（slug / JSON 文字列）はそのまま（編集入力・PATCH はこちらを使う）
    assert row["primary_team"] == "kouzou"
    assert row["secondary_teams"] == '["kouzou","denki"]'
    # 表示は班名
    assert row["_display"]["primary_team"] == "構造班"
    assert row["_display"]["secondary_teams"] == "構造班、電気班"


def test_attach_display_member_without_teams():
    maps = NameMaps(teams=TEAMS)
    row = {**_member_row(), "primary_team": None, "secondary_teams": "[]"}
    display = attach_display(TABLES["members"], [row], maps)[0]["_display"]
    # 主所属なしは表示変換なし（フロントが「—」）。副所属の空配列は空表示
    assert "primary_team" not in display
    assert display["secondary_teams"] == ""


def test_export_rows_replaces_team_slugs_with_names():
    maps = NameMaps(users={"42": "山田太郎"}, teams=TEAMS)
    rows = export_rows(TABLES["members"], [_member_row()], maps)
    assert rows[0]["primary_team"] == "構造班"
    assert rows[0]["secondary_teams"] == "構造班、電気班"
    # 変換対象外の列は生のまま
    assert rows[0]["skills"] == "[]"


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
