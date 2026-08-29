"""週次ダイジェスト（G4-5）の期間と集計の純関数。

`/report weekly` と、月曜朝の自動投稿が**同じ数字**を出すために、
「先週」の定義と数え方をここ1箇所に置く。

**ADR 0023 は覆さない。** マイルストーン警告（遅延があるときだけ送る）と
このダイジェスト（週次の実績報告）は別物として共存させる。
ダイジェスト側に「遅延はありません」の定型文を入れないこと——
それを入れると 0023 が却下した「毎週届く定型文」そのものになる。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta
from typing import Any

from utils.parser import TZ, from_iso

#: ダイジェストを送る曜日の既定（0 = 月曜）
DEFAULT_DIGEST_WEEKDAY = 0


def last_week_range(current: datetime) -> tuple[datetime, datetime]:
    """「先週」の半開区間 `[月曜 0:00, 今週の月曜 0:00)` を返す。

    月曜の朝に流す前提なので、**当日を含めない**。当日を含めると
    「今日の朝までの実績」が混ざり、翌週の集計と二重になる。
    """
    day = datetime.combine(current.date(), time(0, 0), tzinfo=current.tzinfo or TZ)
    this_monday = day - timedelta(days=day.weekday())
    return this_monday - timedelta(days=7), this_monday


def count_completed_between(
    tasks: Iterable[Mapping[str, Any]], start: datetime, end: datetime
) -> int:
    """`completed_at` が `[start, end)` に入るタスクの件数（純関数）。

    ISO 文字列の大小比較ではなく datetime に直して比べる。
    オフセット表記が混ざると文字列比較が黙って壊れるため
    （`+09:00` と `Z` は辞書順で逆転する）。読めない行は数えない。
    """
    count = 0
    for row in tasks:
        raw = row.get("completed_at")
        if not raw:
            continue
        try:
            done_at = from_iso(str(raw))
        except (TypeError, ValueError):
            continue
        if start <= done_at < end:
            count += 1
    return count


def week_label(start: datetime, end: datetime) -> str:
    """「8/18〜8/24」の表示用ラベル（end は半開区間なので1日戻す）。"""
    last_day = end - timedelta(days=1)
    return f"{start.month}/{start.day}〜{last_day.month}/{last_day.day}"
