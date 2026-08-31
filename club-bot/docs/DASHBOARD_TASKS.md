# ダッシュボード改良タスク管理（D0〜D3）

`docs/IMPROVEMENT_TASKS.md` の「この表に入れなかったもの」で
**「G4 完了後に別表として起こす」**と保留にしたダッシュボード改良を、実際に起票した表。

- 根拠: `docs/IMPROVEMENT_REPORT.md` の **P1-12**（ダッシュボードの実用上の弱点）13項目
- UI デザインの参照: X アカウント **@basit_designs**（Basit A. Khan）の作風。§デザイン方針に翻訳済み
- 実装ループ: `/acm-bot-loop` ＋ `docs/DASHBOARD_LOOP_PROMPT.md`
- `docs/IMPROVEMENT_TASKS.md`（G0〜G4）とは**別トラック**。ブランチが別なので並行して回せる

---

## 運用ルール

- 実装は **必ず `/acm-bot-loop` の手順**（受入基準確定 → 失敗するテスト → 最小実装 → ruff + pytest → 自己修正）で回す。
  「実装したので確認してください」で止めない。全パスして初めて完了
- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
- タスクごとに `feat/<タスクID小文字>` または `fix/<タスクID小文字>` ブランチを切る（ADR 0014: 1タスク＝1ブランチ＝1PR）
- 完了時にチェックを入れ、末尾の完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
- **フェーズ順序を守る**（D0 → D1 → D2 → D3）。D0 は全部の前提
- 【人間タスク】はエージェントが飛ばす
- コミット・push は**ユーザーから明示指示があったときのみ**

## 全タスク共通の受入基準（AGENTS.md より。各タスクで再掲しない）

- 新規データ・新規設定はすべて `guild_id` スコープ。ギルド別設定は `config.for_guild(guild_id)` 経由
- ダッシュボードのハンドラは `ScopedGuild` / `EditorGuild` / `AdminGuild` だけを受け取り、生の `guild_id` を扱わない
- テーブル名・列名・並び順・検索対象は**必ずホワイトリスト**で解決する。リクエスト由来の値は必ずバインドする
- スキーマ変更は同じイテレーション内で `migrations/NNN_*.sql` と `utils/db.py` の
  `_migrate_vNN_*()` ＋ `SCHEMA_VERSION` 更新をセットで行う
- 班名・チャンネル・ロール・機体名・桁構成をコードに埋め込まない
- 実装とドキュメント（`README.md` / `dashboard/README.md` / `docs/`）が矛盾したら両方直す。
  この表で特に影響するのは `docs/DASHBOARD_SETUP.md`（構成・起動）と
  `docs/OPERATION.md` 8章（権限・監視・トラブル対応）
- `ruff check .` と `python -m pytest tests/ -q -rs` がフルセットで緑

## この表に固有の受入基準

- **外部 CDN・npm パッケージ・フレームワークを増やさない。** `static/style.css` 冒頭と
  `static/app.js` 冒頭のコメントが宣言している設計方針であり、
  IMPROVEMENT_REPORT.md「良くできている点 6.（依存を増やさない判断）」の対象でもある。
  依存を足す必要があると判断したら**実装せず止まって報告する**（D0-2 だけは例外扱い。本文参照）
- **SQLite が緑でも PostgreSQL が壊れている前提で書く。** G1-0 / G1-9 は
  「SQLite の型親和性で CI だけ緑」だった。SQL に触るタスク（D1-2 / D1-3 / D2-4）は
  `CLUB_TEST_PG_DSN` 条件付きの PG テストを必ず追加する
- **skip を「緑」と数えない。** `pytest tests/ -q -rs` で skip 理由まで報告する
  （gotcha `dashboard-tests-silently-skipped`）
- **ADR に反する変更をしない。** 衝突したら実装せず「ADR NNNN と衝突。判断を仰ぐ」と書いて止まる
- **ADR は2箇所にある。両方読む。** リポジトリ内の `club-bot/docs/adr/`（3件。
  `0008-dashboard-guild-scope.md` はこの表の全タスクに効く）と、ClaudeVault の
  `projects/acm_bot/decisions/`（33件）。**番号が独立して振られており 0008 が両方に存在する**ので、
  完了ログで ADR に言及するときは `docs/adr/0008` / `Vault ADR 0008` のように**出典を明記する**
- 画面の見た目を変えるタスク（D3 全部と D0-4）は、**before / after のスクリーンショットを完了ログに添える**

## スキーマバージョンの割り当て（衝突防止）

`docs/IMPROVEMENT_TASKS.md` が **v16〜v19 を G3-4 / G4-1 / G4-2 / G4-4 に予約済み**。
この表は **v20 以降**を使う。

| 版 | migration | タスク |
|---|---|---|
| v22 | `021_club_name_key.sql` | D2-1（`GUILD_NAME` → `CLUB_NAME`）。着手時の SCHEMA_VERSION が 21（G4 完了後）だったため v20 予約から繰り下げ |

着手時に `utils/db.py` の `SCHEMA_VERSION` の現在値を必ず読み直す。
G3/G4 の進み方によっては番号がずれる。

---

## P1-12 の13項目 → この表への対応

2026-08-30 時点でコードを確認した結果。**2項目は既に解消済み**なので起票しない。

| P1-12 の項目 | 状態 | タスク |
|---|---|---|
| ページング（フロントが `limit/offset` を送らない） | 未解消 | D1-1 |
| 検索・絞り込み・ソート（存在しない） | 未解消 | D1-2 / D1-3 |
| CSV が500行で無言の切り捨て | **解消済み**（`cfb42a8`。`list_all_rows` へ差し替え、`?sheet=` 対応） | — |
| セル編集が blur で確定 | 未解消（`app.js` の `input.addEventListener("blur", () => finish(true))` が現存） | D1-4 |
| 設定画面が無い（`/settings` がデッドコード） | 未解消 | D2-2 |
| `GUILD_NAME` を保存しても反映されない | 未解消（`routers/settings.py:38` が `GUILD_NAME`、`config.py:319` が `CLUB_NAME`） | D2-1 |
| 値の検証がサーバー側に無い | **解消済み**（G0-2 の `8b9c0f4` ＋ G1-9。`_coerce` / `InvalidValueError` → HTTP 400） | — |
| エラーで画面全体が消える | 未解消（`showError` が `appEl.replaceChildren`） | D1-6 |
| キーボード操作不可 | 未解消（`td` に `tabindex` も `keydown` も無い） | D1-5 |
| モバイル未対応 | 未解消（`.grid-wrap` に高さ制限が無く sticky ヘッダが効かない） | D3-5 |
| セッションが7日固定 | 未解消（`config.py:20` の `DEFAULT_SESSION_MAX_AGE = 7 * 24 * 60 * 60`） | D2-4 |
| ログが捨てられている | 未解消（`dashboard/main.py` に `setup_logging()` の呼び出しが無い） | D2-5 |
| 同時起動でマイグレーションがレースする | 未解消（`dashboard/db.py` と `bot.py` が両方 `connect()`） | D2-6 |

P1-12 の「設定画面が無い」の行は `/settings` と `/summary` の**2つの API** を1行にまとめている
（`docs/IMPROVEMENT_REPORT.md:304`）。この表では **D2-2（設定画面）** と **D2-3（サマリー）** に
分けて起票した。

---

## デザイン方針（@basit_designs の翻訳）

**参照元の但し書き:** X（`x.com/basit_designs`）は robots.txt により機械取得できなかったため、
同一人物の Dribbble ポートフォリオ（`dribbble.com/BasitAkhan`）から作風を読み取った。
**人間が実際の X アカウントを見て、この節の内容を1度校正すること**（D0-1）。

### 読み取った作風

ミニマル志向。要素を減らして余白で階層を作る。bento グリッド（大小のカードをタイル状に並べる）、
大きめの角丸、ごく薄い多層の影、アクセントは1色＋グラデーション、ダーク/ライト両対応、
グレイン／ディザのテクスチャ、短いモーション。ランディングページが中心の作家。

> **【2026-08-31 追記】この §デザイン方針は Liquid Glass 刷新で置き換えられた。**
> ユーザー指示（feat/liquid-glass-ui）により、以下の項目は現行実装と異なる:
> Web フォントは **woff2 の自己ホスト**で導入（外部 CDN 禁止は維持）、
> ゼブラ縞は復活（偶数行 `--row-alt`）、静止画の背景画像を採用、
> モーションはタブ切替（0.45s）とバー伸長（1.1s）を常時有効にし
> `prefers-reduced-motion` の全停止ルールは**意図して置かない**。
> トークン方式（D0-3）・外部 CDN 禁止・表の情報密度・tabular-nums は維持。
> preview.html も Liquid Glass の部品カタログへ更新済み。
>
> **色の実測（§色の受入条件の完了ログ。不透明色への合成値で計算）:**
> - ライト: `--muted` rgba(29,34,48,.7) × ページ地色 #e8eef6 = **5.0:1**（白地 5.9:1。
>   当初の .55 は 3.7:1 で AA 未達だったため引き上げた）/
>   `--accent` #0071e3 × 白文字 = **4.7:1**
> - ダーク: `--muted` rgba(238,241,248,.65) × #0c111c = **7.5:1** /
>   `--accent` #5ac8fa × 文字色 #0b1524 = **9.7:1**
> - 半透明ガラス＋写真背景上の実効値は変動するため、ここでは最悪側に近い
>   地色ベタでの合成値を採った。

