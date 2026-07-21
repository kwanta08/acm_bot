# NocoDB 運用ガイド

Google Sheets 連携を廃止し、bot が直接 DB を読み書きし、
NocoDB を同じ DB に対する視覚的な閲覧・編集 UI として利用する構成の
セットアップ・運用手順をまとめる。

```
Discord ユーザー → club-bot ─┐
                             ├→ PostgreSQL（業務 DB: clubdb）← 正本
                NocoDB ──────┘        ※ NocoDB メタ DB は別 DB（nocodb_meta）
```

- **本番の業務 DB は PostgreSQL**。bot（asyncpg）と NocoDB が同じ業務 DB に接続する
- NocoDB 自身のメタデータ DB と bot 業務 DB は**別データベース**に分離する
- bot は NocoDB API に一切依存しない（NocoDB が止まっても bot は動く）
- bot が書き込みの主経路。NocoDB からの編集は閲覧・軽微な修正に限定する
- すべてのテーブルは `guild_id` でギルド分離されている
- **SQLite（data/club.db）はローカル開発・テスト専用**であり、
  本番の NocoDB 接続先としては使用しない

---

## 1. 起動手順（Docker Compose）

前提: Docker と Docker Compose が使えること（さくらのVPS の場合は
`sudo apt install docker.io docker-compose-v2` 等で導入）。

1. 環境変数ファイルを作る（秘密情報。**絶対にコミットしない**。
   `.gitignore` の `.env` パターンで除外される）。
   ```bash
   $ cd ~/club-bot/app/deploy
   $ cp .env.example .env
   $ nano .env
   ```
   ```ini
   POSTGRES_USER=clubbot
   POSTGRES_PASSWORD=<強固なパスワード>
   POSTGRES_DB=clubdb
   NOCODB_JWT_SECRET=<openssl rand -hex 32 の結果>
   ```
2. PostgreSQL と NocoDB を起動する。
   ```bash
   $ docker compose -f docker-compose.nocodb.yml up -d
   ```
   初回起動時に `deploy/initdb/01-create-nocodb-meta.sql` が実行され、
   NocoDB メタ DB（`nocodb_meta`）が業務 DB（`clubdb`）とは別に作られる。
3. bot の `.env` に `DATABASE_URL` を設定して bot を起動（または再起動）する。
   ```ini
   DATABASE_URL=postgresql://clubbot:<パスワード>@127.0.0.1:5432/clubdb
   ```
   bot 起動時にスキーマ（全テーブル・ビュー）が自動作成される。
   ログに「PostgreSQL に接続しました」「スキーマバージョンを 5 に更新しました」
   と出れば OK。
4. NocoDB にアクセスする。ポートは `127.0.0.1:8080` バインドのため、
   VPS では SSH トンネル経由を推奨する。
   ```bash
   # 手元の PC で
   $ ssh -L 8080:localhost:8080 ubuntu@<VPSのIP>
   # ブラウザで http://localhost:8080 を開く
   ```
5. 初回は管理者アカウント作成画面が出るので、運用者のメールアドレスと
   パスワードで登録する。
6. 業務 DB へ接続する: 「Connect to Data Source」→ **PostgreSQL** を選び、
   host=`postgres`（Compose 内のサービス名）、port=`5432`、
   database=`clubdb`、ユーザー・パスワードは deploy/.env の値を入力する。

停止・更新:
```bash
$ docker compose -f docker-compose.nocodb.yml down          # 停止
$ docker compose -f docker-compose.nocodb.yml pull && up -d # 更新
```

## 2. DB 接続構成

| 項目 | 内容 |
|---|---|
| 業務 DB | PostgreSQL `clubdb`（bot が asyncpg で読み書きする正本） |
| NocoDB メタ DB | PostgreSQL `nocodb_meta`（業務 DB とは別データベース） |
| bot の接続 | `.env` の `DATABASE_URL`（systemd は EnvironmentFile から読む） |
| NocoDB の接続 | Compose の `NC_DB`（メタ DB 用）＋ UI で業務 DB を外部ソースとして追加 |
| SQLite | `data/club.db`（ローカル開発・テスト専用。本番接続先ではない） |

接続情報はすべて環境変数（deploy/.env、bot の .env）で管理し、
**コミットしない**（パスワードは NocoDB の UI 接続設定にも保存される点に注意）。

## 3. 既存 SQLite からの移行

一回限りの移行スクリプト `scripts/migrate_sqlite_to_pg.py` を使う。

```bash
# 0. PostgreSQL 側のバックアップ方針を確認（下記「5. バックアップ・復元」）

# 1. dry-run で件数を確認（両 DB とも変更されない）
$ venv/bin/python scripts/migrate_sqlite_to_pg.py \
    --dsn postgresql://clubbot:<パスワード>@127.0.0.1:5432/clubdb

# 2. 実行（対象が空でない場合は --force が必要。TRUNCATE して上書き）
$ venv/bin/python scripts/migrate_sqlite_to_pg.py \
    --dsn postgresql://clubbot:<パスワード>@127.0.0.1:5432/clubdb --apply
```

- 全テーブルを FK 安全な順序でコピーし、件数を検証表示する
- IDENTITY シーケンスを最大値に修復する（明示 ID 挿入後の PK 衝突を防止）
- 秘密情報（Todoist トークン暗号文の内容等）は出力しない
- 移行後は bot の `.env` の `DATABASE_URL` を有効にして bot を再起動する

## 4. ギルド別フィルタビューの作り方

NocoDB のグリッドビューにはフィルタ機能がある。各テーブルを開き、
ビューを複製して `guild_id` でフィルタをかける。

例: ギルド ID `123456789012345678` 専用のタスク一覧

