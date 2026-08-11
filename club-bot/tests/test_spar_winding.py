"""spar_winding_service の純粋関数のユニットテスト。

DB ベースの反映処理（sync_spar_winding_db / plan_spar_sync）は
tests/test_progress_sync_db.py で検証する。ここでは移行時のみ使う
旧「桁マスタ」パーサと、進捗率 → 状態の導出を確認する。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import spar_winding_service as spar


# ---------------------------------------------------------------------
# 旧・桁巻きブックの解釈（移行スクリプトが使用）
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


def test_progress_status():
    assert spar.progress_status(0.0) == "未着手"
    assert spar.progress_status(0.5) == "製作中"
    assert spar.progress_status(1.0) == "完了"


# ---------------------------------------------------------------------
# 更新計画（純粋関数）
# ---------------------------------------------------------------------
def test_plan_spar_sync_computes_ratio():
    plan = spar.plan_spar_sync(
        {"spar1", "spar2"},
        [{"keta_name": "主桁1", "node_id": "spar1", "target_layers": 4},
         {"keta_name": "主桁2", "node_id": "spar2", "target_layers": 10}],
        {"主桁1": 3, "主桁2": 0})
    assert plan.updates == [("spar1", 0.75), ("spar2", 0.0)]
    assert plan.errors == []


def test_plan_spar_sync_clamps_and_reports_missing_node():
    plan = spar.plan_spar_sync(
        {"spar1"},
        [{"keta_name": "主桁1", "node_id": "spar1", "target_layers": 2},
         {"keta_name": "主桁2", "node_id": "nope", "target_layers": 5}],
        {"主桁1": 9})
    assert plan.updates == [("spar1", 1.0)]   # 目標超過は 100% でクランプ
    assert any("nope" in e for e in plan.errors)
