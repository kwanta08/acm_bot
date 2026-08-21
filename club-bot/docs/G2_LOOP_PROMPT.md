# Claude Code で「パッチの検証 → G2 実装」を回すための入力

ターミナルで Claude Code を起動して、以下を順に貼るだけの形にしてあります。
`docs/IMPROVEMENT_LOOP_PROMPT.md` の続きで、**今回の状況（パッチ13枚が未適用）専用**です。

---

## 0. 起動（毎回これ）

```bash
cd /c/Users/yoshi/acm_bot
claude --model opus --add-dir /c/Users/yoshi/ClaudeVault/ClaudeVault/projects/acm_bot
```

`--add-dir` を忘れると ADR と gotcha を読めず、既に「やらない」と決めたことを実装します。

### 先に人間がやること（1回だけ・PowerShell）

> **改訂 (2026-08-20)**: 初版の「CRLF 汚染を戻す」手順は**誤りでした。**
> `git ls-files --eol` が `i/lf w/crlf` を返すのは Windows の git（`core.autocrlf=true`）の
> 正常な状態です。161 ファイルが変更ありに見えたのは Linux 側からマウント越しに見ていたためで、
> Windows 上では最初からきれいでした。`core.autocrlf false` を設定すると**逆に壊れます**。

```powershell
cd C:\Users\yoshi\acm_bot

git config --local --unset core.autocrlf          # 初版の手順を実行してしまった場合の取り消し
if (Test-Path .git\index.lock) { Remove-Item .git\index.lock }
git fetch origin
git status
```

**`git checkout -- .` は実行しないこと。** ステージ済みの変更が `origin/main` に
入っていない実作業のことがあります。`M ` で出るものは先に別コミットとして確定させてください。

パッチの展開:

```powershell
Expand-Archive -Path _patches\acm_bot_patches.zip -DestinationPath _patches -Force
```

手元のブランチは `origin/main` より古いので、作業は必ず `origin/main` を基点にします。

---

## 1. パッチの検証（最初に1セッション使う）

`_patches/acm_bot_patches.zip` の13枚は**別のエージェントが書いたもの**です。
そのまま信用せず、Claude Code に検証させます。

```
/clear
```

