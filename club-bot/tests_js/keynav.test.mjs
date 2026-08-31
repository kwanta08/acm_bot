// dashboard/static/lib/keynav.js（キーボード操作の対応表）のテスト（D1-5）。
import test from "node:test";
import assert from "node:assert/strict";

import { gridKeyAction, nextCellPosition } from "../dashboard/static/lib/keynav.js";

test("gridKeyAction: Enter / Space は編集開始", () => {
  assert.equal(gridKeyAction("Enter"), "edit");
  assert.equal(gridKeyAction(" "), "edit");
});

test("gridKeyAction: 矢印キーは移動", () => {
  assert.equal(gridKeyAction("ArrowUp"), "move-up");
  assert.equal(gridKeyAction("ArrowDown"), "move-down");
  assert.equal(gridKeyAction("ArrowLeft"), "move-left");
  assert.equal(gridKeyAction("ArrowRight"), "move-right");
});

test("gridKeyAction: その他のキーは何もしない（Tab は素通し）", () => {
  assert.equal(gridKeyAction("Tab"), "none");
  assert.equal(gridKeyAction("a"), "none");
  assert.equal(gridKeyAction("Escape"), "none");
});

test("nextCellPosition: 隣接セルへ移動する", () => {
  const grid = { rows: 3, cols: 2 };
  assert.deepEqual(nextCellPosition({ row: 1, col: 1, ...grid }, "move-up"), { row: 0, col: 1 });
  assert.deepEqual(nextCellPosition({ row: 1, col: 1, ...grid }, "move-down"), { row: 2, col: 1 });
  assert.deepEqual(nextCellPosition({ row: 1, col: 1, ...grid }, "move-left"), { row: 1, col: 0 });
  assert.deepEqual(nextCellPosition({ row: 1, col: 0, ...grid }, "move-right"), { row: 1, col: 1 });
});

test("nextCellPosition: 端では動かない（巡回しない）", () => {
  const grid = { rows: 3, cols: 2 };
  assert.deepEqual(nextCellPosition({ row: 0, col: 0, ...grid }, "move-up"), { row: 0, col: 0 });
  assert.deepEqual(nextCellPosition({ row: 2, col: 1, ...grid }, "move-down"), { row: 2, col: 1 });
  assert.deepEqual(nextCellPosition({ row: 0, col: 0, ...grid }, "move-left"), { row: 0, col: 0 });
  assert.deepEqual(nextCellPosition({ row: 0, col: 1, ...grid }, "move-right"), { row: 0, col: 1 });
});
