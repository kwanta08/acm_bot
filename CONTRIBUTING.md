# 開発ガイド

このリポジトリで開発を始めるための手順です。
**守るべきルールは [AGENTS.md](AGENTS.md) が正**なので、作業前に一読してください。

---

## 1. 環境を作る

必要なもの: **Python 3.10 以上**（CI は 3.10 / 3.11 / 3.12 で回しています）。
DB は開発・テストでは SQLite を使うため、追加のインストールは要りません。

```bash
git clone https://github.com/kwanta08/acm_bot.git
cd acm_bot/club-bot

python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install -r dashboard/requirements.txt   # ダッシュボードのテストに必要
venv/bin/pip install ruff pytest
```

Windows では `venv/Scripts/python.exe` / `venv/Scripts/pip.exe` を使います。

> **`dashboard/requirements.txt` を入れ忘れると `tests/test_dashboard_*.py` が
> 静かに skip されます。** CI では必ず入れているので、手元だけ緑になる状態を
> 避けるために入れておいてください。

## 2. テストを回す

**`club-bot/` をカレントディレクトリにして実行します。**

```bash
cd club-bot

venv/bin/ruff check .              # lint（CI が回すのはこれ）
venv/bin/python -m pytest tests/ -q
```

**全パスするまでは作業完了ではありません**（AGENTS.md の絶対ルール 7）。

> `ruff format` はリポジトリ全体には適用していません（既存ファイルに未整形のものが
> 残っています）。**リポジトリ全体に `ruff format .` をかけないでください** —
> 変更と無関係な差分が大量に混ざり、レビューできない PR になります。
> 整形するとしても自分が触った範囲に留めてください。

### PostgreSQL 経路も確認したいとき

本番は PostgreSQL、SQLite は開発・テスト専用です。SQLite は型に寛容なため
（`'5'` を `5` として扱う）、**SQLite だけ緑で PostgreSQL の本番だけ落ちる**
不具合があり得ます。DB ドライバに触る変更をしたら、PostgreSQL でも回してください。

```bash
# DB 名に "test" を含めること（含まないとテスト側の安全装置が skip します）
export CLUB_TEST_PG_DSN=postgresql://<ユーザー>:<パスワード>@127.0.0.1:5432/clubbot_test
venv/bin/python -m pytest tests/ -q -rs
```

CI では `test-postgres` ジョブが PostgreSQL 16 のサービスコンテナで同じことをします。
**skip は緑ではありません** — CI は skip が1件でもあると失敗します。

## 3. Bot を起動して動かす

```bash
cd club-bot
cp .env.example .env      # DISCORD_TOKEN と ENCRYPTION_KEY を設定
venv/bin/python bot.py
```

`ENCRYPTION_KEY` の生成:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Discord Developer Portal で ON にする特権インテントは **SERVER MEMBERS のみ**。
  `MESSAGE CONTENT` と `PRESENCE` は **OFF のまま**にしてください
- 起動すると最小権限の招待URLがログに出力されます
- `GUILD_ID` は任意です（未指定なら参加中の全サーバーで独立に動きます）

VPS へのデプロイ手順は [`club-bot/docs/SETUP.md`](club-bot/docs/SETUP.md) にあります。

## 4. 変更するときに外せない4点

詳細は [AGENTS.md](AGENTS.md)。特に踏みやすいのはこの4つです。

| ルール | 具体的には |
|---|---|
| **`guild_id` スコープ** | 新しいテーブル・設定は必ず `guild_id` を持たせる。リポジトリ層のメソッドは `guild_id` を必須引数で受け取り、全 SQL に条件を付ける。**グローバル状態を新設しない** |
| **後方互換性** | 既存サーバーの DB を壊す変更には、必ず `migrations/` か `utils/db.py` の `_migrate()` を同時に書く |
| **ドメインは固定、組織構造は可変** | 桁巻き・積層・主桁・機体はコードに書いてよい。班名・サークル名・機体名・チャンネル・ロールは**埋め込まず**ギルド別 settings で扱う |
| **秘密情報と個人情報を書かない** | `.env` の実値・トークン・個人名・ローカルの絶対パス・実在のギルドID。例示は `<...>` や `example.com` を使う。例外は `docs/TERMS.md` / `docs/PRIVACY.md` の運営者名と連絡先（公開Botに必要な開示のため意図的に実名） |

### ドキュメントも一緒に直す

実装とドキュメントが食い違ったら**両方**直します。特にコマンドを追加・削除したら、
**`docs/OPERATION.md` の2章と `docs/GUIDE.md` の「付録: コマンド早見表」の両方**を
更新してください。`tests/test_docs_commands.py` が双方向に検査するので、
忘れるとテストが落ちます。

ドキュメントの置き場所は [`club-bot/docs/README.md`](club-bot/docs/README.md) の
とおりです。`docs/` 直下は利用者・運用者向けの製品ドキュメント専用で、
作業用の表やレポートは `docs/development/`、旧構成の記録は `docs/archive/` に置きます。

## 5. ブランチとコミット

- **`main` へ直接コミットしない。** 作業用のブランチを切ってください
- コミットメッセージは [Conventional Commits](https://www.conventionalcommits.org/ja/)
  （`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`）
- Pull Request の前に `ruff check .` と `pytest tests/` が全パスしていること

## 6. 設計判断を変えるとき

一度決めた設計判断は [`club-bot/docs/adr/`](club-bot/docs/adr/) に ADR として
残しています。ここに書かれた判断に反する変更をする場合は、**まず新しい ADR を書いて
既存のものを `superseded` にしてから**実装してください。同じ議論を二度やらないためです。
