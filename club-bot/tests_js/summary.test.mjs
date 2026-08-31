// dashboard/static/lib/summary.js（サマリーカードの組み立て）のテスト（D2-3）。
import test from "node:test";
import assert from "node:assert/strict";

import { summaryCards } from "../dashboard/static/lib/summary.js";

test("summaryCards: 件数4枚＋権限1枚を組み立てる", () => {
  const cards = summaryCards({
    guild: { id: "1", name: "A大学" },
    viewer: { level: 4, manage_guild: true, can_edit: true },
    counts: { members: 24, teams: 5, progress_nodes: 120, open_tasks: 7 },
  });
  assert.deepEqual(cards, [
    { label: "メンバー", value: "24" },
    { label: "班", value: "5" },
    { label: "進捗ノード", value: "120" },
    { label: "未完了タスク", value: "7" },
    { label: "自分の権限", value: "L4（サーバー管理）" },
  ]);
});

test("summaryCards: 管理権限なしはレベルだけ", () => {
  const cards = summaryCards({
    viewer: { level: 2, manage_guild: false, can_edit: true },
    counts: { members: 1, teams: 0, progress_nodes: 0, open_tasks: 0 },
  });
  assert.equal(cards[4].value, "L2");
  assert.equal(cards[1].value, "0");
});

test("summaryCards: 取得失敗（null）では全カード —（表の表示を妨げない）", () => {
  const cards = summaryCards(null);
  assert.equal(cards.length, 5);
  for (const card of cards) assert.equal(card.value, "—");
});