```
/acm-bot-loop

_patches/ にあるパッチ13枚（別のエージェントが origin/main の eaade27 を基点に作ったもの）を
レビューして、問題が無ければ適用して検証して。**まだ G2 の実装には入らない。**

## 前提

- 手元のブランチは origin/main より古い。git fetch origin 済みの origin/main を基点にする
- 内容の説明は _patches/APPLY.md にある。ただし**説明を信用せず、実際の diff で確認**すること
- ADR と gotcha は ClaudeVault（/add-dir 済み）にある。
  decisions/_index.md と gotchas/_index.md を先に読む

## 手順

1. git checkout -b fix/verify-patches origin/main
2. パッチを適用する前に、13枚の diff を読んで次を確認する（適用しないと分からないものは
   適用後に確認してよいが、**先に読む**こと）
3. パッチを当てる（PowerShell では glob が展開されないので明示的に渡す）:
   $patches = Get-ChildItem _patches\patches\*.patch | Sort-Object Name | Select-Object -ExpandProperty FullName
   git am $patches
4. cd club-bot && python -m ruff check . && python -m pytest tests/ -q -rs
   期待値: ruff は All checks passed、pytest は 663 passed / 4 skipped
   （skip は CLUB_TEST_PG_DSN 未設定の PostgreSQL テスト4件だけ。それ以外が skip されたら
     dashboard/requirements.txt が入っていない → 入れてから回し直す）
5. 下の「重点的に見てほしい点」を1つずつ検証し、結果を報告する

## 重点的に見てほしい点（作成者が判断に迷った箇所）

### A. 0003 のコンフリクト解消（最優先）

`fix/code-audit-v2` の `8b9c0f4` は `dashboard/routers/tables.py` に `_csv_safe` と
インライン CSV writer を入れていたが、main には既に
`repositories/table_repository.py` の `csv_safe` + `rows_to_csv` がある。
作成者は「main 側が上位互換」と判断してブランチ側を捨て、
**`tests/test_table_value_coercion.py` の CSV テストを main の契約に書き換えた**
（`csv_safe(None) == ""` / `csv_safe(42) == "42"`）。

- main の `csv_safe` は本当にブランチ側と同等以上か。数式記号のエスケープ範囲、
  BOM、None の扱いを両方読んで比べること
- **テストを実装に合わせて書き換えるのは、本来やってはいけない類の操作**。
  この変更が「実装の仕様変更を追認しただけ」なのか「テストの意図を殺した」のかを判定する

### B. 0001 の import 解決

`cogs/progress.py` の import で、main が使う `parse_deadline` とブランチが使う
`require_manage_guild_or` の両方を残している。両方とも実際に使われているか確認する。

### C. 0007 が ADR 0023 に反していないか

`resolve_default_channel_id` に `PROGRESS_DEFAULT_CHANNEL_ID` →
`DEFAULT_PROGRESS_CHANNEL_ID` → `DEFAULT_TASK_CHANNEL_ID` の3段フォールバックを入れ、
送信先が無いときに `bot.log_to_channel` へ1行出すようにしている。

ClaudeVault の 0023-silent-when-nothing-is-wrong.md を読み、
**「遅延が無い週は沈黙する」という決定を壊していないか**を確認する。
（作成者の主張: ADR の「影響範囲」に書かれた3段フォールバックが実装されていなかっただけで、
 沈黙の条件は変えていない。#bot-log は運用者向けなので部員の注意力を消費しない）

### D. 0010 が既存の運用を壊さないか

`teams` のロール ID 3列と `members.is_leader` を editable=False にしている。
ダッシュボードでこれらを編集して運用しているサークルがあれば、
その手段が Discord 側（`/team-role` は管理者限定、`/member set-leader` は L3）に
残っているかを確認する。**代替手段が無い操作を塞いでいないか。**

### E. 0011 の権限判定

`utils/permissions.is_self_or_level()` と `cogs/tasks._may_modify()` を読み、
- 担当者が未設定（assignee_id が NULL）のタスクを作成者以外が触れないか
- 班長が他人のタスクを触れることは維持されているか
- `interaction.user` が discord.Member でない場合（DM 等）に例外にならないか

### F. テストが本当に落ちることを確認する

13枚が追加した各テストについて、**対応する実装だけを元に戻すと落ちること**を
少なくとも 0006 / 0007 / 0010 / 0011 / 0013 で確かめる
（gotcha `test-asserts-permission-but-decorator-missing` と同じ形の嘘を防ぐため）。

### G. ドキュメントとの整合

0001 / 0008 / 0009 / 0010 / 0011 が docs/OPERATION.md と README.md を触っている。
実装と記述が一致しているか、`pytest tests/test_docs_commands.py -q` が緑かを確認する。

## 報告

- 問題が無ければ「13枚とも問題なし」と明記し、ruff / pytest の結果を貼る
- 問題があれば、**パッチ番号・ファイル:行番号・何が間違っているか・どう直すべきか**を書く。
  勝手に直さず、まず報告する（作成者の判断を覆すかどうかは私が決める）
- A で「テストの意図を殺した」と判断した場合は、元の意図を満たすテストの書き方を提案する

コミットはしない。ブランチを切って git am するところまで。
```

### 検証が通ったら

```
指摘が無かったので、このブランチ（fix/verify-patches）を fix/improvement-g0-g1 に
リネームして、docs/IMPROVEMENT_TASKS.md の G0-2 / G0-4 / G1-1〜G1-7 にチェックを入れ、
完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記して。

申し送りには必ず次を含めること:
- G1-8（deploy.yml）は origin/main の 4dc33c9 で解決済みだったため、レポートの P0-7 は取り下げ
- ClaudeVault の gotcha 3件（progress-subtree-disappears /
  progress-stops-after-dashboard-edit / test-asserts-permission-but-decorator-missing）は
  unfixed タグを外せる
- fix/code-audit-v2 の残り3コミット（13f5451 / d9996e7 / a3b97e4）は main に反映済みのため
  取り込んでいない
```

---

## 2. G2 の実装（1タスク＝1セッション）

検証が終わってから。**新しいタスクに入る前に必ず `/clear`。**

```
/clear
```

