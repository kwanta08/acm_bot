"""工具・機材の貸出判定（G4-9）。

`/tool` が使う純関数だけを置く。DB も Discord も触らない。

**G4-2（積層セッションの押し忘れ検知）と同じ形**にしてある——
「閾値を超えたものを選び、通知済みフラグで1回に絞る」。
ただし `classify_stale_sessions` をそのまま呼ぶことはしない。
あちらの閾値は**分**で `started_at` からの経過を見るのに対し、
こちらは**日**で `due_date` を過ぎたかを見る。共用するには貸出行を
偽のセッション辞書へ詰め替えることになり、読む人にとって
「なぜ工具に `keta` と `layer_num` があるのか」が分からなくなる。
形をそろえるのは有効だが、型を無理に合わせるのは別の話。

`due_date` が無い貸出は**督促しない**。返却予定日を決めていない貸出を
「本日返却」とみなすのは、分からないものを数字にすることにあたる（ADR 0021）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from services.milestone_service import parse_date


@dataclass
class OverdueLoan:
    """返却予定日を過ぎた貸出。"""

    loan_id: int
    tool_id: int
    tool_name: str
    user_id: str
    due_date: date
    days_over: int


def overdue_loans(
    loans: Iterable[Mapping[str, Any]], today: date, *, only_unnotified: bool = True
) -> list[OverdueLoan]:
    """返却予定日を過ぎた貸出を、超過日数の多い順に返す（純関数）。

    - `due_date` が無い／読めない貸出は対象外（督促しない）
    - `returned_at` が入っている行は対象外（返却済み）
    - `only_unnotified` が True なら、まだ督促していない貸出だけを返す
      （`overdue_notified_flag`。1貸出につき1回だけ DM する）

    予定日**当日**は督促しない。「本日中に返す」つもりの人へ朝に
    「超過しています」と送るのは誤報になる。
    """
    out: list[OverdueLoan] = []
    for row in loans:
        if row.get("returned_at"):
            continue
        if only_unnotified and row.get("overdue_notified_flag"):
            continue
        due = parse_date(row.get("due_date"))
        if due is None:
            continue
        days_over = (today - due).days
        if days_over <= 0:
            continue
        out.append(
            OverdueLoan(
                loan_id=int(row["loan_id"]),
                tool_id=int(row["tool_id"]),
                tool_name=str(row.get("tool_name") or ""),
                user_id=str(row["user_id"]),
                due_date=due,
                days_over=days_over,
            )
        )
    out.sort(key=lambda loan: (-loan.days_over, loan.tool_name))
    return out


def loan_status_label(loan: Mapping[str, Any] | None, today: date) -> str:
    """一覧に出す貸出状態の1行。貸出中でなければ「貸出可」。"""
    if loan is None:
        return "貸出可"
    due = parse_date(loan.get("due_date"))
    if due is None:
        # 返却予定日を決めていない貸出を「期限なし」と正直に書く
        return "貸出中（返却予定日なし）"
    days_over = (today - due).days
    if days_over > 0:
        return f"貸出中（**{days_over}日超過**・予定 {due.isoformat()}）"
    return f"貸出中（予定 {due.isoformat()}）"
