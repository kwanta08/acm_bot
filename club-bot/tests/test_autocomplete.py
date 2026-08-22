"""schedule_id / task_id のオートコンプリートのテスト（G2-2）。

これまで5コマンドが素の `schedule_id: str`、4コマンドが素の `task_id: int` で、
利用者は一覧コマンドの出力から ID を手で写す必要があった。写し間違いは
G2-1 で確認ステップを付けた削除系に流れ込む（確認は付いたが、そもそも
間違った対象を選ばせない方がよい）。

- `/schedule close|remind|edit-deadline` は**開催中のみ**候補に出す
  （締切済みに close は意味がなく、remind は嘘になる）
- `/schedule status|delete` は**締切済みも含めて**出す
- `/task done|delete|assign|priority` は未完了タスクを `Choice[int]` で出す

候補は Discord の制約どおり25件以内、必ず guild_id でスコープする。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from discord import app_commands

from cogs.schedule import Schedule, schedule_choices
from cogs.tasks import Tasks, task_choices
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from utils.db import Database

G1 = 111
G2 = 222


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _interaction(guild_id: int | None = G1):
    guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
    return SimpleNamespace(guild=guild)


# ---------------------------------------------------------------------
# 純粋関数: schedule_choices
# ---------------------------------------------------------------------
def _srow(schedule_id: str, title: str, deadline: str, closed: int = 0) -> dict:
    return {
        "schedule_id": schedule_id,
        "title": title,
        "deadline": deadline,
        "closed_flag": closed,
    }


def test_schedule_choice_label_has_title_and_deadline():
    """候補名は「イベント名（〜締切）」。ID だけ見せられても選べない。"""
    rows = [_srow("sch_1", "第1回 全体ミーティング", "2026-09-01T23:59:00")]
    (label, value), *_ = schedule_choices(rows, "")
    assert "第1回 全体ミーティング" in label
    assert "〜" in label and "09/01" in label
    assert value == "sch_1"


def test_schedule_choices_mark_closed_ones():
    """締切済みも出す一覧では、開催中と見分けられること。"""
    rows = [
        _srow("sch_1", "夏合宿", "2026-08-01T23:59:00", closed=1),
        _srow("sch_2", "秋合宿", "2026-10-01T23:59:00", closed=0),
    ]
    labels = [label for label, _ in schedule_choices(rows, "")]
    assert any("終了" in label for label in labels if "夏合宿" in label)
    assert all("終了" not in label for label in labels if "秋合宿" in label)


def test_schedule_choices_filter_by_current():
    rows = [
        _srow("sch_1", "夏合宿", "2026-08-01T23:59:00"),
        _srow("sch_2", "秋合宿", "2026-10-01T23:59:00"),
    ]
    got = schedule_choices(rows, "秋")
    assert [v for _, v in got] == ["sch_2"]
    # ID の部分一致でも絞れる（一覧から写した ID の検証にも使える）
    got = schedule_choices(rows, "sch_1")
    assert [v for _, v in got] == ["sch_1"]


def test_schedule_choices_cap_at_25():
    rows = [_srow(f"sch_{i}", f"ミーティング{i}", "2026-09-01T23:59:00") for i in range(30)]
    assert len(schedule_choices(rows, "")) == 25


def test_schedule_choice_label_is_within_discord_limit():
    rows = [_srow("sch_1", "あ" * 200, "2026-09-01T23:59:00")]
    (label, _), *_ = schedule_choices(rows, "")
    assert len(label) <= 100


# ---------------------------------------------------------------------
# 純粋関数: task_choices
# ---------------------------------------------------------------------
def _trow(task_id: int, title: str) -> dict:
    return {"local_task_id": task_id, "title": title}


def test_task_choice_label_has_id_and_title():
    (label, value), *_ = task_choices([_trow(12, "主桁の積層")], "")
    assert "#12" in label and "主桁の積層" in label
    assert value == 12


def test_task_choices_filter_by_title_and_id():
    rows = [_trow(1, "主桁の積層"), _trow(2, "リブ切り出し")]
    assert [v for _, v in task_choices(rows, "リブ")] == [2]
    assert [v for _, v in task_choices(rows, "1")] == [1]


def test_task_choices_cap_at_25():
    rows = [_trow(i, f"タスク{i}") for i in range(1, 31)]
    assert len(task_choices(rows, "")) == 25


def test_task_choice_label_is_within_discord_limit():
    (label, _), *_ = task_choices([_trow(1, "あ" * 200)], "")
    assert len(label) <= 100


# ---------------------------------------------------------------------
# Schedule コグ経由（guild スコープ・開催中/全件の切り替え）
# ---------------------------------------------------------------------
async def _seed_schedules(db: Database) -> None:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1, "sch_open", "秋合宿", None, None, None, "2026-10-01T23:59:00", "tester", "1"
    )
    await repo.create_schedule(
        G1, "sch_closed", "夏合宿", None, None, None, "2026-08-01T23:59:00", "tester", "1"
    )
    await repo.close_schedule(G1, "sch_closed")
    # 別ギルド
    await repo.create_schedule(
        G2, "sch_other", "他大学の合宿", None, None, None, "2026-10-01T23:59:00", "tester", "1"
    )


def _schedule_cog(db: Database) -> Schedule:
    return Schedule(SimpleNamespace(db=db, guilds=[], get_channel=lambda _cid: None))


def test_open_autocomplete_offers_only_open_schedules():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedules(db)
            cog = _schedule_cog(db)
            choices = await cog._schedule_ac_open(_interaction(G1), "")
            values = [c.value for c in choices]
            assert values == ["sch_open"], values
        finally:
            await db.close()

    run(_main())


def test_all_autocomplete_includes_closed_schedules():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedules(db)
            cog = _schedule_cog(db)
            choices = await cog._schedule_ac_all(_interaction(G1), "")
            values = {c.value for c in choices}
            assert values == {"sch_open", "sch_closed"}, values
        finally:
            await db.close()

    run(_main())


def test_schedule_autocomplete_is_guild_scoped():
    """他ギルドの投票が候補に出ないこと。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedules(db)
            cog = _schedule_cog(db)
            for method in (cog._schedule_ac_open, cog._schedule_ac_all):
                values = {c.value for c in await method(_interaction(G1), "")}
                assert "sch_other" not in values
        finally:
            await db.close()

    run(_main())


