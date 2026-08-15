"""ダッシュボードの表示整形（共通ヘルパー。ここ以外に散らさない）。

方針:
- **DB は ID・ISO 文字列のまま保持し、表示はこの層で作る**。
  行データそのものは書き換えず、`_display`（列名 → 表示文字列）を添える
- 日時は timezone-aware な UTC へ正規化してから、表示直前に
  Asia/Tokyo (JST) へ変換し**秒まで**出す（分で丸めない）
- 既存 DB には naive な日時文字列（`2026-08-11 10:00` 等）が混在する。
  これは bot の保存規約（utils/parser.py: naive = ローカル TZ）どおり
  `utils.parser.TZ` の壁時計として解釈する。naive を新たに増やさない
- 本モジュールは**純関数のみ**（DB を触らない）。名前の辞書は
  ルーター側が guild_id スコープのリポジトリで取得して渡す
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from repositories.table_repository import TableSpec
from utils.parser import TZ

# 表示タイムゾーンは Asia/Tokyo 固定（要件）。
# naive 値の解釈には保存規約どおり utils.parser.TZ を使う（既定は同じ
# Asia/Tokyo。TZ 設定を変えた運用でも「保存時の壁時計 → JST 表示」が保たれる）
JST = ZoneInfo("Asia/Tokyo")

# 秒まで表示する（分で丸めない）
DATETIME_DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"

# 解決できなかったときのフォールバック（ID を添えて出す。要件の最終段）
UNKNOWN_USER_FORMAT = "不明なユーザー ({})"
UNKNOWN_CHANNEL_FORMAT = "不明なチャンネル ({})"


def as_utc(value: Any) -> datetime | None:
    """日時らしい値を timezone-aware な UTC datetime へ正規化する。

    - datetime: naive なら保存規約（TZ）を付与してから UTC へ
    - str: ISO 8601 として解釈（`2026-08-11 10:00` の空白区切りも可）。
      解釈できなければ None
    - それ以外・空値: None
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def fmt_jst(value: Any) -> str | None:
    """日時を JST（Asia/Tokyo）の秒付き文字列にする。

    - None・空文字は None（フロントは「—」を出す）
    - 解釈できない文字列は**そのまま返す**（表示でクラッシュさせない）
    """
    if value is None or value == "":
        return None
    dt = as_utc(value)
    if dt is None:
        return str(value)
    return dt.astimezone(JST).strftime(DATETIME_DISPLAY_FORMAT)


@dataclass(frozen=True)
class NameMaps:
    """ID → 表示名の辞書の束（ルーターが guild_id スコープで取得する）。

    users は「名前キャッシュ（Discord のギルド表示名）を members 台帳で
    補完したもの」を渡す。優先順位の合成は呼び出し側で行う。
    """

    users: Mapping[str, str] = field(default_factory=dict)
    channels: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, str] = field(default_factory=dict)


def user_label(value: Any, users: Mapping[str, str]) -> str | None:
    """ユーザー ID を表示名へ。解決できなければ ID 付きフォールバック。"""
    if value is None or value == "":
        return None
    name = users.get(str(value))
    return name if name else UNKNOWN_USER_FORMAT.format(value)


def channel_label(value: Any, channels: Mapping[str, str]) -> str | None:
    """チャンネル ID を `#名前` へ。削除済み等は ID 付きフォールバック。"""
    if value is None or value == "":
        return None
    name = channels.get(str(value))
    return f"#{name}" if name else UNKNOWN_CHANNEL_FORMAT.format(value)


def option_label(value: Any, options: Mapping[str, str]) -> str | None:
    """日程調整の候補 ID を候補ラベル（例: `9/1(月) 19:00`）へ。"""
    if value is None or value == "":
        return None
    return options.get(str(value)) or str(value)


def display_cell(column_type: str, value: Any, maps: NameMaps) -> str | None:
    """列型に応じた表示文字列を返す。表示変換が無い型は None。"""
    if column_type == "datetime":
        return fmt_jst(value)
    if column_type == "user":
        return user_label(value, maps.users)
    if column_type == "channel":
        return channel_label(value, maps.channels)
    if column_type == "option":
        return option_label(value, maps.options)
    return None


# 表示変換の対象になる列型（この型の列があるときだけ名前辞書を引けばよい）
RESOLVED_TYPES = ("user", "channel", "option", "datetime")


