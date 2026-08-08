# 鳥人間サークル 統合運営 Discord Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

**鳥人間サークル（人力飛行機・鳥人間コンテスト系）の運営を一元化する Discord Bot** です。
日程調整・タスク管理・班/メンバー管理・桁巻き積層記録・リマインド・レポートを
Discord 上だけで回せます。あなたのサークルのサーバーに招待するだけで導入でき、
設定はすべて Discord 上の `/setup` で行えます。

- 言語: Python 3.10 以上 / discord.py 2.x
- データ保存: SQLite（標準）/ PostgreSQL（本番構成）
- タスク連携: Todoist REST API（任意。トークンはサーバーごとに暗号化して保存）
- Google Sheets 連携: タスク・メンバー一覧のエクスポート（任意）

## 機能一覧

| 機能 | 内容 |
|---|---|
| 日程調整 | 候補日時へのリアクション投票・自動締切・未回答者への催促 DM |
| タスク管理 | 期限付きタスクの登録・班別通知・Todoist 連携（任意）・`/today` ラベル |
| 班・メンバー管理 | 班の作成・ロール紐付け・技能タグ・班をまたいだ支援候補の検索 |
| 桁巻き積層記録 | `/layer start` `/layer end` で積層作業を記録（作業時間を自動計算） |
| 機体進捗管理 | Google Sheets を正本に機体→パーツ→部品…の進捗ツリーを管理。`/progress` でドリルダウン表示・Todoist の親子構造を自動同期 |
| リマインド | 締切前の未回答催促・毎朝の期限タスク通知・期限超過の警告 |
| レポート | 週次サマリー（サークル名入り）・タスク CSV 出力・出欠率・監査ログ |

## マルチサーバー対応

この Bot は **1プロセスで複数の Discord サーバー（ギルド）を安全に扱える**
マルチテナント仕様です。全データ・設定・権限・通知は `guild_id` 単位で完全に分離され、
他のサーバーのデータが見えることはありません。サークルごとに Bot を立てる必要はなく、
1つの Bot を複数サークルで共用できます。

**参加団体はセルフサービスで完結します。** 設定はすべて Discord 上の `/setup` で行えます。
Bot の運用者による個別対応は必要ありません。

### 招待方法

