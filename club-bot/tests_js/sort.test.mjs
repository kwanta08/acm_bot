// dashboard/static/lib/sort.js（ソート状態遷移の純粋関数）のテスト（D1-3）。
import test from "node:test";
import assert from "node:assert/strict";

import { nextSortState, ariaSort } from "../dashboard/static/lib/sort.js";

test("nextSortState: 未ソート列をクリックすると昇順", () => {
  assert.deepEqual(nextSortState({ sort: null, dir: null }, "title"),
    { sort: "title", dir: "asc" });
  // 他の列でソート中でも、新しい列は昇順から
  assert.deepEqual(nextSortState({ sort: "status", dir: "desc" }, "title"),
    { sort: "title", dir: "asc" });
});

test("nextSortState: 昇順 → 降順 → 既定 の3状態を巡回する", () => {
  const s1 = nextSortState({ sort: null, dir: null }, "title");
  assert.deepEqual(s1, { sort: "title", dir: "asc" });
  const s2 = nextSortState(s1, "title");
  assert.deepEqual(s2, { sort: "title", dir: "desc" });
  const s3 = nextSortState(s2, "title");
  assert.deepEqual(s3, { sort: null, dir: null });
});

test("ariaSort: 現在の状態を aria-sort 値へ変換する", () => {
  assert.equal(ariaSort({ sort: "title", dir: "asc" }, "title"), "ascending");
  assert.equal(ariaSort({ sort: "title", dir: "desc" }, "title"), "descending");
  assert.equal(ariaSort({ sort: "title", dir: "asc" }, "status"), "none");
  assert.equal(ariaSort({ sort: null, dir: null }, "title"), "none");
});