### acm_bot のダッシュボードへの翻訳

**制約:** 外部 CDN 禁止 / 素の JS / Web フォント読み込み不可 / 表は業務用（情報密度を落とせない）。
「ランディングページの作風」をそのまま持ち込むと表が読めなくなるので、
**面の作り方（カード・角丸・余白・影・アクセントの面積）だけを採り、装飾は表の外に置く。**

| 作風 | ダッシュボードでの実装 | やらないこと |
|---|---|---|
| ミニマル | 行のゼブラ縞（`tbody tr:nth-child(even)`）を廃止し、区切りは 1px の低コントラスト線と行ホバーだけにする | 列や情報量を減らすこと |
| bento グリッド | サマリー（`/summary` の4つの件数）を `grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr))` のカードで | 表そのものをカードに割ること |
| 大きめの角丸 | `--radius-card: 16px` / `--radius-control: 10px` / `--radius-input: 8px` | 表のセルに角丸を付ける |
| 薄い多層の影 | `--shadow-card: 0 1px 2px rgb(0 0 0 / .04), 0 8px 24px -12px rgb(0 0 0 / .12)` | ダークモードで影に頼る（暗所では境界線で面を分ける） |
| アクセント1色＋グラデ | アクセントは1色。グラデーションは**プライマリボタンと進捗バーだけ**に使う | 文字色にグラデーションを使う |
| グレイン／ディザ | **見送る。** 表の可読性を下げるうえ、CSS だけでの再現に SVG フィルタが要る | 背景テクスチャの追加 |
| モーション | 120〜180ms、`opacity` と `transform: translateY(2px)` のみ。`prefers-reduced-motion: reduce` で全無効化 | 画面遷移アニメーション・パララックス |
| タイポグラフィ | 現行のシステムフォント指定を維持。数値列に `font-variant-numeric: tabular-nums` を当てて桁を揃える | Web フォントの読み込み（CDN 禁止） |

### 色の受入条件（実測して満たすこと）

現行の `--accent: #4a86e8` は**白文字とのコントラスト比が約 3.6:1** で WCAG AA（4.5:1）に届いていない。
プライマリボタン（`--accent` 背景 ＋ `--accent-fg: #ffffff`）と選択中タブがこの組み合わせなので、
トークン設計時に**アクセントを暗い方へ寄せる**。

- 候補: `#2f6fd0`（白文字とのコントラスト約 4.9:1）。**必ず実測して 4.5:1 以上を確認する**
- ライト／ダークそれぞれで、本文・ミュート文字・境界線・アクセントの比率を計算して完了ログに残す
- ダーク側のアクセントは背景が暗いので、**明るい方**へ寄せる別トークンにしてよい

---

## Phase D0: 足場（フロントを触る前提。ここを飛ばすと検証できない）

- [ ] **D0-1** 【人間タスク】現行画面の記録と、デザイン参照の校正。
      - **記録**: 5画面のスクリーンショットを `docs/img/dashboard-before/` に置く
        （未ログイン / 表の一覧 / `progress`（グラフ付き） / `schedule_votes`（ピボット） / エラー時）。
        D3 の after 比較の基準になる
      - **校正**: `x.com/basit_designs` を実際に見て、上の「読み取った作風」を修正する。
        機械取得できていないので、**この節は人間の目で1度直すまで仮**とする
      - **受入**: `docs/img/dashboard-before/` に5枚。デザイン方針の節に「校正済み（日付）」を追記

- [x] **D0-2** フロントの検証土台を作る（**依存追加の判断を含む。プランモード必須**）。
      `static/app.js` は 425 行あるが**テストが1件も無い**。CI も Python だけ。
      この状態で D1〜D3 を回すと「全テストパス」が front の変更を1行も検証しない。
      - **方針（この方針で良いか人間に確認してから実装する）**: `app.js` から
        DOM に触れない純粋関数（`parseInput` / `formatCell` / ページング計算 / クエリ組み立て /
        ソート状態遷移）を `static/lib/*.js` へ ES モジュールとして切り出し、
        **テストは `club-bot/tests_js/` に置いて** Node 標準の `node --test` で検証する。
        npm パッケージは1つも入れない
      - **テストを static の外に置く理由**: `dashboard/main.py:116` が
        `app.mount("/static", StaticFiles(directory=STATIC_DIR))` で **static ディレクトリを
        丸ごと公開配信している**。`static/lib/*.test.js` を置くと認証なしで配信される
      - **受入**: `node --test tests_js/` が緑。`package.json` を作らない
        （作る必要が出たら止まって報告）。CI（`.github/workflows/ci.yml`）に
        `actions/setup-node` の軽量ジョブを1つ追加し、**失敗が CI を落とす**
      - **検証**: 切り出した関数を `app.js` が `import` して使っており、ロジックが二重定義に
        なっていないことを目視で確認する。`index.html` の `<script>` に `type="module"` が要る
      - **注意**: 「依存を増やさない」は明文の設計方針。**Node は開発・CI 専用で、
        配信物には1バイトも増えない**という点が既存方針と両立する理由。
        完了ログに **ADR 草案「フロントの検証は Node 標準テストランナーだけで行う」** を書く
      - **止まる条件**: ES モジュール化で `file://` 直開きの動作確認ができなくなる等、
        運用手順が変わる副作用が出たら、実装せず報告する

- [x] **D0-3** デザイントークンを定義する（見た目はまだ変えない）。
      `static/style.css` の `:root` を、§デザイン方針の表に沿ったトークン体系へ置き換える。
      **この時点では既存の見た目を意図的に維持する**（トークンの追加と参照の付け替えだけ）。
      - **受入**: 色・余白（`--space-1`〜`--space-6`）・角丸・影・モーション時間・
        `z-index` の各トークンが `:root` に定義され、`style.css` 内の
        **リテラル値（`#rrggbb` / `0.75rem` / `6px` 等）の直書きが 0 件**になる
      - **検証**: ライト／ダークの両方で、本文・ミュート文字・アクセント上の白文字の
        コントラスト比を計算し、AA を満たすことを完了ログの表に書く（§色の受入条件）。
        D0-1 の before スクリーンショットと見比べ、**レイアウトが変わっていない**ことを確認する
      - **注意**: `prefers-color-scheme` のブロックはトークンの再定義だけにする。
        `[data-theme]` 属性による明示切替は D3-4 で足すので、
        **ここで `:root` にしか定義しない色を作らない**（D3-4 で全部書き直しになる）

- [x] **D0-4** コンポーネントのプレビュー画面を作る（**D3 の設計をデータ無しで先に決めるため**）。
      `dashboard/static/preview.html` を追加し、ボタン・タブ・シートタブ・ツールバー・
      カード・表・空状態・エラー・入力中セル・進捗バーを**全状態まとめて**並べる。
      ログイン不要・API 不要の静的ページにする。
      - **受入**: `/static/preview.html` が単体で開ける。ライト／ダーク両方で全要素が見える。
        `style.css` 以外の CSS を持たない（プレビュー専用のスタイルを書かない）
      - **検証**: `tests/test_dashboard_app.py` に、`/static/preview.html` が 200 で返り、
        **本番の導線（`index.html`）からはリンクされていない**ことを検査するケースを追加する
      - **注意**: これは開発用ページなので、**認証の後ろに置かない代わりに、
        データを一切含めない**（ダミーはすべてハードコードの文字列）。
        D3 の各タスクは「まず preview.html で形を決めてから本番画面へ適用する」順で進める

---

## Phase D1: 表を使えるようにする（P1-12 のフロント機能。効果が最も大きい）

- [x] **D1-1** ページングをフロントから送る（**最優先**）。
      API には `limit` / `offset` があり `total` も返っているのに、`app.js` が一度も送らない。
      現状 201 行目以降は**到達不能**で、「3000 件中 200 件を表示」と出るだけ。
      - **受入**: ツールバーに「前へ / 次へ」と「N〜M 件 / 全 T 件」を出す。
        先頭で「前へ」、末尾で「次へ」が `disabled`。表・シートを切り替えたら **offset を 0 に戻す**。
        1ページの件数は `DEFAULT_LIMIT`（200）を既定に、50 / 100 / 200 / 500（`MAX_LIMIT`）から選べる
      - **検証**: `node --test` でページ計算（`offset`・`hasPrev`・`hasNext`・表示レンジ文字列）を検査。
        `tests/test_dashboard_tables.py` に `offset` 付きリクエストが正しい行を返すことを追加
      - **注意**: ピボット表（`schedule_votes`）はページングの対象外。**ツールバーに出さない**
        （1行 = 候補日時で、行数がそもそも少ない）。`total` は `count_rows` の値で、
        シート絞り込み後の件数であることを表示文言でも間違えない

