# ダッシュボード セットアップ手順書

Web ダッシュボード（表グリッド）を公開するための手順です。
**Bot をホストする運営者向け**で、導入するサークル側の作業はありません。

- 所要時間: 30〜60分（ドメインの DNS 反映待ちを除く）
- 前提: [`SETUP.md`](SETUP.md) の手順で bot が VPS 上で動いていること
- ダッシュボードは**任意機能**です。動かさなくても bot は通常どおり動きます

> 日々の運用（権限・監視・トラブル対応）は
> [`OPERATION.md`](OPERATION.md) の 8 章にあります。

---

## 0. 何を作るのか

```
   ブラウザ
     │ HTTPS(443)
     ▼
  ┌────────┐   HTTP(127.0.0.1:8000)   ┌──────────────┐
  │ Caddy  │ ───────────────────────▶ │ uvicorn      │
  │(証明書 │                          │ (FastAPI)    │
  │ 自動)  │                          └──────┬───────┘
  └────────┘                                 │
                                             ▼
                                    ┌──────────────┐
                                    │ PostgreSQL   │◀── bot（別プロセス）
                                    └──────────────┘
```

**重要な性質**

- ダッシュボードは **bot とは別プロセス**です。Web 側が落ちても
  Discord 接続は切れません（逆も同様）
- ダッシュボードに **Discord Bot トークンは不要**です。使うのは
  OAuth2 のクライアント ID / シークレットだけ
- uvicorn は **127.0.0.1 にのみ**バインドし、インターネットへは
  Caddy 経由でしか出しません
- **PostgreSQL 構成を推奨**します。SQLite でも動きますが、
  ダッシュボードからの設定変更が bot に伝わりません（後述 8.5 / OPERATION 8.5）

### 用意するもの

| もの | 説明 |
|---|---|
| ドメイン | 例 `dashboard.example.com`。VPS の IP を向ける A レコードが必要 |
| Discord アプリ | bot と同じアプリを使います（新規作成は不要） |
| VPS の 80 / 443 ポート | Let's Encrypt の証明書取得と HTTPS 配信に使います |

---

## 1.【準備】Discord Developer Portal で OAuth2 を設定する

ダッシュボードのログインは Discord OAuth2 を使います。
**この設定を間違えるとログインできません**。最も間違いやすい箇所なので丁寧に進めます。

### 1-1. クライアント ID とシークレットを控える

