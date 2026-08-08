# Directus 運用ガイド（推奨の閲覧 UI）

bot が直接 DB を読み書きし、**Directus** を同じ DB に対する
視覚的な閲覧・編集 UI として利用する構成のセットアップ・運用手順をまとめる。

```
Discord ユーザー → club-bot ─┐
                             ├→ PostgreSQL（業務 DB: clubdb）← 正本
        各サークルの管理者 ──┤   ※ Directus のメタテーブル（directus_*）も
                  Directus ──┘      同じ DB 内に作られる
```

- **本番の業務 DB は PostgreSQL**。bot（asyncpg）と Directus が同じ DB に接続する
- bot は Directus API に依存しない（Directus が止まっても bot は動く）
- bot が書き込みの主経路。Directus からの編集は閲覧・軽微な修正に限定する
- すべてのテーブルは `guild_id` でギルド分離されている
- **SQLite（data/club.db）はローカル開発・テスト専用**であり、
  本番の Directus 接続先としては使用しない

## NocoDB ではなく Directus を使う理由

| | NocoDB（レガシー） | Directus（推奨） |
|---|---|---|
| ギルド分離 | テーブルごとに `guild_id` 固定のフィルタビューを**手動で複製**（[`NOCODB.md`](NOCODB.md) 4章） | ユーザーのカスタムフィールドを使った**動的フィルタを1回設定するだけ** |
| 団体追加時の作業 | 参加団体×テーブル数だけビュー作成が必要 | **不要**（`/directus-setup` で管理者が自分で発行） |
| 分離の担保 | ビューの作り忘れ・共有ミスが漏洩に直結 | 権限フィルタが全リクエストに適用される |

Directus は権限フィルタに `$CURRENT_USER.guild_id` を書けるため、
「ログイン中のユーザーの `guild_id` と一致する行だけ返す」という規則を
**コレクションごとに一度だけ**書けばよい。以降は新しいサークルが増えても
運用者の追加作業は発生しない。

---

## 1. 起動手順（Docker Compose）

前提:

- Docker と Docker Compose が使えること（さくらのVPS の場合は
  `sudo apt install docker.io docker-compose-v2` 等で導入）
- **メモリ 2GB 以上**（Directus 公式の推奨下限）。1GB では OOM Killer に
  よる強制終了が繰り返し発生し、管理画面が空白ページになる。
  NocoDB も併用する場合は 4GB を見込むこと（6.2 参照）

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
   DIRECTUS_KEY=<openssl rand -hex 32 の結果>
   DIRECTUS_SECRET=<openssl rand -hex 32 の結果（KEY とは別の値）>
   DIRECTUS_ADMIN_EMAIL=<運用者のメールアドレス>
   DIRECTUS_ADMIN_PASSWORD=<強固なパスワード>
   ```
2. PostgreSQL と Directus を起動する。
   ```bash
   $ docker compose -f docker-compose.directus.yml up -d
   ```
3. bot の `.env` に `DATABASE_URL` を設定して bot を起動（または再起動）する。
   ```ini
   DATABASE_URL=postgresql://clubbot:<パスワード>@127.0.0.1:5432/clubdb
   ```
   bot 起動時にスキーマ（全テーブル・ビュー）が自動作成される。
   ログに「PostgreSQL に接続しました」「スキーマバージョンを 9 に更新しました」
   と出れば OK。
4. Directus にアクセスする。ポートは `127.0.0.1:8055` バインドのため、
   VPS では SSH トンネル経由を推奨する。
   ```bash
   # 手元の PC で
   $ ssh -L 8055:localhost:8055 ubuntu@<VPSのIP>
   # ブラウザで http://localhost:8055 を開く
   ```
5. `deploy/.env` の `DIRECTUS_ADMIN_EMAIL` / `DIRECTUS_ADMIN_PASSWORD` で
   ログインする（初回起動時に自動作成される運用者アカウント）。

停止・更新:
```bash
$ docker compose -f docker-compose.directus.yml down          # 停止
$ docker compose -f docker-compose.directus.yml pull && \
  docker compose -f docker-compose.directus.yml up -d         # 更新
