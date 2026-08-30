// ダッシュボードのフロント（依存なしの素の JS。外部 CDN を読み込まない）
//
// ES モジュール（index.html が type="module" で読み込む）。
// DOM に触れない純粋関数は static/lib/ に置き、Node 標準の
// `node --test "tests_js/*.test.mjs"` で検証する（D0-2）。
import { formatCell, parseInput } from "./lib/format.js";
import { PAGE_SIZES, pageInfo } from "./lib/paging.js";
import { buildTableQuery } from "./lib/query.js";
import { ariaSort, nextSortState } from "./lib/sort.js";
import { editAction } from "./lib/edit.js";
import { gridKeyAction, nextCellPosition } from "./lib/keynav.js";
import { errorDisposition } from "./lib/errors.js";
import { settingsDiff } from "./lib/settings.js";
import { summaryCards } from "./lib/summary.js";
import { readStoredTheme, storeTheme, themeAttribute } from "./lib/theme.js";

const appEl = document.getElementById("app");
const accountEl = document.getElementById("account");

const state = {
  me: null,
  guildId: null,
  tables: [],
  tableKey: null,
  canEdit: false,
  // ページング（D1-1）。表・シートを切り替えたら offset は 0 に戻す
  sheetId: null,
  limit: 200,
  offset: 0,
  // 検索語（D1-2）。表を切り替えたらリセットする
  q: "",
  // ソート（D1-3）。null は既定（サーバーの order_by）。表切替でリセット
  sort: null,
  dir: null,
  // 行の高さ「詰めて」（D3-3）。セッション中は表をまたいで維持する
  dense: false,
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

function showError(message, onRetry) {
  appEl.replaceChildren(
    el("p", { class: "error", text: message }),
    onRetry ? el("p", {}, [el("button", { text: "再試行", onclick: onRetry })]) : null,
  );
}

// ---------------------------------------------------------------- 表示
function renderLoginPrompt() {
  accountEl.replaceChildren();
  const slot = document.getElementById("guild-slot");
  if (slot) slot.replaceChildren();
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
    "aria-label": "サーバーを選択",
    onchange: (e) => selectGuild(e.target.value),
  }, guilds.map((g) =>
    el("option", { value: g.id, text: g.name, ...(g.id === state.guildId ? { selected: "selected" } : {}) })));

  // サーバー選択はヘッダーへ（D3-1）
  const slot = document.getElementById("guild-slot");
  if (slot) slot.replaceChildren(el("label", {}, ["サーバー: ", select]));

  // シェル（D3-1）: 900px 以上でサイドナビ＋コンテンツの2カラム、
  // 未満では横スクロールのタブ（切替は style.css のメディアクエリ）。
  // #tabs / #grid の ID は据え置く（app.js の他箇所が参照するため）
  appEl.replaceChildren(
    el("div", { class: "shell" }, [
      el("nav", { id: "tabs", class: "tabs", "aria-label": "表の一覧" }),
      el("div", { class: "content" }, [
        el("div", { id: "summary" }),
        el("div", { id: "grid" }),
      ]),
    ]),
  );
}

// サマリーカード（D2-3）。/summary の失敗は表の表示を妨げない（カードだけ —）
function renderSummary(summary) {
  const box = document.getElementById("summary");
  if (!box) return;
  box.replaceChildren(
    el("div", { class: "bento" }, summaryCards(summary).map((card) =>
      el("div", { class: "card" }, [
        el("div", { class: "card-value", text: card.value }),
        el("div", { class: "card-label", text: card.label }),
      ]))),
  );
}

async function loadSummary() {
  try {
    renderSummary(await api(`/api/guilds/${state.guildId}/summary`));
  } catch (e) {
    if (errorDisposition(e.status) === "login") {
      renderLoginPrompt();
      return;
    }
    renderSummary(null);
  }
}

// 設定タブの内部キー（表ホワイトリストの "settings"（L4 の生テーブル）とは別物）
const SETTINGS_TAB = "__settings__";

