# `/autonomous-dev-loop` で堅牢化タスク（H1）を回すための起動プロンプト

> **これは内部の作業手順です（[development/README.md](README.md)）。**
> Bot の使い方ではありません。使い方は [`../GUIDE.md`](../GUIDE.md) を参照してください。
>
> 文中の `<開発ノートのパス>` は、開発者のローカルにある設計判断・既知の落とし穴の
> メモ置き場を指します。**このリポジトリには含まれません。** 手元に無い場合は
> `--add-dir` の手順ごと読み飛ばしてください（公開すべき設計判断は
> [`../adr/`](../adr/) にあります）。`<リポジトリのパス>` は各自のクローン先です。

[`HARDENING_TASKS.md`](HARDENING_TASKS.md)（H1-1〜H1-5）を `/autonomous-dev-loop` で
1タスクずつ実装するための入力集。[`IMPROVEMENT_LOOP_PROMPT.md`](IMPROVEMENT_LOOP_PROMPT.md) の続編。

**このスキルは `/acm-bot-loop` とは役割が違う。**

| | 役割 |
|---|---|
| `/acm-bot-loop` | 実装 → `ruff` + `pytest` → 自己修正 を**緑になるまで**回す内側のループ |
| `/autonomous-dev-loop` | plan → 客観評価 → plan修正 → 実装 → コードレビュー → 修正 → 記録 の**外側**のループ |

外側が内側を呼ぶ。`/autonomous-dev-loop` の「実装」フェーズの中身が `/acm-bot-loop` である、
という入れ子だと思ってよい。§A のプロンプトはその前提で書いてある。

---

## 0. セッション開始前（人間が1回だけやる）

### 0-1. 開発ノートを読ませる

ADR と gotcha は `<開発ノートのパス>/projects/acm_bot/` にある。
Claude Code は既定で作業ディレクトリの外を読めないので、**セッション開始時に追加する**。

```bash
claude --model opus --add-dir <開発ノートのパス>/projects/acm_bot
```

すでにセッション中なら:

```
/add-dir <開発ノートのパス>\projects\acm_bot
```

読ませる順序（プロンプトに書いてあるので手で開く必要はない）:

| ファイル | 何のために |
|---|---|
| `decisions/_index.md` | ADR の索引と「通底する判断軸」 |
| `gotchas/_index.md` | ハマりどころ。**症状で引く** |
| `_index.md` | 現在地・未処理・直近セッション |

### 0-2. `CLAUDE.md` の参照先を H1 に向ける

`CLAUDE.md` の「実装タスク」節が `IMPROVEMENT_TASKS.md` を指したままだと、
エージェントが古い表の未完了タスク（G1-8 / G1-10）を拾う。

```markdown
## 実装タスク
- 進行中の実装タスクは club-bot/docs/development/HARDENING_TASKS.md の表に従う
  （H1-1〜H1-5。根拠は G4 完了後の全コード分析）
- 完了済み: IMPROVEMENT_TASKS.md（G0〜G4）/ FEATURE_TASKS.md / PUBLIC_RELEASE_TASKS.md /
  DASHBOARD_TASKS.md。いずれも作業用の内部資料で、現状の仕様の根拠にはしない
- 実装ループは /autonomous-dev-loop の手順で回す（内側の検証は /acm-bot-loop）
```

### 0-3. 依存を入れる

```
> cd club-bot
> venv\Scripts\python.exe -m pip install -r requirements.txt -r dashboard/requirements.txt ruff pytest
```

`dashboard/requirements.txt` を入れないと `test_dashboard_*.py` が丸ごと skip される
（gotcha `dashboard-tests-silently-skipped`）。**skip を「緑」と数えない。**
確認は `pytest tests/ -q -rs` で skip 理由を出す。

### 0-4. 環境の確認

- 作業ディレクトリは `club-bot/`（リポジトリルートではない）
- Windows / PowerShell。コマンドは PowerShell の書式で書かせること
  （`cp` は `Copy-Item` の別名で `/tmp` は `C:\tmp` になる、`printf` は無い、glob は展開されない）
- Python は `venv\Scripts\python.exe`（無ければ `python`）
- `.claude/settings.local.json` に `git commit` は入れてよいが **`git push` は入れない**
- Stop フック（`scripts/loop_gate.ps1`）が入っていれば、赤いうちはエージェントが終われない

---

## A. 標準の1イテレーション（これを毎回使う）

新しいタスクに入る前に **必ず `/clear`**。

```
/clear
```

そのうえで、次をそのまま貼る。

