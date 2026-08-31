# ADR — 設計判断の記録

一度決めた設計判断と、その理由を残しておくディレクトリです
（Architecture Decision Record）。

「なぜこの実装になっているのか」「なぜこの方法を採らなかったのか」を
後から読めるようにするのが目的です。**同じ議論を二度やらないため**の記録なので、
ここに書かれた判断に反する変更をする場合は、まず新しい ADR で
既存のものを `superseded`（置き換え済み）にしてください。

## 書式

各ファイルは YAML フロントマターで状態を持ちます。

| 項目 | 意味 |
|---|---|
| `status` | `accepted`（有効） / `superseded`（別の ADR に置き換えられた） |
| `supersedes` | この ADR が置き換えた ADR の番号 |
| `superseded_by` | この ADR を置き換えた ADR の番号 |

## 一覧

| # | 題目 | 状態 |
|---|---|---|
| [0007](0007-for-guild-proxy-under-services-freeze.md) | `services/` 変更禁止と `for_guild()` プロキシによる `guild_id` 注入 | `superseded`（→ 0009） |
| [0008](0008-dashboard-guild-scope.md) | ダッシュボードにおける `guild_id` スコープの強制層 | `accepted` |
| [0009](0009-services-guild-id-freeze-lifted.md) | `services/` 変更禁止を解除し、services も `guild_id` を受け取る形へ改修する | `accepted`（未実装） |

> **0001〜0006 が無いのはなぜか**: 初期の設計判断は開発者のローカルのメモ置き場に
> 書かれていて、リポジトリには含まれていません。0007 以降は
> 「リポジトリを読む人が知る必要のある判断」としてここへ置いています。
> 欠番はそのまま残しています（番号を詰めると過去の文書からの参照が壊れるため）。
