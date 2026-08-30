// 表 API のクエリ組み立て（D1-2。DOM に触れない。テスト: tests_js/query.test.mjs）。
//
// 一覧（limit / offset あり）と CSV（絞り込みだけ）の両方で使い、
// 画面と CSV の中身がずれないようにする。
export function buildTableQuery({
  limit = null, offset = null, sheet = null, q = "", sort = null, dir = null,
} = {}) {
  const params = new URLSearchParams();
  if (limit !== null && limit !== undefined) params.set("limit", String(limit));
  if (offset !== null && offset !== undefined) params.set("offset", String(offset));
  if (sheet !== null && sheet !== undefined) params.set("sheet", sheet);
  if (q) params.set("q", q);
  if (sort) {
    params.set("sort", sort);
    if (dir) params.set("dir", dir);
  }
  return params.toString();
}