```

### NocoDB との構成上の違い（重要）

NocoDB はメタデータ DB を業務 DB と別データベース（`nocodb_meta`）に
分離できたが、**Directus は自身のメタデータテーブル（`directus_*`）を
業務 DB（`clubdb`）内に直接作成する**。これは Directus の仕様であり、
分離できない。運用上の含意:

- `pg_dump clubdb` のバックアップに `directus_*` も一緒に含まれる
  （復元時も一体で戻るため、運用としてはむしろ単純）
- **`directus_` プレフィックスは Directus の予約名前空間**であり、
  bot 側のテーブルには絶対に使わない。過去にアクセス発行状況テーブルを
  `directus_access` という名前で作ってしまい、Directus 11 の同名システム
  テーブル（ユーザー/ロールとポリシーの紐付け）と衝突して Directus が
  初期化不能になる不具合があった（現在は `guild_directus_access` に改名済み。
  6章のトラブルシュートを参照）。回帰は
  `tests/test_directus_admin.py::test_schema_has_no_directus_prefixed_table`
  が防いでいる

---

## 2. グローバル設定（運用者が一度だけ行う）

ここが本構成の要である。**この設定を1回行えば、以降は新しいサークルが
増えても運用者の作業は発生しない**。

Directus のスキーマ設定をコードから自動投入する仕組みは意図的に用意していない
（Directus のバージョン差異でスキーマスナップショットが壊れるリスクを避けるため）。
以下の手順は Directus の管理画面から手動で行う。

### 2.1 `directus_users` に `guild_id` フィールドを追加

**Settings → Data Model → `directus_users` → Create Field**

| 項目 | 値 |
|---|---|
| Key | `guild_id` |
| Type | **Big Integer**（`bigint`） |
| Nullable | 許可する（チェックを外さない） |

> ⚠️ **Type は必ず Big Integer にすること。** Discord のギルド ID は
> 64bit の Snowflake（約 1.4×10¹⁸）であり、通常の Integer（`int4`,
> 上限約 2.1×10⁹）では格納できずエラーになる。bot 側でも同じ理由で
> 全テーブルの `guild_id` を `BIGINT` にしている（スキーマ v6）。

Nullable にするのは fail-closed のためである。`guild_id` が未設定の
ユーザーはフィルタに一致する行が無く、**何も見えない**状態になる。

### 2.2 Policy「サークル管理者」を作る

**Settings → Access Control → Policies → Create Policy**（名前: `サークル管理者`）

対象コレクションごとに **Read / Update** の権限を追加し、
**Item Permissions（カスタムフィルタ）** に次を設定する:

```json
{ "guild_id": { "_eq": "$CURRENT_USER.guild_id" } }
```

**権限を付与するコレクション:**

| コレクション | 内容 |
|---|---|
| `teams` | 班マスタ |
| `members` | メンバー名簿 |
| `schedules` / `schedule_options` / `schedule_votes` | 日程調整・出欠 |
| `tasks` | タスク |
| `layer_sessions` / `layer_records` / `layer_keta` | 積層作業の記録 |
| `skill_tags` | 技能タグ マスタ |

**権限を付与しない（＝一切見えない）コレクション:**

| コレクション | 理由 |
|---|---|
| `todoist_configs` | Todoist トークンの**暗号文**を含む |
| `settings` | チャンネル/ロール ID 等の運用情報 |
| `guilds` | 全参加ギルドの一覧（他団体の存在が見える） |
| `audit_log` | 管理操作の証跡 |
| `reminders_log` | 通知の内部ログ |
| `todoist_sections` | Todoist 連携の内部データ |
| `guild_directus_access` | Directus 発行状況（他団体のメールアドレスを含む） |
| `schema_meta` | スキーマバージョン |
| `v_todoist_status` | Todoist 連携状態（暗号文は含まないが運用情報） |

> `settings` と `todoist_sections` は複合主キー
> （`(guild_id, setting_key)` / `(guild_id, section_id)`）であり、
> Directus は単一列の主キーを要求するため、そもそもコレクションとして
> 登録できない。結果的に二重に守られている。

Delete 権限は付与しないことを推奨する（誤操作による記録の消失を防ぐ）。
データの登録・更新は原則 bot のコマンドで行い、Directus からの編集は
誤記修正などの例外に留める運用とする。

### 2.3 Role「サークル管理者」を作り、Policy を紐付ける

**Settings → Access Control → Roles → Create Role**（名前: `サークル管理者`）
→ 作成した Policy「サークル管理者」を割り当てる。

作成後、URL または詳細画面から **Role の UUID** を控える
（例: `a1b2c3d4-....`）。これを bot の `.env` の `DIRECTUS_ROLE_ID` に設定する。

### 2.4 bot 用の管理トークンを発行する

**Settings → Users → 運用者の管理ユーザー → Token → Generate**

生成された Static Token を bot の `.env` の `DIRECTUS_ADMIN_TOKEN` に設定する。

> ⚠️ このトークンは **Directus 全体の管理権限**を持つ。`.env` のみに保持し、
> 絶対にコミット・共有しないこと。漏洩した場合は Directus 側で
> トークンを再生成すれば無効化できる。

### 2.5 bot の `.env` を設定して再起動

```ini
DIRECTUS_URL=http://localhost:8055
DIRECTUS_ADMIN_TOKEN=<2.4 で生成した Static Token>
DIRECTUS_ROLE_ID=<2.3 で控えた Role の UUID>
```

```bash
$ sudo systemctl restart club-bot
```

3つすべてが揃うまで `/directus-setup` は「未設定です」という案内を返す
（エラーにはならない）。

### 2.6 （任意）出欠集計ビューを見せる

`v_attendance`（出欠一覧）と `v_team_summary`（班ごとの人数）は
`guild_id` を持つ読み取り専用ビューであり、同じ権限フィルタで公開できる。

ただし Directus はデータベースビューの自動検出に対応していないバージョンが
あり、**必須手順ではない**。公開したい場合は Data Model からビューを
コレクションとして登録し（主キーには一意な列を指定する）、2.2 と同じ
フィルタで Read 権限のみを付与する。うまく登録できない場合は、
テーブル（`schedule_votes` 等）の公開だけで運用しても支障はない。

---

## 3. 各サークルへのアカウント発行（`/directus-setup`）

グローバル設定が済んでいれば、以降は **各サークルの管理者が Discord 上で
自分で発行できる**。運用者の作業は発生しない。

| コマンド | 権限 | 説明 |
|---|---|---|
| `/directus-setup` | L4 | 自サーバー用の Directus アカウントを発行（招待メールを送信） |
| `/directus-status` | L4 | 発行状況（メールアドレス・状態・日時）を表示 |
| `/directus-revoke` | L4 | 発行済みアカウントを失効（Directus 側で停止） |

**発行の流れ:**

1. サークルの管理者が Discord で `/directus-setup` を実行する
2. 管理者にだけ見えるメッセージとボタンが表示される
3. ボタンから開くフォーム（Modal）にメールアドレスを入力する
4. bot が Directus に招待を送り、そのユーザーの `guild_id` に
   自サーバーの ID を書き込む
5. 届いた招待メールのリンクからパスワードを設定してログインする

**セキュリティ上の性質:**

- メールアドレスはコマンドの引数では受け取らない（オプション値は Discord の
  履歴に残るため）。Modal の入力値は履歴に残らない
- **パスワードは bot が生成も保存もしない**。Directus のメール招待フローに委ねる。
  `guild_directus_access` テーブルに秘密情報は保存されない
- 1つの Directus ユーザーには `guild_id` を1つしか設定できないため、
  **同じメールアドレスを複数のサークルで使うことはできない**
  （既に登録済みのアドレスを指定すると、その旨を案内して処理を中止する）
- 発行・失効はすべて `audit_log` に記録される

---

## 4. 招待メールが届かない場合（SMTP フォールバック）

`deploy/.env` の `EMAIL_*`（SMTP）が未設定・不正だと、Directus は
起動するが招待メールを送信できない。この場合でも発行自体は成功しており、
Directus 側にはユーザーが作成されている。

運用者が管理画面から招待リンクを取得して本人に転送する:

1. Directus の **Settings → Users** で該当ユーザー（status が
   `Invited`）を開く
2. 「Invite」を再送するか、パスワードを管理者が直接設定して
   本人に安全な手段（DM 等）で伝える
3. 本人がログイン後、**自分でパスワードを変更**してもらう

恒久対策として `deploy/.env` に SMTP を設定して
`docker compose -f docker-compose.directus.yml up -d` で再作成する:

```ini
EMAIL_FROM=no-reply@example.com
EMAIL_TRANSPORT=smtp
EMAIL_SMTP_HOST=smtp.example.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USER=<SMTP ユーザー>
EMAIL_SMTP_PASSWORD=<SMTP パスワード>
EMAIL_SMTP_SECURE=false
```

`DIRECTUS_PUBLIC_URL` が実際にアクセスできる URL になっていないと、
メール内のリンクが開けない点にも注意する。

---

## 5. バックアップ・復元（PostgreSQL）

```bash
# バックアップ（VPS 上で実行）
$ docker compose -f deploy/docker-compose.directus.yml exec postgres \
    pg_dump -U clubbot clubdb > backup_$(date +%Y%m%d).sql