- [x] **D1-2** 検索（絞り込み）を足す。
`app.js` に該当実装が無い。サーバー側に `q` を足して**ホワイトリスト方式**で絞る。
      - **受入**: `TableSpec` に `searchable: tuple[str, ...]`（検索対象列）を持たせ、
        `GET /tables/{key}?q=` で `OR` 検索する。`q` は必ずバインドし、`%` と `_` をエスケープする。
        `q` は `total` にも効く（「12 件中 12 件」になる）。**`export.csv` にも同じ `q` が効く**
        （画面と CSV の中身がずれない。`?sheet=` と同じ原則）
      - **検証**: `tests/test_dashboard_tables.py` に (a) 部分一致でヒット、(b) `%` を含む検索語が
        ワイルドカードとして扱われない、(c) `searchable` に無い列は検索対象外、を追加。
        **PG 実機テストを `CLUB_TEST_PG_DSN` 条件付きで必ず追加する**
      - **注意**: **SQLite の `LIKE` は既定で大文字小文字を区別しない（ASCII のみ）が、
        PostgreSQL の `LIKE` は区別する。** そのまま書くと「SQLite の CI は緑、本番だけ効かない」
        という G1-0 / G1-9 と同型の事故になる。`lower(col) LIKE lower(?)` で両者を揃えるか、
        ドライバごとに `ILIKE` を出し分ける。**どちらにしたかを完了ログに書く**
      - **注意2**: 表示は `_display`（名前解決後）だが検索は**生の値**に当たる。
        「ユーザー名で検索したのに出ない」が起きる。当面の仕様として
        「検索は DB の値に対して行う」ことを画面の placeholder に明記し、
        表示名検索は「入れなかったもの」へ回す

- [x] **D1-3** 列ヘッダのソートを足す。
      並び順は `TableSpec.order_by` 固定。`ORDER BY` はバインドできないので**ホワイトリスト必須**。
      - **受入**: `TableSpec` に `sortable: tuple[str, ...]` を持たせ、`?sort=<列>&dir=asc|desc`。
        `sortable` に無い列・不正な `dir` は **400**（500 にしない）。既定は現行の `order_by` のまま。
        ヘッダをクリックで 昇順 → 降順 → 既定 の3状態を巡回し、現在の状態が `aria-sort` に出る。
        **`export.csv` にも同じ `sort` / `dir` が効く**
      - **検証**: `node --test` で3状態の巡回を検査。`tests/test_dashboard_tables.py` に
        許可列での並び替えと、非許可列での 400 を追加。PG 実機テストも足す
      - **注意**: SQL 組み立てにリクエスト由来の文字列を**連結しない**。
        `sortable` に含まれるかを検査したうえで、`spec` 側が持つ列名を使う
        （IMPROVEMENT_REPORT.md「セキュリティ面で問題なしと確認したもの」の
        ホワイトリスト方針を壊さない）。NULL の並び順をドライバ間で揃えること（`NULLS LAST` は
        SQLite に無い。`ORDER BY col IS NULL, col` で揃うかを PG テストで確認する）

- [x] **D1-4** セル編集の確定を Enter と ✓ ボタンだけにする。
      現状 `blur` で保存が走る。**モバイルには Escape が無く、誤タッチを取り消せない。**
      - **受入**: `blur` は**キャンセル**（値を戻す）。確定は Enter キーと、
        入力中セルに出る ✓ ボタンのみ。✕ ボタン（または Escape）でキャンセル。
        ✓ / ✕ は最小 44×44px のタップ領域を持つ
      - **検証**: `node --test` で、確定・キャンセルの状態遷移（どの入力でどちらになるか）を検査。
        `tests/test_dashboard_edit.py` は API 側なので変更不要（変更したら理由を書く）
      - **注意**: ✓ を押す動作そのものが `blur` を起こす。**`blur` ハンドラの中で
        「フォーカス移動先が ✓ ボタンなら何もしない」**（`relatedTarget` を見る）か、
        `mousedown` で `preventDefault` する。ここを外すと「✓ を押すと取り消される」になる。
        `pointerdown` / `touchstart` 経路でも同じことを確認する

- [x] **D1-5** キーボードだけで表を操作できるようにする。
      `td` に `tabindex` も `keydown` も無く、編集はマウス／タッチ必須。
      - **受入**: 編集可能な `td` に `tabindex="0"` と `role="button"`（または適切な代替）を付け、
        Enter / Space で編集を開始する。矢印キーで隣接セルへ移動する。
        フォーカスリングを **`outline` で明示**する（`outline: none` を書かない）
      - **検証**: `node --test` でキー → 動作の対応表を検査。
        `preview.html` にフォーカス状態の見本を追加する
      - **注意**: `tabindex="0"` を全セルに付けると Tab の回数が行×列になって逆に使えない。
        **Tab は表全体で1ストップ**（roving tabindex: フォーカス中のセルだけ `0`、
        他は `-1`）にする。編集不可のセルは対象外

- [x] **D1-6** ログイン後の 401 を拾い、エラーに再試行を付ける。
      **現状の正確な姿（P1-12 の記述より狭い。実コードで確認した）**: `showError`（`#app` の全置換）を
      使うのは `selectGuild` の catch（`app.js:402`）と `main` の catch（`app.js:414`）の**2箇所だけ**。
      表の読み込みエラーは `selectTable`（`app.js:373-375`）と `selectSheet`（`app.js:387-389`）が
      **既に `#grid` 内に留めている**。401 → `renderLoginPrompt()` も `main()` には**既にある**
      （`app.js:410-413`）。**足りないのは「ログイン後に起きた 401」と「再試行」の2つ。**
      - **受入**: (1) `selectGuild` / `selectTable` / `selectSheet` の 401 でも
        `renderLoginPrompt()` に落ちる（全画面置換を許すのは 401 のときだけ）。
        (2) 401 以外のエラーはグリッド内に留める（**既存動作の維持**。壊さないことが受入基準）。
        (3) エラー表示に「再試行」ボタンを付け、押すと直前のリクエストをやり直す
      - **検証**: `node --test` で「status → どの表示に落ちるか」の分岐を検査。
        `tests/test_dashboard_auth.py` に 401 のレスポンス形が変わっていないことを追加
      - **注意**: 401 の判定は `err.status` で行う（`api()` が既に載せている）。
        文言でのマッチをしない。D2-4（セッション短縮）と組み合わさるので、**先にこちらを済ませる**

---

## Phase D2: 画面を増やす / 運用の穴を塞ぐ

- [x] **D2-1** `GUILD_NAME` を `CLUB_NAME` に統一する（**スキーマ v20。プランモード必須**）。
      ダッシュボードは `GUILD_NAME` を「サークル名」として書く（`routers/settings.py:38`）が、
      週次サマリーが読むのは `CLUB_NAME`（`config.py:319`）。**保存しても反映されない。**
      - **受入**: `EDITABLE_SETTINGS` のキーを `CLUB_NAME` に変える。
        `migrations/019_club_name_key.sql` と `_migrate_v20_*()` で、既存ギルドの
        `GUILD_NAME` の値を `CLUB_NAME` へ移す。**`CLUB_NAME` が既にあるギルドは上書きしない**
      - **検証**: マイグレーション前後で (a) `GUILD_NAME` だけのギルド、(b) 両方あるギルド、
        (c) どちらも無いギルド の3ケースを `tests/` で検査する。PG 実機テストも足す
      - **注意**: **ADR 0024（既定値で既存データを動かさない）に触れる。**
        ClaudeVault の `decisions/0024-*` を読み、「覆す条件」に照らしてから実装する。
        衝突すると判断したら実装せず報告する。`GUILD_NAME` の行を消すか残すかは
        設計判断として完了ログに理由を書く（消さない方が安全側）

- [x] **D2-2** 「設定」タブを追加する。
      `GET /settings` も `PATCH /settings` も**画面から一度も呼ばれていない**（デッドコード）。
      - **受入**: 表タブの並びに「設定」を足し、`EDITABLE_SETTINGS` の8項目を
        ラベル・説明つきで表示する。`can_edit`（L4）が偽なら入力欄を `readonly` にし、
        「サーバー管理権限が必要です」と出す。保存は差分だけを `PATCH` し、
        400 のときはサーバーが返した `detail` をその項目の下に出す
      - **検証**: `tests/test_dashboard_settings.py` に、L1 でロール ID が
        「（設定済み）」で伏せられること（既存仕様）が壊れていないことを追加
      - **注意**: **D2-1 の後に実施する。** 先にやるとキー名が `GUILD_NAME` のまま画面に出て、
        「サークル名を保存しても効かない」を UI で追認することになる。
        チャンネル／ロールは現状 ID の手入力。**セレクタ化はこのタスクでやらない**
        （Bot トークンを Web 層に置かない方針に触れる。「入れなかったもの」参照）

- [x] **D2-3** サマリーカードを出す（`GET /summary` のデッドコード解消）。
      メンバー数・班数・進捗ノード数・未完了タスク数と、自分の権限を返す API があるのに未使用。
      - **受入**: サーバーを選んだ直後に `/summary` を1回呼び、表の上に **bento カード4枚**で出す。
        自分の権限（レベルと `manage_guild`）もカードに出す。
        `/summary` が失敗しても**表の表示を妨げない**（カードだけ「—」にする）
      - **検証**: `node --test` でカードの組み立てを検査。
        `tests/test_dashboard_views.py` に `/summary` の応答形が変わっていないことを追加
      - **注意**: §デザイン方針の bento グリッドはここが本命。**D0-4 の preview.html で
        先にカードの形を決めてから**本番へ入れる。`open_tasks` は `list_tasks` の
        len なので件数が多いと重い。遅ければ `count` を返す API 側の改善を起票する