function renderTabs() {
  const tabs = document.getElementById("tabs");
  if (!tabs) return;
  tabs.setAttribute("role", "tablist");
  tabs.replaceChildren(
    ...state.tables.map((t) =>
      el("button", {
        class: t.key === state.tableKey ? "primary" : "",
        role: "tab",
        "aria-selected": t.key === state.tableKey ? "true" : "false",
        text: t.label,
        title: t.description || "",
        onclick: () => selectTable(t.key),
      })),
    // 設定画面（D2-2）。表の並びの末尾に置く
    el("button", {
      class: state.tableKey === SETTINGS_TAB ? "primary" : "",
      role: "tab",
      "aria-selected": state.tableKey === SETTINGS_TAB ? "true" : "false",
      text: "設定",
      title: "サーバー設定の表示と変更",
      onclick: () => selectSettings(),
    }),
  );
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
        // キャンセル: 値を元に戻す（blur・Escape・✕。D1-4）
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
    const act = (action) => {
      if (action === "commit") finish(true);
      else if (action === "cancel") finish(false);
    };
    // ✓（確定）と ✕（キャンセル）。最小 44×44px のタップ領域（style.css）。
    // pointerdown の preventDefault で input の blur 自体を起こさせない
    // （relatedTarget を見る blur 側の防御と二重にする。
    //   ここを外すと「✓ を押すと取り消される」になる）
    const keepFocus = (ev) => ev.preventDefault();
    const commitBtn = el("button", {
      class: "cell-action primary",
      type: "button",
      "aria-label": "確定",
      text: "✓",
      onpointerdown: keepFocus,
      onmousedown: keepFocus,
      ontouchstart: keepFocus,
      onclick: () => act(editAction({ type: "commit-button" })),
    });
    const cancelBtn = el("button", {
      class: "cell-action cell-action-cancel",
      type: "button",
      "aria-label": "キャンセル",
      text: "✕",
      onpointerdown: keepFocus,
      onmousedown: keepFocus,
      ontouchstart: keepFocus,
      onclick: () => act(editAction({ type: "cancel-button" })),
    });
    const editor = el("span", { class: "cell-editor" }, [input, commitBtn, cancelBtn]);
    input.addEventListener("keydown", (ev) => {
      const action = editAction({ type: "keydown", key: ev.key });
      if (action !== "none") ev.preventDefault();
      act(action);
    });
    input.addEventListener("blur", (ev) => {
      act(editAction({
        type: "blur",
        toEditorControl: Boolean(ev.relatedTarget && editor.contains(ev.relatedTarget)),
      }));
    });
    td.replaceChildren(editor);
    input.focus();
    input.select();
  });
}

// 表のキーボード操作（D1-5）。Tab は表全体で1ストップ（roving tabindex）。
// 全セルに tabindex=0 を付けると Tab の回数が行×列になるため、
// フォーカス中のセルだけ 0、他は -1 にする。編集不可のセルは対象外
function setupGridKeyboard(cells) {
  if (cells.length === 0 || cells[0].length === 0) return;
  let cur = { row: 0, col: 0 };
  cells.forEach((rowCells, r) => rowCells.forEach((td, c) => {
    td.tabIndex = r === 0 && c === 0 ? 0 : -1;
    // Enter / Space で「編集開始」が発火するボタンとして公開する
    td.setAttribute("role", "button");
    td.addEventListener("focus", () => {
      // クリック等でフォーカスが移った場合も roving の現在地を追従させる
      if (cur.row !== r || cur.col !== c) {
        cells[cur.row][cur.col].tabIndex = -1;
        cur = { row: r, col: c };
        td.tabIndex = 0;
      }
    });
    td.addEventListener("keydown", (ev) => {
      if (td.querySelector("input")) return; // 編集中はセル移動しない
      const action = gridKeyAction(ev.key);
      if (action === "none") return;
      ev.preventDefault();
      if (action === "edit") {
        td.click();
        return;
      }
      const next = nextCellPosition(
        { row: r, col: c, rows: cells.length, cols: rowCells.length }, action);
      cells[cur.row][cur.col].tabIndex = -1;
      cur = next;
      const target = cells[next.row][next.col];
      target.tabIndex = 0;
      target.focus();
    });
  }));
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
  return el("div", { class: "grid-wrap card" }, [
    el("table", { class: "grid pivot" }, [
      el("caption", { class: "sr-only", text: "出欠回答のピボット表（候補日時ごとの回答者）" }),
      el("thead", {}, [head]),
      el("tbody", {}, body),
    ]),
  ]);
}

