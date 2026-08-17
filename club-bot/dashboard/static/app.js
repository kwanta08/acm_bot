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

function formatCell(value, column, row) {
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

function parseInput(raw, column) {
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

function editableCell(td, row, column, data) {
  td.classList.add("editable");
  td.title = "クリックして編集";
  td.addEventListener("click", () => {
    if (td.querySelector("input")) return;
    const current = row[column.name];
    const input = el("input", {
      value: current === null || current === undefined ? "" : String(current),
    });
    const finish = async (commit) => {
      if (!commit) {
        td.replaceChildren(document.createTextNode(formatCell(row[column.name], column, row)));
        return;
      }
      let value;
      try {
        value = parseInput(input.value, column);
      } catch (e) {
        td.replaceChildren(el("span", { class: "cell-error", text: e.message }));
        return;
      }
      td.replaceChildren(document.createTextNode("保存中…"));
      try {
        const res = await api(
          `/api/guilds/${state.guildId}/tables/${data.table.key}/${row[data.table.pk]}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [column.name]: value }),
          },
        );
        Object.assign(row, res.row);
        td.replaceChildren(document.createTextNode(formatCell(row[column.name], column, row)));
      } catch (e) {
        td.replaceChildren(el("span", { class: "cell-error", text: e.message }));
      }
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") finish(true);
      if (ev.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
    td.replaceChildren(input);
    input.focus();
    input.select();
  });
}

// 進捗グラフ（インライン SVG。ライブラリも外部 CDN も使わない）。
// bot 側の matplotlib を撤去し、描画はここへ移した。
const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function progressChart(rows, { max = 25 } = {}) {
  const items = rows
    .filter((r) => r.manual_progress !== null && r.manual_progress !== undefined)
    .slice(0, max)
    .map((r) => ({
      label: String(r.name || r.node_id || ""),
      value: Math.min(Math.max(Number(r.manual_progress) || 0, 0), 1),
    }));
  if (items.length === 0) return null;

  const rowH = 24;
  const labelW = 160;
  const barW = 320;
  const height = items.length * rowH + 8;
  const chart = svg("svg", {
    width: labelW + barW + 56,
    height,
    viewBox: `0 0 ${labelW + barW + 56} ${height}`,
    role: "img",
    "aria-label": "進捗グラフ",
  });

  items.forEach((item, i) => {
    const y = i * rowH + 4;
    const label = item.label.length > 18 ? `${item.label.slice(0, 17)}…` : item.label;
    chart.append(
      svg("text", {
        x: labelW - 8, y: y + 14, "text-anchor": "end",
        "font-size": "12", fill: "currentColor",
      }, [document.createTextNode(label)]),
      svg("rect", {
        x: labelW, y: y + 4, width: barW, height: 14, rx: 3,
        fill: "currentColor", "fill-opacity": "0.12",
      }),
      svg("rect", {
        x: labelW, y: y + 4, width: Math.max(barW * item.value, 1), height: 14,
        rx: 3, fill: "var(--accent)",
      }),
      svg("text", {
        x: labelW + barW + 8, y: y + 15, "font-size": "12", fill: "currentColor",
      }, [document.createTextNode(`${Math.round(item.value * 100)}%`)]),
    );
  });
  return chart;
}

// シートタブ（Google スプレッドシートのタブ相当。表の**上**に置く。
// 表タブ(#tabs) → シートタブ → ツールバー → 表 の順で並ぶ）。
// 出欠回答（予定ごと）と桁巻き記録（桁ごと）で共通に使う。
// タブ名はタイトルのみ（日程調整の開催日時はツールチップで確認できる）。
function renderSheetTabs(data) {
  const sheets = data.sheets;
  return el("div", { class: "sheetbar", role: "tablist" },
    sheets.items.map((s) => el("button", {
      class: s.id === sheets.active ? "sheet-tab active" : "sheet-tab",
      role: "tab",
      "aria-selected": s.id === sheets.active ? "true" : "false",
      title: s.at ? `${s.label} — ${s.at}` : s.label,
      onclick: () => { if (s.id !== sheets.active) selectSheet(data.table.key, s.id); },
    }, [
      el("span", { class: "sheet-label", text: s.label }),
    ])));
}

// 出欠回答のピボット表（1行 = 候補日時、セル = 表示名を改行区切りで列挙）
function attendancePivotTable(pivot) {
  const head = el("tr", {}, pivot.columns.map((c) => el("th", { text: c.label })));
  const body = pivot.rows.map((row) =>
    el("tr", {}, pivot.columns.map((c) => {
      if (c.key === "at") {
        return el("td", { class: "pivot-at", text: row.at, title: row.label || "" });
      }
      const names = row.groups[c.key] || [];
      const td = el("td", { class: "pivot-cell" });
      if (names.length > 0) {
        td.append(
          el("div", { class: "pivot-count", text: `${c.label} (${names.length})` }),
          el("div", { class: "pivot-names", text: names.join("\n") }),
        );
      }
      return td;
    })));
  return el("div", { class: "grid-wrap" }, [
    el("table", { class: "grid pivot" }, [
      el("thead", {}, [head]),
      el("tbody", {}, body),
    ]),
  ]);
}

function renderGrid(data) {
  const grid = document.getElementById("grid");
  if (!grid) return;

  // 予定・桁がまだ1件も無い場合の空状態（タブも表も出さない）
  if (data.sheets && data.sheets.items.length === 0) {
    grid.replaceChildren(el("p", {
      class: "empty",
      text: `${data.sheets.noun}がまだ登録されていません。`,
    }));
    return;
  }

  // シートタブは表の上（表タブ #tabs のすぐ下）に置く。
  // 以下の3分岐（ピボット表 / 行0件 / 通常の表）すべてで先頭に来る
  const sheetbar = data.sheets ? renderSheetTabs(data) : null;
  const csvHref = `/api/guilds/${state.guildId}/tables/${data.table.key}/export.csv`;

  // 出欠回答はピボット表（候補日時 × 参加/不参加/未定/未回答）で表示する
  if (data.pivot) {
    grid.replaceChildren(...[
      sheetbar,
      el("div", { class: "toolbar" }, [
        el("a", { class: "button", href: csvHref, download: "", text: "CSV をダウンロード" }),
      ]),
      data.pivot.rows.length > 0
        ? attendancePivotTable(data.pivot)
        : el("p", { class: "empty", text: "候補日時がまだ登録されていません。" }),
    ].filter(Boolean));
    return;
  }

  if (data.rows.length === 0) {
    grid.replaceChildren(...[
      sheetbar,
      el("p", { class: "empty", text: "データがありません。" }),
    ].filter(Boolean));
    return;
  }
  const head = el("tr", {}, data.columns.map((c) =>
    el("th", { text: c.editable && data.can_edit ? `${c.label} ✎` : c.label })));

  const body = data.rows.map((row) =>
    el("tr", {}, data.columns.map((c) => {
      const td = el("td", { text: formatCell(row[c.name], c, row) });
      if (c.editable && data.can_edit) editableCell(td, row, c, data);
      return td;
    })));

  const hint = data.can_edit
    ? "✎ の付いた列はクリックして編集できます（変更は監査ログに記録されます）。"
    : "閲覧のみの権限です（編集には班長以上の権限が必要です）。";

  const chart = data.table.key === "progress" ? progressChart(data.rows) : null;

  grid.replaceChildren(...[
    sheetbar,
    el("div", { class: "toolbar" }, [
      el("span", { class: "empty", text: `${data.total} 件中 ${data.rows.length} 件を表示 — ${hint}` }),
      el("a", { class: "button", href: csvHref, download: "", text: "CSV をダウンロード" }),
    ]),
    chart ? el("div", { class: "chart-wrap" }, [chart]) : null,
    el("div", { class: "grid-wrap" }, [
      el("table", { class: "grid" }, [
        el("thead", {}, [head]),
        el("tbody", {}, body),
      ]),
    ]),
  ].filter(Boolean));
}

// ---------------------------------------------------------------- 操作
async function selectTable(key) {
  state.tableKey = key;
  renderTabs();
  const grid = document.getElementById("grid");
  if (grid) grid.replaceChildren(el("p", { class: "loading", text: "読み込み中…" }));
  try {
    // シート未指定: シート対応の表ではサーバーが先頭（最新）のシートを選ぶ
    const data = await api(`/api/guilds/${state.guildId}/tables/${key}`);
    state.canEdit = data.can_edit;
    renderGrid(data);
  } catch (e) {
    if (grid) grid.replaceChildren(el("p", { class: "error", text: e.message }));
  }
}

// シート（予定・桁）の切替。ページ全体はリロードせず表だけを差し替える
async function selectSheet(key, sheetId) {
  const grid = document.getElementById("grid");
  if (grid) grid.replaceChildren(el("p", { class: "loading", text: "読み込み中…" }));
  try {
    const data = await api(
      `/api/guilds/${state.guildId}/tables/${key}?sheet=${encodeURIComponent(sheetId)}`);
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
