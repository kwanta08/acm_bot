// dashboard/static/lib/settings.js（設定フォームの差分計算）のテスト（D2-2）。
import test from "node:test";
import assert from "node:assert/strict";

import { settingsDiff } from "../dashboard/static/lib/settings.js";

test("settingsDiff: 変更された項目だけを返す", () => {
  const original = { CLUB_NAME: "鳥人研", BOT_LOG_CHANNEL_ID: "123" };
  const current = { CLUB_NAME: "新鳥人研", BOT_LOG_CHANNEL_ID: "123" };
  assert.deepEqual(settingsDiff(original, current), { CLUB_NAME: "新鳥人研" });
});

test("settingsDiff: 変更なしなら空オブジェクト", () => {
  const values = { CLUB_NAME: "鳥人研" };
  assert.deepEqual(settingsDiff(values, { ...values }), {});
});

test("settingsDiff: 空文字への変更（削除）も差分になる", () => {
  assert.deepEqual(settingsDiff({ CLUB_NAME: "鳥人研" }, { CLUB_NAME: "" }), { CLUB_NAME: "" });
});

test("settingsDiff: 未設定（undefined/null）と空文字は同じ扱い", () => {
  assert.deepEqual(settingsDiff({ CLUB_NAME: null }, { CLUB_NAME: "" }), {});
  assert.deepEqual(settingsDiff({}, { CLUB_NAME: "" }), {});
});
