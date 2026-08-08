# 鳥人間サークル 統合運営 Discord Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

**鳥人間サークル（人力飛行機・鳥人間コンテスト系）専用の運営 Discord Bot** です。
日程調整・タスク管理・班/メンバー管理・**桁巻き積層記録**・**機体進捗管理**・
リマインド・レポートを、Discord の中だけで回せます。

このBotは**開発者が1インスタンスを常時運用する公開Bot**です。
導入するサークルは **招待URLを踏むだけ**。サーバー（VPS）もBotトークンも
`.env` 編集も必要ありません。

> **対象を鳥人間サークルに絞っています。** 「どんなサークルでも使える汎用運営bot」
> ではありません。桁巻き・積層・主桁・機体といった用語をそのまま扱うのは、
> 他大学の鳥人間サークルにも同じ言葉が通じるからです。
> 一方で**サークルごとに違うもの**（サークル名・班構成・機体名・桁の構成・
> チャンネル・ロール）は一切ハードコードされておらず、すべてサーバーごとに設定します。

---

## 導入手順（3ステップ）

### 1. 招待する

サーバーの管理者（`サーバー管理` 権限を持つ人）が招待URLを開き、
自分のサークルの Discord サーバーを選ぶだけです。

<!-- P0-5（Discord Developer Portal で Public Bot を ON）完了後に確定URLへ差し替える -->
> **招待URL**: 公開準備中です。公開後にここへ掲載します。
> 現時点で試したい場合は、下記「[開発者・セルフホスト向け](#開発者セルフホスト向け)」の
> 手順で自分のBotとして起動すると、起動ログに最小権限の招待URLが出力されます。

Botが要求する権限は最小限です（`Administrator` は要求しません）。

| 権限 | 用途 |
|---|---|
| チャンネルを見る / メッセージを送信 | コマンドへの応答・各種通知 |
| 埋め込みリンク / ファイルを添付 | 一覧表示・CSV・進捗グラフの送信 |
| リアクションを追加 / メッセージ履歴を読む | 日程調整の出欠投票 |

招待した瞬間に初期セットアップ（サーバー登録・`幹部` / `Bot管理者` ロール・
`#bot-log` チャンネルの作成）が自動で走ります。
ロールとチャンネルの自動作成を使う場合のみ、招待後に `ロールの管理` /
`チャンネルの管理` を追加で付与してください（無い場合はその部分だけスキップして動作します）。

招待をもって [利用規約](docs/TERMS.md) に同意したものとみなします。
記録される情報は [プライバシーポリシー](docs/PRIVACY.md) を参照し、
サークルのメンバーへ周知してください。

> **スラッシュコマンドはグローバル登録です。** 招待直後は Discord 側の反映に
> **最大1時間程度**かかることがあります。コマンドが出てこない場合は少し待つか、
> Discord クライアントを再起動（Ctrl+R）してください。

### 2. `/setup` を実行する

管理者が `/setup` を実行すると設定ウィザードが開きます。

- 通知チャンネル・ログチャンネル・管理者ロールをセレクトメニューから設定
- 「サークル名を設定」ボタンで、レポート等に表示するサークル名を登録
- 「班を一括作成」ボタンで班名をカンマ区切り入力すると、班と対応する
  Discord ロールをまとめて作成（**班のデフォルト値はありません**。
  各サークルが自分の班を作ります）

後からの変更は `/team-add` `/team-remove` `/team-role` でも行えます。

### 3. 使い始める

`/schedule create` で日程調整、`/task add` でタスク登録、
`/layer start` で積層記録、`/progress view` で機体進捗の確認。
日常の使い方は [`docs/OPERATION.md`](docs/OPERATION.md) を参照してください。

---

## 鳥人間サークル向けの機能

### `/layer` — 桁巻き積層記録

主桁の積層作業を、誰が・どの桁の・何層目を・何分やったかの単位で記録します。
紙の作業日誌やスプレッドシートへの手入力を置き換えるための機能です。

| コマンド | 内容 |
|---|---|
| `/layer keta-add` `/layer keta-remove` `/layer keta-list` | 桁名の登録・無効化・一覧（桁の構成はサークルごとに自由に登録します） |
| `/layer start` | 桁名（登録済みから自動補完）と層番号を指定して作業開始を記録。層番号は数字のほか「シュリンク」等のテキストも可 |
| `/layer end` | 進行中の作業を終了し、**作業時間を自動計算**して保存 |
| `/layer status` | 現在進行中の作業を一覧表示（誰が何をどれだけ続けているか） |

同じ人が二重に開始することはできず、記録はサーバーごとに完全に分離されます。
記録した積層データは `/progress` の進捗にも自動反映されます。

### `/progress` — 機体進捗管理

**機体 → パーツ → 部品 …** の木構造で機体製作の進捗を管理し、
下位ノードから上位ノードへ進捗率を再帰的に集計します。

| コマンド | 内容 |
|---|---|
| `/progress view` | 進捗ツリーをドリルダウン表示（機体からパーツ、パーツから部品へボタンで降りていく）。進捗グラフ画像も表示 |
| `/progress setup` | Todoist プロジェクトを進捗ツリーのノードに紐付ける（サーバー管理権限または班長以上）。プロジェクトの親子構造は自動で同期されます |
| `/progress sync` | Todoist 同期と進捗の再集計を今すぐ実行（管理者。通常は20分ごとに自動実行） |
| `/progress init` | 進捗管理シートの登録・初期化（管理者） |

Todoist のタスク完了状況と `/layer` の桁巻き記録が進捗に反映されるため、
「Todoist は埋まっているのに機体全体で何%なのか分からない」状態を解消できます。

> ⚠️ **現状の制約**: `/progress` だけは進捗データの正本が Google スプレッドシートで、
> 導入サークルがシートをサービスアカウントに共有する手作業が必要です。
> 正本を他機能と同じ DB へ移す作業を進めており、完了後はこの手作業が不要になります
> （[`docs/DESIGN_PUBLIC_DISTRIBUTION.md`](docs/DESIGN_PUBLIC_DISTRIBUTION.md) Phase 1）。
> それまでの間、`/progress` 以外の機能は招待するだけで利用できます。

---

## その他の機能

| 機能 | 内容 |
|---|---|
| 日程調整 | 候補日時へのリアクション投票・自動締切・未回答者への催促DM（`/schedule`）。投票に使う絵文字はサーバーごとに変更可 |
| タスク管理 | 期限付きタスクの登録・班別通知・`/today` ラベル（`/task`）。Todoist 連携は任意 |
| 班・メンバー管理 | 班の作成・ロール紐付け・技能タグ・班をまたいだ支援候補の検索（`/team-*` `/member`） |
| リマインド | 締切前の未回答催促・毎朝の期限タスク通知・期限超過の警告 |
| レポート | 週次サマリー（サークル名入り）・タスクCSV出力・出欠率・監査ログ（`/report`） |
| 設定 | `/setup` ウィザードと `/settings_*` `/set_channel` `/set_role`（すべてサーバーごと） |

Todoist 連携を使う場合は、各サーバーの管理者が `/todoist-setup` のフォームから
自分のサークルのAPIトークンを登録します（Botの運用者にトークンを渡す必要はありません。
トークンは暗号化してDBに保存されます）。

---

## よくある質問

**Q. 自分たちでサーバーを借りたりBotを立てたりする必要はありますか？**
いいえ。招待URLを踏むだけです。VPSもBotトークンも `.env` 編集も不要です。

**Q. 他大学のサークルにこちらのデータが見えませんか？**
見えません。全データ・設定・権限・通知はサーバー（`guild_id`）単位で完全に分離されています。
コマンドはすべて実行されたサーバーにスコープされ、DMからの実行は拒否されます。

**Q. どんなデータが保存されますか？**
Discord のユーザーID・表示名・所属班・出欠回答・タスク・積層作業記録などです。
メッセージの本文は**読み取っていません**（`MESSAGE CONTENT` 特権インテントを
要求しない設計です）。収集項目の全一覧は
[プライバシーポリシー](docs/PRIVACY.md) を参照してください。

**Q. 使うのをやめたい / データを消したいときは？**
Botをサーバーからキックすれば動作は止まりますが、**記録済みのデータは
自動では削除されません**。削除を希望する場合は
[プライバシーポリシー](docs/PRIVACY.md) の「削除請求」の手順で、
サーバーID を添えて運営者へご連絡ください。

**Q. 班名や桁の名前は決まっていますか？**
いいえ。班・桁名・機体構成に初期値はなく、各サークルが自分の運用に合わせて登録します。

---

## 開発者・セルフホスト向け

自分のBotとして動かす場合の手順です。**導入するだけのサークルには不要**です。

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

- Discord Developer Portal で ON にする特権インテントは **SERVER MEMBERS のみ**です。
  `MESSAGE CONTENT` と `PRESENCE` は **OFF のまま**にしてください
  （このBotはメッセージ本文を読みません）
- 起動すると最小権限の招待URLがログに出力されます
- ローカルは SQLite（`DB_PATH`）、本番は `.env` の `DATABASE_URL=postgresql://...` で
  PostgreSQL に切り替わります
- VPS へのデプロイ手順は [`docs/SETUP.md`](docs/SETUP.md) を参照してください

### Docker で起動する場合

リポジトリルートに Bot 単体起動用の `docker-compose.yml` があります。

```bash
cp club-bot/.env.example club-bot/.env   # DISCORD_TOKEN と ENCRYPTION_KEY を設定
docker compose up -d --build
```

- `.env` の実値はイメージに含めず、起動時に `env_file` で注入します
- SQLite DB（`club-bot/data/`）とログ（`club-bot/logs/`）はホスト側にボリュームマウントされ、
  コンテナを再作成してもデータは残ります
- PostgreSQL の本番構成は
  [`deploy/docker-compose.postgres.yml`](deploy/docker-compose.postgres.yml) を参照してください
  （NocoDB 構成 [`deploy/docker-compose.nocodb.yml`](deploy/docker-compose.nocodb.yml) は
  レガシーとして残しています）

---

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/TERMS.md`](docs/TERMS.md) | 利用規約 |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | プライバシーポリシー（収集データ・保存場所・削除請求） |
| [`docs/OPERATION.md`](docs/OPERATION.md) | 運用マニュアル（全コマンド一覧・権限・トラブル対応） |
| [`docs/SETUP.md`](docs/SETUP.md) | セットアップ手順書（セルフホスト・VPSデプロイ向け） |
| [`docs/DESIGN_PUBLIC_DISTRIBUTION.md`](docs/DESIGN_PUBLIC_DISTRIBUTION.md) | 公開配布の設計方針 |
| [`docs/PUBLIC_RELEASE_TASKS.md`](docs/PUBLIC_RELEASE_TASKS.md) | 公開配布の進捗管理表 |
| [`docs/MULTI_TENANT_MIGRATION.md`](docs/MULTI_TENANT_MIGRATION.md) | 旧・単一サーバー運用版からの移行手順 |
| [`docs/NOCODB.md`](docs/NOCODB.md) | NocoDB 運用ガイド（レガシー） |

## 技術メモ

- 言語: Python 3.10 以上 / discord.py 2.x
- データ保存: SQLite（ローカル）/ PostgreSQL（本番構成）
- **マルチテナント**: 全テーブルに `guild_id` を保持し、`(guild_id, ...)` 先頭の
  複合ユニーク制約・インデックスで分離。ギルド別設定は `config.for_guild(guild_id)` が
  キャッシュ付きで解決します（優先順: ギルド別DB設定 > 環境変数 > デフォルト）
- **インテント**: 特権インテントは `members` のみ。`message_content` は要求せず、
  `on_message` ハンドラ・prefix コマンドを持ちません
  （再混入は `tests/test_intents.py` の回帰テストで検出）
- **Todoist 連携**: サーバーごとに独立。トークンは `/todoist-setup` の Modal から
  登録し、Fernet で暗号化してDBに保存します（暗号鍵 `ENCRYPTION_KEY` はホスト側の
  `.env` のみに保持）
- **機体進捗管理**: 現状は中央 Google Sheets 1枚を正本とし、Botは深さ・集計進捗率の
  再帰計算と Todoist・桁巻きの同期（20分ごと）を行います。複数サーバーが同じシートIDを
  登録しても、同期ジョブはシート単位で1回だけ実行されるため Sheets API のクォータは
  サーバー数に比例しません。DB移行後にこの構成は置き換わります
- **モジュール構成**: 13 Cog（Core / Schedule / Tasks / Members / Reminders /
  Reports / LayerTracking / Settings / SetupWizard / Teams / Sheets /
  TodoistAdmin / Progress）

## License

MIT License — 詳細は [LICENSE](../LICENSE) を参照してください。
