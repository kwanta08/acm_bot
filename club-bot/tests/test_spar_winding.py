"""spar_winding_service（桁巻き → 進捗ツリー反映）のユニットテスト。"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import progress_sheet_service as pss  # noqa: E402
from services import spar_winding_service as spar  # noqa: E402

HEADER = pss.PROGRESS_HEADER


def run(coro):
    return asyncio.run(coro)


def _row(node_id="", parent="", order="", depth="", name="", assignee="",
         status="", manual="", agg="", bar="", source="", td_id="",
         updated=""):
    return [node_id, parent, order, depth, name, assignee, status,
            manual, agg, bar, source, td_id, updated]


# ---------------------------------------------------------------------
# 桁巻きブックの解釈（純粋関数）
# ---------------------------------------------------------------------
def test_parse_master_grid():
    grid = [
        spar.SPAR_MASTER_HEADER,
        ["主桁", "20"],
        ["後桁", "0"],       # 0 は無効
        ["", "10"],          # 桁名なしはスキップ
        ["尾桁", "abc"],     # 数値でない
        ["補助桁", "8"],
    ]
    assert spar.parse_master_grid(grid) == {"主桁": 20, "補助桁": 8}


def test_count_completed_layers():
    grid = [
        spar.SPAR_LAYER_HEADER,
        ["1", "山田", "10:00", "11:00", "60"],
        ["2", "山田", "12:00", "", ""],       # 終了未記入 → 未完了
        ["3", "佐藤", "13:00", "14:00", "60"],
        ["1", "田中", "15:00", "16:00", "60"],  # 巻き直し → 重複カウントしない
    ]
    assert spar.count_completed_layers(grid) == 2


def test_progress_status():
    assert spar.progress_status(0.0) == "未着手"
    assert spar.progress_status(0.5) == "製作中"
    assert spar.progress_status(1.0) == "完了"


def test_plan_spar_updates():
    grid = [
        HEADER,
        _row("wing", "", "1", "", "主翼"),
        _row("spar1", "wing", "1", "", "主桁", manual="0.1",
             source="manual"),
        _row("spar2", "wing", "2", "", "尾桁",
             source=pss.SOURCE_SPAR_WINDING),
    ]
    nodes = pss.grid_to_nodes(grid)
    mappings = [
        {"spar_key": "主桁", "node_id": "spar1"},
        {"spar_key": "尾桁", "node_id": "spar2"},
        {"spar_key": "幻の桁", "node_id": "ghost"},   # ノード不在
    ]
    plan = spar.plan_spar_updates(
        nodes, mappings, {"主桁": 0.5, "尾桁": 1.0})
    assert plan.updated == 2
    ranges = {r["range"]: r["values"] for r in plan.cell_ranges}
    # 進捗率と状態（G:H）
    assert ranges[f"'{pss.PROGRESS_SHEET}'!G3:H3"] == [["製作中", 0.5]]
    assert ranges[f"'{pss.PROGRESS_SHEET}'!G4:H4"] == [["完了", 1.0]]
    # ソースは manual → spar_winding へ更新。既に spar_winding の行は触らない
    assert ranges[f"'{pss.PROGRESS_SHEET}'!K3"] == [["spar_winding"]]
    assert f"'{pss.PROGRESS_SHEET}'!K4" not in ranges
    assert any("ghost" in e for e in plan.errors)


# ---------------------------------------------------------------------
# オーケストレーション（フェイク client）
# ---------------------------------------------------------------------
class FakeClient:
    def __init__(self, central: dict[str, list], spar_book: dict[str, list]):
        self.central = central       # シート名 → グリッド
        self.spar_book = spar_book
        self.applied: list = []

    def read_progress_grid(self, spreadsheet_id):
        return self.central[pss.PROGRESS_SHEET]

    def read_spar_mapping_grid(self, spreadsheet_id):
        return self.central[pss.SPAR_MAPPING_SHEET]

    def read_grid(self, spreadsheet_id, sheet_title):
        assert spreadsheet_id == "SPAR_BOOK"
        return self.spar_book[sheet_title]   # 不在なら KeyError

    def apply_value_ranges(self, spreadsheet_id, ranges):
        self.applied.append(ranges)


def _central(spar_mapping_rows):
    return {
        pss.PROGRESS_SHEET: [
            HEADER,
            _row("wing", "", "1", "", "主翼"),
            _row("spar1", "wing", "1", "", "主桁"),
        ],
        pss.SPAR_MAPPING_SHEET: [pss.SPAR_MAPPING_HEADER, *spar_mapping_rows],
    }


def test_sync_spar_winding_full_flow():
    client = FakeClient(
        _central([["主桁", "spar1"]]),
        {
            spar.SPAR_MASTER_SHEET: [spar.SPAR_MASTER_HEADER, ["主桁", "4"]],
            "主桁": [
                spar.SPAR_LAYER_HEADER,
                ["1", "山田", "10:00", "11:00", "60"],
                ["2", "山田", "12:00", "13:00", "60"],
            ],
        })
    settings = {pss.SHEET_KEY_SPAR_BOOK: "SPAR_BOOK"}
    plan = run(spar.sync_spar_winding(client, "CENTRAL", settings))
    assert plan.updated == 1
    assert not plan.errors
    ranges = {r["range"]: r["values"] for r in client.applied[0]}
    assert ranges[f"'{pss.PROGRESS_SHEET}'!G3:H3"] == [["製作中", 0.5]]


def test_sync_spar_winding_skips_without_book_id():
    client = FakeClient(_central([["主桁", "spar1"]]), {})
    plan = run(spar.sync_spar_winding(client, "CENTRAL", {}))
    assert plan.updated == 0
    assert client.applied == []


def test_sync_spar_winding_reports_missing_master_and_sheet():
    client = FakeClient(
        _central([["主桁", "spar1"], ["未知の桁", "spar1"]]),
        {
            spar.SPAR_MASTER_SHEET: [spar.SPAR_MASTER_HEADER, ["主桁", "4"]],
            # 「主桁」の桁別シートが無い
        })
    settings = {pss.SHEET_KEY_SPAR_BOOK: "SPAR_BOOK"}
    plan = run(spar.sync_spar_winding(client, "CENTRAL", settings))
    assert plan.updated == 0
    assert any("主桁" in e and "桁別シート" in e for e in plan.errors)
    assert any("未知の桁" in e and "目標層数" in e for e in plan.errors)


def test_sync_spar_winding_caps_progress_at_100_percent():
    client = FakeClient(
        _central([["主桁", "spar1"]]),
        {
            spar.SPAR_MASTER_SHEET: [spar.SPAR_MASTER_HEADER, ["主桁", "1"]],
            "主桁": [
                spar.SPAR_LAYER_HEADER,
                ["1", "山田", "10:00", "11:00", "60"],
                ["2", "山田", "12:00", "13:00", "60"],  # 目標超過
            ],
        })
    settings = {pss.SHEET_KEY_SPAR_BOOK: "SPAR_BOOK"}
    plan = run(spar.sync_spar_winding(client, "CENTRAL", settings))
    ranges = {r["range"]: r["values"] for r in client.applied[0]}
    assert ranges[f"'{pss.PROGRESS_SHEET}'!G3:H3"] == [["完了", 1.0]]
