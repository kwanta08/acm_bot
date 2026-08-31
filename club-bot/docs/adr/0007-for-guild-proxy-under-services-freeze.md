---
id: 0007
title: "`services/` 変更禁止と `for_guild()` プロキシによる guild_id 注入"
slug: for-guild-proxy-under-services-freeze
status: superseded
date: 2026-08-20
supersedes: —
superseded_by: 0009
---

# 0007: `services/` 変更禁止と `for_guild()` プロキシ

## 文脈

マルチテナント改修（`docs/archive/MULTI_TENANT_MIGRATION.md`）で、全リポジトリの公開メソッドは
`guild_id: int` を第1引数に必須化し、全 SQL に `WHERE guild_id = ?` を付けた。

一方この改修では `services/` が変更対象から外されていた。`services/` は `guild_id` を
受け取れないまま `guild_id` を要求するリポジトリを呼ぶ必要があり、その差を埋めるために
`repositories/base.py` に **`repo.for_guild(guild_id)` プロキシ**を導入した。
Cog 側は `LayerTrackingService(self.session_repo.for_guild(gid), ...)` のように、
ギルド固定スコープでサービスを組み立てる。

## 決定（および、決定ではなかったこと）

**「`services/` 変更禁止」は意図的な設計判断ではなかった。**

2026-08-20 に当時の判断者へ確認したところ、制約を課した理由の記憶はなく、記録も残っていない。
テスト不足を避けるためでも、PR サイズを抑えるためでも、並行作業との衝突回避でもない。
作業を進めるうちにそうなった、という性質のものである。

したがって `for_guild()` プロキシは、**根拠の無い制約を回避するために生まれた実装**であり、
層の設計として選ばれたものではない。

## 影響範囲

- 後続の [0009](./0009-services-guild-id-freeze-lifted.md) がこの制約を明示的に解除し、
  プロキシを移行期間の互換用途に限定した（R4）。ただし 2026-08-20 時点でその改修は未実施。
- **例外**: `dashboard/security.py` の `GuildScope.bind()` は内部で `for_guild()` を使うが、
  これはレガシー互換ではなく「検証済み `guild_id` 以外をリポジトリへ渡せなくする」ための
  意図的な用法である（[0008](./0008-dashboard-guild-scope.md)）。
- `todoist_manager.for_guild()` / `config.for_guild()` は名前が同じだけの別物。混同しないこと。

## 教訓

制約が設計書に「そうなっている」とだけ書かれ、理由が併記されていなかったため、
後続の設計者はそれを尊重すべき前提なのか単なる惰性なのか判断できず、
回避策（プロキシ）を作る方向へ進んだ。

**制約を課すときは理由を必ず併記する。理由を書けないなら、それは制約ではない。**

## 根拠

- `docs/archive/MULTI_TENANT_MIGRATION.md` §「リポジトリの guild_id 強制と services 互換」
- `repositories/base.py`（`for_guild` / `GuildBoundRepository`）
- 制約の理由について 2026-08-20 に判断者へ確認 — 記憶・記録ともに無し
