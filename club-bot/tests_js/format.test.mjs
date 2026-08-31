// dashboard/static/lib/format.js（表示整形・入力解釈の純粋関数）のテスト。
//
// 実行: club-bot/ で `node --test tests_js/`
// npm パッケージは使わない（Node 標準の node:test / node:assert のみ。D0-2）。
// テストを static/ の外に置くのは、static/ が丸ごと認証なしで
// 配信されるため（dashboard/main.py の StaticFiles マウント）。
import test from "node:test";
import assert from "node:assert/strict";

import { formatCell, parseInput } from "../dashboard/static/lib/format.js";

// ---------------------------------------------------------------- formatCell
test("formatCell: _display の解決値が最優先される", () => {
  const column = { name: "assignee_id", type: "user" };
  const row = { assignee_id: "42", _display: { assignee_id: "山田" } };
  assert.equal(formatCell("42", column, row), "山田");
});

test("formatCell: 解決値が空文字なら —（副所属班が空配列のケース）", () => {
  const column = { name: "secondary_teams", type: "team_list" };
  const row = { secondary_teams: "[]", _display: { secondary_teams: "" } };
  assert.equal(formatCell("[]", column, row), "—");
});

test("formatCell: null / undefined / 空文字は —", () => {
  const column = { name: "note", type: "text" };
  assert.equal(formatCell(null, column, { note: null }), "—");
  assert.equal(formatCell(undefined, column, {}), "—");
  assert.equal(formatCell("", column, { note: "" }), "—");
});

test("formatCell: bool は はい / いいえ", () => {
  const column = { name: "active_flag", type: "bool" };
  assert.equal(formatCell(1, column, { active_flag: 1 }), "はい");
  assert.equal(formatCell(0, column, { active_flag: 0 }), "いいえ");
});

test("formatCell: progress は百分率へ丸める", () => {
  const column = { name: "manual_progress", type: "progress" };
  assert.equal(formatCell(0.5, column, { manual_progress: 0.5 }), "50%");
  assert.equal(formatCell(0.333, column, { manual_progress: 0.333 }), "33%");
});

test("formatCell: progress に数値でない値が入っていても落ちない", () => {
  const column = { name: "manual_progress", type: "progress" };
  assert.equal(formatCell("abc", column, { manual_progress: "abc" }), "abc");
});

test("formatCell: text はそのまま文字列化", () => {
  const column = { name: "title", type: "text" };
  assert.equal(formatCell("主翼", column, { title: "主翼" }), "主翼");
  assert.equal(formatCell(5, column, { title: 5 }), "5");
});

// ---------------------------------------------------------------- parseInput
test("parseInput: 空文字は null（クリア）", () => {
  assert.equal(parseInput("", { type: "text" }), null);
});

test("parseInput: number は数値へ、数値でなければ例外", () => {
  assert.equal(parseInput("42", { type: "number" }), 42);
  assert.equal(parseInput("-1.5", { type: "number" }), -1.5);
  assert.throws(() => parseInput("abc", { type: "number" }));
});

test("parseInput: bool は 1 / true / はい を真とする", () => {
  assert.equal(parseInput("1", { type: "bool" }), true);
  assert.equal(parseInput("true", { type: "bool" }), true);
  assert.equal(parseInput("はい", { type: "bool" }), true);
  assert.equal(parseInput("0", { type: "bool" }), false);
  assert.equal(parseInput("いいえ", { type: "bool" }), false);
});

test("parseInput: progress は 0.5 / 50% / 50％ を 0..1 へ", () => {
  assert.equal(parseInput("0.5", { type: "progress" }), 0.5);
  assert.equal(parseInput("50%", { type: "progress" }), 0.5);
  assert.equal(parseInput("50％", { type: "progress" }), 0.5);
  // % なしの 1 超は百分率と解釈する
  assert.equal(parseInput("80", { type: "progress" }), 0.8);
});

test("parseInput: progress は 0..1 へクランプし、数値でなければ例外", () => {
  assert.equal(parseInput("150%", { type: "progress" }), 1);
  assert.equal(parseInput("-10%", { type: "progress" }), 0);
  assert.throws(() => parseInput("abc", { type: "progress" }));
});

test("parseInput: text はそのまま返す", () => {
  assert.equal(parseInput("主翼", { type: "text" }), "主翼");
});
