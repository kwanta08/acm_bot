"""schedule_id / task_id のオートコンプリートのテスト（G2-2）。

これまで5コマンドが素の `schedule_id: str`、3コマンドが素の `task_id` で、
利用者は一覧コマンドの出力から ID を手で写す必要があった。写し間違いは
G2-1 で確認ステップを付けた削除系に流れ込む（確認は付いたが、そもそも
間違った対象を選ばせない方がよい）。

- `/schedule close|remind|edit-deadline` は**開催中のみ**候補に出す
  （締切済みに close は意味がなく、remind は嘘になる）
- `/schedule status|delete` は**締切済みも含めて**出す
- `/task done|delete|priority` は Todoist の未完了タスクを `Choice[str]` で出す
  （タスクの正本は Todoist。ID も Todoist のもの）

候補は Discord の制約どおり25件以内、必ず guild_id でスコープする。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from discord import app_commands

from cogs.schedule import Schedule, schedule_choices
from cogs.tasks import Tasks, task_choices
from repositories.schedule_repository import ScheduleRepository
from services.todoist_task_service import TodoistTask
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
def _ttask(task_id: str, content: str) -> TodoistTask:
    return TodoistTask(
        id=task_id,
        content=content,
        description="",
        due_date=None,
        due_string=None,
        priority=1,
        section_id=None,
    )


def test_task_choice_label_is_the_task_name():
    (label, value), *_ = task_choices([_ttask("12", "主桁の積層")], "")
    assert label == "主桁の積層"
    assert value == "12"


def test_task_choices_filter_by_name_and_id():
    rows = [_ttask("1", "主桁の積層"), _ttask("2", "リブ切り出し")]
    assert [v for _, v in task_choices(rows, "リブ")] == ["2"]
    assert [v for _, v in task_choices(rows, "1")] == ["1"]


def test_task_choices_cap_at_25():
    rows = [_ttask(str(i), f"タスク{i:02d}") for i in range(1, 31)]
    assert len(task_choices(rows, "")) == 25


def test_task_choice_label_is_within_discord_limit():
    (label, _), *_ = task_choices([_ttask("1", "あ" * 200)], "")
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


def registered_autocomplete(command, param_name: str) -> str:
    """コマンドの引数に**実際に束ねられている**補完コールバック名を返す（G4-13）。

    **公開 API の `Parameter.autocomplete` は `bool` を返すプロパティ**なので、
    `param.autocomplete is not None` は補完が1つも登録されていなくても
    `False is not None` → `True` で通ってしまう。
    G2-2 で追加した2つの検査は、これで**何も担保していなかった**
    （実測: `/schedule create` の `title` は補完が無いのに `True`）。

    private 属性 `_params` に依存するが、公開 API では「どのコールバックが
    束ねられているか」を取れない。消えたときは `AttributeError` / `KeyError` で
    **大きな音を立てて落ちる**ので、テスト専用の依存として許容する。
    """
    return command._params[param_name].autocomplete.__name__


def test_the_registration_check_is_not_vacuous():
    """**この検査自体が空振りしていないこと**を先に固定する（G4-13）。

    補完が付いていない引数に対して `registered_autocomplete` が
    落ちること——公開 API の `autocomplete` プロパティを見ていたときは
    ここが `True` を返して素通りしていた。
    """
    create = next(
        c
        for c in Schedule.group.commands
        if isinstance(c, app_commands.Command) and c.name == "create"
    )
    # 補完が付いていない引数（title）。公開 API では False が返るだけ
    assert create.get_parameter("title").autocomplete is False
    with pytest.raises(AttributeError):
        registered_autocomplete(create, "title")


def test_schedule_autocomplete_is_registered_on_the_right_commands():
    """開催中のみ: close / remind / edit-deadline、全件: status / delete。"""
    open_only = {"close", "remind", "edit-deadline"}
    with_closed = {"status", "delete"}
    for command in Schedule.group.commands:
        if not isinstance(command, app_commands.Command):
            continue
        if command.name in open_only | with_closed:
            # **公開 API の `autocomplete` は bool。** 名前まで見ないと
            # 「登録されていないのに緑」になる（G4-13）
            name = registered_autocomplete(command, "schedule_id")
            expected = "_schedule_ac_open" if command.name in open_only else "_schedule_ac_all"
            assert name == expected, (
                f"/schedule {command.name} の schedule_id に付いている補完が {name}"
            )


# ---------------------------------------------------------------------
# Tasks コグ経由
# ---------------------------------------------------------------------
def _raw(task_id: str, content: str):
    return SimpleNamespace(
        id=task_id, content=content, description="", due=None, priority=1, section_id=None
    )


class _FakeTodoist:
    """ギルドごとに違うタスクを返す Todoist の代わり。"""

    project_id = None

    def __init__(self, tasks_by_guild: dict[int, list], guild_id: int):
        self.enabled = True
        self._tasks = tasks_by_guild.get(guild_id, [])

    async def get_tasks(self, **kwargs):
        return list(self._tasks)


def _tasks_cog(db: Database, tasks_by_guild: dict[int, list] | None = None) -> Tasks:
    by_guild = tasks_by_guild or {}

    class _Manager:
        async def for_guild(self, guild_id):
            return _FakeTodoist(by_guild, guild_id)

    return Tasks(SimpleNamespace(db=db, guilds=[], todoist_manager=_Manager()))


#: Todoist の get_tasks は未完了タスクしか返さない（完了済みは出てこない）
SEEDED_TASKS = {
    G1: [_raw("td_1", "主桁の積層")],
    G2: [_raw("td_9", "他大学の作業")],
}


def test_task_autocomplete_offers_only_this_guilds_tasks():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _tasks_cog(db, SEEDED_TASKS)
            choices = await cog._task_autocomplete(_interaction(G1), "")
            labels = [c.name for c in choices]
            assert any("主桁の積層" in x for x in labels)
            assert not any("他大学" in x for x in labels), "他ギルドのタスクが候補に出ている"
        finally:
            await db.close()

    run(_main())


def test_task_autocomplete_returns_str_choices():
    """task_id: str（Todoist のタスク ID）に合わせて Choice[str] を返すこと。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _tasks_cog(db, SEEDED_TASKS)
            choices = await cog._task_autocomplete(_interaction(G1), "")
            assert choices and all(isinstance(c.value, str) for c in choices)
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


def test_task_autocomplete_is_empty_without_todoist():
    """未設定ギルドでは補完を出さない（例外にもしない）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            class _Manager:
                async def for_guild(self, guild_id):
                    return SimpleNamespace(enabled=False, project_id=None)

            cog = Tasks(SimpleNamespace(db=db, guilds=[], todoist_manager=_Manager()))
            assert await cog._task_autocomplete(_interaction(G1), "") == []
        finally:
            await db.close()

    run(_main())


def test_task_autocomplete_is_registered_on_the_right_commands():
    targets = {"done", "delete", "priority"}
    for command in Tasks.group.commands:
        if not isinstance(command, app_commands.Command):
            continue
        if command.name in targets:
            name = registered_autocomplete(command, "task_id")
            assert name == "_task_autocomplete", (
                f"/task {command.name} の task_id に付いている補完が {name}"
            )
