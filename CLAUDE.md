# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク（2トラック並行。1セッションで混ぜない）
- bot 側の改善: club-bot/docs/IMPROVEMENT_TASKS.md（G0〜G4。根拠は docs/IMPROVEMENT_REPORT.md）
- ダッシュボード改良: club-bot/docs/DASHBOARD_TASKS.md（D0〜D3。根拠は同レポート P1-12）
- 完了済み: club-bot/docs/FEATURE_TASKS.md / docs/PUBLIC_RELEASE_TASKS.md
- 実装ループは /acm-bot-loop スキルの手順で回す（全テストパスまで自走）

## 設計判断の正
- ADR と既知のハマりどころは Obsidian の ClaudeVault にある
  （/add-dir C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot）
- ADR に反する実装をしない。覆す必要があると判断したら実装せず報告する

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は club-bot/venv/Scripts/python.exe（無ければ python）
- ダッシュボードのフロントは club-bot/dashboard/static/。
  外部 CDN・npm パッケージ・フレームワークを増やさない
- フロントの純粋関数は static/lib/ に置き、club-bot/ で
  node --test "tests_js/*.test.mjs" で検証する（Node 22.7+）

## ナレッジベース

開発ノートは C:\Users\yoshi\ClaudeVault\ にある。
- セッション開始時: ClaudeVault/index.md と
  ClaudeVault/projects/acm_bot/_index.md を読むこと
- 書き込みルール: ClaudeVault/CLAUDE.md に従うこと
