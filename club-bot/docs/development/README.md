# 開発記録（内部資料）

このディレクトリは、**開発の過程で使った作業用ドキュメント**を残しておく場所です。

- 製品の使い方ではありません。Bot を導入・利用する方は
  [`../GUIDE.md`](../GUIDE.md) を読んでください
- 運用する方は [`../OPERATION.md`](../OPERATION.md) を読んでください

ここにある文書は**書かれた時点のスナップショット**です。実装が進んだ結果、
現在のコードと食い違っている記述が含まれます。**現状の仕様の根拠には使えません。**
仕様を知りたい場合は、コードと `../` 直下の製品ドキュメントを参照してください。

---

## タスク管理表

実装を1タスクずつ進めるために作った作業表です。完了済みタスクには
「完了内容 / 設計判断 / 次タスクへの申し送り」が追記されており、
**なぜその実装になったのかを追う履歴**として残しています。

| ファイル | 対象 | 状態 |
|---|---|---|
| [`PUBLIC_RELEASE_TASKS.md`](PUBLIC_RELEASE_TASKS.md) | 公開配布の準備（P0〜P3） | ほぼ完了 |
| [`FEATURE_TASKS.md`](FEATURE_TASKS.md) | `/help`・データ削除・重量管理・大会逆算・年度替わり（F0〜F6） | ほぼ完了 |
| [`IMPROVEMENT_TASKS.md`](IMPROVEMENT_TASKS.md) | 改善タスク（G0〜G4）。現在進行中の表 | 進行中 |
| [`DASHBOARD_TASKS.md`](DASHBOARD_TASKS.md) | ダッシュボード改良（D0〜D3）と Liquid Glass 刷新の記録 | 完了 |

## 分析レポート

| ファイル | 内容 |
|---|---|
| [`IMPROVEMENT_REPORT.md`](IMPROVEMENT_REPORT.md) | 全コード分析にもとづく改善提案（P0/P1/P2 の32件）。`IMPROVEMENT_TASKS.md` の根拠 |

> **注意**: このレポートが「壊れている」と書いた項目の**大半はすでに修正済み**です。
> 各項目の現在の状態は `IMPROVEMENT_TASKS.md` のチェックボックスと完了ログで確認してください。

## 起票用チケット

タスク表へ追加するために書いた個別チケットです。内容は `IMPROVEMENT_TASKS.md` へ
取り込み済みで、起票時の調査結果を残す目的で保存しています。

| ファイル | 内容 |
|---|---|
| [`G1-0_TICKET.md`](G1-0_TICKET.md) | ダッシュボードの `row_id` 型変換（PostgreSQL でのみ落ちる不具合） |
| [`G1-9_G1-10_TICKETS.md`](G1-9_G1-10_TICKETS.md) | `_coerce()` の型不整合 / CI への PostgreSQL 追加 |

## エージェント向け起動プロンプト

タスク表をコーディングエージェントで1件ずつ回すための入力集です。
**この作業手順に依存せずにリポジトリを開発できます**（通常の開発手順は
リポジトリルートの [`../../../CONTRIBUTING.md`](../../../CONTRIBUTING.md) を参照）。

| ファイル | 対象の表 |
|---|---|
| [`FEATURE_LOOP_PROMPT.md`](FEATURE_LOOP_PROMPT.md) | `FEATURE_TASKS.md` |
| [`IMPROVEMENT_LOOP_PROMPT.md`](IMPROVEMENT_LOOP_PROMPT.md) | `IMPROVEMENT_TASKS.md` |
| [`G2_LOOP_PROMPT.md`](G2_LOOP_PROMPT.md) | `IMPROVEMENT_TASKS.md` の G2 フェーズ（当時の状況専用） |
| [`DASHBOARD_LOOP_PROMPT.md`](DASHBOARD_LOOP_PROMPT.md) | `DASHBOARD_TASKS.md` |

これらの文書には「開発ノート」への参照が出てきます。**開発ノートは開発者の
ローカル環境にある個人的なメモ置き場で、このリポジトリには含まれません。**
外部の方が読む際は、その部分は読み飛ばして構いません
（設計判断のうち公開すべきものは [`../adr/`](../adr/) に置いています）。
