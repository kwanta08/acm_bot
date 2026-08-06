"""Google Sheets エクスポート連携（ギルド別）の単体テスト。

外部 API（gspread）は使わず、Fake クライアントを注入して検証する。

- スプレッドシート ID がギルド別 settings から解決されること
- 未設定ギルドでは sync_guild が None を返し例外にならないこと
- 設定済みギルドで tasks / members シートが上書きされること
- 行組み立て（build_*_rows）の内容が正しいこと

実行: venv/bin/python -m pytest tests/
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.settings_repository import SettingsRepository  # noqa: E402
from repositories.task_repository import TaskRepository  # noqa: E402
from services import sheets_service  # noqa: E402
from utils.db import Database  # noqa: E402

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 側で新規作成させる
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ------------------------------------------------------------------
# gspread クライアントの Fake（外部 API のモック）
# ------------------------------------------------------------------

class FakeWorksheet:
    def __init__(self):
        self.cleared = False
        self.values = None

    def clear(self):
        self.cleared = True

    def update(self, values):
        self.values = values


class FakeBook:
    def __init__(self):
        self.worksheets: dict[str, FakeWorksheet] = {}
        self.added: list[str] = []

    def worksheet(self, title):
        if title not in self.worksheets:
            raise KeyError(title)  # gspread.WorksheetNotFound 相当
        return self.worksheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet()
        self.worksheets[title] = ws
        self.added.append(title)
        return ws


class FakeClient:
    def __init__(self):
        self.books: dict[str, FakeBook] = {}
        self.opened: list[str] = []

    def open_by_key(self, spreadsheet_id):
        self.opened.append(spreadsheet_id)
        return self.books.setdefault(spreadsheet_id, FakeBook())


def _exporter(client: FakeClient) -> sheets_service.SheetsExporter:
    return sheets_service.SheetsExporter(client=client)


# ------------------------------------------------------------------
# テスト
# ------------------------------------------------------------------

def test_spreadsheet_id_resolved_per_guild():
    db = run(_make_db())
    try:
        repo = SettingsRepository(db)
        run(repo.set(G1, "SPREADSHEET_ID", "sheet-g1"))
        assert run(sheets_service.get_spreadsheet_id(db, G1)) == "sheet-g1"
        # 未設定ギルドは None（例外にならない）
        assert run(sheets_service.get_spreadsheet_id(db, G2)) is None
    finally:
        run(db.close())


def test_sync_skips_unset_guild():
    db = run(_make_db())
    try:
        client = FakeClient()
        result = run(sheets_service.sync_guild(db, G1, _exporter(client)))
        assert result is None
        assert client.opened == []  # 外部 API は呼ばれない
    finally:
        run(db.close())


def test_sync_writes_tasks_and_members():
    db = run(_make_db())
    try:
        run(SettingsRepository(db).set(G1, "SPREADSHEET_ID", "sheet-g1"))
        members = MemberRepository(db)
        run(members.upsert_team(G1, "design", "設計"))
        run(members.upsert_member(G1, "u1", "Taro", primary_team="design"))
        run(TaskRepository(db).create_task(
            G1, "翼リブ加工", created_by="u1", team_key="design",
            due_date="2026-07-05", priority=3))

        client = FakeClient()
        result = run(sheets_service.sync_guild(db, G1, _exporter(client)))

        assert result == {"spreadsheet_id": "sheet-g1", "tasks": 1, "members": 1}
        assert client.opened == ["sheet-g1"]

        book = client.books["sheet-g1"]
        # シートが無ければ作成される
        assert set(book.added) == {"tasks", "members"}

        tasks_ws = book.worksheets["tasks"]
        assert tasks_ws.cleared and tasks_ws.values is not None
        assert tasks_ws.values[0] == sheets_service.TASKS_HEADER
        row = tasks_ws.values[1]
        assert row[2] == "翼リブ加工"      # title
        assert row[4] == "設計"            # team は表示名
        assert row[5] == "2026-07-05"      # due_date

        members_ws = book.worksheets["members"]
        assert members_ws.values[0] == sheets_service.MEMBERS_HEADER
        mrow = members_ws.values[1]
        assert mrow[1] == "Taro"
        assert mrow[2] == "設計"
    finally:
        run(db.close())


def test_sync_guild_isolation():
    """他ギルドのデータがエクスポートに混ざらないこと。"""
    db = run(_make_db())
    try:
        run(SettingsRepository(db).set(G1, "SPREADSHEET_ID", "sheet-g1"))
        run(TaskRepository(db).create_task(G1, "G1タスク", created_by="u1"))
        run(TaskRepository(db).create_task(G2, "G2タスク", created_by="u2"))

        client = FakeClient()
        result = run(sheets_service.sync_guild(db, G1, _exporter(client)))
        assert result["tasks"] == 1
        titles = [r[2] for r in client.books["sheet-g1"].worksheets["tasks"].values[1:]]
        assert titles == ["G1タスク"]
    finally:
        run(db.close())


def test_build_rows_pure():
    team_names = {"design": "設計"}
    tasks = [{
        "local_task_id": 1, "todoist_task_id": None, "title": "t",
        "assignee_id": None, "team_key": "design", "due_date": None,
        "priority": None, "status": "open", "created_by": "u1",
        "created_at": "2026-01-01", "completed_at": None,
    }]
    rows = sheets_service.build_tasks_rows(tasks, team_names)
    assert rows[0][4] == "設計"
    assert rows[0][1] == "" and rows[0][5] == ""  # None は空文字

    members = [{
        "user_id": "u1", "display_name": "Taro", "primary_team": "design",
        "secondary_teams": ["unknown"], "is_leader": 1,
        "skills": ["CAD", "はんだ"], "joined_at": "2026-01-01",
    }]
    mrows = sheets_service.build_members_rows(members, team_names)
    assert mrows[0][2] == "設計"
    assert mrows[0][3] == "unknown"  # 未登録班はキーそのまま
    assert mrows[0][4] == "1"
    assert mrows[0][5] == "CAD, はんだ"
