// サマリーカードの組み立て（D2-3。DOM に触れない。テスト: tests_js/summary.test.mjs）。
//
// `/summary` が失敗しても表の表示を妨げない: null を渡すと全カードが「—」になる。

export function summaryCards(summary) {
  const counts = (summary && summary.counts) || {};
  const viewer = (summary && summary.viewer) || null;
  const dash = (v) => (v === null || v === undefined ? "—" : String(v));
  const level = viewer
    ? `L${viewer.level}${viewer.manage_guild ? "（サーバー管理）" : ""}`
    : "—";
  return [
    { label: "メンバー", value: dash(counts.members) },
    { label: "班", value: dash(counts.teams) },
    { label: "進捗ノード", value: dash(counts.progress_nodes) },
    { label: "未完了タスク", value: dash(counts.open_tasks) },
    { label: "自分の権限", value: level },
  ];
}