def test_schedule_autocomplete_outside_a_guild_is_empty():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _schedule_cog(db)
            assert await cog._schedule_ac_open(_interaction(None), "") == []
            assert await cog._schedule_ac_all(_interaction(None), "") == []
        finally:
            await db.close()

    run(_main())


def test_schedule_autocomplete_is_registered_on_the_right_commands():
    """開催中のみ: close / remind / edit-deadline、全件: status / delete。"""
    open_only = {"close", "remind", "edit-deadline"}
    with_closed = {"status", "delete"}
    for command in Schedule.group.commands:
        if not isinstance(command, app_commands.Command):
            continue
        if command.name in open_only | with_closed:
            param = command.get_parameter("schedule_id")
            assert param is not None and param.autocomplete is not None, (
                f"/schedule {command.name} の schedule_id に補完が無い"
            )


# ---------------------------------------------------------------------
# Tasks コグ経由
# ---------------------------------------------------------------------
async def _seed_tasks(db: Database) -> None:
    repo = TaskRepository(db)
    open_id = await repo.create_task(G1, "主桁の積層", created_by="tester")
    assert open_id
    done_id = await repo.create_task(G1, "済んだ作業", created_by="tester")
    await repo.complete_task(G1, done_id)
    await repo.create_task(G2, "他大学の作業", created_by="tester")


def _tasks_cog(db: Database) -> Tasks:
    return Tasks(SimpleNamespace(db=db, guilds=[]))


def test_task_autocomplete_offers_only_open_tasks():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_tasks(db)
            cog = _tasks_cog(db)
            choices = await cog._task_autocomplete(_interaction(G1), "")
            labels = [c.name for c in choices]
            assert any("主桁の積層" in x for x in labels)
            assert not any("済んだ作業" in x for x in labels), "完了済みが候補に出ている"
            assert not any("他大学" in x for x in labels), "他ギルドのタスクが候補に出ている"
        finally:
            await db.close()

    run(_main())


def test_task_autocomplete_returns_int_choices():
    """task_id: int の引数に合わせて Choice[int] を返すこと。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_tasks(db)
            cog = _tasks_cog(db)
            choices = await cog._task_autocomplete(_interaction(G1), "")
            assert choices and all(isinstance(c.value, int) for c in choices)
        finally:
            await db.close()

    run(_main())


def test_task_autocomplete_outside_a_guild_is_empty():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _tasks_cog(db)
            assert await cog._task_autocomplete(_interaction(None), "") == []
        finally:
            await db.close()

    run(_main())


def test_task_autocomplete_is_registered_on_the_right_commands():
    targets = {"done", "delete", "assign", "priority"}
    for command in Tasks.group.commands:
        if not isinstance(command, app_commands.Command):
            continue
        if command.name in targets:
            param = command.get_parameter("task_id")
            assert param is not None and param.autocomplete is not None, (
                f"/task {command.name} の task_id に補完が無い"
            )
