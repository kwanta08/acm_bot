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
cp .env.example .env   # DISCORD_TOKEN, GUILD_ID を最低限設定
venv/bin/python bot.py
```

詳細は [`docs/SETUP.md`](docs/SETUP.md) を参照してください。