- [x] **D2-4** セッションの既定を24時間にし、失効の意味を正しく書く。
      `DEFAULT_SESSION_MAX_AGE = 7 * 24 * 60 * 60`。Cookie に**所属ギルド一覧と `manage_guild`**を
      焼き込むため、退会・降格が最大7日反映されない。
      - **受入**: 既定を `24 * 60 * 60` にする。`DASHBOARD_SESSION_MAX_AGE` での上書きは維持。
        `dashboard/README.md` / `dashboard/.env.example` / **`docs/DASHBOARD_SETUP.md:169,190`**
        （「既定は7日」と `604800`）を同時に直す。1つでも残すと共通受入基準に自分で違反する
      - **検証**: `tests/test_dashboard_auth.py` に既定値と上書きの両方を検査するケースを追加
      - **注意**: **権限レベル（L1〜L4）は Cookie に焼かれていない。**
        `security.py` の `require_guild_scope` が毎リクエスト `resolve_level()` で DB から引くので、
        班長の降格は即時反映される。**古くなるのは「所属ギルド一覧」と `manage_guild` だけ**。
        この区別を `dashboard/README.md` に書く。「7日間なんでも古い」と書くと嘘になる。
        Discord への再問い合わせによる能動的な再検証は**このタスクではやらない**
        （アクセストークンの保存が要る。「入れなかったもの」参照）

- [x] **D2-5** ダッシュボードの INFO ログを残す（**`utils/logger.py` の改修を含む。範囲外ファイルを触る例外**）。
      `setup_logging()` の呼び出しは `bot.py` にしか無く、ダッシュボードのルートロガーは未設定。
      - **落ちているものの正確な範囲**: WARNING 以上は `logging.lastResort` で stderr に出るため、
        `deploy/club-bot-dashboard.service:45-46` の `StandardOutput/StandardError=append:` 経由で
        `logs/dashboard.log` / `.err` に**残っている**。落ちているのは **INFO 以下**
        （`dashboard/main.py:56` の起動ログなど）。「ログが全部消えている」とは書かない
      - **前提（間違えやすい）**: `utils/logger.py` の `setup_logging(level=logging.INFO)` には
        **出力ファイル名の引数が無い**。出力先は `_LOG_DIR = "logs"`（相対）＋ `"bot.log"` の固定。
        つまりこのタスクは **bot と共有の `utils/logger.py` を触らないと成立しない**。
        運用ルールの「1タスクの範囲外のファイルを触らない」の**明示的な例外**として扱う
      - **受入**: (1) `setup_logging()` に出力ファイル名の引数を足す（bot 側の既定は `bot.log` のまま）。
        (2) `create_app()` から `setup_logging("dashboard.log")` 相当を呼ぶ。
        (3) レベルは環境変数で上書きできる
      - **検証**: `tests/test_dashboard_app.py` に `create_app()` 後に `dashboard` ロガーが
        INFO を出せることを、bot 側のテストに**既定が `bot.log` のままである**ことを追加する
      - **注意1**: **同じログファイルを2プロセスが掴むと、`RotatingFileHandler` の
        ローテーション時に取り合いになって落ちる。**ファイル名の分離が受入基準の本体
      - **注意2（これを外すと本番が再起動ループする）**: `_LOG_DIR` は**相対パス**なので
        実際の書き込み先は `WorkingDirectory=/home/ubuntu/club-bot/club-bot` 基準の
        `.../club-bot/club-bot/logs`。一方 `deploy/club-bot-dashboard.service` の
        `ReadWritePaths` は **1階層上の** `/home/ubuntu/club-bot/logs` で、`ProtectHome=read-only`
        が効いている。このまま `setup_logging()` を呼ぶとディレクトリ作成が失敗し、
        `Restart=always` と相まって再起動ループになる。**出力先を絶対パスか環境変数で
        指定できるようにするか、`ReadWritePaths` に実際の書き込み先を追加する**
      - **注意3**: ログの見方は `docs/OPERATION.md` 8章に追記する

- [x] **D2-6** 同時起動でマイグレーションがレースしないようにする。
      bot（`bot.py:120`）と dashboard（`dashboard/db.py:30`）が両方 `connect()` を呼ぶが
      排他制御が無い。**デプロイ時に両方を同時再起動すると踏む。**
      - **前提（間違えやすい。ここを外すと空振りする）**: `utils/db.py` の `_migrate()` は
        **SQLite 専用**で、`connect()` は `self._is_pg` なら `_connect_pg()` へ抜けて
        `_migrate()` に到達しない（`utils/db.py:709-712`）。
        **PostgreSQL のマイグレーション入口は `_connect_pg()` 内の
        `_migrate_versioned()`（`utils/db.py:756`）。**
        `_migrate()` を囲むと、PG では一度もロックを取らないまま「受入基準を満たした」ことになる
      - **受入**: PostgreSQL では `pg_advisory_lock(<固定の定数>)` で
        **`_migrate_versioned()` の呼び出し**を囲み、終了時に確実に解放する。
        SQLite 経路（`_migrate()`）は no-op（理由をコメントに書く）
      - **検証**: 2つの `Database` から同時に `connect()` して、
        マイグレーションが2回走らないことを PG 実機テスト（`CLUB_TEST_PG_DSN` 条件付き）で検査する
      - **注意**: ロックは**マイグレーションの外側**で取り、取得できるまで待つ。
        `pg_try_advisory_lock` で「取れなければ飛ばす」にすると、
        待たずに古いスキーマのまま起動して**後で静かに壊れる**。
        定数はマジックナンバーにせず `utils/db.py` に名前付き定数で置く

---

## Phase D3: 見た目の全面刷新（@basit_designs 準拠）

**進め方:** 各タスクとも **(1) `preview.html` で形を決める → (2) 本番画面へ適用する →
(3) before/after のスクリーンショットを完了ログに添える** の順で進める。

- [x] **D3-1** アプリシェルを組み直す（ヘッダー＋サイドナビ＋コンテンツ）。
      現状はヘッダーの下にタブが横一列。表が7つあり、設定タブ（D2-2）で8つになる。
      - **受入**: 900px 以上でサイドナビ（表の一覧＋設定）、未満で現行どおり横スクロールのタブ。
        サーバー選択はヘッダーへ移す。**キーボードのフォーカス順が視覚順と一致する**
      - **検証**: `preview.html` に両レイアウトを出す。D1-5 のキーボード操作が壊れていないこと
      - **注意**: `#tabs` / `#grid` の ID は `app.js` の複数箇所が参照している。
        **ID の付け替えは1タスクに閉じる**。D1 で入れた要素（ページャ・検索・ソート）の
        クラス名を変えるときは、`node --test` の対象になっている純粋関数に影響しないことを確認する

- [x] **D3-2** カードとサマリーを bento グリッドで組む。
      - **受入**: `--radius-card` / `--shadow-card` を使ったカードを1種類だけ定義し、
        サマリー（D2-3）・グラフ・表を同じカードで包む。
        `repeat(auto-fit, minmax(11rem, 1fr))` で列数が幅に追随する
      - **検証**: `preview.html` で 320px / 768px / 1440px の3幅を確認する
      - **注意**: **カードの入れ子を1段までにする。**カードの中にカードを置くと
        境界線と影が二重になって、ミニマルの逆になる

- [x] **D3-3** データグリッドを作り直す。
      - **受入**: ゼブラ縞を廃止し、区切りは 1px 線＋行ホバー。ヘッダーは sticky。
        数値列に `font-variant-numeric: tabular-nums`。編集可能列の ✎ をヘッダーの
        バッジにする。行の高さを「標準 / 詰めて」で切り替えられる
      - **検証**: `preview.html` に 3 列 / 12 列の両方を出し、横スクロールが
        **表の中だけ**に閉じていることを確認する
      - **注意**: 進捗グラフ（`progressChart`）はインライン SVG で `fill: var(--accent)` を
        参照している。**トークンを変えるとグラフの色も変わる**ので、
        グラフだけ別トークン（`--accent-chart`）に分けるかを判断して完了ログに書く

- [x] **D3-4** テーマ切替（system / light / dark）を足す。
      現状 `prefers-color-scheme` のみで、**利用者が選べない**。
      - **受入**: ヘッダーに3状態のトグル。`<html data-theme="light|dark">` を立て、
        未選択（system）では属性を付けない。選択は `localStorage` に保存する。
        `:root` / `@media (prefers-color-scheme: dark)` / `:root[data-theme="dark"]` の
        3箇所でトークンが**矛盾なく**定義される
      - **検証**: `node --test` で「保存値 × OS 設定 → 適用テーマ」の表を検査する
      - **注意**: **`localStorage` が例外を投げる環境がある**（プライベートウィンドウ、
        サイトデータ拒否）。読み書きを `try/catch` で囲み、
        値が取れなくても system として正しく描画する

- [x] **D3-5** モバイル対応（P1-12 の「モバイル未対応」）。
      メディアクエリは `prefers-color-scheme` だけ。`.grid-wrap` に高さ制限が無いので
      **sticky ヘッダも実際には効いていない。**
      - **受入**: `.grid-wrap` に `max-height: 70vh` を入れて sticky を機能させる。
        640px 未満で (a) ツールバーを2段に折り返す、(b) サーバー選択とテーマ切替を
        ヘッダー内で畳む、(c) タップ対象を最小 44×44px にする
      - **検証**: `preview.html` を 320px / 375px / 414px で確認し、
        **`body` が横スクロールしない**ことを確認する（横スクロールは表の中だけ）
      - **注意**: `70vh` はモバイルのアドレスバー伸縮で跳ねる。`dvh` が使えるかを確認し、
        使うなら `vh` のフォールバックを先に書く

