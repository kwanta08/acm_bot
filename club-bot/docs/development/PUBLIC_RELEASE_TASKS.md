# 公開配布タスク管理

> **内部の作業用ドキュメントです（[development/README.md](README.md)）。**
> 書かれた時点のスナップショットで、現在のコードとは食い違う記述を含みます。
> **現状の仕様の根拠には使えません。** 使い方は [`../GUIDE.md`](../GUIDE.md)、
> 運用は [`../OPERATION.md`](../OPERATION.md) を参照してください。

他大学の鳥人間サークルへの公開配布（`DESIGN_PUBLIC_DISTRIBUTION.md`）の進捗管理表。

## 運用ルール

- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
  （ユーザーの明示指示があればフェーズ単位でまとめて実施する）
- 【人間タスク】と記されたものはエージェントは飛ばす
- タスクごとに `feat/public-<タスクID>` ブランチを切る（例 `feat/public-P1-1`）
- 完了時にチェックを入れ、「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
- フェーズ順序を守る（Phase 1 が全て完了するまで Phase 2 に着手しない）

---

## Phase 0: 公開準備

- [x] **P0-1** `bot.py` の `message_content` 特権インテントを削除する。
      `on_message` ハンドラ・`message.content` 参照・prefix コマンドのいずれも
      存在しないことを確認済み。再混入を防ぐ回帰テストも追加する
- [x] **P0-2** `README.md` を全面書き換え。「各サークルが自分で Bot を立てる」前提を捨て、
      「公開招待URLを踏むだけ」の導入手順にする。対象が鳥人間サークルであることを明示し、
      `/layer`（桁巻き積層記録）と `/progress`（機体進捗管理）が何をする機能かを説明する
- [x] **P0-3** `docs/TERMS.md`（利用規約）と `docs/PRIVACY.md`（プライバシーポリシー）を作成する。
      収集するデータ（Discord ユーザーID・表示名・所属班・出欠・作業記録）、
      保存場所、削除請求の方法を明記する
- [x] **P0-4** `ENCRYPTION_KEY` のバックアップとローテーション方針を `docs/OPERATION.md` に追記する。
      単一の鍵が全テナントの Todoist トークンを保護する構成であることを明記する
- [ ] **P0-5** 【人間タスク】Discord Developer Portal で Public Bot を ON にする

## Phase 1: /progress の DB 移行

- [x] **P1-1** 機体→パーツ→部品の木構造を保持する `progress_nodes` テーブルを設計し、
      `migrations/009` として追加する。`guild_id` スコープ必須
- [x] **P1-2** `ProgressRepository` を `repositories/` に実装する（他リポジトリの構造に合わせる）
- [x] **P1-3** `services/progress_tree.py` を Sheets 依存から DB ベースへ書き換える
- [x] **P1-4** `cogs/progress.py` を DB ベースへ切り替える。`/progress view` のドリルダウンと
      `/progress setup` の挙動は維持する
- [x] **P1-5** 既存の中央スプレッドシートから DB への一回限りの移行スクリプトを
      `scripts/` に追加する（dry-run を既定とし、`--apply` で実行）
- [x] **P1-6** `progress_sheet_service.py` / `progress_sync_service.py` の Sheets 依存を整理し、
      `GOOGLE_CREDENTIALS_PATH` なしで `/progress` が完全動作することを確認する

## Phase 2: Web ダッシュボード

- [x] **P2-1** bot とは別プロセスの FastAPI アプリの雛形を `dashboard/` に作る。
      requirements を bot 本体と分離する
- [x] **P2-2** Discord OAuth2（`identify` + `guilds` スコープ）ログインと署名付きセッションを実装する
- [x] **P2-3** 【最重要】`guild_id` スコープ強制のアクセス制御層を実装する。
      セッションで検証済みの `guild_id` 以外を絶対にリポジトリへ渡さない設計にし、
      「他ギルドのデータが取得できないこと」のテストを必ず書く
- [x] **P2-4** 読み取り専用の表グリッド画面を作る
      （tasks / members / teams / schedules・schedule_votes / layer_records / progress）
- [x] **P2-5** 表グリッドからの編集機能を追加する。監査ログ（`audit_log`）に必ず記録する
- [x] **P2-6** PostgreSQL の LISTEN/NOTIFY で、ダッシュボードの `settings` 更新を
      bot プロセスの config キャッシュへ伝播させる
- [x] **P2-7** `utils/db.py` の asyncpg プール（現状 `max_size=5`）を見直す
- [x] **P2-8** Caddy によるリバースプロキシと HTTPS 化の deploy 構成を追加する

## Phase 3: 依存削減

