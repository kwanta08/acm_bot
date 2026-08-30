// dashboard/static/lib/edit.js（セル編集の確定・キャンセル判定）のテスト（D1-4）。
//
// 確定は Enter と ✓ ボタンだけ。blur は**キャンセル**（値を戻す）。
// ただしフォーカス移動先が ✓/✕ ボタン自身のときは blur では何もしない
// （ここを外すと「✓ を押すと取り消される」になる）。
import test from "node:test";
import assert from "node:assert/strict";

import { editAction } from "../dashboard/static/lib/edit.js";

test("editAction: Enter は確定、Escape はキャンセル、他キーは何もしない", () => {
  assert.equal(editAction({ type: "keydown", key: "Enter" }), "commit");
  assert.equal(editAction({ type: "keydown", key: "Escape" }), "cancel");
  assert.equal(editAction({ type: "keydown", key: "a" }), "none");
  assert.equal(editAction({ type: "keydown", key: "Tab" }), "none");
});

test("editAction: ✓ ボタンは確定、✕ ボタンはキャンセル", () => {
  assert.equal(editAction({ type: "commit-button" }), "commit");
  assert.equal(editAction({ type: "cancel-button" }), "cancel");
});

test("editAction: blur はキャンセル（誤タッチを保存しない）", () => {
  assert.equal(editAction({ type: "blur", toEditorControl: false }), "cancel");
});

test("editAction: ✓/✕ へのフォーカス移動による blur では何もしない", () => {
  assert.equal(editAction({ type: "blur", toEditorControl: true }), "none");
});

test("editAction: 未知のイベントは何もしない", () => {
  assert.equal(editAction({ type: "mousemove" }), "none");
});
