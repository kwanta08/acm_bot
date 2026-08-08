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
- [ ] **P0-2** `README.md` を全面書き換え。「各サークルが自分で Bot を立てる」前提を捨て、
      「公開招待URLを踏むだけ」の導入手順にする。対象が鳥人間サークルであることを明示し、
      `/layer`（桁巻き積層記録）と `/progress`（機体進捗管理）が何をする機能かを説明する
- [ ] **P0-3** `docs/TERMS.md`（利用規約）と `docs/PRIVACY.md`（プライバシーポリシー）を作成する。
      収集するデータ（Discord ユーザーID・表示名・所属班・出欠・作業記録）、
      保存場所、削除請求の方法を明記する
- [ ] **P0-4** `ENCRYPTION_KEY` のバックアップとローテーション方針を `docs/OPERATION.md` に追記する。
      単一の鍵が全テナントの Todoist トークンを保護する構成であることを明記する
- [ ] **P0-5** 【人間タスク】Discord Developer Portal で Public Bot を ON にする

## Phase 1: /progress の DB 移行

- [ ] **P1-1** 機体→パーツ→部品の木構造を保持する `progress_nodes` テーブルを設計し、
      `migrations/009` として追加する。`guild_id` スコープ必須
- [ ] **P1-2** `ProgressRepository` を `repositories/` に実装する（他リポジトリの構造に合わせる）
- [ ] **P1-3** `services/progress_tree.py` を Sheets 依存から DB ベースへ書き換える
- [ ] **P1-4** `cogs/progress.py` を DB ベースへ切り替える。`/progress view` のドリルダウンと
      `/progress setup` の挙動は維持する
- [ ] **P1-5** 既存の中央スプレッドシートから DB への一回限りの移行スクリプトを
      `scripts/` に追加する（dry-run を既定とし、`--apply` で実行）
- [ ] **P1-6** `progress_sheet_service.py` / `progress_sync_service.py` の Sheets 依存を整理し、
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
| P0-1 | intents 構築を `bot.build_intents()` に切り出して `message_content` を削除。特権インテントは `members` のみに（`presences` も不要）。`tests/test_intents.py` で intents の検証に加え、`on_message` / `message.content` / prefix コマンド / `message_content =` のソース再混入を走査（コメント・文字列リテラルは `tokenize` で除外）。`docs/SETUP.md` の Portal 手順も MESSAGE CONTENT を OFF に更新。**申し送り**: `command_prefix="!club "` は `commands.Bot` の必須引数のため残置（prefix コマンドは未定義で機能しない）。P0-2 の README 書き換え時に、必要インテントは SERVER MEMBERS のみと明記すること |
