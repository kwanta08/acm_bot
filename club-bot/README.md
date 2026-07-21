# 鳥人間サークル 統合運営 Discord Bot

日程調整・タスク管理（Todoist連携）・班/メンバー管理・桁巻き積層記録・
自動通知を Discord 上で一元化する統合運営 Bot です。仕様書 v1 に基づいて実装しています。
記録の正本は SQLite で、閲覧・編集 UI として NocoDB を同じ DB に接続して使えます。

- 言語: Python 3.10 以上
- Discord ライブラリ: discord.py 2.x
- データ保存: PostgreSQL（本番・NocoDB 構成）/ SQLite（ローカル開発・テスト）
- 閲覧 UI: NocoDB（Docker Compose。PostgreSQL に接続）
- タスク連携: Todoist REST API（トークンはサーバーごとに暗号化して DB 保存）
- ホスティング: さくらのVPS（Ubuntu 24.04 + systemd 常駐）

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | セットアップ手順書（初心者向け・ローカル動作確認 〜 さくらのVPS デプロイ） |
| [`docs/OPERATION.md`](docs/OPERATION.md) | 運用マニュアル（全コマンド一覧・権限・トラブル対応） |
| [`docs/NOCODB.md`](docs/NOCODB.md) | NocoDB 運用ガイド（起動・接続・権限・Sheets からの移行） |

## モジュール構成（10 Cog）

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
| Teams | 班・技能タグのマスタ管理（管理者向け） |
| TodoistAdmin | Todoist トークンの登録・状態確認・削除（管理者向け） |

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

ローカルでは SQLite（`DB_PATH`）で動きます。本番（NocoDB 構成）では
`.env` に `DATABASE_URL=postgresql://...` を設定して PostgreSQL に切り替えます
（[`docs/NOCODB.md`](docs/NOCODB.md)）。

詳細は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。

## マルチテナント対応（複数サーバー運用）

この Bot は **1プロセスで複数の Discord サーバー（ギルド）を安全に扱える**
マルチテナント仕様になっています。全テーブルが `guild_id` を保持し、
データ・設定・権限・バックグラウンド通知はすべてギルド単位で分離されます。

### 変更内容の要約

- **DB**: 全テーブルに `guild_id INTEGER NOT NULL CHECK (guild_id >= 0)` を保持
  （PostgreSQL の BIGINT へ移行しやすい設計）。`(guild_id, ...)` 先頭の複合
  ユニーク制約・インデックスを整備（例: members は `(guild_id, user_id)` が PK、
  teams は `(guild_id, team_key)` UNIQUE、settings は `(guild_id, setting_key)` PK）。
- **config**: `GUILD_ID` 環境変数は任意に（`validate()` は `DISCORD_TOKEN` のみ必須）。
  ギルド固有の設定（チャンネル ID・ロール ID 等）は `config.for_guild(guild_id)` が
  キャッシュ付きで解決（優先順: ギルド別 DB 設定 > 環境変数 > デフォルト）。
- **コマンド**: すべて `interaction.guild.id` でデータをスコープ。DM からの実行は
  ギルドを特定できないため拒否メッセージを返します。
- **バックグラウンド処理**: リマインド・自動締切は
  「参加中の全ギルド」をギルドごとにループして処理します。
- **権限**: 幹部/管理者/班長ロール ID もギルド別設定から判定します。

### 新しいサーバーを追加する場合（運用フロー）

**Bot を招待するだけで追加作業は不要です。**

1. Bot を新しいサーバーに招待すると `on_guild_join` が自動で:
   - ギルド用デフォルト設定を settings に保存（未存在時のみ）
   - 権限があれば「幹部」「Bot管理者」ロールと `#bot-log` チャンネルを
     自動作成し、ID をギルド別設定に保存（失敗してもログに残して続行します）
   - スラッシュコマンドをそのギルドへ即時同期
2. 班・技能タグは**空の状態で開始**します（固定の初期値は投入しません）。
   管理者が `/team-add` `/skill-add` で登録し、必要に応じて `/team-role` で
   既存の Discord ロールと紐付けてください。
3. あとは従来どおり `/set_channel` `/set_role` 等で必要に応じて調整してください。

### 既存サーバー（単一運用）からの移行

- `.env` の `GUILD_ID` を設定したまま起動すれば **自動移行**されます。
  `utils/db.py` の `_migrate()` が全テーブルを guild_id 付きスキーマへ再作成し、
  既存行を `GUILD_ID` でバックフィルします（起動前に `data/club.db` の
  バックアップを推奨）。
- 手動で移行する場合は [`migrations/001_add_guild_id.sql`](migrations/001_add_guild_id.sql)
  を使用してください（`:legacy_guild_id` に GUILD_ID をバインド）。
- さらに起動時に 002〜005 マイグレーション（`guilds` ギルド台帳・`audit_log`
  監査ログ・`skill_tags`・`todoist_configs` の追加、teams ロール紐付けの
  バックフィル、NocoDB 表示用ビュー）が `PRAGMA user_version` による
  スキーマバージョン管理のもと自動適用されます。
  手動適用は [`migrations/`](migrations/) 配下の各 SQL を使用してください。
- 詳細は [`docs/MULTI_TENANT_MIGRATION.md`](docs/MULTI_TENANT_MIGRATION.md) を参照。

### 制約

- Todoist 連携は**サーバー（ギルド）ごとに独立**しています。トークンは
  `.env` には書かず、各サーバーの管理者が `/todoist-setup` を実行し、
  表示されるフォーム（Modal）から登録します（Fernet で暗号化して DB に保存。
  暗号鍵 `ENCRYPTION_KEY` は `.env` のみに保持します）。未登録のサーバーでは
  Todoist 関連機能は自動的に無効になり、他サーバーのトークンやタスクは
  参照されません。
- Google Sheets 連携は廃止されました。記録の正本は PostgreSQL（ローカル開発は
  SQLite）で、閲覧・編集 UI には NocoDB を利用します（[`docs/NOCODB.md`](docs/NOCODB.md)）。
  旧 Sheets のデータは `scripts/migrate_sheets_to_db.py` で取り込めます。
