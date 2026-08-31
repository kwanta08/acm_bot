// セル編集の確定・キャンセル判定（D1-4。DOM に触れない純粋関数）。
//
// 確定は Enter キーと ✓ ボタンだけ。blur は**キャンセル**（値を戻す）。
// モバイルには Escape キーが無いため、blur で保存すると誤タッチを
// 取り消せない（P1-12）。
//
// 注意: ✓ を押す動作そのものが input の blur を起こす。呼び出し側は
// blur イベントの relatedTarget が編集 UI（✓/✕）の中かどうかを
// `toEditorControl` として渡すこと。true のときは blur では何もしない
// （ボタン側の click が commit / cancel を発行する）。

export function editAction(ev) {
  if (ev.type === "keydown") {
    if (ev.key === "Enter") return "commit";
    if (ev.key === "Escape") return "cancel";
    return "none";
  }
  if (ev.type === "commit-button") return "commit";
  if (ev.type === "cancel-button") return "cancel";
  if (ev.type === "blur") return ev.toEditorControl ? "none" : "cancel";
  return "none";
}
