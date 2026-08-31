// ダッシュボードのフロント（依存なしの素の JS。外部 CDN を読み込まない）
//
// ES モジュール（index.html が type="module" で読み込む）。
// DOM に触れない純粋関数は static/lib/ に置き、Node 標準の
// `node --test "tests_js/*.test.mjs"` で検証する（D0-2）。
//
// Liquid Glass 刷新: 左サイドバー（#tabs）とモバイル Dock（#dock）は
// 同じ状態を共有し、renderTabs が両方を描画する。選択ピルの背景は
// .nav-indicator / .sheet-indicator が transform で滑って追従する。
import { formatCell, parseInput } from "./lib/format.js";
import { PAGE_SIZES, pageInfo } from "./lib/paging.js";
import { buildTableQuery } from "./lib/query.js";
import { ariaSort, nextSortState } from "./lib/sort.js";
import { editAction } from "./lib/edit.js";
import { gridKeyAction, nextCellPosition } from "./lib/keynav.js";
import { errorDisposition } from "./lib/errors.js";
import { settingsDiff } from "./lib/settings.js";
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
  // フェードイン（.rise）を次の描画で発火させるか。
  // 表・シート・サーバーの切替でのみ立てる（ページ送りでは点滅させない）
  rise: false,
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

// ------------------------------------------------- 液体インジケーター
// 選択ピルの背景を1個だけ持ち、選択変更時に transform で滑らせる。
// 要素はモジュールで使い回す（作り直すと transition が働かない）
const navIndicator = el("span", { class: "nav-indicator", "aria-hidden": "true" });
const sheetIndicator = el("span", { class: "sheet-indicator", "aria-hidden": "true" });

function placeIndicator(indicator, btn) {
  if (!btn) return;
  const first = !indicator.dataset.placed;
  if (first) indicator.classList.add("no-anim");
  indicator.style.transform = `translate(${btn.offsetLeft}px, ${btn.offsetTop}px)`;
  indicator.style.width = `${btn.offsetWidth}px`;
  indicator.style.height = `${btn.offsetHeight}px`;
  if (first) {
    indicator.dataset.placed = "1";
    // 強制リフローで no-anim を反映させてから外す（初期配置は滑らせない）
    void indicator.offsetWidth;
    indicator.classList.remove("no-anim");
  }
}

// フォント読み込みやリサイズでピルの寸法が変わったら追従させる
function repositionIndicators() {
  const activeNav = document.querySelector("#tabs .nav-item.active");
  if (activeNav) placeIndicator(navIndicator, activeNav);
  const activeSheet = document.querySelector(".sheetbar .sheet-tab.active");
  if (activeSheet) placeIndicator(sheetIndicator, activeSheet);
}
window.addEventListener("resize", repositionIndicators);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(repositionIndicators);

// コンテンツのフェードイン。クラスを付け直してアニメを再発火させる
function applyRise() {
  if (!state.rise) return;
  state.rise = false;
  const content = document.getElementById("content");
  if (!content) return;
  content.classList.remove("rise");
  void content.offsetWidth;
  content.classList.add("rise");
}

// ---------------------------------------------------------------- 表示
function renderLoginPrompt() {
  accountEl.hidden = true;
  accountEl.replaceChildren();
  const picker = document.getElementById("guild-picker");
  if (picker) picker.replaceChildren();
  const tabs = document.getElementById("tabs");
  if (tabs) tabs.replaceChildren();
  const dock = document.getElementById("dock");
  if (dock) {
    dock.hidden = true;
    dock.replaceChildren();
  }
  appEl.replaceChildren(
    el("div", { class: "card login-card" }, [
      el("p", { text: "Discord でログインすると、自分が所属するサーバーのデータを表示できます。" }),
      el("p", {}, [
        el("a", { class: "button primary", href: "/auth/login", text: "Discord でログイン" }),
      ]),
    ]),
  );
}