# 復元
$ cat backup_YYYYMMDD.sql | docker compose -f deploy/docker-compose.directus.yml \
    exec -T postgres psql -U clubbot clubdb
```

- 日次で cron に登録するのが簡単（systemd タイマーでも可）
- bot 停止中に復元するのが安全（整合性のため）
- このダンプには Directus のメタデータ（`directus_*`）も含まれるため、
  権限設定ごと復元される

---

## 6. トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| `/directus-setup` が「未設定です」と返す | bot の `.env` に `DIRECTUS_URL` / `DIRECTUS_ADMIN_TOKEN` / `DIRECTUS_ROLE_ID` のいずれかが無い。3つ揃えて bot を再起動する |
| 「既に Directus に登録されています」 | そのメールアドレスのユーザーが既にある。別のアドレスを使う（1アカウント＝1サーバー） |
| `DIRECTUS_API_FAILED` | Directus に到達できないか、管理トークン・ロール ID が不正。`docker compose logs directus` と `.env` を確認する |
| ログインできたが**何も表示されない** | そのユーザーの `guild_id` が未設定か、Policy のフィルタが未設定。Settings → Users で `guild_id` を確認する（fail-closed の正常動作） |
| **他サークルのデータが見える** | Policy のフィルタが抜けているコレクションがある。2.2 を見直す。管理者ロールのユーザーは全件見える点にも注意 |
| `value out of int32 range` | `guild_id` フィールドを Integer で作成している。**Big Integer** に修正する（2.1） |
| メールが届かない | SMTP 未設定。4章のフォールバック手順を使う |
| 初回セットアップ画面で `Value for field "guild_id" in collection "directus_access" can't be null` | bot の旧バージョンが作った `directus_access` テーブルが Directus のシステムテーブルと**名前衝突**している。下記「6.1 テーブル名衝突からの復旧」を参照 |
| 管理画面が**空白ページ**／`NS_ERROR_NET_EMPTY_RESPONSE` が返る | Directus プロセスがメモリ不足で強制終了（OOM Kill）と再起動を繰り返している可能性が高い。下記「6.2 メモリ不足（OOM）」を参照 |