// 検索欄（D1-2）。検索は DB の生の値に対して行う（表示名では検索できない）。
// searchable 列の無い表（例: 出欠回答のピボット）では出さない
function searchBox(data) {
  if ((data.table.searchable || []).length === 0) return null;
  return el("input", {
    type: "search",
    class: "search-box",
    value: state.q,
    placeholder: "検索（DB の値。表示名では検索できません）",
    "aria-label": "この表を検索",
    onchange: (e) => {
      state.q = e.target.value.trim();
      loadGrid({ offset: 0 });
    },
  });
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
  // 画面と同じ絞り込み（シート・検索語）を CSV にも効かせる
  // （出欠回答で全予定が混ざった CSV が落ちてくるのを防ぐ。検索も同じ原則）
  const csvQuery = buildTableQuery({
    sheet: data.sheets ? data.sheets.active : null,
    q: state.q,
    sort: state.sort,
    dir: state.dir,
  });
  const csvHref = `/api/guilds/${state.guildId}/tables/${data.table.key}/export.csv`
    + (csvQuery ? `?${csvQuery}` : "");

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
    // 検索で 0 件になった場合も、検索欄を残して語を消せるようにする
    const search0 = state.q ? searchBox(data) : null;
    grid.replaceChildren(...[
      sheetbar,
      search0 ? el("div", { class: "toolbar" }, [search0]) : null,
      el("p", {
        class: "empty",
        text: state.q ? "検索に一致する行がありません。" : "データがありません。",
      }),
    ].filter(Boolean));
    return;
  }
  // 列ヘッダのソート（D1-3）。クリックで 昇順 → 降順 → 既定 を巡回し、
  // 現在の状態を aria-sort に出す。並び替えは DB の生の値に対して行う
  const sortableSet = new Set(data.table.sortable || []);
  // 数値列（number / progress）は tabular-nums で桁を揃える（D3-3）
  const numClass = (c) => (c.type === "number" || c.type === "progress" ? " num" : "");
  // 編集可能列の ✎ はヘッダーのバッジとして1つだけ出す（D3-3）
  const editBadge = (c) => (c.editable && data.can_edit
    ? el("span", { class: "badge-edit", title: "この列は編集できます", text: "✎" })
    : null);
  const head = el("tr", {}, data.columns.map((c) => {
    if (!sortableSet.has(c.name)) {
      return el("th", { class: numClass(c).trim() }, [
        document.createTextNode(c.label), editBadge(c),
      ]);
    }
    const current = { sort: state.sort, dir: state.dir };
    const aria = ariaSort(current, c.name);
    const mark = aria === "ascending" ? " ▲" : aria === "descending" ? " ▼" : "";
    return el("th", {
      class: "sortable" + numClass(c),
      "aria-sort": aria,
      role: "columnheader",
    }, [
      el("button", {
        class: "sort-button",
        text: c.label + mark,
        onclick: () => {
          const next = nextSortState(current, c.name);
          state.sort = next.sort;
          state.dir = next.dir;
          loadGrid({ offset: 0 });
        },
      }),
      editBadge(c),
    ]);
  }));

  // 編集可能セルの行列（キーボード操作の対象。D1-5）
  const editableMatrix = [];
  const body = data.rows.map((row) => {
    const rowCells = [];
    const tr = el("tr", {}, data.columns.map((c) => {
      const td = el("td", {
        text: formatCell(row[c.name], c, row),
        ...(numClass(c) ? { class: numClass(c).trim() } : {}),
      });
      if (c.editable && data.can_edit) {
        editableCell(td, row, c, data);
        rowCells.push(td);
      }
      return td;
    }));
    if (rowCells.length > 0) editableMatrix.push(rowCells);
    return tr;
  });
  setupGridKeyboard(editableMatrix);

  const hint = data.can_edit
    ? "✎ の付いた列はクリックして編集できます（変更は監査ログに記録されます）。"
    : "閲覧のみの権限です（編集には班長以上の権限が必要です）。";

  const chart = data.table.key === "progress" ? progressChart(data.rows) : null;

  // ページャ（D1-1）。ピボット表（出欠回答）はページング対象外なのでここに来ない。
  // total は count_rows の値＝シート絞り込み後の件数
  const info = pageInfo({
    total: data.total, limit: data.limit, offset: data.offset, count: data.rows.length,
  });
  const search = searchBox(data);

  const pager = [
    el("button", {
      text: "前へ",
      ...(info.hasPrev ? {} : { disabled: "" }),
      onclick: () => loadGrid({ offset: info.prevOffset }),
    }),
    el("span", { class: "empty", text: info.rangeText }),
    el("button", {
      text: "次へ",
      ...(info.hasNext ? {} : { disabled: "" }),
      onclick: () => loadGrid({ offset: info.nextOffset }),
    }),
    el("label", { class: "empty" }, [
      "表示件数: ",
      el("select", {
        onchange: (e) => {
          state.limit = Number(e.target.value);
          loadGrid({ offset: 0 });
        },
      }, PAGE_SIZES.map((n) => el("option", {
        value: String(n),
        text: `${n} 件`,
        ...(n === data.limit ? { selected: "selected" } : {}),
      }))),
    ]),
    // 行の高さ 標準 / 詰めて（D3-3）。再取得せずクラスだけ切り替える
    el("button", {
      text: state.dense ? "行の高さ: 詰めて" : "行の高さ: 標準",
      "aria-pressed": state.dense ? "true" : "false",
      onclick: (e) => {
        state.dense = !state.dense;
        const table = document.querySelector("table.grid");
        if (table) table.classList.toggle("dense", state.dense);
        e.target.textContent = state.dense ? "行の高さ: 詰めて" : "行の高さ: 標準";
        e.target.setAttribute("aria-pressed", state.dense ? "true" : "false");
      },
    }),
  ];

  grid.replaceChildren(...[
    sheetbar,
    el("div", { class: "toolbar" }, [
      search,
      ...pager,
      el("span", { class: "empty", text: hint }),
      el("a", { class: "button", href: csvHref, download: "", text: "CSV をダウンロード" }),
    ].filter(Boolean)),
    chart ? el("div", { class: "chart-wrap card" }, [chart]) : null,
    el("div", { class: "grid-wrap card" }, [
      el("table", { class: state.dense ? "grid dense" : "grid" }, [
        el("caption", { class: "sr-only", text: `${data.table.label} の一覧` }),
        el("thead", {}, [head]),
        el("tbody", {}, body),
      ]),
    ]),
  ].filter(Boolean));
}

