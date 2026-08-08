"""progress_sheet_service（進捗シート読み書き）のユニットテスト。

gspread は使わず、フェイクの book / client を注入して検証する。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import progress_sheet_service as pss
from services.progress_tree import build_and_aggregate

HEADER = pss.PROGRESS_HEADER


def _row(node_id="", parent="", order="", depth="", name="", assignee="",
         status="", manual="", agg="", bar="", source="", td_id="",
         updated=""):
    return [node_id, parent, order, depth, name, assignee, status,
            manual, agg, bar, source, td_id, updated]


# ---------------------------------------------------------------------
# grid_to_nodes
# ---------------------------------------------------------------------
def test_grid_to_nodes_basic():
    grid = [
        HEADER,
        _row("m1", "", "1", "", "本機", "", "", "", "", "", "manual"),
        _row("p1", "m1", "1", "", "主翼", "山田", "製作中", "50%", "", "",
             "manual"),
        _row("td_100", "p1", "2", "", "リブ", "", "", "1", "", "",
             "todoist", "100"),
    ]
    nodes = pss.grid_to_nodes(grid)
    assert [n.node_id for n in nodes] == ["m1", "p1", "td_100"]
    assert nodes[0].parent_id is None
    assert nodes[1].parent_id == "m1"
    assert nodes[1].manual_progress == 0.5
    assert nodes[1].assignee == "山田"
    assert nodes[2].source == "todoist"
    assert nodes[2].todoist_task_id == "100"
    assert nodes[2].row_index == 4  # シート上の行番号


def test_grid_to_nodes_skips_blank_id_rows():
    grid = [HEADER, _row(), _row("a", name="A")]
    nodes = pss.grid_to_nodes(grid)
    assert [n.node_id for n in nodes] == ["a"]
    assert nodes[0].row_index == 3


def test_grid_to_nodes_ragged_rows():
    grid = [HEADER, ["a", "", "1"]]  # 列数不足でも落ちない
    nodes = pss.grid_to_nodes(grid)
    assert nodes[0].node_id == "a"
    assert nodes[0].source == "manual"  # 既定値


def test_grid_to_nodes_default_source_is_manual():
    grid = [HEADER, _row("a", source="")]
    assert pss.grid_to_nodes(grid)[0].source == "manual"


# ---------------------------------------------------------------------
# build_writeback_ranges
# ---------------------------------------------------------------------
def _make_grid_and_tree():
    grid = [
        HEADER,
        _row("m1", "", "1", "9", "本機"),                      # 深さは古い値 9
        _row("p1", "m1", "1", "", "主翼", manual="0.5"),
        _row("bad", "ghost", "1", "7", "孤児", agg="0.9",
             updated="old"),                                   # スキップ対象
    ]
    tree = build_and_aggregate(pss.grid_to_nodes(grid))
    return grid, tree


def test_writeback_computed_columns():
    grid, tree = _make_grid_and_tree()
    ranges = pss.build_writeback_ranges(grid, tree, "2026-08-08 12:00")
    by_range = {r["range"]: r["values"] for r in ranges}

    depth = by_range[f"'{pss.PROGRESS_SHEET}'!D2:D4"]
    agg = by_range[f"'{pss.PROGRESS_SHEET}'!I2:I4"]
    updated = by_range[f"'{pss.PROGRESS_SHEET}'!M2:M4"]

    assert depth == [[0], [1], ["7"]]          # スキップ行は既存値を保持
    assert agg[0] == [0.5] and agg[1] == [0.5]
    assert agg[2] == ["0.9"]
    assert updated[0] == ["2026-08-08 12:00"]
    assert updated[2] == ["old"]


def test_writeback_sets_sparkline_only_when_empty():
    grid = [
        HEADER,
        _row("a", "", "1", "", "A", manual="1", bar="=SPARKLINE(...)"),
        _row("b", "", "2", "", "B", manual="0"),
    ]
    tree = build_and_aggregate(pss.grid_to_nodes(grid))
    ranges = pss.build_writeback_ranges(grid, tree, "now")
    bar_ranges = [r for r in ranges if "!J" in r["range"]]
    # J2 は設置済みなので J3 のみ
    assert len(bar_ranges) == 1
    assert bar_ranges[0]["range"] == f"'{pss.PROGRESS_SHEET}'!J3"
    assert "SPARKLINE" in bar_ranges[0]["values"][0][0]
    assert "I3" in bar_ranges[0]["values"][0][0]


def test_writeback_empty_grid():
    assert pss.build_writeback_ranges([HEADER], None or
                                      build_and_aggregate([]), "now") == []


# ---------------------------------------------------------------------
# parse_mapping_grid
# ---------------------------------------------------------------------
def test_parse_mapping_grid():
    grid = [
        pss.MAPPING_HEADER,
        ["主翼班", "p1", "111", "222"],
        ["", "x"],           # 名前欠落はスキップ
        ["尾翼班", ""],      # ノード ID 欠落はスキップ
        ["電装班", "p2"],    # 旧2列形式（通知チャンネル・ギルド列なし）も読める
        ["桁班", "p3", "333"],  # 旧3列形式（ギルド列なし）も読める
    ]
    mapping = pss.parse_mapping_grid(grid)
    assert mapping == [
        {"project_name": "主翼班", "node_id": "p1",
         "notify_channel_id": "111", "guild_id": "222"},
        {"project_name": "電装班", "node_id": "p2",
         "notify_channel_id": "", "guild_id": ""},
        {"project_name": "桁班", "node_id": "p3",
         "notify_channel_id": "333", "guild_id": ""},
    ]


def test_parse_settings_grid():
    grid = [
        pss.SETTINGS_HEADER,
        [pss.SHEET_KEY_DEFAULT_CHANNEL, " 123 ", "メモ"],
        ["", "無視される"],
        [pss.SHEET_KEY_SPAR_BOOK, ""],
        ["独自キー", "abc"],
    ]
    settings = pss.parse_settings_grid(grid)
    assert settings[pss.SHEET_KEY_DEFAULT_CHANNEL] == "123"
    assert settings[pss.SHEET_KEY_SPAR_BOOK] == ""
    assert settings["独自キー"] == "abc"


def test_parse_spar_mapping_grid():
    grid = [
        pss.SPAR_MAPPING_HEADER,
        ["主桁", "spar1"],
        ["", "x"],
        ["後桁", ""],
        ["尾桁", "spar2"],
    ]
    mapping = pss.parse_spar_mapping_grid(grid)
    assert mapping == [
        {"spar_key": "主桁", "node_id": "spar1"},
        {"spar_key": "尾桁", "node_id": "spar2"},
    ]


# ---------------------------------------------------------------------
# ProgressSheetClient（フェイク注入）
# ---------------------------------------------------------------------
class FakeWorksheet:
    _next_id = 100

    def __init__(self, title, values=None):
        self.title = title
        self.id = FakeWorksheet._next_id
        FakeWorksheet._next_id += 1
        self._values = values or []
        self.updates: list = []
        self.appended: list = []

    def get_all_values(self):
        return self._values

    def update(self, values):
        self.updates.append(values)

    def append_rows(self, rows, value_input_option=None):
        self.appended.extend(rows)


class FakeBook:
    def __init__(self, worksheets=()):
        self.sheets = {ws.title: ws for ws in worksheets}
        self.value_batches: list = []
        self.format_batches: list = []

    def worksheet(self, title):
        if title not in self.sheets:
            raise KeyError(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        self.sheets[title] = ws
        return ws

    def values_batch_update(self, body):
        self.value_batches.append(body)

    def batch_update(self, body):
        self.format_batches.append(body)

    def worksheets(self):
        return list(self.sheets.values())


class FakeClient:
    def __init__(self, book):
        self.book = book

    def open_by_key(self, spreadsheet_id):
        return self.book


def test_client_read_and_apply():
    ws = FakeWorksheet(pss.PROGRESS_SHEET, values=[HEADER, _row("a")])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))

    grid = client.read_progress_grid("sid")
    assert grid[1][0] == "a"

    client.apply_value_ranges("sid", [{"range": "X", "values": [[1]]}])
    assert book.value_batches[0]["valueInputOption"] == "USER_ENTERED"

    client.apply_value_ranges("sid", [])  # 空なら何もしない
    assert len(book.value_batches) == 1


def test_client_append_rows():
    ws = FakeWorksheet(pss.PROGRESS_SHEET, values=[HEADER])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))
    client.append_progress_rows("sid", [["td_1", "p1"]])
    assert ws.appended == [["td_1", "p1"]]


def test_client_append_mapping_row():
    ws = FakeWorksheet(pss.MAPPING_SHEET, values=[pss.MAPPING_HEADER])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))
    client.append_mapping_row("sid", "主翼班", "wing", "999", "12345")
    assert ws.appended == [["主翼班", "wing", "999", "12345"]]


def test_client_append_mapping_row_without_guild_id():
    """guild_id 省略時は空欄で追記される（後方互換）。"""
    ws = FakeWorksheet(pss.MAPPING_SHEET, values=[pss.MAPPING_HEADER])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))
    client.append_mapping_row("sid", "主翼班", "wing")
    assert ws.appended == [["主翼班", "wing", "", ""]]


def test_client_read_grid_and_titles():
    ws1 = FakeWorksheet("主桁", values=[["層番号"], ["1"]])
    ws2 = FakeWorksheet("桁マスタ", values=[["桁名", "目標層数"]])
    book = FakeBook([ws1, ws2])
    client = pss.ProgressSheetClient(client=FakeClient(book))
    assert client.read_grid("sid", "主桁")[1] == ["1"]
    assert set(client.list_sheet_titles("sid")) == {"主桁", "桁マスタ"}


def test_setup_book_creates_sheets_idempotently():
    book = FakeBook()
    client = pss.ProgressSheetClient(client=FakeClient(book))

    created = client.setup_book("sid")
    assert created == {pss.PROGRESS_SHEET: True, pss.MAPPING_SHEET: True,
                       pss.SETTINGS_SHEET: True, pss.SPAR_MAPPING_SHEET: True,
                       pss.DASHBOARD_SHEET: True}
    # ヘッダーが書かれている
    assert book.sheets[pss.PROGRESS_SHEET].updates == [[pss.PROGRESS_HEADER]]
    assert book.sheets[pss.MAPPING_SHEET].updates == [[pss.MAPPING_HEADER]]
    # 設定タブには初期キーが投入される
    settings_rows = book.sheets[pss.SETTINGS_SHEET].updates[0]
    assert settings_rows[0] == pss.SETTINGS_HEADER
    keys = [r[0] for r in settings_rows[1:]]
    assert pss.SHEET_KEY_DEFAULT_CHANNEL in keys
    assert pss.SHEET_KEY_SPAR_BOOK in keys
    assert (book.sheets[pss.SPAR_MAPPING_SHEET].updates
            == [[pss.SPAR_MAPPING_HEADER]])
    # 条件付き書式は1回だけ
    assert len(book.format_batches) == 1
    rule = book.format_batches[0]["requests"][0]["addConditionalFormatRule"]
    assert (rule["rule"]["ranges"][0]["sheetId"]
            == book.sheets[pss.PROGRESS_SHEET].id)
    # ダッシュボード数式が設置されている
    assert any(pss.DASHBOARD_SHEET in d["range"]
               for batch in book.value_batches for d in batch["data"])

    # 2回目: 既存シートを作り直さず、書式・数式も重複しない
    created2 = client.setup_book("sid")
    assert created2 == {pss.PROGRESS_SHEET: False, pss.MAPPING_SHEET: False,
                        pss.SETTINGS_SHEET: False,
                        pss.SPAR_MAPPING_SHEET: False,
                        pss.DASHBOARD_SHEET: False}
    assert len(book.format_batches) == 1
    assert len(book.sheets[pss.PROGRESS_SHEET].updates) == 1


def _mapping_header_batches(book):
    return [d for batch in book.value_batches for d in batch["data"]
            if d["range"] == f"'{pss.MAPPING_SHEET}'!A1:D1"]


def test_setup_book_extends_legacy_mapping_header():
    """旧3列の Todoist対応表に「登録ギルドID」ヘッダーだけを追記する。"""
    legacy_header = pss.MAPPING_HEADER[:3]
    ws = FakeWorksheet(pss.MAPPING_SHEET,
                       values=[legacy_header, ["主翼班", "wing", "999"]])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))

    client.setup_book("sid")
    batches = _mapping_header_batches(book)
    assert batches == [{"range": f"'{pss.MAPPING_SHEET}'!A1:D1",
                        "values": [pss.MAPPING_HEADER]}]
    # データ行には触れない（append も update もされない）
    assert ws.appended == []
    assert ws.updates == []


def test_setup_book_keeps_current_mapping_header():
    """4列ヘッダー済みのシートにはヘッダーを書き直さない。"""
    ws = FakeWorksheet(pss.MAPPING_SHEET, values=[list(pss.MAPPING_HEADER)])
    book = FakeBook([ws])
    client = pss.ProgressSheetClient(client=FakeClient(book))

    client.setup_book("sid")
    assert _mapping_header_batches(book) == []


def test_client_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    client = pss.ProgressSheetClient()
    try:
        client.read_progress_grid("sid")
        raise AssertionError("ProgressSheetUnavailable が送出されるべき")
    except pss.ProgressSheetUnavailable:
        pass