- [x] **D3-6** アクセシビリティを詰める。
      - **受入**: (1) ライト／ダーク両方で本文・ミュート文字・アクセント上の文字が **AA（4.5:1）**、
        (2) すべての操作要素にフォーカスリングが見える、(3) タブ・シートタブに
        `role` / `aria-selected` が付く（シートタブは既に付いている）、
        (4) 表に `<caption>` かそれに準ずる説明、(5) `prefers-reduced-motion: reduce` で
        モーションが全無効
      - **検証**: 各色の組み合わせのコントラスト比を計算して完了ログに表で残す。
        キーボードだけでログイン → 表切替 → セル編集 → CSV ダウンロードまで到達できることを手で確認する
      - **注意**: `outline: none` をどこにも書かない。
        アクセント色を変えるときは §色の受入条件の実測をやり直す

---

## 実施順の理由

- **D0 が先。** `app.js` にテストが1件も無い状態で D1〜D3 を回すと、
  「ruff と pytest が全パス」がフロントの変更を1行も検証しない。
  `/acm-bot-loop` の自走が**空回りする**のでここを最初に埋める
- **D1（機能）が D3（見た目）より先。** 壊れている表を綺麗にしても、
  201 行目以降は相変わらず到達不能。D1 のツールバー要素（ページャ・検索・ソート）は
  D3 で作り直すシェルに**そのまま載る形**で作るので、二度手間は D0-3 のトークンと
  D0-4 のプレビューで吸収する
- **D2-1 → D2-2 の順は入れ替えない。** キー名を直す前に設定画面を出すと、
  「サークル名を保存しても効かない」不具合を UI で追認することになる
- **D1-6 → D2-4 の順も入れ替えない。** セッションを24時間に縮めると 401 の頻度が上がる。
  401 でログイン導線が出るようになってから縮める
- **見た目を早く見たい場合の例外:** D3-1（シェル）だけは D0 の直後に前倒しできる。
  その場合 D1 の各タスクは新しいシェルの中に実装することになるので、
  **D3-1 を前倒しするならこの表の順序も書き換える**こと（口頭で順序だけ変えない）

---

## この表に入れなかったもの（再提案しないための記録）

| 項目 | 理由 |
|---|---|
| 表示名での検索（`_display` に対する検索） | 名前解決はサーバー側で行ごとに付けており、DB へ落とすには名前キャッシュを結合する必要がある。D1-2 の後に効果を見てから別途起票する |
| チャンネル／ロールのセレクタ化（設定画面） | 選択肢を出すには Discord API 呼び出しが要り、**Bot トークンを Web 層に置かない**方針に触れる。ADR を覆す判断が必要 |
| Discord への能動的な再検証（退会・降格の即時反映） | アクセストークンをセッションに保存する必要がある。攻撃面が増える判断を伴うので、必要になってから ADR とセットで起こす |
| 画面状態の URL 同期（表・シート・ページ・検索語） | 共有には便利だが D1 の完了後でないと設計できない。D3-1 の後に別途 |
| グレイン／ディザのテクスチャ | 参照元の特徴だが、表の可読性を下げるうえ CSS だけでの再現に SVG フィルタが要る。§デザイン方針で明示的に見送った |
| 行の追加・削除（ダッシュボードから） | 現状は既存行の**セル編集だけ**。追加・削除は Discord のコマンド側が正で、二重の入口を作らない |
| npm / バンドラ / フレームワークの導入 | 明文の設計方針に反する。D0-2 は**開発と CI だけ**で完結し、配信物を1バイトも増やさないことを条件に例外扱いとした |

---

## 停止条件（`/acm-bot-loop` §4 の再掲。特に本表で起きやすいもの）

- **ADR と衝突する実装になったとき。** 勝手に ADR を覆さない
  （D2-1 は ADR 0024、設定セレクタ化は Bot トークンの置き場所に触れる）
- **配信物に外部依存を足さないと実装できないと判断したとき**（この表の固有基準）
- 同じテストが3周直しても緑にならない
- SQLite では緑だが PostgreSQL で落ちる／その逆が起き、原因が特定できない（D1-2 / D1-3 / D2-6）
- 既存ギルドの設定値を壊す変更が避けられない（D2-1）
- 秘密情報や本番 DB に触る必要が出た

---

## 完了ログ

<!-- 各タスク完了時に「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する -->
<!-- 見た目を変えたタスクは before/after のスクリーンショットへのパスも書く -->

### D3-6 アクセシビリティ（2026-08-30）

- **完了内容**: (1) 全操作要素の `:focus-visible` に統一フォーカスリング
  （`outline: 2px solid var(--accent)` ＋ offset。`outline: none` は 0 件で、
  `test_no_outline_none_anywhere` が恒久ガード）。(2) 表タブに `role="tablist"` /
  `role="tab"` / `aria-selected`（シートタブは既存）。(3) 表とピボット表に
  `<caption class="sr-only">`（視覚非表示・SR には読める）。
  (4) モーションを §デザイン方針どおり最小定義（150ms、opacity / translateY のみ。
  ボタン押下の `--press-shift: 2px`）し、`prefers-reduced-motion: reduce` で
  **全無効**（`test_reduced_motion_disables_all_motion` がガード）
- **コントラスト実測（D0-3 から色は不変。再計算して確認）**:

  | 組み合わせ | ライト | ダーク |
  |---|---|---|
  | 本文 `--fg` / `--bg` | 15.80:1 ✓ | 16.02:1 ✓ |
  | ミュート `--muted` / `--bg` | 5.25:1 ✓ | 6.50:1 ✓ |
  | アクセント上の文字 `--accent-fg` / `--accent` | 4.88:1 ✓ | 4.88:1 ✓ |
  | 危険色 `--danger` / `--bg` | 6.57:1 ✓ | 7.51:1 ✓ |

  すべて AA（4.5:1）以上
- **検証の限界（人間の手での確認が残る）**: `:focus-visible` ルールと
  reduced-motion ブロックの適用は CSSOM で実測したが、
  **キーボードだけでログイン → 表切替 → セル編集 → CSV 到達の通し確認は
  Discord OAuth を伴うため自動では実施できていない**。実ログインで1度なぞること
- **次タスクへの申し送り**: なし（D3 完了）

### D3-5 モバイル対応（2026-08-30）

- **完了内容**: `.grid-wrap` に `max-height: var(--grid-max-h)`（既定 70vh、
  `@supports (height: 1dvh)` で 70dvh に差し替え。**vh フォールバックを先に定義**）
  を入れ、sticky ヘッダを実際に機能させた（`overflow: auto` 化）。
  640px 未満: ヘッダーを2段に畳む（タイトル1段目・サーバー選択＋テーマ切替2段目）、
  検索欄を1行専有にしてツールバーを2段へ折り返し、
  ボタン・タブ・セレクタのタップ対象を `--tap-min`（44px）以上に
- **検証（ブラウザ実測）**: 320 / 375 / 414px すべてで **body の横スクロールなし**
  （横スクロールは `.grid-wrap` の中だけ）。320px で max-height 490px（=70dvh）、
  `th` sticky、ボタン min-height 44px、検索欄 flex-basis 100% を確認
- **設計判断**: 「ヘッダー内で畳む」は折り返しによる2段化とした
  （ハンバーガーメニュー等の新規 UI は追加しない。操作の到達性は変わらない）
- **次タスクへの申し送り**: なし

### D3-4 テーマ切替（2026-08-30）

- **完了内容**: `lib/theme.js`（`resolveTheme` / `themeAttribute` /
  `readStoredTheme` / `storeTheme`）を追加し、`tests_js/theme.test.mjs`（6件）で
  「保存値 × OS 設定 → 適用テーマ」の表と例外安全性を固定。ヘッダーに
  3状態セレクタ（システム / ライト / ダーク）。明示選択で
  `<html data-theme="light|dark">`、system では属性を付けない。
  選択は `localStorage`（キー `clubbot-dashboard-theme`）に保存。
  CSS は3箇所構造: `:root`（ライト）/ `@media (prefers-color-scheme: dark)` 内の
  `:root:not([data-theme="light"])` / `:root[data-theme="dark"]`。
  2と3の値の同一性は `test_dashboard_style_tokens.py` が色トークンの過不足として検査
- **設計判断**: `window.localStorage` は**アクセス自体が例外を投げる環境がある**
  ため、`safeStorage()`（try/catch）で包み、読めなければ system として描画。
  読み書きの try/catch は lib 側にも二重にある
- **検証**: 実アプリ（uvicorn）でブラウザ実測 — dark 選択で `data-theme="dark"` ＋
  背景 #0d1117、light で #ffffff、system で属性なし・保存値も削除される
- **次タスクへの申し送り**: D3-6 のコントラスト実測はライト／ダーク両方の
  トークン値で行う（D0-3 の表を再確認）

### D3-3 データグリッド（2026-08-30）

- **完了内容**: ゼブラ縞（`nth-child(even)`）を廃止し、区切りは 1px 線＋
  行ホバー（`tr:hover`）だけに。ヘッダーは sticky のまま。数値列
  （number / progress 型）の th/td に `.num` を付け ` font-variant-numeric:
  tabular-nums` を適用。編集可能列の ✎ をセルのテキスト連結から
  **ヘッダーのバッジ**（`.badge-edit`、ツールチップ付き）へ変更。
  行の高さ「標準 / 詰めて」トグルをツールバーに追加（`table.grid.dense`。
  再取得せずクラス切替のみ。`aria-pressed` 付き）
- **設計判断**: 進捗グラフの色は **`--accent-chart` へ分離しない**。
  現行 `--accent`（#2f6fd0）はダーク背景と 3.88:1 で、非テキスト UI の
  AA（3:1）を満たす。トークンを増やす必要が出たら D3-6 の実測時に再判断