// ---------------------------------------------------------------- 操作
// 表の読み込み。表・シート・ページの切替はすべてここを通る（D1-1）
async function loadGrid({ sheet = state.sheetId, offset = state.offset } = {}) {
  const grid = document.getElementById("grid");
  if (grid) grid.replaceChildren(el("p", { class: "loading", text: "読み込み中…" }));
  // シート未指定（null）: シート対応の表ではサーバーが先頭（最新）のシートを選ぶ
  const params = buildTableQuery({
    limit: state.limit, offset, sheet, q: state.q, sort: state.sort, dir: state.dir,
  });
  try {
    const data = await api(
      `/api/guilds/${state.guildId}/tables/${state.tableKey}?${params}`);
    state.canEdit = data.can_edit;
    state.offset = data.offset;
    state.sheetId = data.sheets ? data.sheets.active : null;
    renderGrid(data);
  } catch (e) {
    // ログイン後に起きた 401（セッション失効）はログイン導線へ（D1-6）。
    // それ以外はグリッド内に留め、直前のリクエストをやり直せるようにする
    if (errorDisposition(e.status) === "login") {
      renderLoginPrompt();
      return;
    }
    if (grid) {
      grid.replaceChildren(
        el("p", { class: "error", text: e.message }),
        el("p", {}, [
          el("button", { text: "再試行", onclick: () => loadGrid({ sheet, offset }) }),
        ]),
      );
    }
  }
}

// 設定画面（D2-2）。GET /settings を表示し、差分だけを PATCH する
async function selectSettings() {
  state.tableKey = SETTINGS_TAB;
  renderTabs();
  const grid = document.getElementById("grid");
  if (grid) grid.replaceChildren(el("p", { class: "loading", text: "読み込み中…" }));
  try {
    const data = await api(`/api/guilds/${state.guildId}/settings`);
    renderSettingsForm(data);
  } catch (e) {
    if (errorDisposition(e.status) === "login") {
      renderLoginPrompt();
      return;
    }
    if (grid) {
      grid.replaceChildren(
        el("p", { class: "error", text: e.message }),
        el("p", {}, [el("button", { text: "再試行", onclick: () => selectSettings() })]),
      );
    }
  }
}