1. [Discord Developer Portal](https://discord.com/developers/applications) を開く
2. **bot と同じアプリケーション**を選ぶ（新しく作らないこと）
3. 左メニュー「**OAuth2**」を開く
4. **CLIENT ID** をコピーして控える（数字の羅列。公開されても問題ない値です）
5. **CLIENT SECRET** の「Reset Secret」を押して表示された値を控える
   - **一度しか表示されません**。安全な場所に保存してください
   - これは**パスワード相当**です。他人に見せない・リポジトリに入れない

> Bot の「Reset Token」とは別物です。ダッシュボードに Bot トークンは使いません。

### 1-2. リダイレクト URL を登録する

同じ「OAuth2」画面の **Redirects** で「Add Redirect」を押し、次を入力します。

```
https://dashboard.example.com/auth/callback
```

- `dashboard.example.com` は**あなたのドメイン**に置き換えます
- 末尾は必ず `/auth/callback` です
- 入力後、画面下の「**Save Changes**」を押す

> ⚠️ ここで登録した文字列と、後述の `DASHBOARD_REDIRECT_URI` は
> **1文字も違わず一致**していなければなりません。
> `http` と `https`、末尾スラッシュの有無、大文字小文字すべてが対象です。
> 不一致だと Discord の画面に `Invalid OAuth2 redirect_uri` と表示されます。

ローカルで試す場合は、次も追加しておくと便利です（複数登録できます）。

```
http://127.0.0.1:8000/auth/callback
```

### 1-3. スコープについて

ダッシュボードが要求するスコープは `identify` と `guilds` の2つだけで、
これはコード側で固定されています。Portal 側で何か選ぶ必要はありません。

| スコープ | 何に使うか |
|---|---|
| `identify` | ログインした人の Discord ユーザーID・表示名 |
| `guilds` | その人が所属するサーバーの一覧と「サーバー管理」権限の有無 |

メッセージや DM を読むスコープは要求しません。

---

## 2.【STEP1】依存をインストールする

VPS にログインし、bot を置いたディレクトリで実行します。

```bash
cd ~/club-bot/club-bot
~/club-bot/club-bot/venv/bin/pip install -r dashboard/requirements.txt
```

入るのは `fastapi` / `uvicorn` / `itsdangerous` / `httpx` の4つです
（bot 本体の依存とは分離されています）。

確認:

```bash
~/club-bot/club-bot/venv/bin/python -c "import fastapi, uvicorn; print('ok')"
```

---

## 3.【STEP2】設定ファイルを作る

### 3-1. 署名鍵を生成する

セッション Cookie の署名に使う鍵です。推測不能な値を生成します。

```bash
~/club-bot/club-bot/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

出力された文字列を控えてください。

> この鍵が漏れると、セッションを偽造して**他人になりすませます**。
> bot の `ENCRYPTION_KEY` と同様に扱ってください。
> 鍵を変更すると全員がログアウトされます（実害はありません）。

### 3-2. `dashboard.env` を作る

bot の `.env` とは**別ファイル**にします（ダッシュボードに Bot トークンを
置かないため）。

```bash
nano ~/club-bot/dashboard.env
```

次の内容を貼り付け、`<>` の部分を実際の値に置き換えます。

```bash
# ===== 認証（必須） =====
DASHBOARD_SECRET_KEY=<3-1 で生成した文字列>
DISCORD_CLIENT_ID=<Portal の CLIENT ID>
DISCORD_CLIENT_SECRET=<Portal の CLIENT SECRET>
# 1-2 で Portal に登録したものと完全一致させる
DASHBOARD_REDIRECT_URI=https://dashboard.example.com/auth/callback

# ===== 公開 URL =====
DASHBOARD_BASE_URL=https://dashboard.example.com

# ===== DB（bot と同じものを指す） =====
DATABASE_URL=postgresql://clubbot:<パスワード>@127.0.0.1:5432/clubdb

# ===== 任意 =====
# セッションの有効期間（秒。既定は24時間）
# DASHBOARD_SESSION_MAX_AGE=86400
# 接続プールの上限（既定10。bot 側とは独立）
# DASHBOARD_DB_POOL_MAX_SIZE=10
```

保存したら、他のユーザーから読めないようにします。

```bash
chmod 600 ~/club-bot/dashboard.env
```

### 3-3. 設定項目リファレンス

| 変数 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `DASHBOARD_SECRET_KEY` | ✅ | — | セッション Cookie の署名鍵。未設定でも起動はするが、再起動のたびに全員ログアウトされる |
| `DISCORD_CLIENT_ID` | ✅ | — | Portal の OAuth2 クライアント ID |
| `DISCORD_CLIENT_SECRET` | ✅ | — | 同 クライアントシークレット |
| `DASHBOARD_REDIRECT_URI` | ✅ | — | Portal の Redirects と**完全一致**させる |
| `DASHBOARD_BASE_URL` | | `http://127.0.0.1:8000` | 公開 URL |
| `DASHBOARD_SESSION_MAX_AGE` | | `86400`（24時間） | セッション有効期間（秒） |
| `DASHBOARD_SECURE_COOKIE` | | 有効 | `0` で Cookie の Secure 属性を外す。**ローカル開発専用** |
| `DATABASE_URL` | | — | PostgreSQL 接続 URL。未設定なら `DB_PATH` の SQLite |
| `DB_PATH` | | `./data/club.db` | SQLite のパス |
| `DASHBOARD_DB_POOL_MIN_SIZE` | | 1 | 接続プールの下限 |
| `DASHBOARD_DB_POOL_MAX_SIZE` | | 10 | 接続プールの上限 |

必須項目が欠けている場合、ダッシュボードは**起動はします**が
（`/healthz` と静的ファイルは応答します）、ログインしようとすると
`503` を返します。起動ログにも不足している変数名が出ます。

---

## 4.【STEP3】手動で起動して動作確認

まず HTTPS を挟まずに、アプリ単体が動くか確かめます。

```bash
cd ~/club-bot/club-bot
set -a; source ~/club-bot/dashboard.env; set +a
~/club-bot/club-bot/venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
```

別のターミナル（または `Ctrl+C` で止めずに別セッション）で確認します。

```bash
curl -s http://127.0.0.1:8000/healthz
```

期待する応答:

```json
{"status":"ok","pool":{"min_size":1,"max_size":10,"size":1,"idle":1,"in_use":0}}
```

- `"status":"ok"` なら DB に接続できています
- `"degraded"` の場合は `DATABASE_URL` を確認してください
- `pool` は PostgreSQL のときだけ出ます（SQLite では出ません）

確認できたら `Ctrl+C` で止めます。

> 手動起動では `dashboard.env` は自動で読まれません（読み込むのは systemd の
> `EnvironmentFile` です）。上のように `source` せずに起動すると
> 「ダッシュボードの設定が不足しています」と出ますが、常駐運用には影響しません。
> また常駐サービスが動いている間は 8000 番が塞がっているため、手動起動すると
> `address already in use` になります。確認したいだけなら
> `sudo systemctl stop club-bot-dashboard` してから起動してください。

---

## 5.【STEP4】ドメインを VPS へ向ける

ドメインの DNS 設定で、A レコードを VPS の IPv4 アドレスへ向けます。

| 種別 | 名前 | 値 |
|---|---|---|
| A | `dashboard`（サブドメイン） | VPS の IP アドレス |

**サブドメインで公開してください。** `example.com/dashboard` のような
サブパス配信には対応していません（アプリはルート直下で `/auth/callback`・
`/static/...` を配信します）。既にサイトを運用しているドメインでも、
`dashboard.` のサブドメインを1つ足すだけで済み、既存サイトの DNS は
そのままで構いません。

VPS の IP は次で確認できます。

```bash
curl -s https://ifconfig.me; echo
```

反映を確認します（数分〜数時間かかることがあります）。

```bash
dig +short dashboard.example.com
# → VPS の IP が返れば OK
```

**ファイアウォールで 80 と 443 を開けておきます。**
さくらのVPSの場合はコントロールパネルの「パケットフィルタ」で
Web（80/443）を許可してください。証明書の自動取得には 80 番も必要です。

---

## 6.【STEP5】Caddy で HTTPS 公開する

Caddy は Let's Encrypt から証明書を自動取得・自動更新します。

### 6-1. Caddy をインストール

Ubuntu の標準リポジトリには入っていないため、公式リポジトリを追加します。

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

> インストール手順は変わることがあります。うまくいかない場合は
> 公式ドキュメント（`caddyserver.com/docs/install`）の Debian/Ubuntu の項を
> 確認してください。

### 6-2. 設定ファイルを置く

リポジトリに用意してある設定をコピーし、ドメインを書き換えます。

```bash
sudo cp ~/club-bot/club-bot/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/dashboard.example.com/実際のドメイン/' /etc/caddy/Caddyfile
```

> アクセスログの出力先は `/var/lib/caddy/dashboard.log` にしてあります。
> 公式パッケージの `caddy.service` は sandbox が有効で `/var/log/caddy` へ
> 書けないため（`chown` しても解消しません）、そこを指定すると
> `permission denied` で **Caddy 自体が起動しなくなります**。

証明書の期限切れ通知を受け取りたい場合は、`/etc/caddy/Caddyfile` 冒頭の
`# email admin@example.com` のコメントを外してアドレスを設定します。

設定に問題がないか検証してから反映します。

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

この設定には次が含まれています（`deploy/Caddyfile` 参照）。

- HTTPS の自動化（証明書の取得・更新、HTTP からのリダイレクト）
- セキュリティヘッダ（HSTS / CSP / X-Frame-Options / Referrer-Policy ほか）
- 静的ファイルのキャッシュ、`/healthz` のログ除外
- `127.0.0.1:8000` への中継

---

## 7.【STEP6】systemd で常駐させる

```bash
sudo cp ~/club-bot/club-bot/deploy/club-bot-dashboard.service /etc/systemd/system/
sudo nano /etc/systemd/system/club-bot-dashboard.service
```

ユーザー名や配置先を変えている場合は、`User` / `WorkingDirectory` /
`ExecStart` / `EnvironmentFile` / `Standard*` のパスを実際の値に書き換えます
（既定は作業ユーザー `ubuntu`、配置先 `/home/ubuntu/club-bot`）。

```bash
# ユニットの ReadWritePaths が指すディレクトリ。**先に作っておくこと**
# （無いと status=226/NAMESPACE で起動できず 10 秒ごとに再起動を繰り返す）
mkdir -p ~/club-bot/logs ~/club-bot/data
sudo systemctl daemon-reload
sudo systemctl enable --now club-bot-dashboard
sudo systemctl status club-bot-dashboard --no-pager
```

`active (running)` になっていれば成功です。

```bash
# ログを見る
journalctl -u club-bot-dashboard -f
```

---

## 8.【STEP7】ログインして確認する

ブラウザで `https://dashboard.example.com` を開きます。

1. 鍵アイコン（HTTPS）になっていること
2. 「Discord でログイン」を押す
3. Discord の認可画面 →「認証」
4. 元の画面に戻り、右上に自分の表示名が出る
5. サーバー選択のプルダウンに、**bot が入っていてあなたも所属している
   サーバー**が並ぶ
6. 表のタブ（タスク / メンバー / …）を切り替えてデータが見えること

ここまで確認できれば完了です。

> サーバーが1つも出ない場合は、そのサーバーに bot が参加しているか
> （`guilds` 台帳に載っているか）を確認してください。
> bot を招待した直後であれば、bot 側で一度 `on_guild_join` が
> 走っている必要があります。

---

## 9. Docker で動かす場合（STEP5〜6 の代替）

systemd を使わず、Caddy ごとコンテナで動かす構成も用意しています。

```bash
cd ~/club-bot/club-bot/deploy
cp ~/club-bot/dashboard.env ./dashboard.env      # 3-2 で作ったもの
echo "DASHBOARD_DOMAIN=dashboard.example.com" >> .env
docker compose -f docker-compose.dashboard.yml up -d --build
```

- `dashboard.env` の `DATABASE_URL` はコンテナから見たホストを指す必要があります
  （例: `postgresql://clubbot:<パスワード>@host.docker.internal:5432/clubdb`）
- ダッシュボードのポートはホストへ公開されません（Caddy 経由のみ）
- 証明書は `caddy_data` ボリュームに保存されます。**このボリュームを消すと
  証明書を取り直す**ことになるので注意してください

---

## 10. ローカル PC で試す場合（開発用）

HTTPS もドメインも用意せずに動作を確認したいときの手順です。

1. Portal の Redirects に `http://127.0.0.1:8000/auth/callback` を追加
2. `club-bot/dashboard/.env.example` をコピーして値を設定

```bash
DASHBOARD_SECRET_KEY=<適当な長い文字列>
DISCORD_CLIENT_ID=<CLIENT ID>
DISCORD_CLIENT_SECRET=<CLIENT SECRET>
DASHBOARD_REDIRECT_URI=http://127.0.0.1:8000/auth/callback
DASHBOARD_BASE_URL=http://127.0.0.1:8000
# HTTP で Cookie を送るために必要（本番では絶対に設定しない）
DASHBOARD_SECURE_COOKIE=0
DB_PATH=./data/club.db
```

3. 起動

```bash
cd club-bot
venv/bin/uvicorn dashboard.main:app --reload --port 8000
```

`DASHBOARD_SECURE_COOKIE=0` は **HTTP でログイン状態を保つための開発専用設定**です。
本番で設定すると、Cookie が平文で送られる経路を許すことになります。

---

## 11. 更新・停止・バックアップ

```bash
# コードを更新したあと
cd ~/club-bot/club-bot
~/club-bot/club-bot/venv/bin/pip install -r dashboard/requirements.txt   # 依存が増えたとき
# restart の前に必ずバックアップを取る（起動時に bot・ダッシュボードとも
# スキーマ マイグレーションが自動適用され、down は用意していない）
pg_dump -Fc "$DATABASE_URL" -f "backup-$(date +%Y%m%d-%H%M).dump"
sudo systemctl restart club-bot-dashboard

# 一時停止 / 再開
sudo systemctl stop club-bot-dashboard
sudo systemctl start club-bot-dashboard

# 完全に撤去する
sudo systemctl disable --now club-bot-dashboard
sudo rm /etc/systemd/system/club-bot-dashboard.service
sudo systemctl daemon-reload
```

ダッシュボードは bot と同じ DB を読み書きするだけで、独自のデータを持ちません。
バックアップ対象は **DB と `dashboard.env`** です
（`dashboard.env` は DB のバックアップとは別の場所に保管してください）。

**ダッシュボードを止めても bot は動き続けます。** 逆も同様です。

---

## 12. トラブルシューティング

### ログインまわり

| 症状 | 原因と対処 |
|---|---|
| Discord の画面に `Invalid OAuth2 redirect_uri` | Portal の Redirects と `DASHBOARD_REDIRECT_URI` が不一致。`http`/`https`・末尾スラッシュ・ドメインを1文字ずつ照合する |
| ログインボタンを押すと `503` | 必須設定が不足。`journalctl -u club-bot-dashboard` に不足している変数名が出ています |
| コールバックで `502` | トークン交換に失敗。`DISCORD_CLIENT_SECRET` が古い（Reset した後は再設定が必要）か、VPS から Discord へ出られない |
| コールバックで `400` | `state` の不一致。ブラウザの戻るボタンで古い URL を開いた場合に起きます。トップからやり直してください |
| ログインしてもすぐログアウトされる | HTTPS で配信できていないのに Secure Cookie が有効。ドメインの HTTPS 化を確認（開発時のみ `DASHBOARD_SECURE_COOKIE=0`） |
| 再起動のたびにログアウトされる | `DASHBOARD_SECRET_KEY` が未設定。設定すると永続します |
| ログインできるがサーバーが1つも出ない | 「利用者が所属し、かつ bot も参加している」サーバーだけが出ます。bot の参加状況を確認 |

### 表示・権限まわり

| 症状 | 原因と対処 |
|---|---|
| `403 このサーバーのデータにはアクセスできません` | セッションに無いサーバーの URL を開いています。所属が変わった場合は一度ログアウトして入り直してください |
| セルが編集できない | 権限が L1（閲覧のみ）。編集には Discord の「サーバー管理」権限か、`members.is_leader`（班長）が必要です |
| 設定タブで変更できない | 設定変更は「サーバー管理」権限（L4）のみです |
| ダッシュボードで設定を変えたのに bot に反映されない | SQLite 構成では伝播しません。PostgreSQL 構成にするか、bot を再起動してください |

### サーバーまわり

| 症状 | 原因と対処 |
|---|---|
| ブラウザで 502 Bad Gateway | uvicorn が落ちています。`systemctl status club-bot-dashboard` と `journalctl -u club-bot-dashboard -e` を確認 |
| サービスが `226/NAMESPACE` で再起動を繰り返す | `ReadWritePaths` のディレクトリが無い。`mkdir -p ~/club-bot/logs ~/club-bot/data` |
| サービスが `203/EXEC` で起動しない | `ExecStart` のパスが実際の配置と違う。`systemctl show club-bot-dashboard -p ExecStart -p WorkingDirectory` で確認し、`sudo systemctl edit club-bot-dashboard` で上書きする |
| 手動起動で `address already in use` | 常駐サービスが既に 8000 を使っています。`sudo ss -lptn 'sport = :8000'` で確認。通常は手動起動不要 |
| `caddy` が `permission denied` で起動しない | Caddyfile のログ出力先が `/var/log/caddy` になっている。`/var/lib/caddy` へ変えるか、`caddy.service` に `LogsDirectory=caddy` と `ReadWritePaths=/var/log/caddy` を足す |
| 証明書取得が `NXDOMAIN` で失敗する | DNS の A レコードが未作成。`dig +short <ドメイン>` が VPS の IP を返すか確認。作成後は Caddy が自動で再試行します |
| ブラウザに Apache 風の `Forbidden` が出る | リクエストが VPS に届かず、別サーバー（既存サイト等）が返しています。`dig +short <ドメイン>` と VPS の IP（`curl -s https://ifconfig.me`）を突き合わせてください |
| 証明書が取得できない | 80/443 が開いているか、DNS が VPS を向いているかを確認。`journalctl -u caddy -e` にエラーが出ます |
| `/healthz` が `degraded` | DB に接続できていません。`DATABASE_URL` と PostgreSQL の稼働を確認 |
| 表示が重い・タイムアウトする | `/healthz` の `pool.in_use` が `max_size` に張り付いていれば `DASHBOARD_DB_POOL_MAX_SIZE` を上げてください |

---

## 13. 公開前チェックリスト

- [ ] `dashboard.env` の権限が `600` で、Git に入っていない
- [ ] `DASHBOARD_SECRET_KEY` を設定した（未設定だと再起動で全員ログアウト）
- [ ] `DASHBOARD_SECURE_COOKIE=0` を**本番では設定していない**
- [ ] uvicorn が `127.0.0.1` にのみバインドされている（`--host 0.0.0.0` にしない）
- [ ] ファイアウォールで開いているのは 22 / 80 / 443 のみ
  （PostgreSQL の 5432 は外部へ公開しない）
- [ ] `https://<ドメイン>` が鍵アイコンで開ける
- [ ] `https://<ドメイン>/docs` が **404** を返す（API ドキュメントは非公開）
- [ ] 自分が所属していないサーバーの URL
      （`/api/guilds/<他のギルドID>/summary`）が **403** を返す
- [ ] Discord Developer Portal の CLIENT SECRET を安全な場所に保管した

---

## 関連ドキュメント

| ファイル | 内容 |
|---|---|
| [`OPERATION.md`](OPERATION.md) 8章 | ダッシュボードの日常運用（権限・監視・設定反映） |
| [`../dashboard/README.md`](../dashboard/README.md) | 構成の概要と設計上の約束 |
| [`SETUP.md`](SETUP.md) | bot 本体の VPS セットアップ |
| [`PRIVACY.md`](PRIVACY.md) | ダッシュボード経由のアクセスに関する記載 |
