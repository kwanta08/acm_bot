# セットアップ手順書（初心者向け）

鳥人間サークル 統合運営 Discord Bot を、**さくらのVPS** で 24 時間動かすまでの手順です。
サーバーを触ったことがない人でも、上から順にコピペしていけば完成するように書いています。

> このBotは「Discordのメッセージやコマンドに反応して自動で動くプログラム」です。
> パソコンを閉じても動き続けるように、レンタルサーバー（VPS）の上で常に起動させておきます。
> VPS = Virtual Private Server（仮想専用サーバー）。月数百円で借りられる自分専用のLinuxパソコンだと思ってください。

---

## この手順で使う言葉（超入門）

| 言葉 | かんたんな意味 |
|---|---|
| ターミナル / コンソール | サーバーに文字で命令を出す黒い画面 |
| SSH | 自分のPCからサーバーへ安全に接続するしくみ |
| コマンド | サーバーに打ち込む1行の命令。`$` の後ろの部分を打つ |
| ディレクトリ | フォルダのこと |
| `.env` | パスワードやトークンを書いておく設定ファイル |
| systemd | プログラムを「サービス」として常駐・自動再起動させるLinuxの仕組み |

> 以降、`$` で始まる行はコマンドです。`$` は打たず、その後ろだけを入力します。
> `#` で始まる行は「説明メモ（コメント）」なので打たなくてOKです。

---

## 全体の流れ（所要 1〜2 時間）

1. **【準備A】** Discord Bot を作ってトークンと各種 ID を用意する
2. **【準備B】**（任意）Todoist の連携情報・NocoDB の準備をする
3. **【STEP1】** さくらのVPSを契約して Ubuntu を用意する
4. **【STEP2】** VPSに接続して、初期設定（更新・作業ユーザー作成）
5. **【STEP3】** Bot のファイルを配置し、`.env` を設定する
6. **【STEP4】** 手動で起動して動作確認する
7. **【STEP5】** systemd で 24 時間常駐させる
8. **【STEP6】** バックアップと日常メンテ

> Todoist は未設定でも Bot は動きます（その機能だけOFFになります）。
> まずは Discord だけで動かし、後から追加でも大丈夫です。

---

# 【準備A】Discord Bot を作る

## A-1. Bot アプリの作成とトークン取得