```
/autonomous-dev-loop

club-bot/docs/development/HARDENING_TASKS.md を読み、未完了（チェックが入っていない）で
最も若い番号のタスクを 1 つだけ、plan から記録まで通しで実装して。

このセッションは自走前提です。**各ゲートで私に確認を取らず、判定に従って自分で進めてください。**
環境は Windows / PowerShell。作業ディレクトリは club-bot/ です。

## 事前に読むもの（plan を書くより先に）

1. HARDENING_TASKS.md の「運用ルール」「全タスク共通の受入基準」「この表に固有の受入基準」
   と、対象タスクの受入基準・検証・注意
2. リポジトリルートの AGENTS.md（全文）
3. 開発ノートの decisions/_index.md と gotchas/_index.md
   （--add-dir 済み。パスは <開発ノートのパス>\projects\acm_bot）
4. 対象タスクの「注意」に ADR 番号や gotcha 名が書いてあれば、その本文も

## フェーズ1 — plan を書く

受入基準を1行ずつ書き出し、次を含む plan を作る。曖昧な点は
**最も素直な解釈で仮決めして明記**する（私に聞かない）。

- 触るファイルと、それぞれで何を変えるか
- 先に書く「失敗するテスト」の一覧と、それぞれが何を固定するか
- 既存の挙動が変わる箇所（H1-3 と H1-5 は必ずある）と、通らなくなる入力
- 触る ADR があれば番号と、覆すのか沿うのか
- やらないこと（隣接して見えるが今回の範囲外のもの）

## ゲート1 — plan の客観評価

plan ができたら、**実装に入る前に** @"acm-plan-reviewer (agent)" を呼んで審査させる。

判定ごとの動き:
- APPROVE      → 実装へ進む
- REVISE       → 指摘を直してもう一度 acm-plan-reviewer へ。**2回まで**。
                 3回目も REVISE なら止まって私に報告する
- REJECT       → 実装しない。理由と代替案をまとめて報告して止まる
- NEEDS-HUMAN  → 実装しない。止まって報告する

## フェーズ2 — 実装（内側は /acm-bot-loop の手順）

テスト先行 → 最小実装 → 検証 → 失敗を分類して自己修正、を全パスまで。
**一度に触るのは1論点まで。** 無関係なリファクタを混ぜない。

検証は次のどちらでもよい:
  venv\Scripts\python.exe -m ruff check .
  venv\Scripts\python.exe -m pytest tests/ -q -rs
  powershell -ExecutionPolicy Bypass -File scripts\loop_check.ps1

同じテストが3周直しても緑にならなければ止まって報告する。

## ゲート2 — 実装後（2つ同時に立てる）

全テストが緑になったら、次の2つを**同時に**立てる:

- @"acm-diff-auditor (agent)"  — AGENTS.md と ADR に照らして git diff を監査
- @"acm-test-adversary (agent)" — 実装を一時的に戻してテストが赤くなるかを実測

このタスク表に固有の観点として、diff-auditor に次を明示的に見させること:

- H1-1: 許可リストに入れた除外が妥当か。「低」危険度も同じ形に揃っているか。
        ギルドが解決できないときに**送らない**で終わっているか
- H1-2: DB へ書く前の権限検査と、送信失敗時の soft_delete が**両方**あるか。
        新しい repository メソッドを足していないか
- H1-3: 拒否がコマンド層だけで、SettingsRepository.set() の直接呼び出しを塞いでいないか。
        既存の不正値を移行していないか（ADR 0024）
- H1-4: 文面の分岐が純粋関数になっているか。ui_style 欠損で例外を投げていないか
- H1-5: 判定が純粋関数で、現在時刻を引数で受けているか。
        完全な日付を勝手に翌年へ送っていないか

判定ごとの動き:
- diff-auditor が FINDINGS   → 指摘を直して**両方**もう一度。**2回まで**
- test-adversary が INEFFECTIVE → そのテストを書き直して**両方**もう一度。**2回まで**
- どちらかが NEEDS-HUMAN     → 止まって報告
- CLEAN かつ EFFECTIVE       → 完了処理へ

## フェーズ3 — 完了処理

1. HARDENING_TASKS.md の該当タスクにチェックを入れ、末尾の完了ログへ
   「完了内容 / 設計判断 / ゲートの判定 / 次タスクへの申し送り」を追記する
   （テンプレートは完了ログのコメントにある）
2. 実装は fix/<タスクID小文字> ブランチに1コミット（例: fix/h1-1）。
   表とドキュメントの更新は docs: の別コミット
3. 実装で変えた挙動が README.md / docs/GUIDE.md / docs/OPERATION.md と矛盾していないか確認し、
   矛盾していれば同じイテレーションで両方直す
4. 開発ノートへ書く材料（ADR 草案 / gotcha 草案 / unfixed を外せる gotcha）を出力する。
   ノートのファイルは私が書くので、**出力するだけでよい**
5. 報告は「変更内容 / 検証 / 両ゲートの判定 / test-adversary の実測表 / 残課題」の形で

## 止まる条件（これ以外では止まらない）

- どちらかのゲートが NEEDS-HUMAN / REJECT
- 同じゲートで3回目の差し戻し
- 同じテストが3周直しても緑にならない
- 受入基準そのものが矛盾している
- 既存ギルドのデータを壊す変更が避けられない
- 秘密情報や本番 DB に触る必要が出た
- push / merge が必要になったとき

**push はしない。** コミットはタスク単位で行ってよい。
```

---

## B. タスク別の補足

§A のプロンプトで足りるが、次の2件は**先に伝えておくと1周減る**。

### H1-1（一括置換）に添える1行

