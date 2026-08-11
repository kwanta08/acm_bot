// ダッシュボードのフロント（依存なしの素の JS。外部 CDN を読み込まない）
"use strict";

const appEl = document.getElementById("app");
const accountEl = document.getElementById("account");

const state = {
  me: null,
  guildId: null,
  tables: [],
  tableKey: null,
  canEdit: false,
};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

async function api(path, options) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  if (!res.ok) {
    let detail = `エラーが発生しました (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) { /* JSON でない応答はそのまま */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function showError(message) {
  appEl.replaceChildren(el("p", { class: "error", text: message }));
}

// ---------------------------------------------------------------- 表示
function renderLoginPrompt() {
  accountEl.replaceChildren();
  appEl.replaceChildren(
    el("p", { text: "Discord でログインすると、自分が所属するサーバーのデータを表示できます。" }),
    el("p", {}, [
      el("a", { class: "button primary", href: "/auth/login", text: "Discord でログイン" }),
    ]),
  );
}

function renderAccount() {
  accountEl.replaceChildren(
    el("span", { text: state.me.user.name }),
    el("button", {
      text: "ログアウト",
      onclick: async () => {
        await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
        location.reload();
      },
    }),
  );
}

function renderGuildPicker() {
  const guilds = state.me.guilds;
  if (guilds.length === 0) {
    appEl.replaceChildren(
      el("p", { class: "empty", text: "この Bot が導入されているサーバーが見つかりませんでした。" }),
    );
    return;
  }
  const select = el("select", {
    onchange: (e) => selectGuild(e.target.value),
  }, guilds.map((g) =>
    el("option", { value: g.id, text: g.name, ...(g.id === state.guildId ? { selected: "selected" } : {}) })));

  appEl.replaceChildren(
    el("div", { class: "toolbar" }, [el("label", { text: "サーバー: " }), select]),
    el("div", { id: "tabs", class: "toolbar" }),
    el("div", { id: "grid" }),
  );
}

function renderTabs() {
  const tabs = document.getElementById("tabs");
  if (!tabs) return;
  tabs.replaceChildren(...state.tables.map((t) =>
    el("button", {
      class: t.key === state.tableKey ? "primary" : "",
      text: t.label,
      title: t.description || "",
      onclick: () => selectTable(t.key),
    })));
}

function formatCell(value, column) {
  if (value === null || value === undefined || value === "") return "—";
  if (column.type === "bool") return value ? "はい" : "いいえ";
  if (column.type === "progress") {
    const pct = Math.round(Number(value) * 100);
    return Number.isFinite(pct) ? `${pct}%` : String(value);
  }
  return String(value);
}

function renderGrid(data) {
  const grid = document.getElementById("grid");
  if (!grid) return;
  if (data.rows.length === 0) {
    grid.replaceChildren(el("p", { class: "empty", text: "データがありません。" }));
    return;
  }
  const head = el("tr", {}, data.columns.map((c) => el("th", { text: c.label })));
  const body = data.rows.map((row) =>
    el("tr", {}, data.columns.map((c) =>
      el("td", { text: formatCell(row[c.name], c) }))));

  grid.replaceChildren(
    el("p", { class: "empty", text: `${data.total} 件中 ${data.rows.length} 件を表示` }),
    el("div", { class: "grid-wrap" }, [
      el("table", { class: "grid" }, [
        el("thead", {}, [head]),
        el("tbody", {}, body),
      ]),
    ]),
  );
}

// ---------------------------------------------------------------- 操作
async function selectTable(key) {
  state.tableKey = key;
  renderTabs();
  const grid = document.getElementById("grid");
  if (grid) grid.replaceChildren(el("p", { class: "loading", text: "読み込み中…" }));
  try {
    const data = await api(`/api/guilds/${state.guildId}/tables/${key}`);
    state.canEdit = data.can_edit;
    renderGrid(data);
  } catch (e) {
    if (grid) grid.replaceChildren(el("p", { class: "error", text: e.message }));
  }
}

async function selectGuild(guildId) {
  state.guildId = guildId;
  renderGuildPicker();
  try {
    const data = await api(`/api/guilds/${guildId}/tables`);
    state.tables = data.tables;
    state.canEdit = data.can_edit;
    renderTabs();
    if (state.tables.length > 0) await selectTable(state.tables[0].key);
  } catch (e) {
    showError(e.message);
  }
}

async function main() {
  try {
    state.me = await api("/api/me");
  } catch (e) {
    if (e.status === 401) {
      renderLoginPrompt();
      return;
    }
    showError("サーバーに接続できませんでした。");
    return;
  }
  renderAccount();
  if (state.me.guilds.length > 0) {
    await selectGuild(state.me.guilds[0].id);
  } else {
    renderGuildPicker();
  }
}

main();