### 6.1 テーブル名衝突からの復旧

bot の旧バージョン（スキーマ v7〜v8）は、アクセス発行状況を
`directus_access` という名前のテーブルに保存していた。これは Directus 11 の
システムコレクション `directus_access` と同名であり、同じ `clubdb` に同居
すると Directus の初回セットアップが自身のシステムテーブルへ INSERT する
際に bot 側の `guild_id NOT NULL` 制約に掛かり、初期化できない。

**bot を新しいバージョンに更新して起動するだけで自動的に解消する。**
スキーマ v9 のマイグレーションが `directus_access` を
`guild_directus_access` へ改名する（`guild_id` 列の有無で bot のテーブルか
どうかを判定するため、Directus のシステムテーブルには一切触れない）。

ただし、**衝突したまま Directus を一度でも起動してしまった場合**は、
Directus 側が「マイグレーション済み」と記録した状態で自分のテーブルだけが
無い状態になるため、Directus のシステムテーブルを作り直す必要がある。
Directus の初回セットアップが完了していない＝Directus 側に守るべきデータは
まだ無いので、`directus_*` だけを削除して再起動すればよい
（**bot の業務データは別テーブルなので消えない**）。

```bash
# 0. 必ず先にバックアップを取る（5章参照）
docker compose -f deploy/docker-compose.directus.yml exec -T postgres \
  pg_dump -U clubbot clubdb > backup_before_directus_reset.sql

# 1. Directus を停止する
docker compose -f deploy/docker-compose.directus.yml stop directus

# 2. bot を新バージョンで起動し、v9 マイグレーション（改名）を適用する
#    ログに「directus_access を guild_directus_access へ改名しました（v9）」
#    または「スキーマバージョンを 9 に更新しました」と出れば成功

# 3. Directus のシステムテーブルだけを削除する
#    （消えるのは directus_* のみ。bot の業務テーブルは対象外）
docker compose -f deploy/docker-compose.directus.yml exec -T postgres \
  psql -U clubbot clubdb -c "DO \$\$ DECLARE t text; BEGIN
    FOR t IN SELECT tablename FROM pg_tables
             WHERE schemaname='public' AND tablename LIKE 'directus\_%'
    LOOP EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', t); END LOOP;
  END \$\$;"

# 4. 削除対象が directus_* だけだったことを確認する
docker compose -f deploy/docker-compose.directus.yml exec -T postgres \
  psql -U clubbot clubdb -c "\dt"

# 5. Directus を起動し直す（初回セットアップからやり直せる）
docker compose -f deploy/docker-compose.directus.yml up -d directus
```