def attach_display(
    spec: TableSpec, rows: list[dict[str, Any]], maps: NameMaps
) -> list[dict[str, Any]]:
    """各行に `_display`（列名 → 表示文字列）を添えた新しい行を返す。

    生の値（ID・ISO 文字列）は残す（編集入力とデータの正本は ID のまま）。
    """
    resolved = [(c.name, c.type) for c in spec.columns if c.type in RESOLVED_TYPES]
    if not resolved:
        return list(rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        display: dict[str, str] = {}
        for name, type_ in resolved:
            text = display_cell(type_, row.get(name), maps)
            if text is not None:
                display[name] = text
        out.append({**row, "_display": display})
    return out


def attach_display_row(spec: TableSpec, row: dict[str, Any], maps: NameMaps) -> dict[str, Any]:
    """1行版（編集 API の応答用）。"""
    return attach_display(spec, [row], maps)[0]


def export_rows(
    spec: TableSpec, rows: list[dict[str, Any]], maps: NameMaps
) -> list[dict[str, Any]]:
    """CSV 出力用に、表示変換のある列を表示文字列へ置き換えた行を返す。

    ダッシュボードの CSV は人が Excel で読むためのもの（画面表示と同じく
    ID の生値・生 ISO を出さない）。データの完全なバックアップは
    `/data export`（bot 側）が生値のまま担う。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        converted = dict(row)
        for column in spec.columns:
            if column.type in RESOLVED_TYPES:
                text = display_cell(column.type, row.get(column.name), maps)
                if text is not None:
                    converted[column.name] = text
        out.append(converted)
    return out


def build_sheets(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """シートタブの項目リストを整形する。

    入力: `{"id", "label", "at"}`（at は ISO/naive/None）。
    出力: at を JST 表示へ変換し、**at の降順**（新しい予定が先頭）に並べる。
    at がすべて None（桁タブ）の場合は入力順を保つ（sorted は安定）。
    """
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items = sorted(raw, key=lambda s: as_utc(s.get("at")) or epoch, reverse=True)
    return [{"id": s["id"], "label": s["label"], "at": fmt_jst(s.get("at"))} for s in items]


# ---------------------------------------------------------------------
# 出欠回答のピボット表（Google スプレッドシート風。1行 = 候補日時）
#
# 列は固定: 候補日時 / 参加 / 不参加 / 未定 / 未回答。
# ok/maybe/ng の語彙は bot 側（cogs/schedule.py の STATUS_LABELS）と
# 対応させる。"none"（未回答）はダッシュボード側で計算する区分。
# ---------------------------------------------------------------------
ATTENDANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("at", "候補日時"),
    ("ok", "参加"),
    ("ng", "不参加"),
    ("maybe", "未定"),
    ("none", "未回答"),
)

# 投票として有効なステータス（これ以外の値は表示しない）
_VOTE_STATUSES = ("ok", "ng", "maybe")


def build_attendance_pivot(
    options: list[dict[str, Any]],
    votes: list[dict[str, Any]],
    target_ids: list[str],
    users: Mapping[str, str],
) -> dict[str, Any]:
    """出欠回答のピボット表を組み立てる（純関数）。

    - 1行 = 候補日時1つ（options の並び順 = 開始日時の昇順を保つ）
    - 各セルはその区分に該当するメンバーの表示名リスト（名前順）
    - 未回答 = 回答対象者（target_ids）のうち、**この予定のどの候補にも
      投票していない**メンバー。bot の催促（notify_unanswered）と同じ
      「予定単位」の定義なので、全行で同じ顔ぶれになる
    """
    voted = {str(v["user_id"]) for v in votes}
    unanswered = sorted(user_label(uid, users) for uid in target_ids if str(uid) not in voted)

    by_option: dict[str, dict[str, list[str]]] = {}
    for vote in votes:
        status = str(vote.get("status"))
        if status not in _VOTE_STATUSES:
            continue
        groups = by_option.setdefault(str(vote["option_id"]), {})
        groups.setdefault(status, []).append(user_label(vote["user_id"], users))

    rows: list[dict[str, Any]] = []
    for option in options:
        groups = by_option.get(str(option["option_id"]), {})
        rows.append(
            {
                # 候補日時は JST 秒単位。解釈できない値はそのまま出す
                "at": fmt_jst(option.get("start_at")) or str(option.get("label") or ""),
                "label": str(option.get("label") or ""),
                "groups": {
                    "ok": sorted(groups.get("ok", [])),
                    "ng": sorted(groups.get("ng", [])),
                    "maybe": sorted(groups.get("maybe", [])),
                    "none": list(unanswered),
                },
            }
        )
    return {
        "columns": [{"key": key, "label": label} for key, label in ATTENDANCE_COLUMNS],
        "rows": rows,
    }