1. [Discord Developer Portal](https://discord.com/developers/applications) で
   アプリケーションを作成し、Bot トークンを取得します。
2. 「OAuth2 → URL Generator」で **SCOPES: `bot` + `applications.commands`** を選択し、
   必要な権限（Send Messages / Embed Links / Add Reactions / Manage Roles など）に
   チェックして生成された URL からサーバーに招待します。
3. 招待すると `on_guild_join` が自動で初期セットアップ（ギルド登録・
   管理者ロール・`#bot-log` チャンネルの作成・コマンド同期）を行います。

詳しい権限の一覧は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。

## 導入手順（導入 → /setup → 班作成 → 運用開始）

1. **導入**: 下記クイックスタートで Bot を起動し、上記の手順でサーバーに招待します。
2. **`/setup`**: サーバーの管理者が `/setup` を実行すると、設定状況の一覧が表示されます。
   セレクトメニューから通知チャンネル・ログチャンネル・管理者ロールを対話的に設定し、
   「サークル名を設定」ボタンでレポート等に表示するサークル名を登録します。
3. **班作成**: `/setup` の「班を一括作成」ボタンで班名をカンマ区切り入力すると、
   班と対応する Discord ロールをまとめて作成できます
   （班のデフォルトはありません。各サークルが自分の班を作ります）。
   後からの追加・変更は `/team-add` `/team-remove` `/team-role` でも行えます。
4. **運用開始**: `/schedule create` で日程調整、`/task add` でタスク登録、
   `/layer start` で積層記録を開始してください。

日常の使い方は [`docs/OPERATION.md`](docs/OPERATION.md) を参照してください。

## クイックスタート（ローカル）

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

ローカルでは SQLite（`DB_PATH`）で動きます。本番では
`.env` に `DATABASE_URL=postgresql://...` を設定して PostgreSQL に切り替えます。
VPS へのデプロイを含む詳しい手順は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。

### Docker で起動する場合

リポジトリルートに Bot 単体起動用の `docker-compose.yml` があります。

```bash
cp club-bot/.env.example club-bot/.env   # DISCORD_TOKEN と ENCRYPTION_KEY を設定
docker compose up -d --build
```

- `.env` の実値はイメージに含めず、起動時に `env_file` で注入します
- SQLite DB（`club-bot/data/`）とログ（`club-bot/logs/`）はホスト側に
  ボリュームマウントされ、コンテナを再作成してもデータは残ります
- PostgreSQL の本番構成は
  [`deploy/docker-compose.postgres.yml`](deploy/docker-compose.postgres.yml) を参照してください
  （NocoDB 構成 [`deploy/docker-compose.nocodb.yml`](deploy/docker-compose.nocodb.yml) は
  レガシーとして残しています）

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | セットアップ手順書（初心者向け・ローカル動作確認 〜 VPS デプロイ） |
| [`docs/OPERATION.md`](docs/OPERATION.md) | 運用マニュアル（全コマンド一覧・権限・トラブル対応） |
| [`docs/NOCODB.md`](docs/NOCODB.md) | NocoDB 運用ガイド（レガシー） |

## モジュール構成（13 Cog）

| モジュール | 役割 |
|---|---|
| Core | 起動・設定・権限・ログ・`/ping` `/health` |
| Schedule | 日程調整・出欠投票・締切・未回答者通知 |
| Tasks | Todoist 連携タスク・`/today` ラベル付与 |
| Members | 班所属・班長・技能タグ・支援候補検索 |
| Reminders | 定期通知の統括（締切催促・期限通知・超過警告） |
| Reports | 週次サマリー・CSV出力・監査ログ |
| LayerTracking | 桁巻き積層作業の開始/終了記録 |
| Settings | チャンネル・ロール等の設定管理（管理者向け） |
| SetupWizard | `/setup` 設定ウィザード（管理者向け） |
| Teams | 班・技能タグのマスタ管理（管理者向け） |
| Sheets | Google Sheets への一方向エクスポート（任意・管理者向け） |
| TodoistAdmin | Todoist トークンの登録・状態確認・削除（管理者向け） |
| Progress | 機体進捗管理（Google Sheets 正本・Todoist 同期・`/progress` ドリルダウン表示） |

## 技術メモ

- **マルチテナント**: 全テーブルに `guild_id` を保持し、`(guild_id, ...)` 先頭の
  複合ユニーク制約・インデックスで分離。ギルド別設定は `config.for_guild(guild_id)` が
  キャッシュ付きで解決します（優先順: ギルド別 DB 設定 > 環境変数 > デフォルト）。
- **コマンド**: すべて `interaction.guild.id` でスコープ。DM からの実行は拒否します。
- **Todoist 連携**: サーバーごとに独立。トークンは `.env` には書かず、各サーバーの
  管理者が `/todoist-setup` のフォーム（Modal）から登録します（Fernet で暗号化して
  DB に保存。暗号鍵 `ENCRYPTION_KEY` は `.env` のみに保持）。
- **機体進捗管理**: 進捗データの正本はギルドごとの Google Sheets。bot は
  深さ・集計進捗率の再帰計算と Todoist 同期（20分ごと）だけを行い、
  進捗バー（SPARKLINE）・色分け・ダッシュボードはシートのネイティブ機能に
  委ねます。詳細は [`docs/OPERATION.md`](docs/OPERATION.md) の Progress 節を参照。
- **既存サーバー（旧・単一運用版）からの移行**: `.env` の `GUILD_ID` を設定したまま
  起動すれば自動移行されます。詳細は
  [`docs/MULTI_TENANT_MIGRATION.md`](docs/MULTI_TENANT_MIGRATION.md) を参照してください。

## License

MIT License — 詳細は [LICENSE](../LICENSE) を参照してください。
