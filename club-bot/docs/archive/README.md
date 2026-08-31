# アーカイブ（過去の構成の記録）

このディレクトリは、**現在は使われていない構成・すでに完了した移行作業**の
ドキュメントを残しておく場所です。

**現行構成のセットアップ手順ではありません。** 新しく Bot を動かす場合は
[`../SETUP.md`](../SETUP.md)、運用は [`../OPERATION.md`](../OPERATION.md) を
参照してください。

残している理由は、旧構成のまま動かしているインスタンスの参照先が必要なことと、
「なぜ今の構成になったのか」を後から追えるようにするためです。

---

| ファイル | 内容 | 現在の位置づけ |
|---|---|---|
| [`MULTI_TENANT_MIGRATION.md`](MULTI_TENANT_MIGRATION.md) | 単一サーバー運用版から `guild_id` スコープのマルチテナント構成へ移行したときの変更内容・設計判断・移行手順 | **移行は完了済み**。旧バージョンから上げる場合のみ参照 |
| [`NOCODB.md`](NOCODB.md) | NocoDB を閲覧・編集 UI として使う構成の運用ガイド | **廃止**。現在の閲覧 UI は自前の Web ダッシュボード（[`../DASHBOARD_SETUP.md`](../DASHBOARD_SETUP.md)）。ただし 3 章（SQLite → PostgreSQL 移行）・5 章（バックアップ）は現行構成でも通用する |
| [`DESIGN_NOCODB_MULTITENANT.md`](DESIGN_NOCODB_MULTITENANT.md) | NocoDB 移行・マルチテナント化・トークン暗号化・班/技能の DB 管理の設計書 | **一部のみ有効**。マルチテナント化とトークン暗号化は実装済み、NocoDB 部分は不採用 |
| [`FIX_ENV.md`](FIX_ENV.md) | `.env` が読み込めない問題（BOM・CRLF・パスのズレ）への恒久対策の説明 | **対策は `config.py` に取り込み済み**。同じ症状が出たときの調査メモとして参照 |
| [`GUILD_VIEWS.sql`](GUILD_VIEWS.sql) | NocoDB 向けのギルド別ビュー雛形 SQL | **不要**。ダッシュボードがアプリケーション層で `guild_id` を強制するため |