```
/acm-bot-loop

club-bot/docs/IMPROVEMENT_TASKS.md の Phase G2 のうち、未完了で最も若い番号のタスクを
1つだけ実装して。

## 事前に読むもの（実装より先に）

1. IMPROVEMENT_TASKS.md の「運用ルール」「全タスク共通の受入基準」
   「この表に固有の受入基準」と、対象タスクの受入基準・検証・注意
2. ClaudeVault の decisions/_index.md と gotchas/_index.md
   （/add-dir 済み。C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot）
3. 対象タスクの「注意」に ADR 番号や gotcha 名があれば、その本文も読む

## 手順

1. 受入基準を1行ずつ書き出す。曖昧なら最も素直な解釈で仮決めして明記する
2. TodoWrite にサブステップを登録してから着手する
3. fix/<タスクID小文字> ブランチを切る（例: git checkout -b fix/g2-1）
4. 受入基準を満たす**失敗するテストを先に書く**
5. 最小の実装 → cd club-bot && python -m ruff check . && python -m pytest tests/ -q -rs
   → 失敗を分類して自己修正、を全パスまで繰り返す
6. **実装を元に戻すと新しいテストが落ちること**を1回確認する
7. 全パス後、IMPROVEMENT_TASKS.md のチェックと完了ログを更新する
8. SKILL.md §2 の形式（変更内容 / 検証 / 残課題）で報告する

## 制約

- **ADR に反する実装をしない。** 衝突したら実装せず
  「ADR NNNN と衝突。◯◯という理由で覆すべきだと考える」と書いて止まる
- 途中で私に確認を取らない。SKILL.md §4 の停止条件に当たったときだけ止まる
- コミットはしない。1タスクの範囲外のファイルを触らない
- skip を「緑」と数えない。pytest は必ず -rs 付きで回す

## G2 のタスク（表の該当箇所を必ず読むこと。ここは目次）

- G2-1 破壊的操作に共通の確認ステップ（ConfirmView）。
       /progress remove は配下ごと消えるのに確認が無い。/schedule delete の論理削除化は含めない
- G2-2 schedule_id / task_id のオートコンプリート
- G2-3 通知の抜け3件（/schedule remind の嘘成功 / 作成時のロールメンション / 担当者への DM）
- G2-4 TimeoutAwareView（on_timeout がメッセージを編集していない6箇所）
- G2-5 空状態に「次の1コマンド」を添える
- G2-6 /progress edit の進捗率検証。
       **注意: G0-2 で取り込んだ 8b9c0f4 がダッシュボード側に同じ検証を入れている。
       解釈規則をそちらと揃えること**（0.5 / 50% / 50）
- G2-7 Todoist の同期失敗を利用者に見せる
```

### 破壊的タスクはプランモードを先に挟む

**G2-1 は必ず**（削除の挙動を変えるため）。Shift+Tab を2回押してから:

```
club-bot/docs/IMPROVEMENT_TASKS.md の G2-1 を実装する前に、設計だけ立てて。

- ConfirmView をどこに置くか（utils/views.py 新設か、season.py から切り出すか）
- /progress remove のプレビューに何を出すか（配下の件数だけか、名前も出すか）
- 既存の RolloverView をどう置き換えるか。壊さずに移行できるか
- テストで固定する不変条件（他人が押せない / 確定前は削除が走らない）

実装はまだしない。この方針で良いか確認してから進める。
```

### 実装後は別コンテキストでレビュー

```
Task ツールで general-purpose エージェントを1つ立て、次を調べさせて:

「club-bot の未コミットの変更（git diff）をレビューせよ。

 (1) AGENTS.md の絶対ルール違反:
     guild_id スコープ漏れ / スキーマ変更に対するマイグレーション漏れ /
     discord.HTTPException の捕捉漏れ / サークル固有値のハードコード /
     README.md・docs/ と実装の矛盾

 (2) 設計判断との衝突:
     C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot\decisions\_index.md を読み、
     この変更が既存の ADR に反していないか。特に 0008 / 0016 / 0021 / 0022 / 0023 / 0024

 (3) テストが嘘をついていないか:
     skip が増えていないか（-rs）。テストが実装ではなくヘルパを検査していないか
     （gotcha: test-asserts-permission-but-decorator-missing と同じ形）

 該当箇所をファイル:行番号で列挙し、無ければ『違反なし』と答えること。実装は変更しない。」
```

---

## 3. セッションの最後に必ず1回

```
このセッションで確定した内容を ClaudeVault へ書く材料をまとめて。
Vault のファイルは私が書くので、次の形で出力するだけでよい。

1. 【ADR が必要か】新しい設計判断をしたか。したなら
   decisions/0023-silent-when-nothing-is-wrong.md の構成
   （文脈 / 選択肢 / 決定 / 理由 / 却下した案とその理由 / 影響範囲 / 覆す条件 / 根拠）
   に沿って草案を書く。番号は 0035 から
2. 【gotcha が必要か】踏んだ罠のうち、次に同じ症状を見たら思い出したいもの。
   **原因ではなく「何が見えたか」を症状に書く**
3. 【unfixed を外せる gotcha】このタスクで解消した既知の gotcha 名
4. 【_index.md の「未処理」の更新差分】
```

---

## 覚えておくこと

- **1タスク = 1セッション。終わったら `/clear`。** `/compact` に頼ると受入基準がぼやける
- `git status` が 100 ファイル以上を変更ありと言ったら `core.autocrlf` が false になっている。
  `git config --local --unset core.autocrlf` で戻す（CRLF 自体は Windows の正常な状態）
- Windows の pytest は `venv/Scripts/python.exe -m pytest tests/ -q -rs`
- `.env` の実値は読ませない
- **G0-3【人間タスク】が未着手**: PostgreSQL でダッシュボード編集テストを1回回す。
  `CLUB_TEST_PG_DSN=... pytest tests/test_dashboard_edit.py -q` で5分。
  落ちたら G2 より優先する
