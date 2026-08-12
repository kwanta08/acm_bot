# Claude Code（Opus 5）で回すための起動プロンプト

`docs/FEATURE_TASKS.md` を Claude Code で 1 タスクずつ実装するための入力集。
Cowork（チャット）ではなく **ターミナルの Claude Code で実装する**前提で書いてある。

---

## 0. 最初の1回だけやる準備

### 0-1. `CLAUDE.md` を置く（重要）

Claude Code が自動で読むのは `CLAUDE.md`。このリポジトリの規約は `AGENTS.md` にあるので、
**リポジトリルート**に次の内容で `CLAUDE.md` を作り、import で読ませる。

```markdown
# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク
- 進行中の機能実装は club-bot/docs/FEATURE_TASKS.md の表に従う
- 実装ループは /acm-bot-loop スキルの手順で回す（全テストパスまで自走）

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は club-bot/venv/Scripts/python.exe（無ければ python）
```

### 0-2. 起動と依存

```bash
cd /c/Users/yoshi/acm_bot          # Git Bash。PowerShell なら cd C:\Users\yoshi\acm_bot
claude --model opus

# セッション内で（初回のみ）
> cd club-bot && python -m pip install -r requirements.txt -r dashboard/requirements.txt ruff pytest
```

`dashboard/requirements.txt` を入れないと `test_dashboard_*.py` が丸ごと skip される。

### 0-3. 許可設定（毎回 y を押さないため）

`.claude/settings.local.json` に、ループで頻発するコマンドを許可しておく。
**`git commit` と `git push` は入れない**（AGENTS.md の「指示があるまでコミットしない」を守るため）。

```json
{
  "permissions": {
    "allow": [
      "Bash(cd club-bot && *)",
      "Bash(python -m pytest:*)",
      "Bash(python -m ruff:*)",
      "Bash(venv/Scripts/python.exe -m pytest:*)",
      "Bash(venv/Scripts/python.exe -m ruff:*)",
      "Bash(bash scripts/loop_check.sh:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git checkout -b:*)",
      "Edit",
      "Write"
    ],
    "deny": ["Bash(git push:*)", "Read(./club-bot/.env)"]
  }
}
```

---

## A. 標準の1イテレーション（これを毎回使う）

新しいタスクに入る前に **必ず `/clear`**。前タスクの文脈を持ち越すと、
無関係なリファクタが混ざってループが収束しなくなる。

```
/clear
```

```
/acm-bot-loop

club-bot/docs/FEATURE_TASKS.md を読み、未完了（チェックが入っていない）で
最も若い番号のタスクを 1 つだけ実装して。

手順:
1. 表の「運用ルール」と「全タスク共通の受入基準」、対象タスクの受入基準・検証・注意を読む
2. TodoWrite にそのタスクのサブステップを登録してから着手する
3. feat/<タスクID小文字> ブランチを切る（例: git checkout -b feat/f1-1）
4. 受入基準を満たす**失敗するテストを先に書く**（tests/ に新規ファイル）
5. 最小の実装 → cd club-bot && python -m ruff check . && python -m pytest tests/ -q
   → 失敗を分類して自己修正、を全パスまで繰り返す
6. 全パス後、docs/FEATURE_TASKS.md の該当タスクにチェックを入れ、
   完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を1行追記する
7. SKILL.md §2 の形式（変更内容 / 検証 / 残課題）で報告する

制約:
- 途中で私に確認を取らない。SKILL.md §4 の停止条件に当たったときだけ止まる
- コミットはしない。ブランチを切るところまで
- 1タスクの範囲外のファイルを触らない
```

### 実装前に設計を確認したいとき

上のプロンプトを貼る前に **Shift+Tab を2回押してプラン モード**に入り、
最後に「この方針で実装して」と承認する。F2-4 / F5-2 のような破壊的タスクでは必ずこれを挟む。

---

## B. フェーズをまとめて回す

```
/acm-bot-loop

club-bot/docs/FEATURE_TASKS.md の Phase F3（重量管理）を F3-1 から F3-4 まで通しで実装して。

- タスクごとに ruff + pytest を回し、緑になってから次へ進む（まとめて実装して一度に検証しない）
- 各タスク完了時に表のチェックと完了ログを更新する
- ブランチは feat/f3-weight を1本切り、タスクの区切りで作業ツリーを綺麗に保つ
- コミットはしない
- 途中で確認を取らず、停止条件に当たったときだけ止まる

context が 30% を切ったら /compact せず、その時点までの結果を報告して止まって。
（私が /clear して続きを指示する）
```

