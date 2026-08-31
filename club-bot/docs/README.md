# ドキュメント一覧

鳥人間サークル 統合運営 Discord Bot のドキュメントです。
**まず自分がどれに当てはまるかを選んでください。**

---

## 1. Bot を自分のサークルで使いたい

サーバーを借りたりプログラムを触ったりする必要はありません。

| ドキュメント | 内容 |
|---|---|
| **[取扱説明書 GUIDE.md](GUIDE.md)** | **ここから読んでください。** 導入手順・役割別の使い方・場面別の操作・困ったときの対処・年度替わりの引き継ぎ |
| **[権限レベル別 取扱説明書（PDF）](role_manual.pdf)** | Discord をはじめて使う人向けの冊子。コマンドの打ち方から、L1一般メンバー／L2班長／L3幹部／L4Bot管理者 の権限ごとにできることまでを図で説明（[HTML 版](role_manual.html)） |
| [桁巻きガイド（PDF）](keta_maki_guide.pdf) | `/layer` の使い方だけを抜き出した一般班員向けの1枚もの。印刷や部内共有に使えます（[HTML 版](keta_maki_guide.html)） |
| [利用規約 TERMS.md](TERMS.md) | 提供条件・禁止事項・免責 |
| [プライバシーポリシー PRIVACY.md](PRIVACY.md) | 収集する情報・保存場所・保存期間・削除の方法 |

## 2. Bot をホストして他のサークルへ提供したい（運営者）

| ドキュメント | 内容 |
|---|---|
| [セットアップ手順書 SETUP.md](SETUP.md) | VPS を借りるところから 24 時間常駐させるまで。用語の説明つき |
| [運用マニュアル OPERATION.md](OPERATION.md) | 全コマンド一覧・権限レベル・自動ジョブ・暗号鍵の管理・障害対応 |
| [ダッシュボード セットアップ DASHBOARD_SETUP.md](DASHBOARD_SETUP.md) | Web UI の OAuth2 設定・HTTPS 公開・systemd 常駐・公開前チェックリスト |
| [ダッシュボード構成 ../dashboard/README.md](../dashboard/README.md) | 画面仕様・環境変数・設計上の約束 |

**[TERMS.md](TERMS.md) と [PRIVACY.md](PRIVACY.md) は、このリポジトリの運営者が
運用するインスタンスについてのものです。fork して自分でホストする場合は、
運営者名・連絡先・データの保存場所を自分のものへ置き換えてください。**

## 3. コードを読みたい・開発に参加したい

| ドキュメント | 内容 |
|---|---|
| [開発ガイド ../../CONTRIBUTING.md](../../CONTRIBUTING.md) | 環境構築・テスト実行・ブランチとコミットの規約 |
| [開発規約 ../../AGENTS.md](../../AGENTS.md) | 絶対に守るルール（マルチテナント原則・後方互換性・秘密情報） |
| [技術メモ ../README.md](../README.md) | 構成・アーキテクチャ・マルチテナントの実装方針 |
| [設計方針 DESIGN_PUBLIC_DISTRIBUTION.md](DESIGN_PUBLIC_DISTRIBUTION.md) | 「他大学の鳥人間サークルへ公開配布する」という方針とその根拠 |
| [設計判断の記録 adr/](adr/) | 一度決めた設計判断とその理由（Architecture Decision Record） |

---

## その他のディレクトリ

| ディレクトリ | 内容 |
|---|---|
| [`development/`](development/) | 開発の過程で使った作業用ドキュメント（タスク管理表・分析レポート）。**現状の仕様の根拠には使えません** |
| [`archive/`](archive/) | 現在は使われていない構成の記録（NocoDB 構成・マルチテナント移行手順など） |
