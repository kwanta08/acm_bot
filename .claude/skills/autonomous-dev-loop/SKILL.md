---
name: autonomous-dev-loop
description: acm_bot / club-bot（discord.py 製サークル運営bot）で、plan作成 → サブエージェントによる客観評価 → plan修正 → 実装（内側は /acm-bot-loop）→ diff監査とテスト有効性の実測 → 修正 → ADR・gotcha の記録 までを、人の確認を挟まず1タスク通しで回すための**外側**のループ手順。「自走で実装して」「ループで回して」「planから実装まで全部やって」「HARDENING_TASKS.md のタスクを1つ回して」など、1往復で終わらない実装タスクで使う。plan も評価も要らず、実装→検証→自己修正だけ回したいときは /acm-bot-loop を使う。
---

# acm_bot 自走ループ（外側）

**このスキルは `/acm-bot-loop` とは役割が違う。**

| | 役割 |
|---|---|
| `/acm-bot-loop` | 実装 → `ruff` + `pytest` → 自己修正 を**緑になるまで**回す内側のループ |
| `/autonomous-dev-loop` | plan → 客観評価 → plan修正 → 実装 → コードレビュー → 修正 → 記録 の**外側**のループ |

外側が内側を呼ぶ。このスキルの「フェーズ2 — 実装」の中身が `/acm-bot-loop` である、
という入れ子だと思ってよい。

起動プロンプトの雛形は `club-bot/docs/development/HARDENING_LOOP_PROMPT.md`。
対象の表は同ディレクトリの `HARDENING_TASKS.md`。

## 0. 前提（毎回最初に確認）

- **1タスク = 1セッション**。複数タスクをまとめて回さない
- 作業ディレクトリは `club-bot/`（リポジトリルートではない）。ruff / pytest はそこで実行する
- Python は仮想環境のもの（Windows: `venv\Scripts\python.exe` / macOS・Linux: `venv/bin/python`）。
  無ければ `python`
- ルート直下 `AGENTS.md` は全文読んだ前提で動く。矛盾したら AGENTS.md が優先
- **自走前提のセッションでは、各ゲートで人に確認を取らない。**
  判定に従って自分で進める。止まるのは §6 の条件のときだけ
- 止まるときは必ず `STOPPED:` で始まる行を出力してから終了する
  （無人実行のランナーがこの行を見て打ち切る）

### 使うサブエージェント

| エージェント | いつ | 何を見る |
|---|---|---|
| `acm-plan-reviewer` | 実装前 | plan を AGENTS.md と ADR に照らして審査 |
| `acm-diff-auditor` | 全テスト緑の直後 | 未コミットの diff を AGENTS.md と ADR に照らして監査 |
| `acm-test-adversary` | 全テスト緑の直後 | 実装を一時的に戻してテストが赤くなるかを実測 |

これらの定義は**このリポジトリには含まれない**（開発者のローカル設定にある）。
呼べない環境では、同じ観点を自分でチェックしたうえで
「ゲートはエージェント無しで実施した」と報告に明記する。判定を省略してはいけない。

## 1. 事前に読むもの（plan を書くより先に）

1. 対象タスクの表（`HARDENING_TASKS.md` 等）の「運用ルール」「全タスク共通の受入基準」
   「この表に固有の受入基準」と、対象タスクの受入基準・検証・注意
2. リポジトリルートの `AGENTS.md`（全文）
3. `club-bot/docs/adr/` のうち、対象タスクが触る判断
4. 開発ノート（`--add-dir` 済みなら `decisions/_index.md` と `gotchas/_index.md`）。
   **このリポジトリには含まれない**ので、無い環境ではこの項を飛ばす
5. 対象タスクの「注意」に ADR 番号や gotcha 名が書いてあれば、その本文も

## 2. フェーズ1 — plan を書く

受入基準を1行ずつ書き出し、次を含む plan を作る。曖昧な点は
**最も素直な解釈で仮決めして明記**する（人に聞かない）。

- 触るファイルと、それぞれで何を変えるか
- 先に書く「失敗するテスト」の一覧と、それぞれが何を固定するか
- 既存の挙動が変わる箇所と、**通らなくなる入力**
- 触る ADR があれば番号と、沿うのか覆すのか
  （覆す必要があると判断したら実装せず報告する。CLAUDE.md の原則）
- やらないこと（隣接して見えるが今回の範囲外のもの）

## 3. ゲート1 — plan の客観評価

plan ができたら、**実装に入る前に** `acm-plan-reviewer` を呼んで審査させる。

| 判定 | 動き |
|---|---|
| APPROVE | 実装へ進む |
| REVISE | 指摘を直してもう一度 `acm-plan-reviewer` へ。**2回まで**。3回目も REVISE なら `STOPPED:` を出して止まる |
| REJECT | 実装しない。理由と代替案をまとめて報告して止まる |
| NEEDS-HUMAN | 実装しない。止まって報告する |

