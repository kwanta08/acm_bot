"""積層記録（layer_records）の集計（G4-1）。

`/layer stats` が使う純関数だけを置く。DB アクセスは呼び出し側
（Cog / Repository）が持ち、ここは「行の集まり → 集計結果」の変換に徹する。
こうしておくと期間の境界や数え方を DB 無しで単体テストできる。

**完了層数の数え方は `ProgressRepository.count_completed_layers` と揃える**
（層番号の種類数。巻き直しは1層）。ここだけ数え方を変えると、同じ桁の層数が
`/progress` と `/layer stats` で食い違う。

目標層数は `progress_spar_links.target_layers`。紐付けが無い桁の目標は
**0 ではなく None**（ADR 0021: 分からないものを数字にしない）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from utils.parser import from_iso

PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_ALL = "all"

#: 表示用のラベル（Embed のタイトルに出す）
PERIOD_LABELS = {
    PERIOD_WEEK: "今週",
    PERIOD_MONTH: "今月",
    PERIOD_ALL: "全期間",
}


def period_start(period: str, current: datetime) -> datetime | None:
    """集計の開始時刻を返す。全期間（および未知の値）は None。

    週は月曜始まり。日本の部活の週の区切りに合わせている。
    """
    if period == PERIOD_WEEK:
        day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    if period == PERIOD_MONTH:
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


@dataclass
class KetaStat:
    """桁1本ぶんの集計。"""

    keta: str
    layers: int
    minutes: int
    last_worked_at: str | None
    target: int | None = None

    @property
    def average_minutes(self) -> int | None:
        """1層あたりの平均作業分。層が0なら None（0 で割らない）。"""
        if self.layers <= 0:
            return None
        return round(self.minutes / self.layers)

    @property
    def ratio(self) -> float | None:
        """目標に対する達成率（1.0 でクランプ）。目標が無ければ None。"""
        if not self.target:
            return None
        return min(self.layers / self.target, 1.0)

    @property
    def remaining(self) -> int | None:
        """残り層数。目標が無ければ None。"""
        if not self.target:
            return None
        return max(self.target - self.layers, 0)


@dataclass
class MemberStat:
    """作業者1人ぶんの集計。"""

    user_id: str
    layers: int
    minutes: int


@dataclass
class LayerStats:
    ketas: list[KetaStat] = field(default_factory=list)
    members: list[MemberStat] = field(default_factory=list)
    records: int = 0

    @property
    def total_minutes(self) -> int:
        return sum(m.minutes for m in self.members)


def aggregate_layer_stats(
    records: Iterable[Mapping[str, Any]],
    targets: Mapping[str, int],
    since: datetime | None = None,
) -> LayerStats:
    """積層記録を桁別・人別に集計する（純関数）。

    records の各要素は keta / layer_num / user_id / minutes / ended_at を持つ。
    since を渡すと、それ以降に終了した記録だけを対象にする（境界は含む）。
    ended_at が読めない行は落とさず、期間指定があるときだけ除外する
    （集計から黙って消えるより、全期間で見えている方が気づける）。
    """
    keta_minutes: dict[str, int] = {}
    keta_layers: dict[str, set[str]] = {}
    keta_last: dict[str, str] = {}
    member_minutes: dict[str, int] = {}
    member_layers: dict[str, set[tuple[str, str]]] = {}
    counted = 0

    for row in records:
        ended_at = str(row["ended_at"])
        if since is not None:
            try:
                if from_iso(ended_at) < since:
                    continue
            except ValueError:
                continue
        keta = str(row["keta"])
        layer_num = str(row["layer_num"])
        user_id = str(row["user_id"])
        minutes = int(row["minutes"] or 0)

        counted += 1
        keta_minutes[keta] = keta_minutes.get(keta, 0) + minutes
        keta_layers.setdefault(keta, set()).add(layer_num)
        if ended_at > keta_last.get(keta, ""):
            keta_last[keta] = ended_at
        member_minutes[user_id] = member_minutes.get(user_id, 0) + minutes
        member_layers.setdefault(user_id, set()).add((keta, layer_num))

    ketas = [
        KetaStat(
            keta=keta,
            layers=len(keta_layers[keta]),
            minutes=keta_minutes[keta],
            last_worked_at=keta_last.get(keta),
            target=targets.get(keta),
        )
        for keta in sorted(keta_layers)
    ]
    members = [
        MemberStat(user_id=user_id, layers=len(member_layers[user_id]), minutes=minutes)
        for user_id, minutes in member_minutes.items()
    ]
    members.sort(key=lambda m: (-m.minutes, -m.layers, m.user_id))
    return LayerStats(ketas=ketas, members=members, records=counted)