```
H1-1 は機械的な置換に見えるが、置換だけでは終わらない。
「ギルドが解決できなかったとき何をするか」を箇所ごとに決めること
（送らない / ログだけ残す / 運用者向けに bot-log へ出す）。
既存コードでは ADR 0023 に従って「部員には沈黙し、運用者には見える形で残す」が
使われている箇所がある。その判断を消さないこと。
```

### H1-3（プランモードを挟む）

**H1-3 だけは §A の前にプランモードで方針を固める。** 設定キーの仕様表という
新しい構造を作るうえ、既存の挙動（未知キーが通る）を変えるため。

§A を貼る前に **Shift+Tab を2回**押してプランモードへ。

```
club-bot/docs/development/HARDENING_TASKS.md の H1-3 を実装する前に、設計だけ立てて。

- 設定キーの仕様表をどこに置くか（config.py / utils/settings_spec.py 新規 / 別案）。
  cogs/setup_wizard.py の CHANNEL_SETTINGS・ROLE_SETTINGS と config.py の
  MULTI_ROLE_KEYS が既に部分的な一覧を持っている。統合するのか、参照するだけか
- config.for_guild() が読むキーの全一覧（漏れがあると「設定できないキー」が生まれる）
- 検証の粒度（種別だけか、値域まで見るか）
- 未知キーを拒否したときに壊れうる既存の使い方
- /setup の Modal をどこに足すか（SetupWizardView のボタン行は既に4行使っている）

実装はまだしない。この方針で良いか確認してから進める。
```

承認後に「この方針で §A のとおり実装して」と伝える。

---

## C. 無人で連続実行（headless）

**git worktree の中でだけ**やること。

```powershell
powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1
```

既定では H1-1・H1-2・H1-4・H1-5 の4件を順に回し、**H1-3 は飛ばす**
（§B のとおりプランモードを挟むべきタスクなので、無人実行から外してある）。
全件回したい場合は `-IncludeH13` を付ける。

```powershell
# 2件だけ試す
powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1 -MaxIterations 2

# H1-3 も含めて全件
powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1 -IncludeH13
```

- `--permission-mode acceptEdits` は編集のみ自動承認。`--dangerously-skip-permissions` は使わない
- 実行後は必ず `git diff` を人間が読む。**無人実行の結果をレビューなしでマージしない**
- 1件でも `STOPPED:` を出したらそこで打ち切る（スクリプトがそうしている）

---

## D. セッションの最後に開発ノートへ記録する

**セッションの最後に必ず1回。** 記録しないと次のセッションが同じ調査からやり直す。

```
このセッションで確定した内容を開発ノートへ書く材料をまとめて。
ノートのファイルは私が書くので、次の形で出力するだけでよい。

1. 【ADR が必要か】
   H1-3 を実装したなら「設定キーはホワイトリストで、コマンド層で弾く」は
   新しい設計判断なので必ず草案を書く。他のタスクでも新しい判断をしたなら書く。
   既存 ADR のテンプレート（文脈 / 選択肢 / 決定 / 理由 / 却下した案とその理由 /
   影響範囲 / 覆す条件 / 根拠）に沿うこと。
   公開すべき判断なら club-bot/docs/adr/ 側の草案も出す（現行の最新は 0009）

2. 【gotcha が必要か】
   実装中に踏んだ罠のうち、次に同じ症状を見たら思い出したいもの。
   **原因ではなく「何が見えたか」を症状に書く**

3. 【unfixed を外せる gotcha】
   このセッションで解消した既知の gotcha 名

4. 【_index.md の「未処理」の更新差分】
```

---

## E. 再開 / 表のレビュー

**前回の続きから:**

```
/autonomous-dev-loop

git status と現在のブランチ、club-bot/docs/development/HARDENING_TASKS.md の完了ログを確認して、
前回どこまで進んだかを把握してから続きを1タスク実装して。
未コミットの作業が残っていれば、新しいタスクに入る前にそれを完成させる。
```

**表そのものを見直す:**

```
club-bot/docs/development/HARDENING_TASKS.md の受入基準を、実際のコード
（cogs/ services/ repositories/ utils/）と開発ノートの decisions/・gotchas/ と
突き合わせてレビューして。

- 既に実装済みで不要になった項目
- ADR と衝突していて、そのままでは実装できない項目
- 受入基準が実装不能・検証不能になっている項目

を指摘して、表の修正案を出す。実装はまだしない。
```

---

## 運用メモ

- **1タスク = 1セッション**。終わったら `/clear`。`/compact` に頼ると受入基準がぼやける
- `/context` で残りを確認し、20% を切ったら**そのタスクを完成させることだけ**に集中させる
- 5件とも**スキーマを変えない**。`migrations/` にファイルが増えたら受入基準の読み違いを疑う
- H1-3 と H1-5 は「今まで通っていた入力が通らなくなる」変更。
  完了ログに**何が通らなくなるか**を必ず列挙させる
- Windows の pytest は `venv\Scripts\python.exe -m pytest tests/ -q -rs`
- `.env` の実値は Claude Code に読ませない（`deny` 設定）
- 5件が終わったら、次は分析レポートの「段2」
  （全通知へのジャンプリンク / 監査ログの穴埋め / `/setup-status` に Todoist）
