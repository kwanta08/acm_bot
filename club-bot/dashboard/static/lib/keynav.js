// 表のキーボード操作（D1-5。DOM に触れない。テスト: tests_js/keynav.test.mjs）。
//
// Tab は表全体で1ストップ（roving tabindex: フォーカス中のセルだけ 0、
// 他は -1）。矢印キーで隣接する編集可能セルへ移動し、
// Enter / Space で編集を開始する。

export function gridKeyAction(key) {
  if (key === "Enter" || key === " ") return "edit";
  if (key === "ArrowUp") return "move-up";
  if (key === "ArrowDown") return "move-down";
  if (key === "ArrowLeft") return "move-left";
  if (key === "ArrowRight") return "move-right";
  return "none";
}

// 移動先の座標を返す（端では動かない。巡回しない）
export function nextCellPosition({ row, col, rows, cols }, action) {
  if (action === "move-up") return { row: Math.max(0, row - 1), col };
  if (action === "move-down") return { row: Math.min(rows - 1, row + 1), col };
  if (action === "move-left") return { row, col: Math.max(0, col - 1) };
  if (action === "move-right") return { row, col: Math.min(cols - 1, col + 1) };
  return { row, col };
}