---

## C. 無人で連続実行（headless）

**git worktree の中でだけ**やること。main の作業ツリーで走らせない。

```bash
# Git Bash
cd /c/Users/yoshi/acm_bot
git worktree add ../acm_bot_loop -b feat/feature-tasks
cd ../acm_bot_loop

for i in $(seq 1 5); do
  echo "=== iteration $i ==="
  claude -p --model opus --permission-mode acceptEdits \
    "/acm-bot-loop club-bot/docs/FEATURE_TASKS.md の未完了で最も若いタスクを1つだけ実装し、
     ruff と pytest が全パスするまで自走して。完了したら表のチェックと完了ログを更新する。
     コミットはしない。停止条件に当たったら STOPPED: と理由を出力して終了する。" \
    || break
  grep -q "STOPPED:" /dev/null && break
done
```

PowerShell なら:

```powershell
1..5 | ForEach-Object {
  claude -p --model opus --permission-mode acceptEdits `
    "/acm-bot-loop club-bot/docs/FEATURE_TASKS.md の未完了で最も若いタスクを1つだけ実装し、ruff と pytest が全パスするまで自走して。完了したら表のチェックと完了ログを更新する。コミットはしない。"
}
```

- `--permission-mode acceptEdits` は編集のみ自動承認。`--dangerously-skip-permissions` は
  worktree かつ捨てて良い環境以外では使わない
- 1回の `claude -p` が 1 タスク。5回で F0-1〜F2-1 あたりまで進む想定
- 実行後は必ず `git diff` を人間が読む。無人実行の結果をレビューなしでマージしない

---

## D. 検証をサブエージェントに投げる（Opus 5 を活かす使い方）

実装セッションと同じ文脈で自己レビューさせても粗が残る。**別コンテキスト**で見せる。

```
Task ツールで general-purpose エージェントを1つ立て、次を調べさせて:

「直近のコミットされていない変更（git diff）をレビューし、AGENTS.md の絶対ルール違反を探せ。
 特に (1) guild_id スコープ漏れ、(2) スキーマ変更に対するマイグレーション漏れ、
 (3) discord.HTTPException の捕捉漏れ、(4) サークル固有値のハードコード、
 (5) README.md / docs/ と実装の矛盾。
 該当箇所をファイル:行番号で列挙し、無ければ『違反なし』と答えること。実装は変更しない。」

報告を受けてから、指摘があれば私に見せて。
```

F2-4（全テーブル削除）と F5-2（メンバー status 移行）の後は必ずこれを回す。

---

## E. 再開 / 表のレビュー

**前回の続きから:**

```
/acm-bot-loop

git status と現在のブランチ、club-bot/docs/FEATURE_TASKS.md の完了ログを確認して、
前回どこまで進んだかを把握してから続きを1タスク実装して。
未コミットの作業が残っていれば、新しいタスクに入る前にそれを完成させる。
```

**実装前に表そのものを見直す:**

```
club-bot/docs/FEATURE_TASKS.md の Phase F<N> の受入基準を、実際のコード
（cogs/ repositories/ utils/db.py services/）と突き合わせてレビューして。
実現不可能・二重定義・既存機能と重複している項目を指摘し、表の修正案を出す。
実装はまだしない。
```

---

## F. 任意: hooks で検証を自動化する

`.claude/settings.json` に置くと、Python を編集するたびに ruff が走る（自分で叩く手間が減る）。

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "cd club-bot && (venv/Scripts/python.exe -m ruff check . || python -m ruff check .)"
          }
        ]
      }
    ]
  }
}
```

pytest まで hook で走らせると 1 編集ごとに数十秒かかるので、テストは STEP 3 で明示的に叩く方が速い。

---

## 運用メモ

- **1タスク = 1セッション**。終わったら `/clear`。`/compact` に頼ると受入基準がぼやける
- `/context` で残りを確認し、実装の途中で 20% を切ったら**そのタスクを完成させることだけ**に集中させる
- Windows の pytest は `venv\Scripts\python.exe -m pytest tests/ -q`。
  Git Bash からは `venv/Scripts/python.exe`
- 検証は `bash scripts/loop_check.sh`（F0-1 完了後はリポジトリ内。それまではスキル同梱のもの）
- `.env` の実値は Claude Code に読ませない（0-3 の deny 設定）
- 表のスキーマ版（v11〜v14）は予約済み。フェーズ順を入れ替えるときは番号も振り直す
