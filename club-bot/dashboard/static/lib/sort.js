// ソート状態遷移の純粋関数（D1-3。DOM に触れない。テスト: tests_js/sort.test.mjs）。
//
// ヘッダをクリックするたびに 昇順 → 降順 → 既定（サーバーの order_by）を巡回する。

export function nextSortState(current, column) {
  if (current.sort !== column) return { sort: column, dir: "asc" };
  if (current.dir === "asc") return { sort: column, dir: "desc" };
  return { sort: null, dir: null };
}

// th の aria-sort 属性値（"ascending" | "descending" | "none"）
export function ariaSort(current, column) {
  if (current.sort !== column) return "none";
  return current.dir === "asc" ? "ascending" : "descending";
}