function renderAccount() {
  const name = state.me.user.name || "?";
  accountEl.hidden = false;
  accountEl.replaceChildren(
    el("span", { class: "avatar", "aria-hidden": "true", text: name.charAt(0) }),
    el("span", { class: "account-name", text: name, title: name }),
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
  const picker = document.getElementById("guild-picker");
  if (guilds.length === 0) {
    if (picker) picker.replaceChildren();
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
  if (picker) picker.replaceChildren(select);
}

// コンテンツの骨組み: タイトル行 → 指標カード → 表。
// #grid / #summary の ID は据え置く（app.js の他箇所が参照するため）
function buildContentShell() {
  appEl.replaceChildren(
    el("div", { class: "content", id: "content" }, [
      el("div", { id: "page-head" }),
      el("div", { id: "summary" }),
      el("div", { id: "grid" }, [el("p", { class: "loading", text: "読み込み中…" })]),
    ]),
  );
}

function renderPageHead({ title, desc, csvHref }) {
  const head = document.getElementById("page-head");
  if (!head) return;
  head.classList.add("page-head");
  head.replaceChildren(...[
    el("div", {}, [
      el("h2", { class: "page-title", text: title }),
      desc ? el("p", { class: "page-desc", text: desc }) : null,
    ].filter(Boolean)),
    csvHref ? el("a", { class: "button", href: csvHref, download: "", text: "CSV をダウンロード" }) : null,
  ].filter(Boolean));
}

// ------------------------------------------------- 指標カード（サマリー行）
// 出欠回答と機体進捗だけに出す。値は表の描画データから集計する
// （/summary API は使わない。表示中のシート・ページと必ず一致させるため）
function metricCard(card) {
  const kids = [
    el("div", { class: "metric-label", text: card.label }),
    el("div", { class: card.positive ? "metric-value positive" : "metric-value", text: card.value }),
  ];
  if (card.sub) kids.push(el("div", { class: "metric-sub", text: card.sub }));
  if (card.bar !== null && card.bar !== undefined) {
    const fill = el("span", { class: `metric-bar-fill ${card.barColor || "teal"}` });
    fill.style.width = `${Math.round(Math.min(Math.max(card.bar, 0), 1) * 100)}%`;
    kids.push(el("div", { class: "metric-bar" }, [fill]));
  }
  return el("div", { class: "card metric" }, kids);
}

function renderMetrics(cards) {
  const box = document.getElementById("summary");
  if (!box) return;
  if (!cards || cards.length === 0) {
    box.replaceChildren();
    return;
  }
  box.replaceChildren(el("div", { class: "metrics" }, cards.map(metricCard)));
}

// 出欠回答: 参加 / 不参加 / 未回答 / 回答率。
// 参加・不参加は「いずれかの候補にその回答をした人」の人数（重複なし）。
// 未回答は予定単位（全行で同じ顔ぶれ）なので先頭行から取る
function attendanceMetrics(pivot) {
  if (!pivot || pivot.rows.length === 0) return null;
  const ok = new Set();
  const ng = new Set();
  const maybe = new Set();
  for (const row of pivot.rows) {
    for (const name of row.groups.ok || []) ok.add(name);
    for (const name of row.groups.ng || []) ng.add(name);
    for (const name of row.groups.maybe || []) maybe.add(name);
  }
  const unanswered = (pivot.rows[0].groups.none || []).length;
  const responded = new Set([...ok, ...ng, ...maybe]).size;
  const total = responded + unanswered;
  const rate = total > 0 ? responded / total : null;
  return [
    { label: "参加", value: String(ok.size) },
    { label: "不参加", value: String(ng.size) },
    { label: "未回答", value: String(unanswered) },
    {
      label: "回答率",
      value: rate === null ? "—" : `${Math.round(rate * 100)}%`,
      positive: true,
      bar: rate === null ? 0 : rate,
      barColor: "teal",
    },
  ];
}

function clampProgress(value) {
  return Math.min(Math.max(Number(value) || 0, 0), 1);
}

// 機体進捗: 機体全体の進捗 / ノード数 / 今週の更新 / 実測重量合計（目標比）。
// 機体全体は最上位ノード（parent_id なし）の平均。無ければ全ノードの平均
function progressMetrics(rows, total) {
  const withProgress = rows.filter(
    (r) => r.manual_progress !== null && r.manual_progress !== undefined);
  const tops = withProgress.filter((r) => !r.parent_id);
  const base = tops.length > 0 ? tops : withProgress;
  const overall = base.length > 0
    ? base.reduce((sum, r) => sum + clampProgress(r.manual_progress), 0) / base.length
    : null;
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  const now = Date.now();
  const updatedThisWeek = rows.filter((r) => {
    if (!r.updated_at) return false;
    const t = Date.parse(String(r.updated_at).replace(" ", "T"));
    return Number.isFinite(t) && now - t <= weekMs;
  }).length;
  const actual = rows.reduce((s, r) => s + (Number(r.actual_weight_g) || 0), 0);
  const targetW = rows.reduce((s, r) => s + (Number(r.target_weight_g) || 0), 0);
  return [
    {
      label: "機体全体の進捗",
      value: overall === null ? "—" : `${Math.round(overall * 100)}%`,
      bar: overall === null ? 0 : overall,
      barColor: "blue",
    },
    { label: "進捗ノード数", value: String(total) },
    { label: "今週の更新", value: String(updatedThisWeek) },
    {
      label: "実測重量合計",
      value: actual > 0 ? `${Math.round(actual).toLocaleString("ja-JP")} g` : "—",
      sub: actual > 0 && targetW > 0 ? `目標比 ${Math.round((actual / targetW) * 100)}%` : null,
    },
  ];
}

// 設定タブの内部キー（表ホワイトリストの "settings"（L4 の生テーブル）とは別物）
const SETTINGS_TAB = "__settings__";

// ------------------------------------------------- サイドナビ + Dock
// SVG ストロークアイコン（線画。fill なし・stroke-width 1.6）
const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

const ICON_PATHS = {
  members: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5"],
  teams: ["M4 4h6v6H4Z", "M14 4h6v6h-6Z", "M4 14h6v6H4Z", "M14 14h6v6h-6Z"],
  schedules: ["M4 6h16v14H4Z", "M4 10h16", "M8 3v5", "M16 3v5"],
  schedule_votes: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M8.5 12.5l2.5 2.5 4.5-5"],
  layer_records: ["M3 16c4-8 7 4 10-3s5-2 8-6"],
  progress: ["M5 20V10", "M12 20V4", "M19 20v-7"],
  audit_log: ["M5 4h14v16H5Z", "M9 9h6", "M9 13h6"],
  [SETTINGS_TAB]: [
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
    "M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14 3h-4l-.6 2.7a7 7 0 0 0-2 "
      + "1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 "
      + "1.2L10 21h4l.6-2.7a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z",
  ],
  fallback: ["M4 5h16v14H4Z", "M4 10h16", "M10 10v9"],
};

// Dock 用の短縮ラベル（長い表名は Dock で潰れるため）
const DOCK_LABELS = {
  schedule_votes: "出欠",
  layer_records: "桁巻き",
  layer_keta: "桁",
  progress_milestones: "節目",
  progress_snapshots: "履歴",
  stock_items: "在庫",
  stock_movements: "入出庫",
  audit_log: "ログ",
};

function dockIcon(key) {
  const paths = ICON_PATHS[key] || ICON_PATHS.fallback;
  return svg("svg", {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.6",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, paths.map((d) => svg("path", { d })));
}

// サイドバーの縦ナビと Dock は同じ状態を共有し、ここで両方描画する
function renderTabs() {
  const tabs = document.getElementById("tabs");
  const dock = document.getElementById("dock");
  if (!tabs) return;
  tabs.setAttribute("role", "tablist");
  const entries = [
    ...state.tables.map((t) => ({
      key: t.key,
      label: t.label,
      title: t.description || "",
      active: t.key === state.tableKey,
      onclick: () => selectTable(t.key),
    })),
    // 設定画面（D2-2）。表の並びの末尾に置く
    {
      key: SETTINGS_TAB,
      label: "設定",
      title: "サーバー設定の表示と変更",
      active: state.tableKey === SETTINGS_TAB,
      onclick: () => selectSettings(),
    },
  ];
  tabs.replaceChildren(
    navIndicator,
    ...entries.map((e) => el("button", {
      class: e.active ? "nav-item active" : "nav-item",
      role: "tab",
      "aria-selected": e.active ? "true" : "false",
      text: e.label,
      title: e.title,
      onclick: e.onclick,
    })),
  );
  requestAnimationFrame(() =>
    placeIndicator(navIndicator, tabs.querySelector(".nav-item.active")));

  if (!dock) return;
  dock.setAttribute("role", "tablist");
  dock.replaceChildren(...entries.map((e) => el("button", {
    class: e.active ? "dock-item active" : "dock-item",
    role: "tab",
    "aria-selected": e.active ? "true" : "false",
    title: e.label,
    onclick: e.onclick,
  }, [
    dockIcon(e.key),
    el("span", { class: "dock-label", text: DOCK_LABELS[e.key] || e.label }),
  ])));
  dock.hidden = entries.length <= 1;
  // Dock が横スクロールしている場合、選択中の項目を中央へ寄せる
  requestAnimationFrame(() => {
    const active = dock.querySelector(".dock-item.active");
    if (!active) return;
    dock.scrollTo({
      left: active.offsetLeft - (dock.clientWidth - active.offsetWidth) / 2,
      behavior: "smooth",
    });
  });
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
    // 確定・キャンセルは1回だけ有効にする。replaceChildren が編集中の
    // input を DOM から外すとき、ブラウザは同期的に blur を発火させるため、
    // ガードが無いと cancel が再入して NotFoundError で表示が壊れる
    let settled = false;
    const finish = async (commit) => {
      if (settled) return;
      settled = true;
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
      // stopPropagation: td のクリックリスナーまで泳がせると、確定で
      // エディタを外した直後に新しいエディタが開き直されてしまう
      onclick: (ev) => {
        ev.stopPropagation();
        act(editAction({ type: "commit-button" }));
      },
    });
    const cancelBtn = el("button", {
      class: "cell-action cell-action-cancel",
      type: "button",
      "aria-label": "キャンセル",
      text: "✕",
      onpointerdown: keepFocus,
      onmousedown: keepFocus,
      ontouchstart: keepFocus,
      onclick: (ev) => {
        ev.stopPropagation();
        act(editAction({ type: "cancel-button" }));
      },
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

// 進捗グラフ（HTML の横棒。ライブラリも外部 CDN も使わない）。
// バーは grow アニメで伸び、行ごとに 0.12s ずつ遅らせて階段状にする。
// 最上位ノード（parent_id なし）は青系 + グロー、下位はティール
function progressChart(rows, { max = 25 } = {}) {
  const items = rows
    .filter((r) => r.manual_progress !== null && r.manual_progress !== undefined)
    .slice(0, max)
    .map((r) => ({
      label: String(r.name || r.node_id || ""),
      value: clampProgress(r.manual_progress),
      top: !r.parent_id,
    }));
  if (items.length === 0) return null;

  return el("div", { class: "chart", role: "img", "aria-label": "進捗グラフ" },
    items.map((item, i) => {
      const bar = el("span", { class: item.top ? "chart-bar top" : "chart-bar" });
      bar.style.width = `${Math.max(item.value * 100, 0.5)}%`;
      bar.style.animationDelay = `${(i * 0.12).toFixed(2)}s`;
      return el("div", { class: "chart-row" }, [
        el("span", { class: "chart-label", text: item.label, title: item.label }),
        el("div", { class: "chart-track" }, [bar]),
        el("span", { class: "chart-val", text: `${Math.round(item.value * 100)}%` }),
      ]);
    }));
}

// シートタブ（Google スプレッドシートのタブ相当。表の**上**に置く。
// ページタイトル行 → シートタブ → ツールバー → 表 の順で並ぶ）。
// 出欠回答（予定ごと）と桁巻き記録（桁ごと）で共通に使う。
// タブ名はタイトルのみ（日程調整の開催日時はツールチップで確認できる）。
function renderSheetTabs(data) {
  const sheets = data.sheets;
  const bar = el("div", { class: "sheetbar", role: "tablist" },
    sheets.items.map((s) => el("button", {
      class: s.id === sheets.active ? "sheet-tab active" : "sheet-tab",
      role: "tab",
      "aria-selected": s.id === sheets.active ? "true" : "false",
      title: s.at ? `${s.label} — ${s.at}` : s.label,
      onclick: () => { if (s.id !== sheets.active) selectSheet(data.table.key, s.id); },
    }, [
      el("span", { class: "sheet-label", text: s.label }),
    ])));
  bar.prepend(sheetIndicator);
  requestAnimationFrame(() =>
    placeIndicator(sheetIndicator, bar.querySelector(".sheet-tab.active")));
  return bar;
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
  return el("div", { class: "grid-wrap panel" }, [
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

  // ページタイトル行（表名 + 説明 + CSV ピル）
  renderPageHead({
    title: data.table.label,
    desc: data.table.description || "",
    csvHref,
  });

  // 指標カード（出欠回答・機体進捗のみ。他の表では出さない）
  let metrics = null;
  if (data.table.key === "schedule_votes") metrics = attendanceMetrics(data.pivot);
  else if (data.table.key === "progress") metrics = progressMetrics(data.rows, data.total);
  renderMetrics(metrics);

  // 予定・桁がまだ1件も無い場合の空状態（タブも表も出さない）
  if (data.sheets && data.sheets.items.length === 0) {
    grid.replaceChildren(el("p", {
      class: "empty",
      text: `${data.sheets.noun}がまだ登録されていません。`,
    }));
    applyRise();
    return;
  }

  // シートタブは表の上（タイトル行のすぐ下）に置く。
  // 以下の3分岐（ピボット表 / 行0件 / 通常の表）すべてで先頭に来る
  const sheetbar = data.sheets ? renderSheetTabs(data) : null;

  // 出欠回答はピボット表（候補日時 × 参加/不参加/未定/未回答）で表示する
  if (data.pivot) {
    grid.replaceChildren(...[
      sheetbar,
      data.pivot.rows.length > 0
        ? attendancePivotTable(data.pivot)
        : el("p", { class: "empty", text: "候補日時がまだ登録されていません。" }),
    ].filter(Boolean));
    applyRise();
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
    applyRise();
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
      // ステータス文字（「進行中」等）はアクセント色にする
      const classes = [
        numClass(c).trim(),
        c.name === "status" ? "status-text" : "",
      ].filter(Boolean).join(" ");
      const td = el("td", {
        text: formatCell(row[c.name], c, row),
        ...(classes ? { class: classes } : {}),
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
    ].filter(Boolean)),
    chart ? el("div", { class: "chart-wrap card" }, [chart]) : null,
    el("div", { class: "grid-wrap panel" }, [
      el("table", { class: state.dense ? "grid dense" : "grid" }, [
        el("caption", { class: "sr-only", text: `${data.table.label} の一覧` }),
        el("thead", {}, [head]),
        el("tbody", {}, body),
      ]),
    ]),
  ].filter(Boolean));
  applyRise();
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
  state.rise = true;
  renderTabs();
  renderPageHead({ title: "設定", desc: "サーバー設定の表示と変更" });
  renderMetrics(null);
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
  applyRise();
}

async function selectTable(key) {
  state.tableKey = key;
  // 表を切り替えたらシート選択・ページ位置・検索語・ソートをリセットする
  state.sheetId = null;
  state.offset = 0;
  state.q = "";
  state.sort = null;
  state.dir = null;
  state.rise = true;
  // シートインジケーターは表ごとに初期配置へ戻す（前の表から滑らせない）
  delete sheetIndicator.dataset.placed;
  renderTabs();
  await loadGrid({ sheet: null, offset: 0 });
}

// シート（予定・桁）の切替。ページ全体はリロードせず表だけを差し替える。
// シートを切り替えたらページ位置をリセットする
async function selectSheet(key, sheetId) {
  state.offset = 0;
  state.rise = true;
  await loadGrid({ sheet: sheetId, offset: 0 });
}

async function selectGuild(guildId) {
  state.guildId = guildId;
  state.rise = true;
  renderGuildPicker();
  buildContentShell();
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
    el("span", { class: "theme-text", text: "テーマ: " }),
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
