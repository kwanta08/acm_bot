"""Todoist を正本としたタスク参照ヘルパー。

ローカルの `tasks` テーブルはスキーマ v22 で廃止した。タスクの追加・削除・
参照はすべて Todoist API を通る（`services/todoist_service.py`）。この層は
SDK が返すモデルを、Cog がそのまま Embed に流せる形へ正規化する。

**SDK のモデルを Cog に持ち込まない。** todoist-api-python は v2 と v3 で
`due.date` が str だったり `date` だったり、`priority` が int だったり
Enum だったりする。その差を吸収する場所をここ1箇所に閉じ込め、Cog は
`TodoistTask` だけを見る。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

#: Todoist のタスクを Web/アプリで開く URL
TASK_URL_TEMPLATE = "https://app.todoist.com/app/task/{task_id}"

#: 優先度の表示名（Todoist の 1=最低 〜 4=最高 に合わせる）
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}


def task_url(task_id: Any) -> str:
    return TASK_URL_TEMPLATE.format(task_id=task_id)


def due_date_of(raw_task: Any) -> date | None:
    """Todoist タスクの期限日（date）。未設定・解釈不能なら None。

    `due.date` は SDK の版により date / datetime / ISO 文字列のいずれでも
    来る。**壊れた1件で通知全体を落とさない**ため、読めない値は None に
    倒して呼び出し側が黙って除外できるようにする。
    """
    due = getattr(raw_task, "due", None)
    if due is None:
        return None
    raw = getattr(due, "date", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    # 未知の型でも .date() を持つならそれを使う（SDK の将来版への保険）
    to_date = getattr(raw, "date", None)
    if callable(to_date):
        try:
            return to_date()
        except Exception:  # noqa: BLE001
            return None
    return None


def priority_of(raw_task: Any) -> int:
    """優先度を 1〜4 の int で返す。未設定は 1（最低）。"""
    raw = getattr(raw_task, "priority", None)
    value = raw.value if hasattr(raw, "value") else raw
    try:
        pr = int(value)
    except (TypeError, ValueError):
        return 1
    return pr if 1 <= pr <= 4 else 1


@dataclass(frozen=True)
class TodoistTask:
    """Cog が扱うタスク1件。Todoist が正本で、ローカルには保存しない。"""

    id: str
    content: str
    description: str
    due_date: date | None
    due_string: str | None
    priority: int
    section_id: str | None

    @property
    def url(self) -> str:
        return task_url(self.id)

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(self.priority, "—")

    @classmethod
    def from_raw(cls, raw: Any) -> TodoistTask:
        due = getattr(raw, "due", None)
        section_id = getattr(raw, "section_id", None)
        return cls(
            id=str(getattr(raw, "id", "")),
            content=str(getattr(raw, "content", "") or ""),
            description=str(getattr(raw, "description", "") or ""),
            due_date=due_date_of(raw),
            due_string=getattr(due, "string", None) if due is not None else None,
            priority=priority_of(raw),
            section_id=str(section_id) if section_id else None,
        )


def normalize(raw_tasks: Any) -> list[TodoistTask]:
    return [TodoistTask.from_raw(t) for t in raw_tasks]


async def list_open_tasks(svc) -> list[TodoistTask]:
    """未完了タスク一覧（project_id 設定時はそのプロジェクトのみ）。

    Todoist の `get_tasks` は未完了タスクだけを返すため、完了済みは
    そもそも含まれない。
    """
    kwargs: dict[str, Any] = {}
    if svc.project_id:
        kwargs["project_id"] = svc.project_id
    return normalize(await svc.get_tasks(**kwargs))


def overdue(tasks: list[TodoistTask], today: date) -> list[TodoistTask]:
    """期限が today より前のタスク。期限なしは含めない。"""
    return sorted(
        (t for t in tasks if t.due_date is not None and t.due_date < today),
        key=lambda t: t.due_date,
    )


def due_within(tasks: list[TodoistTask], start: date, end: date) -> list[TodoistTask]:
    """期限が [start, end] にあるタスク。期限なしは含めない。"""
    return sorted(
        (t for t in tasks if t.due_date is not None and start <= t.due_date <= end),
        key=lambda t: t.due_date,
    )


def sort_for_display(tasks: list[TodoistTask]) -> list[TodoistTask]:
    """一覧表示の並び。期限が近い順 → 優先度が高い順、期限なしは末尾。"""
    return sorted(
        tasks,
        key=lambda t: (t.due_date is None, t.due_date or date.max, -t.priority, t.content),
    )