function renderSettingsForm(data) {
  const grid = document.getElementById("grid");
  if (!grid) return;
  const original = {};
  const inputs = {};
  const errorSlots = {};

  const items = data.settings.map((s) => {
    original[s.key] = s.value ?? "";
    const input = el("input", {
      class: "settings-input",
      value: s.value ?? "",
      "aria-label": s.label,
      ...(data.can_edit ? {} : { readonly: "" }),
      ...(s.type === "channel" || s.type === "role"
        ? { inputmode: "numeric", placeholder: "ID（数字）を入力" }
        : {}),
    });
    inputs[s.key] = input;
    const errorSlot = el("p", { class: "cell-error settings-error" });
    errorSlots[s.key] = errorSlot;
    return el("div", { class: "settings-item" }, [
      el("label", { class: "settings-label" }, [
        el("span", { text: s.label }),
        input,
      ]),
      s.description ? el("p", { class: "empty settings-desc", text: s.description }) : null,
      errorSlot,
    ]);
  });

  const status = el("p", { class: "empty" });
  const save = data.can_edit
    ? el("button", {
        class: "primary",
        text: "変更を保存",
        onclick: async () => {
          const current = {};
          for (const [key, input] of Object.entries(inputs)) current[key] = input.value.trim();
          const diff = settingsDiff(original, current);
          for (const slot of Object.values(errorSlots)) slot.replaceChildren();
          if (Object.keys(diff).length === 0) {
            status.textContent = "変更はありません。";
            return;
          }
          // 1項目ずつ PATCH する: 失敗した項目の下にサーバーの detail を
          // そのまま出すため（まとめて送ると、どの項目のエラーか判別できない）
          let saved = 0;
          let failed = 0;
          for (const [key, value] of Object.entries(diff)) {
            try {
              await api(`/api/guilds/${state.guildId}/settings`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ [key]: value }),
              });
              original[key] = value;
              saved += 1;
            } catch (e) {
              if (errorDisposition(e.status) === "login") {
                renderLoginPrompt();
                return;
              }
              errorSlots[key].replaceChildren(document.createTextNode(e.message));
              failed += 1;
            }
          }
          status.textContent = failed > 0
            ? `${saved} 件を保存しました（${failed} 件は保存できませんでした）。`
            : `${saved} 件を保存しました。`;
        },
      })
    : el("p", { class: "empty", text: "サーバー管理権限が必要です（閲覧のみ）。" });

  grid.replaceChildren(
    el("div", { class: "settings-form" }, [...items, save, status]),
  );
}

async function selectTable(key) {
  state.tableKey = key;
  // 表を切り替えたらシート選択・ページ位置・検索語・ソートをリセットする
  state.sheetId = null;
  state.offset = 0;
  state.q = "";
  state.sort = null;
  state.dir = null;
  renderTabs();
  await loadGrid({ sheet: null, offset: 0 });
}

// シート（予定・桁）の切替。ページ全体はリロードせず表だけを差し替える。
// シートを切り替えたらページ位置をリセットする
async function selectSheet(key, sheetId) {
  state.offset = 0;
  await loadGrid({ sheet: sheetId, offset: 0 });
}

async function selectGuild(guildId) {
  state.guildId = guildId;
  renderGuildPicker();
  loadSummary(); // await しない: サマリーの失敗・遅延で表を待たせない
  try {
    const data = await api(`/api/guilds/${guildId}/tables`);
    state.tables = data.tables;
    state.canEdit = data.can_edit;
    renderTabs();
    if (state.tables.length > 0) await selectTable(state.tables[0].key);
  } catch (e) {
    if (errorDisposition(e.status) === "login") {
      renderLoginPrompt();
      return;
    }
    showError(e.message, () => selectGuild(guildId));
  }
}

// テーマ切替（D3-4）。system / light / dark の3状態。
// localStorage が使えない環境でも system として動く
function safeStorage() {
  try {
    return window.localStorage;
  } catch (_) {
    return null;
  }
}

function applyTheme(stored) {
  const attr = themeAttribute(stored);
  if (attr === null) document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", attr);
}

function setupThemeToggle() {
  const slot = document.getElementById("theme-slot");
  if (!slot) return;
  const stored = readStoredTheme(safeStorage());
  applyTheme(stored);
  const current = themeAttribute(stored) || "system";
  slot.replaceChildren(el("label", {}, [
    "テーマ: ",
    el("select", {
      "aria-label": "テーマを選択",
      onchange: (e) => {
        const value = e.target.value === "system" ? null : e.target.value;
        storeTheme(safeStorage(), value);
        applyTheme(value);
      },
    }, [
      el("option", { value: "system", text: "システム", ...(current === "system" ? { selected: "selected" } : {}) }),
      el("option", { value: "light", text: "ライト", ...(current === "light" ? { selected: "selected" } : {}) }),
      el("option", { value: "dark", text: "ダーク", ...(current === "dark" ? { selected: "selected" } : {}) }),
    ]),
  ]));
}

async function main() {
  setupThemeToggle();
  try {
    state.me = await api("/api/me");
  } catch (e) {
    if (errorDisposition(e.status) === "login") {
      renderLoginPrompt();
      return;
    }
    showError("サーバーに接続できませんでした。", () => main());
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
