"""
日時パースユーティリティ（仕様 14.2 INVALID_DATETIME）。

許容フォーマット例:
  2026-07-03 19:00
  2026/07/03 19:00
  2026-07-03T19:00
  07-03 19:00      （年は当年補完）
  07/03 19:00
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import config

TZ = ZoneInfo(config.tz)

DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
]
SHORT_FORMATS = [
    "%m-%d %H:%M",
    "%m/%d %H:%M",
]

DATETIME_HINT = "日時は `2026-07-03 19:00` の形式で指定してください（`YYYY-MM-DD HH:MM`）。"


class InvalidDatetimeError(ValueError):
    """日時形式が不正な場合に送出する。"""

    def __init__(self) -> None:
        super().__init__(DATETIME_HINT)


def parse_datetime(text: str) -> datetime:
    """文字列をタイムゾーン付き datetime に変換する。失敗時は InvalidDatetimeError。"""
    text = (text or "").strip()
    if not text:
        raise InvalidDatetimeError()

    for fmt in DATETIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=TZ)
        except ValueError:
            continue

    now = datetime.now(TZ)
    for fmt in SHORT_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            dt = dt.replace(year=now.year, tzinfo=TZ)
            # 既に過去なら翌年扱い
            if dt < now:
                dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            continue

    raise InvalidDatetimeError()


def now() -> datetime:
    return datetime.now(TZ)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def from_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def fmt_jp(dt: datetime) -> str:
    """日本語表示用フォーマット。"""
    return dt.astimezone(TZ).strftime("%Y/%m/%d %H:%M")


def fmt_sheet(dt: datetime) -> str:
    """Sheets 書き込み用フォーマット（仕様 11.8.4）。"""
    return dt.astimezone(TZ).strftime("%Y/%m/%d %H:%M:%S")