- **検証（ブラウザ実測 @480px）**: 12列の表が `.grid-wrap` の中だけで
  横スクロール（body は横スクロールなし）、ゼブラ廃止（偶数行が透明）、
  dense の padding 縮小（6.4px → 2.4px）、`tabular-nums` 適用を確認。
  preview.html に 12列 / 詰めて表示の見本を追加
- **次タスクへの申し送り**: D3-5 で `.grid-wrap` に max-height を入れると
  sticky ヘッダが実際に効き始める

### D3-2 bento グリッドとカードの統一（2026-08-30）

- **完了内容**: カードの面（border / `--radius-card: 16px` / `--shadow-card` / 背景）を
  `.card` **1種類だけ**に定義し、サマリー（D2-3）・グラフ（`.chart-wrap`）・
  表（`.grid-wrap`）がすべて同じカードで包まれる形にした。`.grid-wrap` /
  `.chart-wrap` はレイアウト（overflow / margin）だけ持つ。旧 `--radius-legacy-l`
  の参照は撤去。bento は `repeat(auto-fit, minmax(11rem, 1fr))` のまま
- **設計判断**: `.grid-wrap.card` だけ padding を 0 に上書き（表をカードの縁まで
  使わせ、横スクロールバーを内側に寄せない）。面の種類は1つで、余白だけの差
- **検証（ブラウザ実測）**: 角丸 16px・多層影が適用、**カードの入れ子 0 件**、
  320 / 768 / 1440px とも body の横スクロールなし、bento の列数が幅に追随
  （768px で3列）
- **次タスクへの申し送り**: D3-3 でゼブラ縞の廃止と sticky ヘッダを仕上げる

### D3-1 アプリシェル（2026-08-30）

- **完了内容**: preview.html でシェルの形を決めてから本番へ適用（受入手順どおり）。
  `index.html` のヘッダーに `#guild-slot` を追加し、**サーバー選択をヘッダーへ移動**。
  `#app` 直下を `.shell`（`nav#tabs.tabs` ＋ `.content`（#summary / #grid））に再構成。
  900px 以上: `grid-template-columns: var(--w-sidenav: 13rem) 1fr` の2カラム＋
  縦一列 sticky サイドナビ（左揃えボタン）。未満: 横スクロールの1列タブ
  （`overflow-x: auto`）。**`#tabs` / `#grid` の ID は据え置き**（app.js の参照を壊さない）
- **検証**: ブラウザの computed style で両レイアウトを実測 —
  1440px: `display: grid` / `columns: 208px 1157px` / `flex-direction: column` /
  `position: sticky`。320px: `display: block` / `row` / `overflow-x: auto` /
  **body の横スクロールなし**。キーボードのフォーカス順は DOM 順
  （ヘッダーの選択 → ナビ → コンテンツ）で視覚順と一致。
  D1-5 のキーボード操作は変更対象外（グリッド内部の構造は不変）
- **before/after**: D0-1 の before スクリーンショットが未取得（人間タスク）のため、
  preview.html 上の確認スクリーンショットで代替した
- **次タスクへの申し送り**: D1 のツールバー（検索・ページャ）はそのまま
  新シェルのコンテンツ側に載っている（表の順序は変えていない）

### D2-6 マイグレーションの排他制御（2026-08-30）

- **完了内容**: `utils/db.py` に名前付き定数 `MIGRATION_ADVISORY_LOCK_KEY`
  （`int.from_bytes(b"clubbot1", "big")`）を追加し、`_connect_pg()` 内の
  **`_migrate_versioned()` の呼び出し**を `pg_advisory_lock` で囲んだ
  （前提どおり `_migrate()` ではない。PG の入口は `_connect_pg()` →
  `_migrate_versioned()`）。ロックは**取得できるまで待ち**（`pg_try_` は使わない）、
  `finally` で確実に解放。advisory lock はセッション（接続）に紐づくため、
  プールから取った専用接続で保持する。SQLite 経路（`_migrate()`）は no-op
  （単一ファイル＋busy_timeout で元々直列。理由をコメントに記載）
- **検証**: PG 実機テスト2件を追加 —
  `test_pg_live_migration_lock_serializes_concurrent_connects`
  （2つの Database から同時 connect() し、計装した `_migrate_versioned` の実行区間が
  重ならないこと。ロックが無いと sleep 0.3s の窓で確実に重なる）と
  `test_pg_live_schema_version_is_current_after_concurrent_connect`。
  既存の Fake 接続テストへ `fetchval`（no-op）を追加
- **監査後の追修正（2026-08-30）**: 別コンテキストの diff 監査で
  「`DB_POOL_MAX_SIZE=1` だと lock 用接続がプール唯一の枠を占有し、
  `_migrate_versioned()` 内のクエリが同じプールを待って**自己デッドロック**する」
  指摘を受け、ロック保持を**プール外の専用 `asyncpg.connect()`** に変更した。
  回帰テスト `test_pg_live_connect_with_pool_of_one_does_not_deadlock`
  （pool 1 で connect が 60 秒以内に完了）を追加し、PG 実機で緑を確認
- **次タスクへの申し送り**: なし（D2 完了）

### D2-5 ダッシュボードの INFO ログ（2026-08-30）

- **完了内容**: `utils/logger.py::setup_logging()` に `filename` 引数を追加
  （**bot 側の既定は `bot.log` のまま**。シグネチャをテストで固定）。
  出力ディレクトリを環境変数 `LOG_DIR` で上書きできるようにした。
  `create_app()` が `setup_logging(level=..., filename="dashboard.log")` を呼び、
  レベルは `DASHBOARD_LOG_LEVEL` で上書き可能。
  `deploy/club-bot-dashboard.service` に `Environment=LOG_DIR=/home/ubuntu/club-bot/logs`
  を追加（**注意2の再起動ループ対策**: 相対 logs/ は ProtectHome=read-only で書けない）。
  `docs/OPERATION.md` 8.6 にログの見方を追記
- **設計判断**: サービスの `StandardOutput` を `dashboard.log` → **`dashboard.out`** へ
  変更した。アプリの RotatingFileHandler が `dashboard.log` を掴むため、
  同じファイルへ systemd の追記 fd を向けるとローテーションと競合する
  （注意1のプロセス間競合と同型の、同一ホスト内の競合）。
  範囲外ファイル `utils/logger.py` の改修はタスク本文の明示的な例外
- **検証**: `tests/test_dashboard_logging.py` 4件（filename 受け入れ / bot 既定 /
  create_app の初期化とレベル上書き / dashboard ロガーが INFO を出せる）
- **次タスクへの申し送り**: 本番適用時は `mkdir -p /home/ubuntu/club-bot/logs` が
  済んでいること（サービスの導入手順に既記載）

### D2-4 セッション既定24時間（2026-08-30）

- **完了内容**: `DEFAULT_SESSION_MAX_AGE` を `24 * 60 * 60` へ変更。
  `DASHBOARD_SESSION_MAX_AGE` での上書きは維持（両方をテストで固定）。
  `dashboard/README.md`（既定値＋「セッションと権限の鮮度」節を新設）・
  `dashboard/.env.example`・`docs/DASHBOARD_SETUP.md`（169行 / 190行相当）を同時更新
- **設計判断**: README には「古くなるのは所属ギルド一覧と manage_guild だけ。
  班長（L2）判定は毎リクエスト DB から引くので即時反映」という**区別**を明記した
  （「セッション中はなんでも古い」と書くと嘘になる）。Discord への能動的な
  再検証は仕様どおり見送り（「入れなかったもの」参照）
- **検証**: `test_session_max_age_defaults_to_24_hours` /
  `test_session_max_age_env_override_still_works` を追加。D1-6（401 → ログイン導線）
  実施済みの後なので、失効頻度の上昇は安全
- **次タスクへの申し送り**: なし

### D2-3 サマリーカード（2026-08-30）

- **完了内容**: `lib/summary.js` の `summaryCards()`（件数4枚＋自分の権限1枚。
  null なら全カード「—」）を追加し、`tests_js/summary.test.mjs`（3件）で固定。
  `selectGuild` が `/summary` を **await せずに**呼び、失敗・遅延が表の表示を
  妨げない。カードは `.bento`（`repeat(auto-fit, minmax(--w-card-min: 11rem, 1fr))`）＋
  `.card`（`--radius-card` / `--shadow-card`。§デザイン方針の bento の本命）。
  数値は `font-variant-numeric: tabular-nums`
- **検証**: `test_summary_response_shape_is_stable`（/summary の応答形）を追加。
  preview.html でカードの形（取得失敗の「—」含む）をライト／ダークで確認
- **設計判断**: `open_tasks` の重さ（list_tasks の len）は現状の規模では
  問題にならないと判断し、count API の起票は見送り（遅くなったら起票する）
- **次タスクへの申し送り**: D3-2 はこの `.card` を「唯一のカード定義」として
  グラフ・表も同じカードで包む

### D2-2 設定タブ（2026-08-30）

- **完了内容**: 表タブの末尾に「設定」を追加（内部キー `__settings__`。表ホワイト
  リストの `settings`（L4 の生テーブル）とは別物）。`GET /settings` の8項目を
  ラベル・説明つきのフォームで表示。`can_edit`（L4）が偽なら全入力を `readonly` にし
  「サーバー管理権限が必要です（閲覧のみ）」と表示。保存は `lib/settings.js` の
  `settingsDiff()`（node テスト4件）で**差分だけ**を PATCH。channel / role は
  `inputmode="numeric"` の ID 手入力（セレクタ化はしない。受入基準どおり）
