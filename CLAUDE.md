# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク
- 進行中の実装タスクは club-bot/docs/development/IMPROVEMENT_TASKS.md の表に従う
  （公開配布まわりは同ディレクトリの PUBLIC_RELEASE_TASKS.md / FEATURE_TASKS.md。
  いずれも作業用の内部資料で、現状の仕様の根拠にはしない）
- 実装ループは /acm-bot-loop スキルの手順で回す（全テストパスまで自走）

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は仮想環境のもの（Windows: `club-bot/venv/Scripts/python.exe` /
  macOS・Linux: `club-bot/venv/bin/python`）。無ければ `python`

## ドキュメント
- 公開ドキュメントの地図は club-bot/docs/README.md
- 実装を変えたら、対応する公開ドキュメント（README.md / docs/GUIDE.md /
  docs/OPERATION.md）も同時に直す
- **公開リポジトリなので、個人名・ローカルの絶対パス・ホスト名・実トークンを
  ドキュメントへ書かない**

## 開発ノート（任意・リポジトリ外）

設計判断と既知の落とし穴を記録したローカルのノート置き場がある場合は、
セッション開始時にその索引を読むこと。**リポジトリには含まれていない**ため、
無い環境ではこの節を無視してよい。

公開すべき設計判断は club-bot/docs/adr/ に ADR として置く。
