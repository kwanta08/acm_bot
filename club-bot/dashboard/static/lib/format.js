// 表示整形・入力解釈の純粋関数（DOM に触れない。D0-2 で app.js から切り出し）。
//
// ここに置く関数はブラウザ（app.js が import）と Node の両方から使う。
// テストは club-bot/tests_js/ にあり、`node --test tests_js/` で検証する
// （static/ は認証なしで丸ごと配信されるため、テストは static/ の外に置く）。
// 外部 CDN・npm パッケージには依存しない。

export function formatCell(value, column, row) {
  // サーバー側の解決層（dashboard/display.py）が付けた表示があれば最優先
  // （ユーザー/チャンネル/候補/班の名前解決・JST 秒表示。生の ID や slug は出さない）。
  // 解決した表示が空文字（例: 副所属班が空の配列 `[]`）は生の値と同じく「—」にする
  const resolved = row && row._display ? row._display[column.name] : undefined;
  if (resolved !== undefined && resolved !== null) return resolved === "" ? "—" : resolved;
  if (value === null || value === undefined || value === "") return "—";
  if (column.type === "bool") return value ? "はい" : "いいえ";
  if (column.type === "progress") {
    const pct = Math.round(Number(value) * 100);
    return Number.isFinite(pct) ? `${pct}%` : String(value);
  }
  return String(value);
}

export function parseInput(raw, column) {
  if (raw === "") return null;
  if (column.type === "number") {
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error("数値を入力してください。");
    return n;
  }
  if (column.type === "bool") return raw === "1" || raw === "true" || raw === "はい";
  if (column.type === "progress") {
    const text = raw.trim().replace("％", "%");
    const n = Number(text.endsWith("%") ? text.slice(0, -1) : text);
    if (!Number.isFinite(n)) throw new Error("0.5 または 50% の形式で入力してください。");
    const value = text.endsWith("%") || n > 1 ? n / 100 : n;
    return Math.min(Math.max(value, 0), 1);
  }
  return raw;
}