再起動後、2章のグローバル設定を最初から行う。

> `guild_id` フィールドは **`directus_users`** に追加する。
> `directus_access` など他のシステムコレクションに追加してはならない
> （Directus 内部の処理はカスタムフィールドの存在を知らないため、
> 必須フィールドを足すと内部の INSERT が失敗する）。

### 6.2 メモリ不足（OOM）

Directus（Node.js）は公式に **最低 2GB RAM** が推奨されている。
1GB の VPS で Directus + PostgreSQL + NocoDB を同時稼働させると、
OOM Killer が数分おきにプロセスを強制終了し、Directus が再起動を
繰り返して「空白ページ」「空レスポンス」になる。

```bash
# メモリの空き状況
free -h

# OOM Killer の発生履歴（Directus が殺されていないか）
sudo dmesg -T | grep -i "out of memory\|oom" | tail -20

# コンテナごとの実使用量
docker stats --no-stream
```

対処:

- **NocoDB を止める**（レガシー構成なので Directus 移行後は不要。
  実測で 500MB 前後を占有する）:
  `docker stop deploy-nocodb-1`
- **VPS のメモリを増やす**。Directus + PostgreSQL のみなら 2GB で足りる
  （実測アイドル時: Directus 約 200MB / PostgreSQL 約 50MB）。
  NocoDB も常時稼働させるなら 4GB が現実的な下限
- **スワップを追加する**（対症療法。落ちにくくはなるが遅くなる）:

  ```bash
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

## 7. NocoDB からの移行

NocoDB 構成（[`NOCODB.md`](NOCODB.md)）から移行する場合、**業務 DB は
そのまま使える**（bot のスキーマは共通）。

1. bot を停止し、`pg_dump` でバックアップを取る
2. NocoDB を停止する: `docker compose -f docker-compose.nocodb.yml down`
3. 本書の1章で Directus を起動する（同じ `clubdb` に接続する）
4. 2章のグローバル設定を行う
5. bot を起動する（スキーマバージョンが 9 へ自動更新される）
6. 各サークルの管理者に `/directus-setup` を案内する

`nocodb_meta` データベースは不要になるが、削除は任意
（残しても業務 DB には影響しない）。