## 4. フェーズ2 — 実装（内側は `/acm-bot-loop` の手順）

テスト先行 → 最小実装 → 検証 → 失敗を分類して自己修正、を**全パスまで**。
**一度に触るのは1論点まで。** 無関係なリファクタを混ぜない。

検証は次のどちらでもよい（`club-bot/` で実行）:

```
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m pytest tests/ -q -rs
```

```
powershell -ExecutionPolicy Bypass -File scripts\loop_check.ps1
```

- **skip を「緑」と数えない。** `pytest` は `-rs` 付きで回し、skip 理由を報告に書く。
  `test_dashboard_*` が丸ごと skip されるのは `dashboard/requirements.txt` 未インストール
- フロントの純粋関数を触ったら `node --test "tests_js/*.test.mjs"` も回す
- 同じテストが**3周直しても**緑にならなければ `STOPPED:` を出して止まる

## 5. ゲート2 — 実装後（2つ**同時に**立てる）

全テストが緑になったら、`acm-diff-auditor` と `acm-test-adversary` を同時に起動する。
逐次にしない（待ち時間が倍になるだけ）。

diff-auditor には、対象タスク表に固有の観点があればそれを明示的に渡す
（例: `HARDENING_TASKS.md` の各タスクの観点は `HARDENING_LOOP_PROMPT.md` §A にある）。

| 判定 | 動き |
|---|---|
| diff-auditor が FINDINGS | 指摘を直して**両方**もう一度。**2回まで** |
| test-adversary が INEFFECTIVE | そのテストを書き直して**両方**もう一度。**2回まで** |
| どちらかが NEEDS-HUMAN | `STOPPED:` を出して止まる |
| CLEAN かつ EFFECTIVE | 完了処理へ |

## 6. フェーズ3 — 完了処理

1. タスク表の該当タスクにチェックを入れ、末尾の完了ログへ
   「完了内容 / 設計判断 / ゲートの判定 / 次タスクへの申し送り」を追記する
2. 実装で変えた挙動が `README.md` / `club-bot/docs/GUIDE.md` / `club-bot/docs/OPERATION.md` と
   矛盾していないか確認し、矛盾していれば**同じイテレーションで**両方直す
3. 新しい設計判断をしたなら `club-bot/docs/adr/` の ADR 草案を出す
4. 開発ノートへ書く材料を出力する（ノートのファイルは人が書くので**出力するだけでよい**）
   - ADR 草案（文脈 / 選択肢 / 決定 / 理由 / 却下した案とその理由 / 影響範囲 / 覆す条件 / 根拠）
   - gotcha 草案。**原因ではなく「何が見えたか」を症状に書く**
   - `unfixed` を外せる gotcha 名
5. コミットは**起動プロンプトで明示的に許可されたときだけ**行う
   （無人実行の既定は「実装は `fix/<タスクID小文字>` に1コミット、表とドキュメントは `docs:` の別コミット」）。
   **`push` と `merge` は絶対にしない。** `main` に直接コミットしない

### 報告の形

```
## 変更内容
- <ファイル>: <何を、なぜ>

## 検証
- ruff check . : パス
- pytest tests/ -q -rs : NN passed, MM skipped（skip理由: ...）

## ゲートの判定
- acm-plan-reviewer  : APPROVE（N周目）
- acm-diff-auditor   : CLEAN（N周目）
- acm-test-adversary : EFFECTIVE

## test-adversary の実測表
| 戻した実装 | 赤くなったテスト |
|---|---|

## 開発ノートへ書く材料
- ADR 草案 / gotcha 草案 / unfixed を外せる gotcha

## 残課題
- <仮決めした解釈、次にやるべきこと>
```

## 7. 止まる条件（これ以外では止まらない）

いずれも `STOPPED:` で始まる行を出力してから終了する。

- どちらかのゲートが NEEDS-HUMAN / REJECT
- 同じゲートで3回目の差し戻し
- 同じテストが3周直しても緑にならない
- 受入基準そのものが矛盾している
- ADR を覆す必要があると判断した
- 既存ギルドのデータを壊す変更が避けられない
- 秘密情報や本番 DB に触る必要が出た
- `push` / `merge` が必要になった

## 8. 無人実行（headless）のときの追加ルール

`club-bot/scripts/autorun_hardening.ps1` から回されている場合:

- **git worktree の中でだけ**動く前提。`main` / `master` では走らない
- `--permission-mode acceptEdits` は編集のみ自動承認。`--dangerously-skip-permissions` は使わない
- タスク ID が指定されているときは、**その範囲外のファイルを触らない**
- 結果は人が `git diff` を読む前提。レビューなしでマージされない形（未 push）で残す
