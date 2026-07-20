# 鳥人間サークル 統合運営 Discord Bot

日程調整・タスク管理（Todoist連携）・班/メンバー管理・桁巻き積層記録（Google Sheets連携）・
自動通知を Discord 上で一元化する統合運営 Bot です。仕様書 v1 に基づいて実装しています。

- 言語: Python 3.10 以上
- Discord ライブラリ: discord.py 2.x
- データ保存: SQLite
- タスク連携: Todoist REST API
- 表計算連携: Google Sheets API（gspread / サービスアカウント）
- ホスティング: さくらのVPS（Ubuntu 24.04 + systemd 常駐）

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | セットアップ手順書（初心者向け・ローカル動作確認 〜 さくらのVPS デプロイ） |
| [`docs/OPERATION.md`](docs/OPERATION.md) | 運用マニュアル（全コマンド一覧・権限・トラブル対応） |

## モジュール構成（8 Cog）

| モジュール | 役割 |
|---|---|
| Core | 起動・設定・権限・ログ・`/ping` `/health` |
| Schedule | 日程調整・出欠投票・締切・未回答者通知 |
| Tasks | Todoist 連携タスク・`/today` ラベル付与 |
| Members | 班所属・班長・技能タグ・支援候補検索 |
| Reminders | 定期通知の統括（締切催促・期限通知・超過警告・Sheets同期） |
| Reports | 週次サマリー・CSV出力・監査ログ |
| Sheets | Google Sheets 同期（全行置換 / 監査ログ追記） |
| LayerTracking | 桁巻き積層作業の開始/終了記録・桁別シート追記 |

## クイックスタート（ローカル）

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # DISCORD_TOKEN を最低限設定（GUILD_ID は任意）
venv/bin/python bot.py
```

詳細は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。

## マルチテナント対応（複数サーバー運用）

この Bot は **1プロセスで複数の Discord サーバー（ギルド）を安全に扱える**
マルチテナント仕様になっています。全テーブルが `guild_id` を保持し、
データ・設定・権限・バックグラウンド通知はすべてギルド単位で分離されます。

### 変更内容の要約

- **DB**: 全12テーブルに `guild_id INTEGER NOT NULL CHECK (guild_id >= 0)` を追加
  （PostgreSQL の BIGINT へ移行しやすい設計）。`(guild_id, ...)` 先頭の複合
  ユニーク制約・インデックスを整備（例: members は `(guild_id, user_id)` が PK、
  teams は `(guild_id, team_key)` UNIQUE、settings は `(guild_id, setting_key)` PK）。
- **config**: `GUILD_ID` 環境変数は任意に（`validate()` は `DISCORD_TOKEN` のみ必須）。
  ギルド固有の設定（チャンネル ID・ロール ID 等）は `config.for_guild(guild_id)` が
  キャッシュ付きで解決（優先順: ギルド別 DB 設定 > 環境変数 > デフォルト）。
- **コマンド**: すべて `interaction.guild.id` でデータをスコープ。DM からの実行は
  ギルドを特定できないため拒否メッセージを返します。
- **バックグラウンド処理**: リマインド・自動締切・Sheets 定期同期は
  「参加中の全ギルド」をギルドごとにループして処理します。
- **権限**: 幹部/管理者/班長ロール ID もギルド別設定から判定します。

### 新しいサーバーを追加する場合（運用フロー）

**Bot を招待するだけで追加作業は不要です。**

1. Bot を新しいサーバーに招待すると `on_guild_join` が自動で:
   - ギルド用デフォルト設定を settings に保存（未存在時のみ）
   - 初期8班（設計/翼/CFRP/駆動/プロペラ/電装/フェアリング/パイロット）を作成
   - 権限があれば「幹部」「Bot管理者」「各班リーダー」「各班」ロールと
     `#bot-log` チャンネルを自動作成し、ID をギルド別設定に保存
     （失敗してもログに残して続行します）
   - スラッシュコマンドをそのギルドへ即時同期
2. あとは従来どおり `/set_channel` `/set_role` 等で必要に応じて調整してください。

### 既存サーバー（単一運用）からの移行

- `.env` の `GUILD_ID` を設定したまま起動すれば **自動移行**されます。
  `utils/db.py` の `_migrate()` が全テーブルを guild_id 付きスキーマへ再作成し、
  既存行を `GUILD_ID` でバックフィルします（起動前に `data/club.db` の
  バックアップを推奨）。
- 手動で移行する場合は [`migrations/001_add_guild_id.sql`](migrations/001_add_guild_id.sql)
  を使用してください（`:legacy_guild_id` に GUILD_ID をバインド）。
- 詳細は [`docs/MULTI_TENANT_MIGRATION.md`](docs/MULTI_TENANT_MIGRATION.md) を参照。

### 制約

- Google Sheets 連携はスプレッドシートがグローバル1冊のため、複数ギルドで
  有効化すると同一シートへの上書き競合が発生します（実質レガシー単一ギルド
  向け機能です）。Todoist 連携もトークン単位でグローバルです。
