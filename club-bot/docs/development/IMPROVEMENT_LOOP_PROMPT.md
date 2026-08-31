# Claude Code（Opus 5）で改善タスクを回すための起動プロンプト

> **これは内部の作業手順です（[development/README.md](README.md)）。**
> Bot の使い方ではありません。使い方は [`../GUIDE.md`](../GUIDE.md) を参照してください。
>
> 文中の `<開発ノートのパス>` は、開発者のローカルにある設計判断・既知の落とし穴の
> メモ置き場を指します。**このリポジトリには含まれません。** 手元に無い場合は
> `--add-dir` の手順ごと読み飛ばしてください（公開すべき設計判断は
> [`../adr/`](../adr/) にあります）。`<リポジトリのパス>` は各自のクローン先です。

`docs/development/IMPROVEMENT_TASKS.md`（G0〜G4）を Claude Code で 1 タスクずつ実装するための入力集。
`docs/development/FEATURE_LOOP_PROMPT.md` の続編。Cowork（チャット）ではなく
**ターミナルの Claude Code で実装する**前提。

**この表は 開発ノート（リポジトリ外のローカルなメモ置き場）の ADR / gotcha と密に結び付いている。**
`/add-dir` で Vault を読ませる手順を §0-3 に書いた。これを飛ばすと、
既に「やらない」と決めたことを実装したり、未マージの修正を二重に書いたりする。

---

## 0. 実装を始める前に（順番厳守）

### 0-1. 【最優先・人間がやる】作業ツリーの CRLF 汚染を戻す

現状 `git status` は 161 ファイルを変更ありと報告するが、実質差分は `CLAUDE.md` の7行だけ。

```bash
cd ~/acm_bot
git diff --stat | tail -1        # 155 files changed, 34877 insertions(+), 34870 deletions(-)
git diff -w --stat | tail -1     # CLAUDE.md | 7 +++++++   ← 実質はこれだけ
```

全ファイルが CRLF に書き換わっているだけ。**この状態で実装を始めると diff が読めず、
レビューもコミットもできない。**

```bash
# 1. 実差分だけ退避
cp CLAUDE.md /tmp/CLAUDE.md.bak

# 2. 改行の扱いを固定
git config core.autocrlf false
printf '* text=auto eol=lf\n' >> .gitattributes

# 3. 戻す（★取り返しがつかない。2 の退避を必ず先に）
git checkout -- .

# 4. 実差分を戻して確認
cp /tmp/CLAUDE.md.bak CLAUDE.md
git status --porcelain          # CLAUDE.md と .gitattributes 以外が出なければ OK
```

**エージェントにやらせない。** `git checkout -- .` は未コミットの変更を消す。

### 0-2. 【人間がやる】未マージの監査修正を取り込む

`fix/code-audit-v2` の6コミットが main にも現行ブランチにも入っていない。
現行コードで未適用であることを確認済み（`grep -rn "descendant_ids" club-bot/services/` が0件、
`club-bot/cogs/progress.py:1106` が `@require(Level.L2)` のまま）。

```bash
git log --oneline main..fix/code-audit-v2
# 8b9c0f4 fix: ダッシュボードの値検証と CSV 出力の安全性を強化した
# 1b741d1 fix: /progress edit で自分の配下を親に指定できないようにした
# 2d044ce fix: /progress setup をサーバー管理権限でも実行できるようにした
# a3b97e4 fix: 定期通知ループの失敗を1サーバーに閉じ込めた
# d9996e7 fix: 絵文字IDの環境変数を _clean 経由で読むようにした
# 13f5451 revert: PR #13 に混入したコード監査の修正2件を取り消す
```

この3件は 開発ノート で「**いま踏むと未修正**」として記録されているもの。
マージ作業自体は Claude Code に投げてよい（§B のプロンプト）。

### 0-3. 開発ノート（リポジトリ外のローカルなメモ置き場）を読ませる

