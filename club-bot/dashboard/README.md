# ダッシュボード（Web UI）

鳥人間サークル運営 Bot のデータを、Discord でログインして表形式で
閲覧・編集するための Web アプリです。

- **bot とは別プロセス**で動きます（Web 層の不具合で Discord 接続を
  落とさないため）
- データは bot と同じ DB を共有します
- **表示・編集はすべてサーバー（Discord ギルド）単位で分離**されます。
  他のサークルのデータは取得できません

## セットアップ

```bash
cd club-bot
venv/bin/pip install -r dashboard/requirements.txt
cp dashboard/.env.example dashboard/.env   # 値を埋める
```

### 環境変数

| 変数 | 必須 | 内容 |
|---|---|---|
| `DASHBOARD_SECRET_KEY` | ✅ | セッション Cookie の署名鍵。`python -c "import secrets; print(secrets.token_urlsafe(48))"` で生成 |
| `DISCORD_CLIENT_ID` | ✅ | Discord Developer Portal の OAuth2 クライアント ID |
| `DISCORD_CLIENT_SECRET` | ✅ | 同 クライアントシークレット |
| `DASHBOARD_REDIRECT_URI` | ✅ | OAuth2 のリダイレクト先（例 `https://example.com/auth/callback`）。Portal 側にも同じ値を登録する |
| `DASHBOARD_BASE_URL` | | 公開 URL（既定 `http://127.0.0.1:8000`） |
| `DASHBOARD_SESSION_MAX_AGE` | | セッション有効期間（秒。既定 7 日） |
| `DASHBOARD_SECURE_COOKIE` | | `0` で Secure 属性を外す（**ローカル開発のみ**） |
| `DATABASE_URL` / `DB_PATH` | | bot と同じ DB を指す |

**Discord Bot トークンは不要です。** ダッシュボードは Discord へ
ログイン用の OAuth2 リクエストしか送りません。

## 起動

```bash
cd club-bot
venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
```

本番は Caddy 等のリバースプロキシ経由で HTTPS 配信します
（`deploy/` を参照）。`127.0.0.1` にバインドし、直接インターネットへ
公開しないでください。

## 構成

| ファイル | 役割 |
|---|---|
| `main.py` | アプリファクトリ・ライフサイクル・静的配信 |
| `config.py` | 環境変数からの設定読み込み |
| `db.py` | DB 接続のライフサイクル（bot と同じ `utils.db.Database`） |
| `static/` | フロント（素の HTML/CSS/JS。外部 CDN に依存しない） |

## 設計上の約束

- リポジトリ層は `guild_id` を必須引数に取る。**Web 層はセッションで
  検証済みの `guild_id` 以外を絶対にリポジトリへ渡さない**
- URL クエリやリクエストボディから来た `guild_id` をそのまま信用しない
- OpenAPI ドキュメント（`/docs`・`/openapi.json`）は公開しない