- **設計判断**: **差分は1項目ずつ PATCH する。** まとめて送ると 400 の detail が
  どの項目のものか判別できない（文言マッチはしない方針）。項目ごとに送れば
  サーバーの detail をそのままその項目の下に出せる。副作用として監査ログが
  項目単位になる（8項目以内なので許容）
- **検証**: `test_l1_sees_role_ids_masked` を追加（L1 にはロール ID 実値を返さず
  「（設定済み）」。既存仕様の回帰ガード）。node 45件パス
- **次タスクへの申し送り**: D2-1 の後に実施済み（キーは CLUB_NAME で保存され、
  週次サマリーに反映される）。preview.html に設定フォームの見本を追加済み（D3-2 用）

### D2-1 GUILD_NAME → CLUB_NAME（2026-08-30）

- **完了内容**: スキーマ **v22**（着手時の SCHEMA_VERSION が 21 だったため、表の
  v20 予約から繰り下げ）。`migrations/021_club_name_key.sql` ＋
  `utils/db.py::_migrate_v22_club_name_key()` ＋ `SCHEMA_VERSION = 22`。
  `INSERT ... SELECT ... WHERE NOT EXISTS` の1文で
  (a) `GUILD_NAME` だけ → コピー / (b) 両方 → 上書きしない / (c) 無し → 何もしない。
  `EDITABLE_SETTINGS` のキーを `CLUB_NAME` へ変更（既存の settings テストも新キーへ更新）。
  検証は `tests/test_club_name_migration.py`（3ケース・冪等性・キー一致）＋
  PG 実機 `test_pg_live_club_name_copy_sql_runs_on_postgres`
- **設計判断**:
  - **旧キー `GUILD_NAME` の行は消さない**（安全側。監査と巻き戻しの余地。
    正はこの移行以降つねに `CLUB_NAME` で、読むコードはもう無い）
  - **ADR 0024（Vault）との照合**: 本移行は「利用者がダッシュボードで明示的に
    保存した値を初めて有効にする」もので、破壊・不可逆な変更を含まない
    （0024 が禁じるのは既定値による勝手なデータ変更）。ただし実装中に
    **`GUILD_NAME` は bot がギルド参加時に Discord サーバー名で自動設定している**
    （bot.py の set_if_absent）ことが判明。CLUB_NAME 未設定のギルドでは週次サマリーの
    表示が既定の「サークル」→ サーバー名に変わる。破壊なし・/setup で上書き可能なため
    許容と判断し、**2026-08-30 にユーザーがこの方針を承認した**（現行実装で確定。
    audit_log でダッシュボード編集済みギルドに絞る代替案は採らない）
  - bot.py の `GUILD_NAME` 書き込みは D2-1 の範囲外なので触っていない
    （掃除するなら別タスクで起票）
- **次タスクへの申し送り**: D2-2（設定画面）はこの後で安全に実施できる
  （キー名はもう正しい）。本番 PG への適用は v22 が自動で走る

### D1-6 401 の捕捉と再試行（2026-08-30）

- **完了内容**: `lib/errors.js` の `errorDisposition(status)`（401 → "login" /
  それ以外 → "stay"）を追加し、`tests_js/errors.test.mjs`（3件）で固定。
  `loadGrid`（表・シート・ページの全読み込み経路）と `selectGuild` の catch で
  401 なら `renderLoginPrompt()` へ（全画面置換は 401 のときだけ）。
  401 以外はグリッド内に留め（既存動作の維持）、「再試行」ボタンで直前の
  リクエスト（同じ sheet / offset）をやり直せる。`selectGuild` / `main` の
  失敗にも再試行を付けた。判定は `err.status`（数値）のみで、文言マッチなし
- **検証**: `tests/test_dashboard_auth.py::test_unauthenticated_401_response_shape_is_stable`
  を追加（/api/me は `{"authenticated": false}`、スコープ必須 API は `detail` 付き
  JSON、どちらも 401 のまま）。node 41件パス
- **次タスクへの申し送り**: D2-4（セッション24時間化）はこのタスクの後で安全に
  実施できる（401 の頻度が上がってもログイン導線に落ちる）

### D1-5 キーボード操作（2026-08-30）

- **完了内容**: `lib/keynav.js`（`gridKeyAction` = キー→操作の対応表、
  `nextCellPosition` = 端で止まる隣接移動）を追加し、`tests_js/keynav.test.mjs`
  （5件）で固定。`app.js` の `setupGridKeyboard()` が編集可能セルの行列に
  **roving tabindex**（フォーカス中のセルだけ 0、他は -1。Tab は表全体で
  1ストップ）を張り、`role="button"`・Enter / Space で編集開始・矢印キーで
  隣接セルへ移動。クリックでフォーカスが移った場合も現在地を追従。
  フォーカスリングは `td.editable:focus` の outline で明示
  （`outline: none` は書いていない）。preview.html に tabindex 付きの見本を追加
- **設計判断**: 矢印移動の対象は**編集可能セルだけの行列**（編集不可セルは
  スキップされる）。編集中（td 内に input がある間）はセル移動キーを無効化し、
  input からのバブリングで編集が再帰的に開始されるのを防いだ
- **検証**: node 38件パス（キー対応表・端のクランプ）
- **次タスクへの申し送り**: D3-1 のシェル変更でもフォーカス順が視覚順と一致する
  ことを確認する（受入基準に含まれる）

### D1-4 セル編集の確定を Enter と ✓ だけに（2026-08-30）

- **完了内容**: `lib/edit.js` の `editAction()`（イベント → commit / cancel / none の
  純粋関数）を追加し、`tests_js/edit.test.mjs`（5件）で遷移表を固定。
  `app.js` の `editableCell` を書き換え: **blur はキャンセル**（値を戻す）、
  確定は Enter と ✓ のみ、✕ / Escape でキャンセル。✓/✕ は
  `--tap-min: 44px` トークンで最小 44×44px のタップ領域
- **設計判断（「✓ を押すと取り消される」対策を二重にした）**:
  1. ✓/✕ の `pointerdown` / `mousedown` / `touchstart` で `preventDefault()`
     （blur 自体を起こさせない）
  2. blur ハンドラでも `relatedTarget` が編集 UI（`.cell-editor`）内なら何もしない
  片方が効かないブラウザ経路（タッチ→フォーカス移動など）でも取り消しにならない
- **検証**: node 33件パス。`tests/test_dashboard_edit.py` は**変更なし**で 27 passed
  （API 側の仕様は不変のため。受入基準どおり）
- **次タスクへの申し送り**: D1-5 のキーボード操作は `.cell-editor` の存在を前提に
  すること（編集開始後のフォーカスは input にある）

### D1-3 列ヘッダのソート（2026-08-30）

- **完了内容**: `TableSpec.sortable`（None = 全列。columns 自体がホワイトリスト）と
  `sortable_columns` プロパティを追加。`TableRepository._order_by()` が
  `?sort=&dir=` を検査し、**列名は必ず spec 側の綴りを使って** ORDER BY を組み立てる
  （リクエスト由来の文字列は連結しない）。NULL の並びは `(col IS NULL), col ASC|DESC`
  で SQLite / PG とも**常に末尾**へ揃えた。ルータの `_validate_sort()` が
  許可外の列・不正な dir を 400 に変換（FastAPI の 422 に任せない）。
  `export.csv` にも同じ sort/dir が効く。フロントは `lib/sort.js`
  （`nextSortState` = 昇順→降順→既定 の3状態巡回、`ariaSort`）を追加し、
  ヘッダを `<th class="sortable" aria-sort=...>` ＋ 中のボタンで実装（▲/▼ 表示）。
  表切替でソートをリセット。ソート変更時は offset を 0 に戻す
- **設計判断**:
  - `sortable` の既定は **None = 全列**とした。列名はすべて定義側から来るため
    安全性は変わらず、19表への列挙の重複を避けた（除外したい表が出たら明示する）
  - 並び替えも検索と同じく **DB の生の値**に対して行う（表示名では並ばない）
  - SQLite の文字列ソートは UTF-8 バイト順（ASCII が仮名より先）。テストの期待値も
    それに合わせた。PG はロケール依存のため、PG 実機テストは数値列で固定した
- **検証**: node 3件（3状態巡回・aria-sort）、pytest 4件（昇順/降順・NULL 末尾・
  400・CSV 連動）、PG 実機 `test_pg_live_sort_nulls_are_always_last`
  （PG の既定は DESC で NULLS FIRST になるので、SQLite では検出できない）
- **次タスクへの申し送り**: D3-3 でヘッダの見た目を変えるときは
  `.sort-button`（th 内のボタン）の存在を前提にすること

### D1-2 検索（2026-08-30）

- **完了内容**: `TableSpec.searchable`（検索対象列のホワイトリスト）を追加し、
  全19表に定義（schedule_votes はピボット表示が正のため対象外＝検索欄も出ない）。
  `TableRepository._search_where()` が OR 部分一致の WHERE を組み立て、
  `list_rows` / `count_rows` / `list_all_rows` に `q` を配線
  （total と export.csv にも同じ `q` が効く）。ルータは `?q=`（上限200字）を受け、
  searchable の無い表への `?q=` は 400。フロントは `lib/query.js` の
  `buildTableQuery()`（node テスト5件）でクエリを組み立て、ツールバーに検索欄を追加。
  検索で 0 件でも検索欄を残す。placeholder に「DB の値。表示名では検索できません」と明記