- [x] **P3-1** 進捗グラフの描画をブラウザ側へ移し、matplotlib 依存を撤去する（実測 -37.5MB）
- [x] **P3-2** CSV エクスポートをダッシュボードへ移し、gspread / google-auth を撤去する（-11.1MB）

---

## 完了ログ

| タスク | 完了内容 / 設計判断 / 申し送り |
|---|---|
| （初期化） | タスク管理表を作成。次は P0-1（`message_content` インテント削除）から着手する |
| （方針確認） | 2026-08-08 ユーザー判断: Phase 2/3 の要望があったが、**Phase 1 を先に完走**し、**1タスクずつ**進める方針を維持する（`migrations/` が 008 までで `progress_nodes` 未作成のため P2-4 の progress グリッドと P3-2 が Phase 1 なしでは完成しないことを確認） |
| P0-1 | intents 構築を `bot.build_intents()` に切り出して `message_content` を削除。特権インテントは `members` のみに（`presences` も不要）。`tests/test_intents.py` で intents の検証に加え、`on_message` / `message.content` / prefix コマンド / `message_content =` のソース再混入を走査（コメント・文字列リテラルは `tokenize` で除外）。`docs/SETUP.md` の Portal 手順も MESSAGE CONTENT を OFF に更新。**申し送り**: `command_prefix="!club "` は `commands.Bot` の必須引数のため残置（prefix コマンドは未定義で機能しない）。P0-2 の README 書き換え時に、必要インテントは SERVER MEMBERS のみと明記すること |
| P0-2 | README を招待URL前提の3ステップ導入（招待 → `/setup` → 運用開始）へ全面書き換え。冒頭で対象＝鳥人間サークルを明示し、`/layer` `/progress` はサブコマンド単位の専用セクションに。`/progress` の Sheets 共有が残る制約と、`message_content` を要求しない旨も記載。**設計判断**: 招待URLは P0-5（人間タスク）完了まで確定しないため、URL 欄は「公開準備中」のプレースホルダとし HTML コメントで差し替え箇所を明示した。**申し送り**: (1) P0-5 完了後に README の招待URLを差し替えること。(2) README から `docs/TERMS.md` / `docs/PRIVACY.md` へのリンクは未設置 — P0-3 で作成したら「よくある質問」の該当箇所とドキュメント表にリンクを追加すること。(3) `docs/DESIGN_PUBLIC_DISTRIBUTION.md` は git 未追跡のままなので、コミットしないと README のリンクが GitHub 上で切れる |
| P0-3 | `docs/TERMS.md` と `docs/PRIVACY.md` を作成し、README からリンク（招待をもって規約同意）。収集項目は `utils/db.py` の `TABLE_DDL` を実地確認して 12 テーブル分を列挙。**設計判断**: (1) 運営者名・連絡先・サーバー所在地は P0-5 まで確定しないため `〈…〉` プレースホルダとし、各文書冒頭に「公開前に記入が必要な項目」として明示。(2) `on_guild_remove` ハンドラが存在せず**キックしてもデータは消えない**ことを確認したため、その事実を規約・ポリシー・README の3箇所に明記し、削除請求フロー（サーバー単位 / 個人単位 / 自分で消せるコマンド）を用意。(3) `guild_directus_access` のメールアドレス欄は現在 Bot から書き込まれないため、その旨を注記して列挙。**申し送り**: P0-5 完了時にプレースホルダ3種を全文書で置換すること |
| P0-4 | `docs/OPERATION.md` に 7章「暗号鍵（ENCRYPTION_KEY）の管理」を新設（旧7章 FAQ は8章へ繰り下げ）。単一鍵が全テナントを保護する構成と紛失/漏洩/変更の影響範囲、バックアップ方針、ローテーション手順、漏洩時の緊急対応、チェックリストを記載。`SETUP.md` と `.env.example` から導線を追加。**設計判断**: (1) 再暗号化スクリプトが存在しないため、現行の推奨は「鍵差し替え + 各サーバーへ `/todoist-setup` 再実行依頼」の手順Aとし、`MultiFernet` を使う手順Bは**未実装の将来方針**として明示（実装する場合は `scripts/rotate_encryption_key.py`、dry-run 既定）。(2) 漏洩時は鍵交換より先に Todoist 側でのトークン失効が必要である点を最優先手順として明記。(3) `crypto.is_encryption_ready()` が鍵の**形式**しか検証しない事実を確認し、`/health` の ✅ を復号可能性の保証と誤解しないよう注記。**申し送り**: 鍵ローテーションを無停止で行いたくなった時点で手順Bのスクリプトを実装すること |
| P1-1 | `progress_nodes` / `progress_todoist_links` / `progress_spar_links` をスキーマ v10 として追加（`migrations/009_progress_nodes.sql` ＋ `_migrate_v10_progress_tables()`）。**設計判断**: (1) `parent_id` に FK を張らない（移行・同期で親より先に子が入る順序を許すため。孤児・循環は `progress_tree` が検出して除外）。(2) 進捗ツリーは **guild_id スコープ**のため、旧構成の「中央シート1枚を複数サーバーで共有」は廃止され、サーバーごとに独立する。(3) `to_pg_ddl` に REAL → DOUBLE PRECISION 変換を追加（PostgreSQL の REAL は4バイトで進捗率の精度が落ちる） |
| P1-2 | `ProgressRepository` を追加。ノード CRUD（`upsert_node` / 許可列ホワイトリスト方式の `update_node` / `delete_subtree`）、Todoist・桁の紐付け、`count_completed_layers`（`layer_records` から桁ごとの完了層数を集計）。**設計判断**: `update_node` の `guild_id` / `node_id` は位置引数と同名にしてキーワードで渡せなくし、テナント越境の更新を構造的に防いだ。`delete_subtree` は再帰 CTE を使わずアプリ側で辿る（ドライバ差の回避＋循環データでも停止） |
| P1-3 | `progress_tree.py` を DB ベースへ。`node_from_row` / `nodes_from_rows` / `load_tree(repo, guild_id)` を追加し、`SOURCE_*` 定数を本モジュールへ移設（`progress_sheet_service` への依存を解消）。構築・集計アルゴリズムは変更なし。`row_index` は移行スクリプトが旧シートを読むときだけ使う |
| P1-4 | `cogs/progress.py` を DB ベースへ。`/progress view` のドリルダウンと `/progress setup` ウィザードの挙動は維持（setup は `progress_todoist_links` へ upsert）。**設計判断**: (1) シートという編集 UI が無くなるため、代替として `/progress add` `/progress edit` `/progress remove` `/progress spar-link` を新設（Phase 2 のダッシュボードが来るまで /progress が使えなくなるのを防ぐ）。(2) `/progress init`（シート登録）は不要になったため削除。(3) DB 読み取りは軽いのでツリーのメモリキャッシュを廃止し常に最新を返す。(4) 通知先は「紐付け行 → settings の `PROGRESS_DEFAULT_CHANNEL_ID` → ギルド既定」の順に解決 |
| P1-5 | `scripts/migrate_progress_sheet_to_db.py`（dry-run 既定 / `--apply`）。進捗管理・Todoist対応表・桁巻き対応表＋桁マスタ・設定タブのデフォルト通知チャンネルを取り込む。node_id 等をキーに upsert するため冪等、`--replace` でやり直し可。**設計判断**: 対応表の「登録ギルドID」が別サーバーの行はスキップし、1枚のシートを共有していた場合はサーバーごとに実行する運用とした |
| P1-6 | `progress_sheet_service.py` を読み取り専用アダプタへ縮小し、シート版の同期コードを全削除。bot 本体が Sheets を一切 import しない状態にした。`tests/test_progress_no_sheets.py` で (1) 実行経路に gspread / progress_sheet_service の import が無いことを AST 検査、(2) `GOOGLE_CREDENTIALS_PATH` 未設定のまま 機体追加 → 表示 → Todoist 同期 → 桁巻き反映 → 集計が通ることを検証。README / OPERATION.md / .env.example も更新。**申し送り**: Phase 2 の P2-4 で読む progress テーブルは `progress_nodes` / `progress_todoist_links` / `progress_spar_links` の3つ。P3-2（gspread 撤去）は `/set_sheet` `/sheet_sync` と移行スクリプトが残る点の判断が必要 |
| P2-1 | `club-bot/dashboard/` に FastAPI 雛形を追加（アプリファクトリ・lifespan での DB 接続・/healthz・静的配信）。`dashboard/requirements.txt` で依存を分離し、CI にインストール手順を追加（未インストール環境ではダッシュボードのテストは自動スキップ）。**設計判断**: (1) 配置は `club-bot/dashboard/`（リポジトリ層を素直に import でき、CI の lint/test 対象にも入る）。(2) OpenAPI ドキュメントは非公開（攻撃面を増やさない）。(3) 静的ファイルはパッケージ相対で解決しカレント依存を排除 |
| P2-2 | Discord OAuth2（`identify` + `guilds`）と署名付きセッション。`/auth/login` → state 検証 → `/auth/callback` → `/api/me`。**設計判断**: (1) アクセストークンはセッションに保存せず、ログイン時に一度だけ使って破棄。(2) アクセス候補は「利用者が所属し、かつ bot も参加している」サーバーのみで、後者は `guilds` 台帳で判定するため **Bot トークンが不要**。(3) Cookie 4KB 制限の保険としてセッションのサーバー数を 50 で打ち切り |
| P2-3 | **【最重要】** `dashboard/security.py` で guild_id スコープを強制。`GuildScope` はセッション照合を通った場合のみ生成され、ルートは `ScopedGuild` / `EditorGuild` / `AdminGuild` の Annotated 依存性しか受け取らない。リポジトリへは `scope.bind(repo)`（＝`for_guild`）経由のみ。権限は L4=サーバー管理権限 / L2=班長 / L1=参加者の3段階（ロール ID による L3 判定は Bot トークンなしでは不可能）。テストで「A大学の利用者が B大学の URL を叩いて 403、本文に B大学の情報が出ない」「件数が必ず自サーバー分だけ（B大学は件数を変えて配置し混入を検出）」を検証し、AST・型注釈でハンドラが生の guild_id を受け取らないことも静的検査 |
| P2-4 | 読み取り専用の表グリッド（tasks / members / teams / schedules / schedule_votes / layer_records / progress）。`repositories/table_repository.py` は**ホワイトリスト方式**でテーブル・列・並び順・編集可否を定義し、リクエスト由来の文字列を SQL へ入れない。フロントは素の JS のみ（外部 CDN なし）。`guild_id` 列は列定義から除外 |
| P2-5 | 表グリッドからの編集（PATCH）。班長以上のみ、編集可能列のみ。変更前後を `audit_log` に `dashboard.update` として記録し、編集不可列への試みも `dashboard.update.rejected` として残す。**設計判断**: 監査ログの記録失敗で編集自体は失敗させない。他サーバーの行 ID を混ぜても `guild_id` 条件付き SELECT で 404 になる |
| P2-6 | `set_setting` / `delete_setting` が `pg_notify('clubbot_settings', guild_id)` を送り、bot が LISTEN 専用接続で購読して `config.invalidate_guild` を呼ぶ。ダッシュボード側に設定 API（GET/PATCH）を追加し、編集キーはホワイトリスト（Todoist トークン等の機密値は一覧にも値にも出さない）。**設計判断**: (1) LISTEN はプール枠を使わない別接続。(2) SQLite では通知も購読も行わない（単一プロセス前提）ため、ダッシュボード併用の本番は PostgreSQL が必須 |
| P2-7 | プール既定を `max_size=5` → `1〜10` に引き上げ、`DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE`（ダッシュボードは `DASHBOARD_DB_POOL_*`）で調整可能に。`command_timeout=30` と `max_inactive_connection_lifetime=300` を追加。`pool_stats()` を `/healthz` で公開（接続文字列は含めない）。接続数の目安は bot(10) + ダッシュボード(10) + LISTEN(1) |
| P2-8 | `deploy/Caddyfile`（Let's Encrypt 自動取得 + HSTS/CSP 等のセキュリティヘッダ。CSP は `default-src 'self'`）、`club-bot-dashboard.service`（127.0.0.1 バインド・権限最小化）、`dashboard.Dockerfile`（bot と別イメージ）、`docker-compose.dashboard.yml`。`docs/OPERATION.md` に 8 章「ダッシュボードの運用」を新設。**申し送り**: Phase 3 の P3-1（matplotlib 撤去）はダッシュボード側にグラフ描画を置く前提が整った。P3-2（gspread 撤去）は `/set_sheet` `/sheet_sync` と移行スクリプトの扱いを決める必要がある |
| P3-1 | `utils/progress_chart.py`（matplotlib で PNG 生成）を削除し、`utils/progress_bar.py`（標準ライブラリのみのテキストバー）へ置き換え。`/progress view` は画像添付をやめ Embed 内に `████░░░░ 75%` を表示する。詳細な横棒グラフは Web ダッシュボードでインライン SVG として描画（ライブラリも CDN も不使用）。**副次的な改善**: 画像を作らなくなったため **CJK フォント（fonts-noto-cjk）の導入が不要**になった。`requirements.txt` から matplotlib を削除し、再混入検出テスト（requirements・import 経路・ファイル存在）を追加 |
| P3-2 | CSV ダウンロード `GET /api/guilds/{guild_id}/tables/{table_key}/export.csv` をダッシュボードに追加（7テーブル対応・表示名見出し・BOM 付き UTF-8・監査ログ `dashboard.export`）。`cogs/sheets.py`（`/set_sheet` `/sheet_sync`）と `services/sheets_service.py` を削除し COGS を 12 に。`requirements.txt` から gspread / google-auth を撤去。**設計判断**: (1) 一覧 API が `members.csv` を拾ってしまうため CSV は `/export.csv` の独立パスにした。(2) 移行スクリプトと読み取り専用アダプタは gspread を遅延 import するため、移行時だけ `pip install gspread google-auth` する運用とし、それ以外への依存拡散をテストで検出する。**確認**: 全 Cog を import しても matplotlib / gspread / google.oauth2 が `sys.modules` に載らないことを実測 |
