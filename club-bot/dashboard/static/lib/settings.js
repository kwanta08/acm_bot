// 設定フォームの差分計算（D2-2。DOM に触れない。テスト: tests_js/settings.test.mjs）。
//
// 保存は差分だけを PATCH する（変更していない項目を送らない）。
// 未設定（null / undefined）と空文字は「値なし」として同一視する。

export function settingsDiff(original, current) {
  const out = {};
  for (const [key, value] of Object.entries(current)) {
    if ((original[key] ?? "") !== value) out[key] = value;
  }
  return out;
}
