# club-bot — 技術メモ

Bot 本体のコードです。**製品としての説明・導入手順は
[リポジトリルートの README](../README.md) にあります。**
このファイルは「コードを読む人・動かす人」向けの補足です。

| 目的 | 読むもの |
|---|---|
| Bot の使い方を知りたい | [`docs/GUIDE.md`](docs/GUIDE.md) |
| 自分でホストしたい | [`docs/SETUP.md`](docs/SETUP.md) → [`docs/OPERATION.md`](docs/OPERATION.md) |
| 開発を始めたい | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| ドキュメントを探したい | [`docs/README.md`](docs/README.md) |

---

## 動かす

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # DISCORD_TOKEN と ENCRYPTION_KEY を最低限設定（GUILD_ID は任意）
venv/bin/python bot.py
```

`ENCRYPTION_KEY` の生成:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Discord Developer Portal で ON にする特権インテントは **SERVER MEMBERS のみ**。
  `MESSAGE CONTENT` と `PRESENCE` は **OFF のまま**にしてください
- 起動すると最小権限の招待URLがログに出力されます
- ローカルは SQLite（`DB_PATH`）、本番は `DATABASE_URL=postgresql://...` で
  PostgreSQL に切り替わります

### Docker

リポジトリルートに Bot 単体起動用の `docker-compose.yml` があります。

```bash
cp club-bot/.env.example club-bot/.env   # DISCORD_TOKEN と ENCRYPTION_KEY を設定
docker compose up -d --build
```

`.env` の実値はイメージに含めず、起動時に `env_file` で注入します。
SQLite DB（`data/`）とログ（`logs/`）はホスト側にボリュームマウントされるため、
コンテナを再作成してもデータは残ります。

PostgreSQL の本番構成は
[`deploy/docker-compose.postgres.yml`](deploy/docker-compose.postgres.yml)、
ダッシュボードは [`deploy/docker-compose.dashboard.yml`](deploy/docker-compose.dashboard.yml)。
NocoDB 構成（[`deploy/docker-compose.nocodb.yml`](deploy/docker-compose.nocodb.yml)）は
レガシーで、現行構成では使いません。

## テスト

```bash
venv/bin/ruff check .
venv/bin/python -m pytest tests/ -q
```

CI は SQLite（Python 3.10 / 3.11 / 3.12）と PostgreSQL 16 の両方で回します。
詳細と PostgreSQL 経路の回し方は [`../CONTRIBUTING.md`](../CONTRIBUTING.md) を参照。

---

## ディレクトリ構成

| ディレクトリ | 役割 |
|---|---|
| `bot.py` | エントリポイント。Cog の読み込み・`on_guild_join` の自動セットアップ・自動ジョブの登録 |
| `config.py` | 設定の解決。`config.for_guild(guild_id)` がギルド別設定をキャッシュ付きで返す |
| `cogs/` | 機能ごとのスラッシュコマンド群（19 Cog） |
| `services/` | ドメインロジックと外部連携（Todoist・日程調整・積層記録・進捗集計） |
| `repositories/` | DB アクセス。**全メソッドが第1引数に `guild_id` を必須で取る** |
| `utils/` | DB 接続（`db.py`）・権限判定（`permissions.py`）・暗号化（`crypto.py`）・通知 |
| `dashboard/` | Web ダッシュボード。**bot とは別プロセス**の FastAPI アプリ（[README](dashboard/README.md)） |
| `migrations/` | スキーマ migration（起動時に自動適用） |
| `deploy/` | systemd ユニット・Caddyfile・Docker Compose 構成 |
| `scripts/` | 一回限りの移行スクリプト・検証スクリプト |
| `tests/` | pytest |
| `docs/` | ドキュメント一式（[地図](docs/README.md)） |

## 設計の要点

### マルチテナント

**このプロジェクトで最も壊してはいけない性質**です。1インスタンスが複数サークルの
サーバーを同時にホストするため、ここが崩れると「他大学のデータが見える」事故になります。

- 全テーブルが `guild_id` を保持し、`(guild_id, ...)` を先頭にした複合ユニーク制約・
  インデックスで分離する
- リポジトリ層のメソッドは `guild_id` を必須引数で受け取り、全 SQL に条件を付ける
- コマンドは `interaction.guild.id` でスコープし、**DM からの実行は拒否**する
- ギルド別設定は `config.for_guild(guild_id)` 経由で解決する
  （優先順: ギルド別DB設定 > 環境変数 > デフォルト）
- ダッシュボードは**セッションで検証済みの `guild_id` 以外をリポジトリへ渡さない**。
  URL クエリやリクエストボディの `guild_id` を信用しない（[ADR 0008](docs/adr/0008-dashboard-guild-scope.md)）
- Discord API 呼び出しは `discord.HTTPException` を捕捉し、あるギルドでの失敗が
  他ギルドへ波及しないようにする

### インテント

特権インテントは **`members` のみ**です。`message_content` は要求せず、
`on_message` ハンドラも prefix コマンドも持ちません。
**再混入は `tests/test_intents.py` の回帰テストで検出します。**

### 機体進捗の集計

正本は DB の `progress_nodes`（ギルドごとに独立した隣接リスト・深さ無制限）です。
Bot が 20 分ごとに、深さと集計進捗率の再計算、Todoist と桁巻き（`layer_records`）の
同期を行います。

Google Sheets とサービスアカウントは**不要**です（依存は撤去済み。旧・中央スプレッドシート
からの取り込みは `scripts/migrate_progress_sheet_to_db.py`）。
**Sheets 依存の再混入は `tests/test_progress_no_sheets.py` の回帰テストで検出します。**

### Todoist 連携

サーバーごとに独立しています。トークンは `/todoist-setup` の Modal から受け取り、
Fernet で暗号化して DB に保存します（コマンド引数ではないため履歴に残りません）。

**暗号鍵 `ENCRYPTION_KEY` はホスト側の `.env` のみに保持します。この鍵1本が
全テナントのトークンを保護する**ため、バックアップ・ローテーション・漏洩時の手順を
[`docs/OPERATION.md`](docs/OPERATION.md) の 7 章にまとめてあります。

### データの持ち出しと削除

`/data export` と `/season rollover` は、`repositories/table_repository.py` の
`TABLES`（**主要14テーブル**）を CSV 群にして ZIP で返します。サーバーIDと
Todoist トークンは含めません。

同じ `TABLES` がダッシュボードの表示・編集対象でもあるため、
**テーブルを増減したときは export の説明も同時に直ります**。
数字の直し忘れは `tests/test_docs_commands.py::test_the_export_table_count_matches_the_whitelist`
がドキュメントと実装の docstring の両方を検査して落とします。

`/data delete` はサーバー名の入力による二段階確認で、実行は翌 04:00。
それまでは `/data delete-cancel` で取り消せます。Bot をキックした場合は
30 日後に自動削除されます。

### データベース

| 用途 | DB |
|---|---|
| 本番 | PostgreSQL（asyncpg） |
| 開発・テスト | SQLite（aiosqlite） |

SQLite は型に寛容で `'5'` を `5` として扱うため、**SQLite の CI だけ緑で
PostgreSQL の本番で落ちる**類の不具合があり得ます。DB ドライバに触る変更では
PostgreSQL 経路も回してください（CI の `test-postgres` ジョブが担当します）。

## License

MIT License — 詳細は [LICENSE](../LICENSE) を参照してください。
