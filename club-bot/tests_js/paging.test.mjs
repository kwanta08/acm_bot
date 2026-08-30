// dashboard/static/lib/paging.js（ページ計算の純粋関数）のテスト（D1-1）。
import test from "node:test";
import assert from "node:assert/strict";

import { PAGE_SIZES, pageInfo } from "../dashboard/static/lib/paging.js";

test("PAGE_SIZES: 50 / 100 / 200 / 500 から選べる", () => {
  assert.deepEqual(PAGE_SIZES, [50, 100, 200, 500]);
});

test("pageInfo: 先頭ページでは前へが無効", () => {
  const info = pageInfo({ total: 3000, limit: 200, offset: 0, count: 200 });
  assert.equal(info.hasPrev, false);
  assert.equal(info.hasNext, true);
  assert.equal(info.rangeText, "1〜200 件 / 全 3000 件");
  assert.equal(info.nextOffset, 200);
});

test("pageInfo: 中間ページでは両方向に動ける", () => {
  const info = pageInfo({ total: 3000, limit: 200, offset: 400, count: 200 });
  assert.equal(info.hasPrev, true);
  assert.equal(info.hasNext, true);
  assert.equal(info.rangeText, "401〜600 件 / 全 3000 件");
  assert.equal(info.prevOffset, 200);
  assert.equal(info.nextOffset, 600);
});

test("pageInfo: 末尾ページでは次へが無効", () => {
  const info = pageInfo({ total: 450, limit: 200, offset: 400, count: 50 });
  assert.equal(info.hasPrev, true);
  assert.equal(info.hasNext, false);
  assert.equal(info.rangeText, "401〜450 件 / 全 450 件");
});

test("pageInfo: ちょうど割り切れる末尾でも次へが無効", () => {
  const info = pageInfo({ total: 400, limit: 200, offset: 200, count: 200 });
  assert.equal(info.hasNext, false);
});

test("pageInfo: 0 件は 0 件表示で両方向とも無効", () => {
  const info = pageInfo({ total: 0, limit: 200, offset: 0, count: 0 });
  assert.equal(info.hasPrev, false);
  assert.equal(info.hasNext, false);
  assert.equal(info.rangeText, "0 件");
});

test("pageInfo: 前へは 0 を下回らない", () => {
  const info = pageInfo({ total: 500, limit: 200, offset: 100, count: 200 });
  assert.equal(info.prevOffset, 0);
});