- **設計判断**:
  - **大文字小文字は `lower(col) LIKE lower(?)` で SQLite / PostgreSQL を揃えた**
    （ILIKE のドライバ別出し分けはしない。方言を1つに保つ）。`%` / `_` / `\` は
    `ESCAPE '\'` 付きでエスケープ
  - searchable は **DDL 上 TEXT の列だけ**に限定し、
    `test_searchable_columns_are_whitelisted_text_columns` が DDL と突き合わせて固定
    （PG では lower(整数列) が型エラーになるため。G1-0 と同型の事故の予防）
  - 表示名検索は仕様どおり見送り（「入れなかったもの」参照）
- **検証**: SQLite 6件（部分一致 / ワイルドカード無効化 / 対象外列 / CSV 連動 /
  400 / DDL 突き合わせ）＋ PG 実機 `test_pg_live_search_is_case_insensitive_and_escaped`
  （大文字検索・% エスケープ・count）。PG 実機はセッション末尾に Docker で実行予定
- **次タスクへの申し送り**: D1-3 のソートは同じ `buildTableQuery` に sort/dir を
  渡すだけ（実装済みの引数）。CSV にも同様に効かせること

### D1-1 ページング（2026-08-30）

- **完了内容**: `static/lib/paging.js`（`PAGE_SIZES` = 50/100/200/500、`pageInfo()` =
  hasPrev / hasNext / prevOffset / nextOffset / 表示レンジ文字列）を追加し、
  `tests_js/paging.test.mjs`（7件）で固定。`app.js` に中央ローダ `loadGrid()` を導入し、
  表・シート・ページ切替がすべてここを通る形にした。ツールバーに
  「前へ / 次へ」「N〜M 件 / 全 T 件」「表示件数セレクタ」を追加。
  先頭で前へ・末尾で次へが disabled。表・シート切替で offset を 0 に戻す。
  ピボット表（schedule_votes）の分岐にはページャを出さない。
  サーバー側は既存の limit/offset をそのまま使い、
  `tests/test_dashboard_tables.py::test_offset_paging_returns_correct_rows` が
  offset 付きリクエストの正しさ（重複なし・並び順維持・total 不変）を固定
- **設計判断**: 既定の表示件数は DEFAULT_LIMIT(200)。`state.limit` は表を
  切り替えても維持する（利用者の選好とみなす）。rangeText の 0 件は「0 件」表示
- **次タスクへの申し送り**: D1-2 / D1-3 のクエリ組み立ては `loadGrid()` の
  URLSearchParams に足すだけでよい。D1-6 の「再試行」も loadGrid の再実行で実装できる

### D0-4 コンポーネントプレビュー（2026-08-30）

- **完了内容**: `dashboard/static/preview.html` を追加。ボタン（標準/プライマリ/
  リンク/無効）・表タブ・シートタブ（選択中/省略記号）・ツールバー・カード
  （グラフ枠）・進捗バー（静的 SVG）・表（通常セル/入力中セル/セル内エラー）・
  ピボット表・空状態・読み込み中・エラーを1ページに列挙。ログイン不要・API 不要・
  データはハードコードのダミー文字列のみ。CSS は `style.css` だけ
  （`<style>` ブロック・インライン style とも 0 件。テストで固定）。
  `tests/test_dashboard_app.py::test_preview_page_is_served_but_not_linked` が
  200 応答と「index.html からリンクされていない」ことを検査する
- **設計判断**: stylesheet の参照は相対パス `style.css` にした（`/static/` 経由でも
  ディレクトリを直接配信しても解決できる。単体で開ける、の受入を満たす）
- **検証**: ブラウザでライト／ダーク両方を表示し、全要素が見えることを確認した
  （D0-1 の before スクリーンショットは人間タスク未実施のため無し）
- **次タスクへの申し送り**: D1・D2 で足す UI 要素（ページャ・検索・ソート済み
  ヘッダ・✓/✕ ボタン・設定フォーム・サマリーカード）は preview.html にも見本を
  追記していくこと（D3 が preview 先行で形を決めるため）

### D0-3 デザイントークン（2026-08-30）

- **完了内容**: `static/style.css` を全面的にトークン化した。色8種・余白スケール
  `--space-1`〜`--space-6`・微調整余白（既存レイアウト保持用の実測値）・タイポ・
  角丸（§デザイン方針の `--radius-card: 16px` / `--radius-control: 10px` /
  `--radius-input: 8px` ＋ 現行値を保持する `--radius-legacy-*`）・線幅・
  `--shadow-card`・`--motion-fast: 150ms`・`--z-sticky` を `:root` に定義し、
  ルール本体のリテラル直書きを 0 件にした。構造は
  `tests/test_dashboard_style_tokens.py`（トークンの存在 / ダーク側での色の再定義 /
  リテラル 0 件）が固定する
- **設計判断**:
  - **アクセントのみ意図的に変更**: `#4a86e8`（白文字と 3.58:1。AA 不適合）→
    `#2f6fd0`（4.88:1）。§色の受入条件が「トークン設計時に暗い方へ寄せる」と
    定めているため、この1点だけは見た目の変更を許容した。他の寸法・レイアウトは
    現行値をそのままトークンへ写した（レイアウト変更なし）
  - §デザイン方針の角丸体系（16/10/8px）は**定義だけ**して現行要素へは当てず、
    現行値は `--radius-legacy-s/m/l`（4/6/8px）で保持した。D3 で参照を体系側へ移す
  - ダーク側は色トークンの再定義のみ（`--shadow-card: none` も含む。
    「ダークでは影に頼らない」の方針）
- **コントラスト実測（WCAG AA = 4.5:1。計算はリレイティブ輝度式）**:

  | 組み合わせ | ライト | ダーク |
  |---|---|---|
  | 本文 `--fg` / `--bg` | 15.80:1 ✓ | 16.02:1 ✓ |
  | ミュート `--muted` / `--bg` | 5.25:1 ✓ | 6.50:1 ✓ |
  | アクセント上の白文字 `--accent-fg` / `--accent` | 4.88:1 ✓ | 4.88:1 ✓ |
  | 危険色 `--danger` / `--bg` | 6.57:1 ✓ | 7.51:1 ✓ |

- **次タスクへの申し送り**: D0-1（before スクリーンショット）が未実施のため、
  スクリーンショット比較は preview.html（D0-4）以降の確認で代替した。
  D3 で色を変えるときはこの表の実測をやり直すこと

### D0-2 フロントの検証土台（2026-08-30）

- **完了内容**: `app.js` から DOM に触れない純粋関数 `formatCell` / `parseInput` を
  `dashboard/static/lib/format.js`（ES モジュール）へ切り出し、`app.js` が import する形にした
  （ロジックの二重定義なし）。`index.html` の script を `type="module"` に変更。
  テストは `tests_js/format.test.mjs`（13件）に置き、Node 標準の test runner だけで検証
  （npm パッケージ・`package.json` なし）。CI に `test-frontend` ジョブを追加
  （`actions/setup-node@v4` + Node 22。ジョブ失敗は CI を落とす）。
  `dashboard/README.md` の構成表と実行方法も更新した
- **設計判断**:
  - 受入基準の `node --test tests_js/` は **Node 24 でディレクトリ引数が展開されず落ちる**
    （`Cannot find module ...	ests_js`。位置引数はグロブとして解釈される）ため、
    正式な起動形をグロブ `node --test "tests_js/*.test.mjs"` とした（CI・README も同形）
  - テストは `.mjs` 拡張子にした。lib 側は `.js` のままにする
    （`.mjs` は StaticFiles の MIME 解決が環境依存で、ブラウザがモジュールとして
    拒否しうるため）。`package.json` 無しで ESM の `.js` を import するには
    モジュール構文の自動判別が要るので、**Node 22.7 以上が必要**（CI は Node 22）
  - `file://` 直開きは ES モジュール化で不可になるが、リポジトリ内のどの文書も
    `file://` での動作確認を定めていない（起動手順は uvicorn）ため、運用手順の変更なし
- **ADR 草案（フロントの検証は Node 標準テストランナーだけで行う）**:
  - 文脈: `app.js` にテストが1件も無く、CI が Python のみ。D1〜D3 の「全テストパス」が
    フロントを検証しない
  - 選択肢: (A) npm + Jest/Vitest 等 (B) Node 標準 `node:test` のみ (C) テストしない
  - 決定: B。純粋関数を `static/lib/*.js` に切り出し、`tests_js/` から
    `node --test` で検証する
  - 理由: 配信物に1バイトも足さず（ADR 0020 と両立）、依存の追従コストゼロで
    CI に組み込める。npm を入れると lockfile・supply chain の管理が生じる
  - 却下した案: A は「依存を増やさない」明文方針に反する。C は D1〜D3 の自走が空回りする
  - 影響範囲: DOM に触れないロジックは今後 `static/lib/` に置くのが既定になる。
    Node 22.7+ が開発・CI の前提に加わる（配信物・本番サーバーには不要）
  - 覆す条件: フロントの規模が拡大し、DOM を含む結合テストが必要になったとき
- **次タスクへの申し送り**: D1 系で足す純粋関数（ページ計算・ソート状態遷移・
  エラー分岐）は最初から `static/lib/` に置くこと。`node --test` はグロブ形式で叩く