1. `tasks` テーブルを開く
2. 左上のビュー名 →「+」で新しいグリッドビューを作成（例: `tasks_g123...`）
3. 「Filter」→ `guild_id` `=` `123456789012345678` を追加
4. 必要なら `status` = `open` などの条件も追加

あらかじめ bot が作成する共有ビュー（ギルド横断）:

| ビュー | 内容 | 正本 |
|---|---|---|
| `v_attendance` | 出欠一覧（旧 attendance シート相当） | schedule_votes 等 |
| `v_team_summary` | 班ごとの所属人数・班長数（旧 team_summary 相当） | teams / members |
| `v_todoist_status` | Todoist 連携状態（暗号文を含まない） | todoist_configs |

SQL ビューをギルド別に作りたい場合は `docs/GUILD_VIEWS.sql` の雛形を
PostgreSQL 用に読み替えて使う（`v_<table>_g<guild_id>` 命名で
`WHERE guild_id = <ID>` を固定）。

## 5. バックアップ・復元（PostgreSQL）

```bash
# バックアップ（VPS 上で実行）
$ docker compose -f deploy/docker-compose.nocodb.yml exec postgres \
    pg_dump -U clubbot clubdb > backup_$(date +%Y%m%d).sql

# 復元
$ cat backup_YYYYMMDD.sql | docker compose -f deploy/docker-compose.nocodb.yml \
    exec -T postgres psql -U clubbot clubdb
```

- 日次で cron に登録するのが簡単（systemd タイマーでも可）
- bot 停止中に復元するのが安全（整合性のため）

## 6. 利用者の権限設計

| ロール | 対象者 | 権限 |
|---|---|---|
| Owner / Creator | 運用者（1〜2名） | 全テーブルの閲覧・編集・ビュー作成 |
| Viewer | 一般メンバー | 共有ビューの閲覧のみ |

運用ルール:

- 初期管理者アカウントは運用者のみとし、一般メンバーには Viewer を発行する
- データの登録・更新は原則 bot のコマンドで行い、NocoDB からの編集は
  誤記修正などの例外に留める
- `settings` テーブル（チャンネル/ロール ID 等の運用情報）も Viewer には
  非共有にする

## 7. Todoist 暗号文列を非表示にする手順

`todoist_configs` テーブルには Todoist トークンの暗号文
（`api_token_encrypted`）が含まれる。以下のいずれかで一般メンバーから隠す。

1. **テーブル単位で隠す（推奨）**: NocoDB のプロジェクト設定
   （Team & Auth / ロール管理）で、Viewer ロールから `todoist_configs`
   テーブルへのアクセスを許可しない。
2. **安全なビューだけ共有する**: `v_todoist_status`（guild_id /
   project_id / today_label_name / enabled_flag / updated_at のみ。
   暗号文を含まない）を共有ビューとして公開し、ベーステーブルは非共有にする。
3. `ENCRYPTION_KEY` は NocoDB 側には一切登録しない
   （NocoDB からは復号できないが、見せないことを原則とする）。

## 8. Google Sheets からの移行手順とロールバック

### 8.1 移行スクリプトの実行（SQLite 運用時に実施）

一回限りの移行スクリプト `scripts/migrate_sheets_to_db.py` を使う。
Sheets → SQLite へ取り込んでから、必要なら上記「3. 既存 SQLite からの移行」で
PostgreSQL へ移す順序が簡単。

```bash
# 0. 移行時のみ gspread を導入（bot 本体の依存からは削除済み）
$ venv/bin/pip install gspread google-auth

# 1. 環境変数を設定（移行時のみ使用）
$ export GOOGLE_CREDENTIALS_PATH=/home/ubuntu/club-bot/credentials.json
$ export SPREADSHEET_ID=<運用台帳ブックID>
$ export LAYER_SPREADSHEET_ID=<桁巻きブックID（同じなら不要）>

# 2. dry-run で対象件数を確認（DB は変更されない）
$ venv/bin/python scripts/migrate_sheets_to_db.py --guild-id <ギルドID>

# 3. 問題なければ実行（事前に自動で DB をバックアップする）
$ venv/bin/python scripts/migrate_sheets_to_db.py --guild-id <ギルドID> --apply
```

- 対象シート: tasks / members / attendance / 桁別シート
  （team_summary と audit_log は DB のビュー・テーブルが正本のため取り込まない）
- 出力: シートごとに 入力行数 / 移行数 / スキップ数 / エラー詳細
  （行番号と理由）。秘密情報は出力しない
- 冪等: 自然キーで重複検知するため、再実行しても重複行は増えない
- 表示名からユーザーを一意に解決できない行（attendance・桁記録）は
  スキップされる（件数は出力される）

### 8.2 ロールバック

- スクリプトは INSERT のみで UPDATE/DELETE を行わない
- `--apply` 実行前に `data/club.db.bak.<日時>` が自動作成される。
  問題があれば Bot を停止し、バックアップを差し戻して再起動する
- Sheets 側は読み取りしかしないため無変更（ロールバック不要）。
  移行完了後は Sheets の共有設定を閲覧専用に変更しておくと安全

---

## 参考: 旧 Sheets 機能との対応表

| 旧 Google Sheets | 移行後 |
|---|---|
| tasks シート | `tasks` テーブル（bot が直接読み書き） |
| attendance シート | `v_attendance` ビュー（正本: schedule_votes） |
| members シート | `members` テーブル |
| team_summary シート | `v_team_summary` ビュー |
| audit_log シート | `audit_log` テーブル（管理者操作の証跡） |
| 日程調整シート | `schedules` / `schedule_options` / `schedule_votes` テーブル |
| 桁別シート | `layer_records` / `layer_keta` テーブル |
