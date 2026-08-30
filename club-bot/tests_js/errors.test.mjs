// dashboard/static/lib/errors.js（status → 表示分岐）のテスト（D1-6）。
import test from "node:test";
import assert from "node:assert/strict";

import { errorDisposition } from "../dashboard/static/lib/errors.js";

test("errorDisposition: 401 だけが全画面のログイン導線へ落ちる", () => {
  assert.equal(errorDisposition(401), "login");
});

test("errorDisposition: 401 以外はその場に留める（既存動作の維持）", () => {
  assert.equal(errorDisposition(403), "stay");
  assert.equal(errorDisposition(404), "stay");
  assert.equal(errorDisposition(500), "stay");
  assert.equal(errorDisposition(400), "stay");
});

test("errorDisposition: ネットワークエラー（status なし）もその場に留める", () => {
  assert.equal(errorDisposition(undefined), "stay");
  assert.equal(errorDisposition(null), "stay");
});
