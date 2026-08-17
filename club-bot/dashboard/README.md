# ダッシュボード（Web UI）

鳥人間サークル運営 Bot のデータを、Discord でログインして表形式で
閲覧・編集するための Web アプリです。

- **bot とは別プロセス**で動きます（Web 層の不具合で Discord 接続を
  落とさないため）
- データは bot と同じ DB を共有します
- **表示・編集はすべてサーバー（Discord ギルド）単位で分離**されます。
  他のサークルのデータは取得できません

## セットアップ

**手順の詳細は [`../docs/DASHBOARD_SETUP.md`](../docs/DASHBOARD_SETUP.md) にあります**
（Discord Developer Portal の OAuth2 設定・Caddy での HTTPS 公開・systemd 常駐・
トラブルシューティング・公開前チェックリスト）。

最短の流れ:

```bash
cd club-bot
venv/bin/pip install -r dashboard/requirements.txt
cp dashboard/.env.example dashboard/.env   # 値を埋める
venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
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
| `DASHBOARD_DB_POOL_MAX_SIZE` | | 接続プールの上限（既定 10。bot とは独立） |

**Discord Bot トークンは不要です。** ダッシュボードは Discord へ
ログイン用の OAuth2 リクエストしか送りません。

## 起動

```bash
cd club-bot
venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8000
```

本番は Caddy 等のリバースプロキシ経由で HTTPS 配信します
（`deploy/Caddyfile`・`deploy/club-bot-dashboard.service`）。
`127.0.0.1` にバインドし、直接インターネットへ公開しないでください。
手順は [`../docs/DASHBOARD_SETUP.md`](../docs/DASHBOARD_SETUP.md) を参照。

## 画面の仕様

- **シートタブ**: 出欠回答は予定（日程調整）ごと、桁巻き記録は桁ごとに、
  スプレッドシートのタブのように切り替えて表示します（ページ全体は
  リロードしません）。タブは**表の上**（表の種類を選ぶタブのすぐ下、
  ツールバーと表より上）にあり、名前は予定のタイトル、並びは開催日時の
  降順で、多いときは横スクロールします。選択中のタブは太字＋背景色で
  示します
- **出欠回答のピボット表**: シート内は「候補日時 / 参加 / 不参加 / 未定 /
  未回答」の固定 5 列で、1 行 = 候補日時 1 つ（昇順）。各セルに該当者の
  表示名を縦に列挙し、冒頭に `参加 (8)` の形式で人数を出します。
  候補日時は JST 秒単位ですが、**時刻を指定せず登録した候補**（`2026-09-01`
  や `9/1` のように日付だけで登録したもの）は `00:00:00` を付けず
  日付だけを出します（判定は登録時の入力文字列＝候補ラベルで行います）。
  「未回答」は**現役の登録メンバー（members 台帳）のうち、その予定の
  どの候補にも投票していない人**です（bot の催促はロール基準ですが、
  ダッシュボードはロールを解決できないため台帳基準で表示します）
- **表示名**: 人物は Discord ユーザー ID ではなく**そのサーバーでの表示名**
  （ニックネーム → グローバル表示名 → ユーザー名）、チャンネルは `#名前` で
  表示します。名前は bot が `discord_name_cache` テーブルへ同期した
  キャッシュから解決し、解決できない場合（退会・チャンネル削除）は
  ID 付きのフォールバック表示になります。**DB には従来どおり ID を保持**し、
  変換は表示層（`display.py`）だけで行います
- **班名**: メンバー表の「主所属班」「副所属班」は班キー（`kouzou` のような
  slug）ではなく、`/team-add` で登録した**班名**（例: 構造班）で表示します。
  副所属班は複数を「、」区切りで並べます。teams に無いキーは slug のまま
  出し、無効化済みの班も名前で出します。セルをクリックして編集するときの
  入力値と保存値は従来どおり生値（slug / JSON 配列の文字列）です。
  班シートの「班キー」列は slug のままです
- **日時**: すべて **Asia/Tokyo（JST）・秒単位**で表示します（例外は上記の
  時刻未指定の候補日時のみ）。DB の値は変更しません
- **CSV**: 画面と同じ表示値（名前・JST）で出力します。生値のままの
  完全バックアップが必要な場合は bot の `/data export` を使ってください

## 構成

| ファイル | 役割 |
|---|---|
| `main.py` | アプリファクトリ・ライフサイクル・静的配信 |
| `config.py` | 環境変数からの設定読み込み |
| `db.py` | DB 接続のライフサイクル（bot と同じ `utils.db.Database`） |
| `display.py` | 表示整形の共通ヘルパー（ID → 表示名解決・JST 秒フォーマット） |
| `static/` | フロント（素の HTML/CSS/JS。外部 CDN に依存しない） |

## 設計上の約束

- リポジトリ層は `guild_id` を必須引数に取る。**Web 層はセッションで
  検証済みの `guild_id` 以外を絶対にリポジトリへ渡さない**
- URL クエリやリクエストボディから来た `guild_id` をそのまま信用しない
- OpenAPI ドキュメント（`/docs`・`/openapi.json`）は公開しない