ADR と gotcha は `<開発ノートのパス>\projects\acm_bot\` にある。
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
| `decisions/_index.md` | 33件の ADR の索引と「通底する判断軸」5つ |
| `gotchas/_index.md` | 23件のハマりどころ。**症状で引く** |
| `_index.md` | 現在地・未処理・直近セッション |

### 0-4. `CLAUDE.md` を更新する

Claude Code が自動で読むのは `CLAUDE.md`。実装タスクの参照先を差し替える。

```markdown
# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク
- 進行中の改善実装は club-bot/docs/development/IMPROVEMENT_TASKS.md の表に従う
  （根拠は club-bot/docs/development/IMPROVEMENT_REPORT.md）
- 完了済み: club-bot/docs/development/FEATURE_TASKS.md（F0〜F6）/ docs/development/PUBLIC_RELEASE_TASKS.md
- 実装ループは /acm-bot-loop スキルの手順で回す（全テストパスまで自走）

## 設計判断の正
- ADR と既知のハマりどころは ローカルの開発ノート にある
  （/add-dir <開発ノートのパス>\projects\acm_bot）
- ADR に反する実装をしない。覆す必要があると判断したら実装せず報告する

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は club-bot/venv/Scripts/python.exe（無ければ python）
```

### 0-5. 依存と許可設定

```
> cd club-bot && python -m pip install -r requirements.txt -r dashboard/requirements.txt ruff pytest
```

`dashboard/requirements.txt` を入れないと `test_dashboard_*.py` が丸ごと skip される
（gotcha `dashboard-tests-silently-skipped`）。**skip を「緑」と数えない。**
確認は `pytest tests/ -q -rs` で skip 理由を出す。

`.claude/settings.local.json` は `docs/development/FEATURE_LOOP_PROMPT.md` §0-3 のものをそのまま使う。
`git commit` と `git push` は入れない。

---

## A. 標準の1イテレーション（これを毎回使う）

新しいタスクに入る前に **必ず `/clear`**。

```
/clear
```

```
/acm-bot-loop

club-bot/docs/development/IMPROVEMENT_TASKS.md を読み、未完了（チェックが入っていない）で
最も若い番号のタスクを 1 つだけ実装して。【人間タスク】は飛ばす。

## 事前に読むもの（実装より先に）

1. club-bot/docs/development/IMPROVEMENT_TASKS.md の「運用ルール」「全タスク共通の受入基準」
   「この表に固有の受入基準」と、対象タスクの受入基準・検証・注意
2. 開発ノート の decisions/_index.md と gotchas/_index.md
   （/add-dir 済み。パスは <開発ノートのパス>\projects\acm_bot）
3. 対象タスクの「注意」に ADR 番号や gotcha 名が書いてあれば、その本文も読む

## 手順

1. 受入基準を1行ずつ書き出す。曖昧なら最も素直な解釈で仮決めして明記する
2. TodoWrite にサブステップを登録してから着手する
3. fix/<タスクID小文字> または feat/<タスクID小文字> ブランチを切る（例: git checkout -b fix/g1-1）
4. 受入基準を満たす**失敗するテストを先に書く**（tests/ に新規ファイル）
5. 最小の実装 → cd club-bot && python -m ruff check . && python -m pytest tests/ -q -rs
   → 失敗を分類して自己修正、を全パスまで繰り返す
6. 全パス後、IMPROVEMENT_TASKS.md の該当タスクにチェックを入れ、
   完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
7. SKILL.md §2 の形式（変更内容 / 検証 / 残課題）で報告する

## 制約

- **ADR に反する実装をしない。** 衝突したら実装せず
  「ADR NNNN と衝突。◯◯という理由で覆すべきだと考える」と書いて止まる
- 修正した不具合が 開発ノート の gotcha に載っていたら、完了ログにノート名を書く
- 途中で私に確認を取らない。SKILL.md §4 の停止条件に当たったときだけ止まる
- コミットはしない。ブランチを切るところまで
- 1タスクの範囲外のファイルを触らない
- skip を「緑」と数えない。pytest は必ず -rs 付きで回し、skip 理由を報告に書く
```

---

## B. G0-2（未マージの監査修正を取り込む）専用

最初の1回だけ。§A より前にこれを回す。

```
/clear
```

```
/acm-bot-loop

fix/code-audit-v2 の6コミットを現行ブランチへ取り込んで。

git log --oneline main..fix/code-audit-v2 で内容を確認したうえで、

