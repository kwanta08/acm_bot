"""メンバー軸の出欠集計（G4-6）。

`/report attendance-rate` は投票ごとの ok 率で、**「最近来ていない人」が
特定できない**。「3回連続で未回答」は退部のほぼ確実な予兆なので、
人単位で積み上げる。

DB も Discord も触らない純関数だけを置く。母集団の決め方（誰を「対象」と
数えるか）は `schedule_service.select_unanswered_targets` と揃える必要が
あり、呼び出し側（`cogs/reports.py`）がそちらを使って `targets` を作る。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class ScheduleAnswers:
    """締切済み予定1件ぶんの、誰が対象で誰が答えたか。"""

    schedule_id: str
    targets: set[str] = field(default_factory=set)
    answered: set[str] = field(default_factory=set)
    ok: set[str] = field(default_factory=set)


@dataclass
class MemberAttendance:
    """1人ぶんの出欠実績。"""

    user_id: str
    targeted: int = 0
    answered: int = 0
    ok: int = 0
    #: 直近から数えて何回続けて未回答か（対象でなかった回は飛ばす）
    streak_unanswered: int = 0

    @property
    def answer_rate(self) -> float | None:
        """回答率 = 回答した回数 ÷ 対象になった回数。対象0回なら None。

        0.0 にしない（ADR 0021: 分からないものを数字にしない）。
        一度も対象になっていない人は「回答率0%」ではない。
        """
        if self.targeted <= 0:
            return None
        return self.answered / self.targeted

    @property
    def ok_rate(self) -> float | None:
        """ok 率 = 参加と答えた回数 ÷ **回答した回数**。回答0回なら None。

        分母を「対象になった回数」にすると回答率との積になり、
        「答えてはいるが来られない人」と「そもそも答えない人」が
        同じ数字に潰れる。両方を並べて出すために分母を分けている。
        """
        if self.answered <= 0:
            return None
        return self.ok / self.answered


def aggregate_member_attendance(
    schedules: Sequence[ScheduleAnswers],
) -> list[MemberAttendance]:
    """人ごとの出欠実績を、回答率の低い順に返す（純関数）。

    `schedules` は**新しい順**（直近が先頭）で渡すこと。連続未回答数は
    先頭から数えるため、順序を取り違えると「昔サボっていた人」が
    直近の要注意人物として上がってくる。

    並び順は 回答率の低い順 → 対象回数の多い順 → user_id。
    回答率が同じなら、対象回数が多い人（判断材料が多い人）を先に出す。
    """
    stats: dict[str, MemberAttendance] = {}
    streak_open: dict[str, bool] = {}

    for entry in schedules:
        answered = {str(a) for a in entry.answered}
        ok = {str(o) for o in entry.ok}
        for user_id in sorted(str(t) for t in entry.targets):
            member = stats.setdefault(user_id, MemberAttendance(user_id=user_id))
            member.targeted += 1
            if user_id in answered:
                member.answered += 1
                streak_open[user_id] = False
                if user_id in ok:
                    member.ok += 1
            elif streak_open.get(user_id, True):
                # 直近から数えて、まだ一度も回答に当たっていない
                member.streak_unanswered += 1

    def _order(member: MemberAttendance) -> tuple:
        # 回答率が未定義（対象0回）の人は末尾へ。0% として先頭に出すと
        # 「一度も対象になっていない人」が要注意人物に見える（ADR 0021）
        rate = member.answer_rate
        return (1.0 if rate is None else rate, -member.targeted, member.user_id)

    return sorted(stats.values(), key=_order)


def format_rate(rate: float | None) -> str:
    """率の表示。未定義（分母0）は 0% ではなく `—`。"""
    return "—" if rate is None else f"{rate * 100:.0f}%"
