// ダッシュボードのフロント（依存なしの素の JS。外部 CDN を読み込まない）
"use strict";

const app = document.getElementById("app");

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.appendChild(child);
  }
  return node;
}

function showError(message) {
  app.replaceChildren(el("p", { class: "error", text: message }));
}

async function main() {
  let health;
  try {
    const res = await fetch("/healthz");
    health = await res.json();
  } catch (e) {
    showError("サーバーに接続できませんでした。");
    return;
  }
  app.replaceChildren(
    el("p", { text: `ダッシュボードは起動しています（status: ${health.status}）。` }),
    el("p", {
      class: "loading",
      text: "ログインと表グリッドはこの後の実装で追加されます。",
    }),
  );
}

main();
