"""progress_sheet_service（進捗シート読み書き）のユニットテスト。

gspread は使わず、フェイクの book / client を注入して検証する。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import progress_sheet_service as pss

HEADER = pss.PROGRESS_HEADER


def _row(
    node_id="",
    parent="",
    order="",
    depth="",
    name="",
    assignee="",
    status="",
    manual="",
    agg="",
    bar="",
    source="",
    td_id="",
    updated="",
):
    return [
        node_id,
        parent,
        order,
        depth,
        name,
        assignee,
        status,
        manual,
        agg,
        bar,
        source,
        td_id,
        updated,
    ]


# ---------------------------------------------------------------------
# grid_to_nodes
# ---------------------------------------------------------------------
def test_grid_to_nodes_basic():
    grid = [
        HEADER,
        _row("m1", "", "1", "", "本機", "", "", "", "", "", "manual"),
        _row("p1", "m1", "1", "", "主翼", "山田", "製作中", "50%", "", "", "manual"),
        _row("td_100", "p1", "2", "", "リブ", "", "", "1", "", "", "todoist", "100"),
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
# parse_mapping_grid
# ---------------------------------------------------------------------
def test_parse_mapping_grid():
    grid = [
        pss.MAPPING_HEADER,
        ["主翼班", "p1", "111", "222"],
        ["", "x"],  # 名前欠落はスキップ
        ["尾翼班", ""],  # ノード ID 欠落はスキップ
        ["電装班", "p2"],  # 旧2列形式（通知チャンネル・ギルド列なし）も読める
        ["桁班", "p3", "333"],  # 旧3列形式（ギルド列なし）も読める
    ]
    mapping = pss.parse_mapping_grid(grid)
    assert mapping == [
        {"project_name": "主翼班", "node_id": "p1", "notify_channel_id": "111", "guild_id": "222"},
        {"project_name": "電装班", "node_id": "p2", "notify_channel_id": "", "guild_id": ""},
        {"project_name": "桁班", "node_id": "p3", "notify_channel_id": "333", "guild_id": ""},
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


def test_client_read_grid_and_titles():
    ws1 = FakeWorksheet("主桁", values=[["層番号"], ["1"]])
    ws2 = FakeWorksheet("桁マスタ", values=[["桁名", "目標層数"]])
    book = FakeBook([ws1, ws2])
    client = pss.ProgressSheetClient(client=FakeClient(book))
    assert client.read_grid("sid", "主桁")[1] == ["1"]
    assert set(client.list_sheet_titles("sid")) == {"主桁", "桁マスタ"}


def test_client_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    client = pss.ProgressSheetClient()
    try:
        client.read_progress_grid("sid")
        raise AssertionError("ProgressSheetUnavailable が送出されるべき")
    except pss.ProgressSheetUnavailable:
        pass