1. 取り込み前に現行コードで未適用であることを確認する:
   - grep -rn "descendant_ids" club-bot/services/          → 0件のはず
   - grep -rn "InvalidValueError" club-bot/repositories/    → 0件のはず
   - grep -n -B4 "async def progress_setup" club-bot/cogs/progress.py
     → @require(Level.L2) のはず
2. マージまたは cherry-pick する（コンフリクトが解決できなければ止まって私に聞く）
3. cd club-bot && python -m ruff check . && python -m pytest tests/ -q -rs をフルセットで回す
4. 取り込み後に上の3つの grep が期待どおり変わっていることを確認する
5. tests/test_permissions.py が「ヘルパを直接呼ぶ」のではなく bot.tree を走査して
   コマンドに権限デコレータが付いていることを検査しているかを目視で確認し、報告に書く
6. docs/development/IMPROVEMENT_REPORT.md の P1-12 の表のうち
   「値の検証がサーバー側に無い」「CSV が500行で無言の切り捨て」が
   この取り込みで解消されたかを確認し、残っていれば
   IMPROVEMENT_TASKS.md の G1 に新タスクとして起票する
7. IMPROVEMENT_TASKS.md の G0-2 にチェックを入れ、完了ログに
   「解消した gotcha 名（progress-subtree-disappears /
   progress-stops-after-dashboard-edit / test-asserts-permission-but-decorator-missing）」を書く

コミットはしない。マージコミットが必要な場合は私に確認する。
```

---

## C. 破壊的タスクはプランモードを挟む

**G3-1 / G3-2 / G3-3 / G4-7 は必ずこれを先にやる。**
（スキーマ変更・ADR 更新・既存データへの影響があるもの）

§A のプロンプトを貼る前に **Shift+Tab を2回**押してプランモードへ。

```
club-bot/docs/development/IMPROVEMENT_TASKS.md の <タスクID> を実装する前に、設計だけ立てて。

- 既存ギルドの DB を壊さないマイグレーション手順（v番号・up の内容・既存値の扱い）
- 触る ADR があれば、その番号と「なぜ覆す必要があるか」
  （開発ノート の decisions/ を読んで、元の判断の理由と『覆す条件』に照らすこと）
- 後方互換のために何を「既定 OFF」にするか
- テストで固定する不変条件

実装はまだしない。この方針で良いか確認してから進める。
```

承認後に「この方針で実装して」と伝える。

---

## D. 実装後の他人視点レビュー（Opus 5 を活かす）

同じ文脈で自己レビューさせても粗が残る。**別コンテキスト**で見せる。
G1-6 / G3-2 / G3-3 / G4-7 の後は必ず回す。

```
Task ツールで general-purpose エージェントを1つ立て、次を調べさせて:

「club-bot の未コミットの変更（git diff）をレビューせよ。

 (1) AGENTS.md の絶対ルール違反:
     guild_id スコープ漏れ / スキーマ変更に対するマイグレーション漏れ /
     discord.HTTPException の捕捉漏れ / サークル固有値のハードコード /
     README.md・docs/ と実装の矛盾

 (2) 設計判断との衝突:
     <開発ノートのパス>\projects\acm_bot\decisions\_index.md を読み、
     この変更が既存の ADR に反していないかを確認せよ。
     特に 0008（guild_id を型で封じる）/ 0016（ホワイトリスト）/
     0021・0022（分からないものを数字にしない）/ 0023（問題が無い週は沈黙する）/
     0024（既定値で既存データを動かさない）

 (3) テストが嘘をついていないか:
     skip されているテストが無いか（-rs で確認）。
     テストが実装ではなくヘルパを検査していないか
     （gotcha: test-asserts-permission-but-decorator-missing と同じ形）

 該当箇所をファイル:行番号で列挙し、無ければ『違反なし』と答えること。実装は変更しない。」

報告を受けてから、指摘があれば私に見せて。
```

---

## E. 完了後に 開発ノート へ記録する

**セッションの最後に必ず1回。** 記録しないと次のセッションが同じ調査からやり直す。

```
このセッションで確定した内容を 開発ノート へ書く材料をまとめて。
Vault のファイルは私が書くので、次の形で出力するだけでよい。

