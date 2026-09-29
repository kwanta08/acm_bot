# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク
- 進行中の実装タスクは club-bot/docs/development/HARDENING_TASKS.md の表に従う
  （H1-1〜H1-5。決めた原則が守られていない箇所を揃える5件。スキーマ変更なし）
- 完了済みの表: IMPROVEMENT_TASKS.md（G0〜G4）/ FEATURE_TASKS.md /
  PUBLIC_RELEASE_TASKS.md / DASHBOARD_TASKS.md。
  いずれも作業用の内部資料で、現状の仕様の根拠にはしない
- 実装ループは /autonomous-dev-loop で回す
  （起動プロンプトは club-bot/docs/development/HARDENING_LOOP_PROMPT.md。
  内側の実装→検証→自己修正は /acm-bot-loop の手順）

## 設計判断の正
- 公開している設計判断は club-bot/docs/adr/ にある
- ADR と既知のハマりどころを記録したローカルの開発ノートがある場合は、
  セッション開始時に `/add-dir` で読ませる（扱いは下の「開発ノート」節）
- ADR に反する実装をしない。覆す必要があると判断したら実装せず報告する

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は仮想環境のもの（Windows: `club-bot/venv/Scripts/python.exe` /
  macOS・Linux: `club-bot/venv/bin/python`）。無ければ `python`
- ダッシュボードのフロントは club-bot/dashboard/static/。
  外部 CDN・npm パッケージ・フレームワークを増やさない
- フロントの純粋関数は static/lib/ に置き、club-bot/ で
  node --test "tests_js/*.test.mjs" で検証する（Node 22.7+）

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

**ノートの実際のパスはこのファイルに書かない。** 公開リポジトリなので、
環境ごとに違う絶対パスは追跡対象外のファイル（`CLAUDE.local.md`）に置く。

公開すべき設計判断は club-bot/docs/adr/ に ADR として置く。
