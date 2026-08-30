// テーマ切替（D3-4。DOM に触れない。テスト: tests_js/theme.test.mjs）。
//
// 3状態: system（保存なし・属性なし。prefers-color-scheme に従う）/
// light / dark（`<html data-theme="...">` を立てる）。
// **localStorage は例外を投げる環境がある**（プライベートウィンドウ、
// サイトデータ拒否）ため、読み書きは必ず try/catch で包み、
// 値が取れなくても system として正しく描画する。

const STORAGE_KEY = "clubbot-dashboard-theme";

// 適用テーマ（表示上どちらになるか）。壊れた保存値は system 扱い
export function resolveTheme(stored, systemDark) {
  if (stored === "light" || stored === "dark") return stored;
  return systemDark ? "dark" : "light";
}

// `<html data-theme>` に立てる値。system では属性を付けない（null）
export function themeAttribute(stored) {
  return stored === "light" || stored === "dark" ? stored : null;
}

export function readStoredTheme(storage) {
  try {
    return storage ? storage.getItem(STORAGE_KEY) : null;
  } catch (_) {
    return null;
  }
}

export function storeTheme(storage, value) {
  try {
    if (!storage) return;
    if (value === null) storage.removeItem(STORAGE_KEY);
    else storage.setItem(STORAGE_KEY, value);
  } catch (_) { /* 保存できなくても動作は継続する */ }
}