1. 【ADR が必要か】
   このタスクで新しい設計判断をしたか。したなら、既存 ADR のテンプレート
   （decisions/0023-silent-when-nothing-is-wrong.md の構成:
   文脈 / 選択肢 / 決定 / 理由 / 却下した案とその理由 / 影響範囲 / 覆す条件 / 根拠）
   に沿って草案を書く。番号は 0035 から。
   既存 ADR を更新・失効させる場合は supersedes / superseded_by を明記する

2. 【gotcha が必要か】
   実装中に踏んだ罠のうち、次に同じ症状を見たら思い出したいものがあるか。
   あれば gotchas/ のテンプレート（症状 / 環境 / 原因 / 対処 / 再発防止 / 参考）で草案を書く。
   **原因ではなく「何が見えたか」を症状に書く**

3. 【unfixed を外せる gotcha】
   このタスクで解消した既知の gotcha 名（あれば）

4. 【_index.md の「未処理」の更新差分】
   消せる項目・追加すべき項目
```

---

## F. 再開 / 表のレビュー

**前回の続きから:**

```
/acm-bot-loop

git status と現在のブランチ、club-bot/docs/development/IMPROVEMENT_TASKS.md の完了ログを確認して、
前回どこまで進んだかを把握してから続きを1タスク実装して。
未コミットの作業が残っていれば、新しいタスクに入る前にそれを完成させる。

git status が 100 ファイル以上を変更ありと報告する場合は、CRLF 汚染が再発している。
実装に入らず、その旨だけ報告して止まって（docs/development/IMPROVEMENT_LOOP_PROMPT.md §0-1）。
```

**表そのものを見直す:**

```
club-bot/docs/development/IMPROVEMENT_TASKS.md の Phase G<N> の受入基準を、実際のコード
（cogs/ repositories/ utils/db.py services/ dashboard/）と
開発ノート の decisions/ ・ gotchas/ と突き合わせてレビューして。

- 既に実装済み・既にマージ済みで不要になった項目
- ADR と衝突していて、そのままでは実装できない項目
- 二重定義・依存順序の誤り

を指摘して、表の修正案を出す。実装はまだしない。
```

---

## G. 無人で連続実行（headless）

**git worktree の中でだけ**やること。G0 を終えてから。

```bash
cd ~/acm_bot
git worktree add ../acm_bot_loop -b feat/improvement-tasks
cd ../acm_bot_loop

for i in $(seq 1 5); do
  echo "=== iteration $i ==="
  claude -p --model opus --permission-mode acceptEdits \
    --add-dir <開発ノートのパス>/projects/acm_bot \
    "/acm-bot-loop club-bot/docs/development/IMPROVEMENT_TASKS.md の未完了で最も若いタスク（【人間タスク】は除く）を
     1つだけ実装し、ruff と pytest（-rs 付き）が全パスするまで自走して。
     開発ノート の decisions/_index.md と gotchas/_index.md を先に読み、ADR に反する実装をしない。
     完了したら表のチェックと完了ログを更新する。コミットはしない。
     停止条件に当たったら STOPPED: と理由を出力して終了する。" \
    || break
done
```

- `--permission-mode acceptEdits` は編集のみ自動承認。`--dangerously-skip-permissions` は使わない
- **G3 のタスクは無人で回さない**（スキーマ変更・ADR 更新を含む）。§C のプランモードを挟む
- 実行後は必ず `git diff` を人間が読む。無人実行の結果をレビューなしでマージしない

---

## 運用メモ

- **1タスク = 1セッション**。終わったら `/clear`。`/compact` に頼ると受入基準がぼやける
- `/context` で残りを確認し、20% を切ったら**そのタスクを完成させることだけ**に集中させる
- 検証は `bash scripts/loop_check.sh`（`club-bot/` から）。ただし **skip 数を必ず見る**
- Windows の pytest は `venv/Scripts/python.exe -m pytest tests/ -q -rs`
- `.env` の実値は Claude Code に読ませない（`deny` 設定）
- スキーマ版 v16〜v19 は表で予約済み。順序を入れ替えるときは番号も振り直す
- **セッションの最後に §E を必ず回す。** 開発ノート が腐ると、
  「なぜそう決めたか」を次の代が誰も説明できなくなる
