// dashboard/static/lib/query.js（クエリ組み立ての純粋関数）のテスト（D1-2）。
import test from "node:test";
import assert from "node:assert/strict";

import { buildTableQuery } from "../dashboard/static/lib/query.js";

test("buildTableQuery: limit / offset を組み立てる", () => {
  assert.equal(buildTableQuery({ limit: 200, offset: 0 }), "limit=200&offset=0");
});

test("buildTableQuery: sheet と q を足す（エンコードされる）", () => {
  assert.equal(
    buildTableQuery({ limit: 50, offset: 100, sheet: "sch 1", q: "主翼" }),
    "limit=50&offset=100&sheet=sch+1&q=%E4%B8%BB%E7%BF%BC",
  );
});

test("buildTableQuery: 空の q / null の sheet は付けない", () => {
  assert.equal(buildTableQuery({ limit: 200, offset: 0, sheet: null, q: "" }), "limit=200&offset=0");
});

test("buildTableQuery: CSV 用（limit / offset なし）", () => {
  assert.equal(buildTableQuery({ sheet: "s1", q: "x" }), "sheet=s1&q=x");
  assert.equal(buildTableQuery({}), "");
});

test("buildTableQuery: sort / dir を足す（D1-3 で使用）", () => {
  assert.equal(
    buildTableQuery({ limit: 200, offset: 0, sort: "title", dir: "desc" }),
    "limit=200&offset=0&sort=title&dir=desc",
  );
  // sort が無ければ dir も付けない
  assert.equal(buildTableQuery({ limit: 200, offset: 0, dir: "desc" }), "limit=200&offset=0");
});
