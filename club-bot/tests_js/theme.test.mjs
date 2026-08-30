// dashboard/static/lib/theme.js（テーマ解決）のテスト（D3-4）。
//
// 「保存値 × OS 設定 → 適用テーマ」の表:
//   保存値 system(null) × OS ライト → light / OS ダーク → dark
//   保存値 light        × どちらでも → light
//   保存値 dark         × どちらでも → dark
import test from "node:test";
import assert from "node:assert/strict";

import {
  readStoredTheme,
  resolveTheme,
  storeTheme,
  themeAttribute,
} from "../dashboard/static/lib/theme.js";

test("resolveTheme: 保存値 × OS 設定 → 適用テーマの表", () => {
  assert.equal(resolveTheme(null, false), "light");
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme("light", false), "light");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("dark", true), "dark");
  // 壊れた保存値は system 扱い
  assert.equal(resolveTheme("banana", true), "dark");
  assert.equal(resolveTheme(undefined, false), "light");
});

test("themeAttribute: system では属性を付けない（null）", () => {
  assert.equal(themeAttribute(null), null);
  assert.equal(themeAttribute("banana"), null);
  assert.equal(themeAttribute("light"), "light");
  assert.equal(themeAttribute("dark"), "dark");
});

test("readStoredTheme: localStorage が例外を投げても system として続行する", () => {
  const throwing = {
    getItem() { throw new Error("SecurityError"); },
  };
  assert.equal(readStoredTheme(throwing), null);
  assert.equal(readStoredTheme(null), null);
});

test("readStoredTheme: 保存値を返す", () => {
  const storage = { getItem: () => "dark" };
  assert.equal(readStoredTheme(storage), "dark");
});

test("storeTheme: 書き込み失敗でも例外を漏らさない", () => {
  const throwing = {
    setItem() { throw new Error("QuotaExceededError"); },
    removeItem() { throw new Error("SecurityError"); },
  };
  assert.doesNotThrow(() => storeTheme(throwing, "dark"));
  assert.doesNotThrow(() => storeTheme(throwing, null));
  assert.doesNotThrow(() => storeTheme(null, "dark"));
});

test("storeTheme: system(null) は保存値を消す", () => {
  const calls = [];
  const storage = {
    setItem: (k, v) => calls.push(["set", k, v]),
    removeItem: (k) => calls.push(["remove", k]),
  };
  storeTheme(storage, "dark");
  storeTheme(storage, null);
  assert.deepEqual(calls[0][0], "set");
  assert.deepEqual(calls[1][0], "remove");
});