1. [Discord Developer Portal](https://discord.com/developers/applications) を開き、右上「**New Application**」。名前は自由（例: 鳥人間運営Bot）。
2. 左メニュー「**Bot**」→「**Reset Token**」→ 表示された文字列を**コピーして安全な場所に保存**。
   これが `DISCORD_TOKEN` です。**他人に見せない・SNS等に貼らない**でください。
3. 同じ Bot 画面の「**Privileged Gateway Intents**」で、次の2つを**ONにして保存**します。
   - ✅ SERVER MEMBERS INTENT（メンバー情報の取得に必要）
   - ✅ MESSAGE CONTENT INTENT（メッセージ内容の取得に必要）

## A-2. Bot をサーバーに招待する

1. 左メニュー「**OAuth2**」→「**URL Generator**」。
2. **SCOPES** で `bot` と `applications.commands` にチェック。
3. **BOT PERMISSIONS** で以下にチェック。
   - Send Messages（メッセージ送信）
   - Embed Links（埋め込み表示）
   - Add Reactions（リアクション追加）
   - Read Message History（履歴の読み取り）
   - Manage Messages（投票の重複リアクション削除に必要）
   - Mention Everyone（未回答者への一斉通知に必要）
   - Attach Files（CSV出力に必要）
4. ページ下部に出る URL をコピーしてブラウザで開き、対象のサークルサーバーを選んで招待します。

## A-3. 各種 ID を取得する

Discord アプリの「**ユーザー設定 → 詳細設定 → 開発者モード**」を **ON** にすると、
右クリックで各種 ID をコピーできるようになります。

| `.env` の項目 | 取り方 |
|---|---|
| `GUILD_ID` | サーバー名を右クリック →「サーバーIDをコピー」 |
| `BOT_LOG_CHANNEL_ID` などチャンネル系 | 各チャンネルを右クリック →「チャンネルIDをコピー」 |
| `EXEC_ROLE_ID` / `ADMIN_ROLE_ID` / `LEADER_ROLE_IDS` | サーバー設定 → ロール → 各ロールを右クリック →「ロールIDをコピー」 |

班長ロールが班ごとに分かれている場合、`LEADER_ROLE_IDS` はカンマ区切りで並べます。
例: `LEADER_ROLE_IDS=111111111,222222222,333333333`

### おすすめチャンネル構成

| チャンネル例 | 対応する `.env` 項目 |
|---|---|
| `#全体連絡` | `DEFAULT_ANNOUNCE_CHANNEL_ID` |
| `#部会まとめ` | `DEFAULT_PROGRESS_CHANNEL_ID` |
| `#タスク通知` | `DEFAULT_TASK_CHANNEL_ID` |
| `#出欠管理` | `DEFAULT_SCHEDULE_CHANNEL_ID` |
| `#bot-log` | `BOT_LOG_CHANNEL_ID` |

---

# 【準備B】Todoist / NocoDB（任意）

## B-1. Todoist（タスク連携）

Todoist の API トークンは **`.env` には書きません**。セキュリティのため、
暗号化して DB に保存する方式になっています。

1. 暗号鍵を生成して `.env` の `ENCRYPTION_KEY` に設定します（全サーバー共通で1つ）。
   ```bash
   $ python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   表示された文字列を `.env` の `ENCRYPTION_KEY=` の右に書きます。
   **この鍵はバックアップして安全に保管してください**（紛失すると保存済み
   トークンを復号できなくなります）。
2. [Todoist の開発者設定](https://todoist.com/app/settings/integrations/developer) で
   **API トークン**をコピーします。
3. Discord で各サーバーの管理者が `/todoist-setup` を実行します（サーバーごとに登録）。
   - 管理者にだけ見えるメッセージとボタンが表示されるので、
     ボタンを押して開いたフォーム（Modal）にトークンを入力します。
   - Modal の入力値はチャンネルの履歴に残りません。
   - トークンは Fernet で暗号化して DB に保存され、画面・ログには表示されません。
4. 登録状況は `/todoist-status`、削除は `/todoist-remove` で行えます。
5. 「今日やること」ラベルは `/task sync` 実行時に自動で作られます。

## B-2. NocoDB（記録の閲覧 UI・任意）

本番では記録の正本を **PostgreSQL** とし、表形式の Web UI として NocoDB を
Docker Compose で起動します（bot と NocoDB が同じ PostgreSQL 業務 DB に接続。
NocoDB が止まっても Bot は動きます）。

手順は [`NOCODB.md`](NOCODB.md) を参照してください
（`deploy/docker-compose.nocodb.yml` で PostgreSQL と NocoDB を起動し、
`.env` の `DATABASE_URL` で Bot を接続します）。

> 以前のバージョンで使っていた Google Sheets 連携は廃止されました。
> 旧 Sheets のデータを移す場合は `scripts/migrate_sheets_to_db.py` を使います
> （[`NOCODB.md`](NOCODB.md) 8章）。

---

# 【STEP1】さくらのVPSを用意する

## 1-1. 申し込み

1. [さくらのVPS](https://vps.sakura.ad.jp/) にアクセスして会員登録・申し込み。
   クレジットカード払いなら **2週間の無料お試し**があります（まず試したい人向け）。
2. **プランを選ぶ**（このBotは軽量なので最小〜1GBで十分です）。

   | プラン | メモリ | CPU | SSD | 月額(石狩・税込) | このBotでの目安 |
   |---|---:|---:|---:|---:|---|
   | 512MB | 512MB | 1コア | 25GB | 643円〜 | Bot単体なら動く（最小構成） |
   | **1G**（推奨） | 1GB | 2コア | 50GB | 880円〜 | 余裕あり。将来拡張も安心 |
   | 2G | 2GB | 3コア | 100GB | 1,738円〜 | かなり余裕 |

   ※料金は 2026年時点・石狩リージョンの月払い税込。年額一括にすると約1か月分お得です（[さくらのVPS 料金・仕様](https://vps.sakura.ad.jp/specification/)）。
   ※リージョンは料金が最安の**石狩**でOK。速度差はこの用途ではほぼ気になりません。

3. **OSは「Ubuntu」**（できれば 24.04 LTS など新しめのLTS版）を選びます。
   OSインストール時に「**管理ユーザー名（例: ubuntu）」と「パスワード**」を決める画面があれば、
   忘れないようにメモしてください。無ければ契約完了メールに初期ログイン情報が届きます。

## 1-2. 契約後に控えるもの

- **サーバーのIPアドレス**（例: `160.16.xxx.xxx`）… コントロールパネルに表示されます
- **ログインユーザー名**（`ubuntu` または `root`）と**パスワード**

## 1-3. パケットフィルタ（さくらの無料ファイアウォール）

さくらのVPSには「**パケットフィルタ**」という無料のファイアウォールがあります。
初期状態で **SSH（ポート22）が許可**されていれば、この手順ではそのままでOKです。
（コントロールパネル →「パケットフィルタ設定」で確認できます。Bot は外部から接続を受けないため、
追加でポートを開ける必要はありません。参考: [さくらのVPS パケットフィルターマニュアル](https://manual.sakura.ad.jp/vps/network/packetfilter.html)）

---

# 【STEP2】VPSに接続して初期設定

## 2-1. サーバーに接続する（SSH）

**Windows** なら「ターミナル」または「PowerShell」、**Mac** なら「ターミナル」を開きます。
次のコマンドの `xxx.xxx.xxx.xxx` を、自分のサーバーIPに置き換えて実行します。

```bash
# ubuntu ユーザーがある場合
$ ssh ubuntu@xxx.xxx.xxx.xxx

# root しかない場合はこちら（この後 2-3 で作業用ユーザーを作ります）
$ ssh root@xxx.xxx.xxx.xxx
```

- 初回は「Are you sure you want to continue connecting?」と聞かれます → `yes` と入力。
- パスワードを聞かれたら、契約時に決めた（またはメールに届いた）パスワードを入力。
  ※入力しても画面に文字は出ません（仕様です）。そのまま Enter。

接続に成功すると、プロンプトが `ubuntu@...:~$` のような表示に変わります。

## 2-2. まずはOSを最新化

```bash
$ sudo apt update && sudo apt upgrade -y
```

途中で何か聞かれたら基本 Enter（既定のまま）で進めて問題ありません。

## 2-3.（rootで入った人だけ）作業用ユーザーを作る

`root` は権限が強すぎて危険なので、普段使い用のユーザーを作ります。
**すでに `ubuntu` で入れている人はこの 2-3 を飛ばして 2-4 へ。**

```bash
# ubuntu という名前のユーザーを作る（パスワードを聞かれるので設定）
# adduser 実行後の質問（氏名など）は空Enterで飛ばしてOK
$ adduser ubuntu

# sudo（管理者コマンド）を使えるようにする
$ usermod -aG sudo ubuntu

# 作ったユーザーに切り替え
$ su - ubuntu
```

## 2-4. 必要なソフトを入れる

```bash
$ sudo apt install python3 python3-venv python3-pip git -y
```

Python のバージョンを確認しておきます（3.10 以上であればOK）。

```bash
$ python3 --version
```

---

# 【STEP3】Botファイルを配置して設定する

## 3-1. 置き場所を作る

```bash
$ mkdir -p ~/club-bot
$ cd ~/club-bot
```

これで `/home/ubuntu/club-bot/` というフォルダができました（`~` は自分のホームの意味）。

## 3-2. プログラム一式（app）を置く

配布 zip（`club-bot.zip`）の中身を、この `~/club-bot/` の中に `app` という名前で置きます。
方法は2通り。どちらか1つでOKです。

### 方法A: 自分のPCからアップロードする（zipを持っている場合）

自分のPC側のターミナルで（VPSではなく手元のPCで）実行します。

```bash
# 手元PCで実行。zipをサーバーのホームに送る
$ scp club-bot.zip ubuntu@xxx.xxx.xxx.xxx:~/
```

送れたら、VPS側に戻って解凍・配置します。

```bash
# VPS側で実行
$ cd ~
$ sudo apt install unzip -y
$ unzip club-bot.zip          # club-bot/ というフォルダが展開される
$ mv club-bot ~/club-bot-app-tmp     # 名前が被らないよう一旦退避
$ mkdir -p ~/club-bot
$ mv ~/club-bot-app-tmp ~/club-bot/app
$ ls ~/club-bot/app           # bot.py などが見えればOK
```

> ※ zip の中身の構成によっては展開後のフォルダ名が異なります。
> 最終的に **`~/club-bot/app/bot.py` が存在する**状態になっていれば正解です。

### 方法B: Git リポジトリから取得する（GitHub等に置いている場合）

```bash
$ cd ~/club-bot
$ git clone <リポジトリのURL> app
$ ls app                      # bot.py などが見えればOK
```

## 3-3. Python の仮想環境を作って依存をインストール

```bash
$ cd ~/club-bot
$ python3 -m venv venv
$ venv/bin/pip install --upgrade pip
$ venv/bin/pip install -r app/requirements.txt
```

エラーなく終われば準備完了です。

## 3-4. `.env`（設定ファイル）を作る

ひな形をコピーして編集します。

```bash
$ cp app/.env.example ~/club-bot/.env
$ nano ~/club-bot/.env
```

`nano` はかんたんなテキストエディタです。矢印キーで移動し、値を書き込みます。
**最低限、次の3つを埋めれば起動できます。**

```
DISCORD_TOKEN=（準備Aで控えたトークン）
GUILD_ID=（準備Aで控えたサーバーID）
ENCRYPTION_KEY=（準備B-1 で生成した暗号鍵）
```

余裕があれば、チャンネルID・ロールID の項目も埋めます。
Todoist は `.env` ではなく、起動後に Discord 上で `/todoist-setup` を使って登録します（準備B-1）。
VPSでは**絶対パス**で書くのが安全です。おすすめの書き方：

```
DB_PATH=/home/ubuntu/club-bot/app/data/club.db
TZ=Asia/Tokyo
```

書き終えたら **Ctrl+O → Enter（保存）→ Ctrl+X（終了）**。

## 3-5. ログ用フォルダを作る

```bash
$ mkdir -p ~/club-bot/logs
```

---

# 【STEP4】まず手動で起動して動作確認

いきなり常駐させる前に、手で起動してエラーが出ないか確認します。

```bash
$ cd ~/club-bot/app
$ ../venv/bin/python bot.py
```

- 「ログイン完了」「スラッシュコマンドを同期」などのログが出れば成功です。
- Discord 側で `/ping` を打って Pong が返るか、`/health` で連携状態を確認しましょう。
- 確認できたら **Ctrl+C** で一旦止めます（次の STEP5 で常駐させます）。

> **エラーが出たら**：末尾の「トラブルシューティング」を確認してください。
> よくあるのは「必須設定が不足」（= `.env` の DISCORD_TOKEN / GUILD_ID 未設定）です。

---

# 【STEP5】systemd で 24 時間常駐させる

手動起動だとターミナルを閉じると止まってしまいます。
`systemd` に登録して、**サーバー起動時に自動開始・落ちても自動再起動**するようにします。

## 5-1. サービス定義ファイルを設置

配布物の `deploy/club-bot.service` をそのまま使えます（パスは `/home/ubuntu/club-bot/` 前提）。

```bash
$ sudo cp ~/club-bot/app/deploy/club-bot.service /etc/systemd/system/club-bot.service
```

> **作業ユーザー名や配置場所を変えた人**は、中身を編集して合わせます。
> ```bash
> $ sudo nano /etc/systemd/system/club-bot.service
> ```
> `User=` `WorkingDirectory=` `ExecStart=` `EnvironmentFile=` `StandardOutput=` `StandardError=`
> の `ubuntu` やパスを、自分の環境に書き換えて保存してください。

## 5-2. 有効化して起動

```bash
$ sudo systemctl daemon-reload
$ sudo systemctl enable club-bot     # サーバー起動時に自動でONにする
$ sudo systemctl start club-bot      # 今すぐ起動
```

## 5-3. 動いているか確認

```bash
$ sudo systemctl status club-bot     # active (running) と緑色で出ればOK
```

ログをリアルタイムで見たいとき：

```bash
$ journalctl -u club-bot -f          # Ctrl+C で抜ける
```

## 5-4. よく使う操作

```bash
$ sudo systemctl restart club-bot    # 設定変更後などに再起動
$ sudo systemctl stop club-bot       # 停止
$ sudo systemctl status club-bot     # 状態確認
```

`.env` を変更したら、必ず `restart` してください。

## 5-5. ログのたまり過ぎを防ぐ（任意）

```bash
$ sudo cp ~/club-bot/app/deploy/club-bot.logrotate /etc/logrotate.d/club-bot
```

これでログファイルが自動で日ごとに整理・圧縮されます。

---

# 【STEP6】バックアップと日常メンテ

- **データのバックアップ**：
  - SQLite 運用: `~/club-bot/app/data/club.db` を定期的にコピー保管。
    ```bash
    $ cp ~/club-bot/app/data/club.db ~/club-bot/backup_$(date +%Y%m%d).db
    ```
  - PostgreSQL 運用: `pg_dump` で定期的に取得（手順は [`NOCODB.md`](NOCODB.md) 5章）。
- **タスクの控え**：週1回 Discord で `/report export-tasks` を実行して CSV を保存。
- **状態チェック**：Discord で `/health`、サーバーで `sudo systemctl status club-bot`。

---

# トラブルシューティング

| 症状 | 確認すること |
|---|---|
| 起動時「必須設定が不足しています」 | `~/club-bot/.env` の `DISCORD_TOKEN` と `GUILD_ID` が埋まっているか |
| スラッシュコマンドが Discord に出ない | 招待時に `applications.commands` を付けたか / `GUILD_ID` が正しいか / 数分待つ |
| メンバー一覧が取れない | Developer Portal で **SERVER MEMBERS INTENT** が ON か |
| 投票のリアクションが反映されない | Bot に **Manage Messages** 権限があるか / **MESSAGE CONTENT INTENT** が ON か |
| `/health` で Todoist が「未登録」 | そのサーバーで `/todoist-setup` を実行したか。`ENCRYPTION_KEY` が `.env` に正しく設定されているか |
| `sudo systemctl status` が failed | `journalctl -u club-bot -e` で赤いエラー行を確認。多くは `.env` かパスのミス |
| SSHで接続できない | IPアドレス/ユーザー名/パスワード、さくらの「パケットフィルタ」でSSH(22)が許可されているか |
| `pip install` が失敗する | `sudo apt install python3-venv python3-pip -y` を先に実行したか |

困ったときは、まず `journalctl -u club-bot -e` の**最後のエラー行**を読むのが近道です。
その内容を「トラブルシューティング」表と照らし合わせてください。

---

## 参考リンク

- [さくらのVPS 公式](https://vps.sakura.ad.jp/) / [料金・仕様一覧](https://vps.sakura.ad.jp/specification/)
- [さくらのVPS マニュアル（Ubuntuインストール）](https://manual.sakura.ad.jp/vps/os-reinstall/iso-install/ubuntu.html)
- [さくらのVPS マニュアル（パケットフィルター）](https://manual.sakura.ad.jp/vps/network/packetfilter.html)
- [Discord Developer Portal](https://discord.com/developers/applications)
