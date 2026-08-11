# 公開配布タスク管理

他大学の鳥人間サークルへの公開配布（`DESIGN_PUBLIC_DISTRIBUTION.md`）の進捗管理表。

## 運用ルール

- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
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

- [ ] **P2-1** bot とは別プロセスの FastAPI アプリの雛形を `dashboard/` に作る。
      requirements を bot 本体と分離する
- [ ] **P2-2** Discord OAuth2（`identify` + `guilds` スコープ）ログインと署名付きセッションを実装する
- [ ] **P2-3** 【最重要】`guild_id` スコープ強制のアクセス制御層を実装する。
      セッションで検証済みの `guild_id` 以外を絶対にリポジトリへ渡さない設計にし、
      「他ギルドのデータが取得できないこと」のテストを必ず書く
- [ ] **P2-4** 読み取り専用の表グリッド画面を作る
      （tasks / members / teams / schedules・schedule_votes / layer_records / progress）
- [ ] **P2-5** 表グリッドからの編集機能を追加する。監査ログ（`audit_log`）に必ず記録する
- [ ] **P2-6** PostgreSQL の LISTEN/NOTIFY で、ダッシュボードの `settings` 更新を
      bot プロセスの config キャッシュへ伝播させる
- [ ] **P2-7** `utils/db.py` の asyncpg プール（現状 `max_size=5`）を見直す
- [ ] **P2-8** Caddy によるリバースプロキシと HTTPS 化の deploy 構成を追加する

## Phase 3: 依存削減

- [ ] **P3-1** 進捗グラフの描画をブラウザ側へ移し、matplotlib 依存を撤去する（実測 -37.5MB）
- [ ] **P3-2** CSV エクスポートをダッシュボードへ移し、gspread / google-auth を撤去する（-11.1MB）

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
