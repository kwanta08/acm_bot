"""progress_sync_service（Todoist → 進捗シート同期）のユニットテスト。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repositories.settings_repository import SettingsRepository
from services import progress_sheet_service as pss
from services import progress_sync_service as sync
from utils.db import Database

HEADER = pss.PROGRESS_HEADER


def run(coro):
    return asyncio.run(coro)


def _row(node_id="", parent="", order="", depth="", name="", assignee="",
         status="", manual="", agg="", bar="", source="", td_id="",
         updated=""):
    return [node_id, parent, order, depth, name, assignee, status,
            manual, agg, bar, source, td_id, updated]


@dataclass
class FakeTask:
    id: str
    content: str
    parent_id: str | None = None


@dataclass
class FakeProject:
    id: str
    name: str


def _nodes(grid):
    return pss.grid_to_nodes(grid)


# ---------------------------------------------------------------------
# plan_todoist_upsert（純粋関数）
# ---------------------------------------------------------------------
def test_plan_adds_new_tasks_with_hierarchy():
    grid = [HEADER, _row("wing", "", "1", "", "主翼")]
    tasks = [
        FakeTask("1", "リブ製作"),
        FakeTask("2", "リブ切り出し", parent_id="1"),
    ]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", tasks)], "now")
    assert plan.added == 2
    assert not plan.errors
    rows = {r[0]: r for r in plan.new_rows}
    # トップレベルはアンカー直下、サブタスクは td_ 親の下
    assert rows["td_1"][1] == "wing"
    assert rows["td_2"][1] == "td_1"
    assert rows["td_1"][4] == "リブ製作"
    assert rows["td_1"][10] == "todoist"
    assert rows["td_1"][11] == "1"


def test_plan_orphan_subtask_falls_back_to_anchor():
    # 親タスクがアクティブ一覧にもシートにも無い → アンカー直下へ
    grid = [HEADER, _row("wing", "", "1", "", "主翼")]
    tasks = [FakeTask("5", "子タスク", parent_id="999")]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", tasks)], "now")
    assert plan.new_rows[0][1] == "wing"


def test_plan_updates_renamed_and_reparented_rows():
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("td_1", "wing", "1", "", "旧名", source="todoist", td_id="1"),
        _row("td_2", "wing", "2", "", "子", source="todoist", td_id="2"),
    ]
    tasks = [
        FakeTask("1", "新名"),
        FakeTask("2", "子", parent_id="1"),  # wing 直下 → td_1 の下へ移動
    ]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", tasks)], "now")
    assert plan.added == 0
    assert plan.updated == 2
    ranges = {r["range"]: r["values"] for r in plan.cell_ranges}
    assert ranges[f"'{pss.PROGRESS_SHEET}'!E3"] == [["新名"]]
    assert ranges[f"'{pss.PROGRESS_SHEET}'!B4"] == [["td_1"]]


def test_plan_never_touches_manual_rows():
    # 同じ td_ 形式の ID を持つ manual 行があっても上書きしない
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("td_1", "wing", "1", "", "手入力の行", manual="0.3",
             source="manual", td_id="1"),
    ]
    plan = sync.plan_todoist_upsert(
        _nodes(grid), [("wing", [FakeTask("1", "新名")])], "now")
    assert plan.added == 0
    assert plan.updated == 0
    assert plan.cell_ranges == []


def test_plan_marks_vanished_tasks_completed():
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("td_1", "wing", "1", "", "リブ", source="todoist", td_id="1"),
        _row("td_2", "td_1", "1", "", "切り出し", source="todoist", td_id="2"),
    ]
    # td_2 だけアクティブ → td_1 は完了扱い
    plan = sync.plan_todoist_upsert(
        _nodes(grid), [("wing", [FakeTask("2", "切り出し", parent_id="1")])],
        "now")
    assert plan.completed == 1
    done = [r for r in plan.cell_ranges if "G3:H3" in r["range"]]
    assert done and done[0]["values"] == [[sync.STATUS_DONE, 1]]


def test_plan_already_completed_rows_not_rewritten():
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("td_1", "wing", "1", "", "リブ", status=sync.STATUS_DONE,
             manual="100%", source="todoist", td_id="1"),
    ]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", [])], "now")
    assert plan.completed == 0
    assert plan.cell_ranges == []


def test_plan_never_touches_spar_winding_rows():
    # アンカー配下に桁巻き行があっても、完了扱い・更新の対象にしない
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("spar1", "wing", "1", "", "主桁", manual="0.5",
             source="spar_winding"),
    ]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", [])], "now")
    assert plan.completed == 0
    assert plan.cell_ranges == []


def test_plan_completion_limited_to_anchored_subtrees():
    # アンカー外（対応表から外れた別サブツリー）の td_ 行は完了扱いしない
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("tail", "", "2", "", "尾翼"),
        _row("td_9", "tail", "1", "", "別プロジェクトの行",
             source="todoist", td_id="9"),
    ]
    plan = sync.plan_todoist_upsert(_nodes(grid), [("wing", [])], "now")
    assert plan.completed == 0


def test_plan_missing_anchor_reports_error():
    grid = [HEADER, _row("wing", "", "1", "", "主翼")]
    plan = sync.plan_todoist_upsert(
        _nodes(grid), [("ghost", [FakeTask("1", "x")])], "now")
    assert any("ghost" in e for e in plan.errors)
    assert plan.added == 0


# ---------------------------------------------------------------------
# sync_guild（オーケストレーション）
# ---------------------------------------------------------------------
class FakeSheetClient:
    """ProgressSheetClient 互換のフェイク（同期メソッドのみ）。"""

    def __init__(self, progress_grid, mapping_grid):
        self.progress_grid = progress_grid
        self.mapping_grid = mapping_grid
        self.applied: list = []
        self.appended: list = []

    def read_progress_grid(self, spreadsheet_id):
        return [list(r) for r in self.progress_grid]

    def read_mapping_grid(self, spreadsheet_id):
        return [list(r) for r in self.mapping_grid]

    def apply_value_ranges(self, spreadsheet_id, ranges):
        self.applied.append(ranges)

    def append_progress_rows(self, spreadsheet_id, rows):
        self.appended.extend(rows)


class FakeTodoist:
    enabled = True

    def __init__(self, projects, tasks_by_project):
        self._projects = projects
        self._tasks = tasks_by_project

    async def get_projects(self):
        return self._projects

    async def get_tasks(self, project_id=None, **kwargs):
        return self._tasks.get(project_id, [])


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


GUILD = 123456789


def test_sync_guild_skips_when_unconfigured():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            client = FakeSheetClient([HEADER], [pss.MAPPING_HEADER])
            result = await sync.sync_guild(db, GUILD, None, client)
            assert result is None
            assert client.applied == []
        finally:
            await db.close()
    run(_main())


def test_sync_guild_full_flow():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, "SID")
            client = FakeSheetClient(
                [HEADER, _row("wing", "", "1", "", "主翼")],
                [pss.MAPPING_HEADER, ["主翼班", "wing"]])
            todoist = FakeTodoist(
                [FakeProject("P1", "主翼班")],
                {"P1": [FakeTask("1", "リブ製作"),
                        FakeTask("2", "切り出し", parent_id="1")]})

            result = await sync.sync_guild(db, GUILD, todoist, client)
            assert result is not None
            assert result.projects == 1
            assert result.added == 2
            assert [r[0] for r in client.appended] == ["td_1", "td_2"]
            # 再集計の書き戻し（深さ・集計・更新日時）が実行されている
            writeback = client.applied[-1]
            assert any("!D2:" in r["range"] for r in writeback)
            assert any("!I2:" in r["range"] for r in writeback)
        finally:
            await db.close()
    run(_main())


def test_sync_guild_unknown_project_reported():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, "SID")
            client = FakeSheetClient(
                [HEADER, _row("wing", "", "1", "", "主翼")],
                [pss.MAPPING_HEADER, ["存在しない班", "wing"]])
            todoist = FakeTodoist([FakeProject("P1", "主翼班")], {})
            result = await sync.sync_guild(db, GUILD, todoist, client)
            assert result.projects == 0
            assert any("存在しない班" in e for e in result.errors)
        finally:
            await db.close()
    run(_main())


def test_sync_guild_recalc_only_when_todoist_disabled():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, "SID")
            client = FakeSheetClient(
                [HEADER,
                 _row("wing", "", "1", "", "主翼"),
                 _row("rib", "wing", "1", "", "リブ", manual="50%")],
                [pss.MAPPING_HEADER])

            class Disabled:
                enabled = False

            result = await sync.sync_guild(db, GUILD, Disabled(), client)
            assert result.added == 0
            writeback = client.applied[-1]
            agg = next(r for r in writeback if "!I2:" in r["range"])
            assert agg["values"] == [[0.5], [0.5]]
        finally:
            await db.close()
    run(_main())


def test_sync_guild_reports_cycle_errors():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, "SID")
            client = FakeSheetClient(
                [HEADER,
                 _row("a", "b", "1", "", "A"),
                 _row("b", "a", "1", "", "B")],
                [pss.MAPPING_HEADER])
            result = await sync.sync_guild(db, GUILD, None, client)
            assert any("循環" in e for e in result.errors)
        finally:
            await db.close()
    run(_main())


def test_resolve_notify_channel_id_priority():
    settings = {pss.SHEET_KEY_DEFAULT_CHANNEL: "200"}
    # 対応表のプロジェクト専用チャンネルが最優先
    assert sync.resolve_notify_channel_id(
        {"notify_channel_id": "100"}, settings) == 100
    # 空欄なら設定タブのデフォルト
    assert sync.resolve_notify_channel_id(
        {"notify_channel_id": ""}, settings) == 200
    # 数値でない値はデフォルトへフォールバック
    assert sync.resolve_notify_channel_id(
        {"notify_channel_id": "abc"}, settings) == 200
    # どちらも無ければ None
    assert sync.resolve_notify_channel_id({"notify_channel_id": ""}, {}) is None


# ---------------------------------------------------------------------
# sync_all（シート単位デデュープの一括同期）
# ---------------------------------------------------------------------
class MultiSheetClient:
    """複数スプレッドシートを持ち、シートごとの read 回数を数えるフェイク。"""

    def __init__(self, books: dict[str, dict], fail_sids: set[str] = frozenset()):
        self.books = books                       # sid -> {progress, mapping}
        self.fail_sids = set(fail_sids)
        self.progress_reads: dict[str, int] = {}
        self.applied: dict[str, list] = {}

    def read_progress_grid(self, sid):
        if sid in self.fail_sids:
            raise RuntimeError("sheet unavailable")
        self.progress_reads[sid] = self.progress_reads.get(sid, 0) + 1
        return [list(r) for r in self.books[sid]["progress"]]

    def read_mapping_grid(self, sid):
        return [list(r) for r in self.books[sid]["mapping"]]

    def apply_value_ranges(self, sid, ranges):
        self.applied.setdefault(sid, []).append(ranges)

    def append_progress_rows(self, sid, rows):
        pass


class FakeTodoistManager:
    def __init__(self, by_guild: dict):
        self.by_guild = by_guild

    async def for_guild(self, guild_id):
        return self.by_guild.get(guild_id)


def test_collect_sheet_groups_dedupes_shared_sheet():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = SettingsRepository(db)
            await repo.set(1, pss.SETTINGS_KEY, "SID1")
            await repo.set(2, pss.SETTINGS_KEY, "SID1")   # 中央シート共有
            await repo.set(3, pss.SETTINGS_KEY, "SID2")
            # guild 4 は未設定 → 除外
            groups = await sync.collect_sheet_groups(db, [1, 2, 3, 4])
            by_sid = {g.spreadsheet_id: g.guild_ids for g in groups}
            assert by_sid == {"SID1": [1, 2], "SID2": [3]}
        finally:
            await db.close()
    run(_main())


def test_sync_all_reads_shared_sheet_once():
    """中央シートを2ギルドで共有しても読み書きはシートごとに1回だけ。"""
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = SettingsRepository(db)
            await repo.set(1, pss.SETTINGS_KEY, "SID1")
            await repo.set(2, pss.SETTINGS_KEY, "SID1")
            await repo.set(3, pss.SETTINGS_KEY, "SID2")
            client = MultiSheetClient({
                "SID1": {"progress": [HEADER, _row("wing", "", "1", "", "主翼")],
                         "mapping": [pss.MAPPING_HEADER, ["主翼班", "wing"]]},
                "SID2": {"progress": [HEADER, _row("m1", "", "1", "", "本機")],
                         "mapping": [pss.MAPPING_HEADER]},
            })
            todoist = FakeTodoist([FakeProject("P1", "主翼班")],
                                  {"P1": [FakeTask("1", "リブ製作")]})
            # Todoist はギルド2にのみ設定（グループ内の有効なサービスを使う）
            manager = FakeTodoistManager({2: todoist})

            outcomes = await sync.sync_all(db, [1, 2, 3], manager, client)
            assert len(outcomes) == 2
            assert all(o.error is None for o in outcomes)
            by_sid = {o.group.spreadsheet_id: o for o in outcomes}
            assert by_sid["SID1"].result.projects == 1
            # SID1: upsert 用 + 再集計用の2回のみ（ギルド数に比例しない）
            assert client.progress_reads["SID1"] == 2
            # SID2: Todoist なし → 再集計の1回のみ
            assert client.progress_reads["SID2"] == 1
        finally:
            await db.close()
    run(_main())


def test_sync_all_isolates_sheet_failures():
    """1シートの失敗が他シートの同期を止めない。"""
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = SettingsRepository(db)
            await repo.set(1, pss.SETTINGS_KEY, "BAD")
            await repo.set(2, pss.SETTINGS_KEY, "SID2")
            client = MultiSheetClient({
                "SID2": {"progress": [HEADER, _row("m1", "", "1", "", "本機")],
                         "mapping": [pss.MAPPING_HEADER]},
            }, fail_sids={"BAD"})
            outcomes = await sync.sync_all(
                db, [1, 2], FakeTodoistManager({}), client)
            by_sid = {o.group.spreadsheet_id: o for o in outcomes}
            assert by_sid["BAD"].error is not None
            assert by_sid["SID2"].error is None
            assert by_sid["SID2"].result is not None
        finally:
            await db.close()
    run(_main())


def test_get_spreadsheet_id_strips_blank():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            assert await sync.get_spreadsheet_id(db, GUILD) is None
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, "  ")
            assert await sync.get_spreadsheet_id(db, GUILD) is None
            await SettingsRepository(db).set(GUILD, pss.SETTINGS_KEY, " X ")
            assert await sync.get_spreadsheet_id(db, GUILD) == "X"
        finally:
            await db.close()
    run(_main())
