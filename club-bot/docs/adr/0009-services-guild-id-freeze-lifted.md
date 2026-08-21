---
id: 0009
title: "`services/` 変更禁止を解除し、services も guild_id を受け取る形へ改修する"
slug: services-guild-id-freeze-lifted
status: accepted (not implemented)
date: 2026-08-20
supersedes: 0007
superseded_by: —
---

# 0009: `services/` 変更禁止の解除

> **状態について**: 決定は有効だが、**実装は行われていない**。
> 2026-08-20 時点の現行コードには [0007](./0007-for-guild-proxy-under-services-freeze.md) の
> プロキシがそのまま残っている（後述「未完了の実態」）。
> ADR 一覧を見ただけで未完了と分かるよう、状態を `accepted (not implemented)` としている。

## 文脈

[0007](./0007-for-guild-proxy-under-services-freeze.md) のとおり、前回のマルチテナント改修では
`services/` が変更禁止とされ、`repositories/base.py` の `repo.for_guild(guild_id)` プロキシで
guild_id を暗黙注入することでその制約を回避していた。0007 で確認したとおり、
**この制約には根拠の記録が無い**。

NocoDB 移行設計（`docs/DESIGN_NOCODB_MULTITENANT.md`）にあたり、この前提を引き継ぐか
決める必要が生じた。同設計書は対象4件の2番目として
**「guild_id によるマルチテナント化の完成（外部サービス設定のギルド分離）」**を掲げている。
括弧内が要件の実体である — Todoist トークン等の外部サービス設定をサーバーごとに分離するには、
services 自身がギルドを識別できなければならない。`services/` を凍結したままでは達成できない。

## 選択肢

| | 内容 |
|---|---|
| **A** | 制約を維持し、`for_guild()` プロキシを恒久的な層の設計として位置づける |
| **B** | 制約を解除し、services も `guild_id` を受け取る形へ改修する。プロキシは移行期間のみ残す |

## 決定

**B を採用する。**

あわせて伝播ルールを規約として明文化した（設計書 §7）:

- **R3** — Repository の公開メソッドは第1引数が必ず `guild_id: int`。全 SQL に `WHERE guild_id = ?`
- **R4** — `guild_id` を引数に取らない公開メソッドを新規追加しない。
  `for_guild()` は移行期間の互換用途に限定し、**新規コードでは使わない**
- **R5** — Service はギルド固有情報（トークン・プロジェクトID・設定）を
  インスタンスのグローバル状態に持たない。`guild_id` を明示引数で受けるか、
  ギルド別インスタンスをファクトリ経由で取得する

## 却下した案 (A)

1. **元の制約に根拠が無かった**（0007）。根拠の無い制約を回避するための実装を、
   層の設計として恒久化するのは筋が通らない。
2. **R5 と整合しない。** プロキシは「ギルド固定済みのリポジトリを保持したサービスインスタンス」を
   作る形であり、これは guild 固有情報をインスタンス状態に持つことに他ならない。
   `for_guild()` を恒久化するなら R5 は書けない。
3. **要件を達成できない。** 外部サービス設定のギルド分離（設計書の対象2）は
   services 側がギルドを識別することを要求する。プロキシはリポジトリ呼び出しの
   guild_id しか埋められず、サービスが自前で持つ設定には届かない。

## 影響範囲

- 設計書 §7 に R3〜R5 が伝播ルール表として明文化された。
- [0008](./0008-dashboard-guild-scope.md) の `GuildScope.bind()` は `for_guild()` を使うが、
  これは解除後も**恒久的な用法**として残る（検証済み guild_id 以外を渡せなくするための意図的な使用）。
  R4 の「移行期間の互換用途」とは区別すること。
- `todoist_manager.for_guild()` および `config.for_guild()` は**名前が同じだけの別物**であり、
  0007 のプロキシとは無関係。撤去対象に数えない。

## 未完了の実態（2026-08-20 現行コード）

`repo.for_guild()`（0007 のプロキシ）の本番コードでの使用箇所:

| 箇所 | 状態 |
|---|---|
| `cogs/layer_tracking.py:39` | `LayerTrackingService(self.session_repo.for_guild(guild_id))` |
| `cogs/schedule.py:178, :361, :527, :660, :710` | `self.repo.for_guild(guild_id)` を Embed 生成へ渡す |
| `dashboard/security.py:59` | **意図的な用法**（0008）。撤去対象外 |

受け手のサービスも改修されていない:

- `LayerTrackingService.__init__(self, session_repo)` — `guild_id` を取らない。
  `has_active(user_id)` / `start(user_id, keta, layer_num)` / `end(user_id, display_name)` も同様
- `schedule_service.build_option_embed(repo, bot, schedule, option, guild)` /
  `build_summary_embed(repo, bot, schedule, guild)` — プロキシ済み repo を受け取る形のまま

## 完了条件

本 ADR を `accepted` に格上げできるのは、以下がすべて満たされたとき:

1. `LayerTrackingService` が `guild_id` を明示引数で受ける形へ改修され、
   `cogs/layer_tracking.py` がプロキシを渡さなくなる
2. `schedule_service.build_option_embed` / `build_summary_embed` が
   `guild_id` を明示引数で受ける形へ改修され、`cogs/schedule.py` の 5 箇所がプロキシを渡さなくなる
3. `repositories/base.py` から `for_guild()` / `GuildBoundRepository` を撤去する
   — ただし 0008 の `GuildScope.bind()` の置き換え先を**先に**用意すること
4. 上記 3 の代替として、プロキシ相当の仕組みを dashboard 専用に閉じるか、
   別名で恒久 API として切り出す

## 覆す条件

解除の撤回は想定しない。R4 の「移行期間」がいつ終わるかは上記「完了条件」で定義する。

## 根拠

- `docs/DESIGN_NOCODB_MULTITENANT.md` 冒頭（対象4件の2番目）
- `docs/DESIGN_NOCODB_MULTITENANT.md` §2「既存『services/ 変更禁止』制約の扱い」
- `docs/DESIGN_NOCODB_MULTITENANT.md` §7 R3〜R5
- 解除を求めた理由について 2026-08-20 に判断者へ確認 — 「マルチテナントを完成させるため」
