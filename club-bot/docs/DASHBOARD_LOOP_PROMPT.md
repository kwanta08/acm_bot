# Claude Code（Opus 5）でダッシュボード改良を回すための起動プロンプト

`docs/DASHBOARD_TASKS.md`（D0〜D3）を Claude Code で 1 タスクずつ実装するための入力集。
`docs/IMPROVEMENT_LOOP_PROMPT.md` と同じ構成で、**ダッシュボード（`club-bot/dashboard/`）専用**。
Cowork（チャット）ではなく **アプリ内 / ターミナルの Claude Code で実装する**前提。

`docs/IMPROVEMENT_TASKS.md`（G0〜G4）とは**別トラック**。ブランチが別なので並行して回せるが、
**同じセッションで両方を回さない**（受入基準が混ざる）。

---

## 0. 実装を始める前に（順番厳守）

### 0-1. 【人間がやる】作業ツリーが clean であることを確認する

```bash
cd /c/Users/yoshi/acm_bot
git status --porcelain | wc -l
```

**100 行以上出るなら CRLF 汚染の再発。** 実装に入らず
`docs/IMPROVEMENT_LOOP_PROMPT.md` §0-1 の手順で戻す。
`git diff -w --stat` が実質の差分。エージェントにやらせない。

### 0-2. 【人間がやる】D0-1 を先に済ませる

D0-1 は【人間タスク】で、エージェントは飛ばす。**が、D3 の判断材料がここにしか無い。**

1. 現行ダッシュボードの5画面をスクリーンショットして `club-bot/docs/img/dashboard-before/` に置く
   （未ログイン / 表の一覧 / `progress` / `schedule_votes` / エラー時）
2. `x.com/basit_designs` を実際に見て、`DASHBOARD_TASKS.md` の
   §デザイン方針「読み取った作風」を校正する。**機械取得できていないので現状は仮**
3. 校正したら同節に「校正済み（YYYY-MM-DD）」と追記する

### 0-3. ClaudeVault（Obsidian）を読ませる

ADR と gotcha は `C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot\` にある。
Claude Code は既定で作業ディレクトリの外を読めないので、**セッション開始時に追加する**。

```bash
claude --model opus --add-dir /c/Users/yoshi/ClaudeVault/ClaudeVault/projects/acm_bot
```

すでにセッション中なら:

```
/add-dir C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot
```

**ADR は2箇所にある。** リポジトリ内の `club-bot/docs/adr/`（3件）は `/add-dir` 不要で読める。
ClaudeVault 側（33件）と**番号体系が独立していて 0008 が両方に存在する**ので、
言及するときは出典を書き分けさせること。

| 場所 | 内容 |
|---|---|
| `club-bot/docs/adr/0008-dashboard-guild-scope.md` | ダッシュボードの guild_id スコープ。**この表の全タスクに効く。最初に読ませる** |
| `club-bot/docs/adr/0007` / `0009` | services 層の guild_id 凍結とその解除 |
| ClaudeVault `decisions/` | 設計判断の本体（33件）。下表 |

ClaudeVault 側でこの表に特に効いてくる ADR:

| ADR | この表での効き方 |
|---|---|
| 0006（本番は PostgreSQL、SQLite は開発専用） | D1-2 / D1-3 / D2-6 の PG 実機テストが必須である理由 |
| 0008（`guild_id` を型で封じる） | ダッシュボードのハンドラは `ScopedGuild` しか受け取らない |
| 0016（ホワイトリスト） | D1-2 の検索対象列・D1-3 の並び替え列も同じ方式で |
| 0024（既定値で既存データを動かさない） | **D2-1 が直接触れる。** 覆す条件に照らしてから実装する |

### 0-4. `CLAUDE.md` を更新する

Claude Code が自動で読むのは `CLAUDE.md`。**現状は完了済みの `FEATURE_TASKS.md` を
指したままなので、2つの表を並行で回せるように書き換える。**

```markdown
# acm_bot

開発規約は AGENTS.md が正。作業前に必ず全文を読むこと。

@AGENTS.md

## 実装タスク（2トラック並行。1セッションで混ぜない）
- bot 側の改善: club-bot/docs/IMPROVEMENT_TASKS.md（G0〜G4。根拠は docs/IMPROVEMENT_REPORT.md）
- ダッシュボード改良: club-bot/docs/DASHBOARD_TASKS.md（D0〜D3。根拠は同レポート P1-12）
- 完了済み: club-bot/docs/FEATURE_TASKS.md / docs/PUBLIC_RELEASE_TASKS.md
- 実装ループは /acm-bot-loop スキルの手順で回す（全テストパスまで自走）

## 設計判断の正
- ADR と既知のハマりどころは Obsidian の ClaudeVault にある
  （/add-dir C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot）
- ADR に反する実装をしない。覆す必要があると判断したら実装せず報告する

## 作業ディレクトリ
- コードは club-bot/ 配下。ruff / pytest は club-bot/ で実行する
- Python は club-bot/venv/Scripts/python.exe（無ければ python）
- ダッシュボードのフロントは club-bot/dashboard/static/。
  外部 CDN・npm パッケージ・フレームワークを増やさない
```

### 0-5. 依存と許可設定

```
> cd club-bot && python -m pip install -r requirements.txt -r dashboard/requirements.txt ruff pytest
```

`dashboard/requirements.txt` を入れないと `test_dashboard_*.py` が丸ごと skip される
（gotcha `dashboard-tests-silently-skipped`）。**skip を「緑」と数えない。**
確認は `pytest tests/ -q -rs` で skip 理由を出す。

D0-2 の完了後は Node も要る（**npm install はしない**。標準の `node --test` だけ）:

```
> node --version   # 18 以上
```

`.claude/settings.local.json` は `docs/FEATURE_LOOP_PROMPT.md` §0-3 のものをそのまま使う。
`git commit` と `git push` は入れない。

---

## A. 標準の1イテレーション（これを毎回使う）

新しいタスクに入る前に **必ず `/clear`**。

```
/clear
```

```
/acm-bot-loop

club-bot/docs/DASHBOARD_TASKS.md を読み、未完了（チェックが入っていない）で
最も若い番号のタスクを 1 つだけ実装して。【人間タスク】は飛ばす。

## 事前に読むもの（実装より先に）

1. club-bot/docs/DASHBOARD_TASKS.md の「運用ルール」「全タスク共通の受入基準」
   「この表に固有の受入基準」「デザイン方針」と、対象タスクの受入基準・検証・注意
2. ClaudeVault の decisions/_index.md と gotchas/_index.md
   （/add-dir 済み。パスは C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot）
3. 対象タスクの「注意」に ADR 番号や gotcha 名が書いてあれば、その本文も読む
4. 触る予定のファイルの現物（dashboard/static/app.js / style.css /
   dashboard/routers/ / repositories/table_repository.py のうち関係する範囲）

## 手順

1. 受入基準を1行ずつ書き出す。曖昧なら最も素直な解釈で仮決めして明記する
2. TodoWrite にサブステップを登録してから着手する
3. feat/<タスクID小文字> または fix/<タスクID小文字> ブランチを切る（例: git checkout -b feat/d1-1）
4. 受入基準を満たす**失敗するテストを先に書く**
   - サーバー側: tests/ に新規または追記（pytest）
   - フロント側: dashboard/static/lib/ の純粋関数に対して node --test（D0-2 の完了後）
5. 最小の実装 → cd club-bot && python -m ruff check . && python -m pytest tests/ -q -rs
   （D0-2 後は node --test dashboard/static/lib/ も）
   → 失敗を分類して自己修正、を全パスまで繰り返す
6. 全パス後、DASHBOARD_TASKS.md の該当タスクにチェックを入れ、
   完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
7. SKILL.md §2 の形式（変更内容 / 検証 / 残課題）で報告する

## 制約

- **外部 CDN・npm パッケージ・フレームワークを配信物に足さない。**
  足さないと実装できないと判断したら、実装せず理由を書いて止まる
- **ADR に反する実装をしない。** 衝突したら実装せず
  「ADR NNNN と衝突。◯◯という理由で覆すべきだと考える」と書いて止まる
- SQL に触ったら、CLUB_TEST_PG_DSN 条件付きの PostgreSQL テストを必ず足す。
  SQLite だけ緑でも「検証できた」と書かない
- 修正した不具合が ClaudeVault の gotcha に載っていたら、完了ログにノート名を書く
- 途中で私に確認を取らない。SKILL.md §4 の停止条件に当たったときだけ止まる
- コミットはしない。ブランチを切るところまで
- 1タスクの範囲外のファイルを触らない。**表のタスク本文に例外が明記されている場合だけ従う**
  （現状は D2-5 の `utils/logger.py` のみ）
- skip を「緑」と数えない。pytest は必ず -rs 付きで回し、skip 理由を報告に書く
```

---

## B. D0-2（フロントの検証土台）専用 — プランモード必須

**最初の実装タスクであり、依存追加の判断を含む。** §A より前にこれを回す。
§A のプロンプトを貼る前に **Shift+Tab を2回**押してプランモードへ。

```
club-bot/docs/DASHBOARD_TASKS.md の D0-2 を実装する前に、設計だけ立てて。

現状 dashboard/static/app.js は 425 行あるがテストが1件も無く、CI も Python だけ。
この状態で D1〜D3 を回すと「全テストパス」がフロントの変更を検証しない。

- app.js から DOM に触れない純粋関数を洗い出す
  （parseInput / formatCell / ページング計算 / クエリ組み立て / ソート状態遷移 /
   エラー status の分岐 / テーマ解決）。どれを static/lib/ に出すか、境界の引き方
- ES モジュール化（index.html の script に type="module"）で壊れる運用があるか。
  file:// で直接開いての動作確認、静的配信の MIME、ブラウザ対応
- CI（.github/workflows/ci.yml）に足す Node ジョブの形。
  package.json を作らずに node --test だけで回せるか
- 「依存を増やさない」という既存の設計方針と両立する理由の説明
  （配信物に1バイトも増えない、開発と CI だけ、という線引きで足りるか）
- ADR 草案の骨子（文脈 / 選択肢 / 決定 / 理由 / 却下した案 / 影響範囲 / 覆す条件）

実装はまだしない。この方針で良いか確認してから進める。
```

承認後に「この方針で実装して」と伝える。

---

## C. D2-1（`GUILD_NAME` → `CLUB_NAME`）専用 — プランモード必須

**既存ギルドの設定値を動かす。ADR 0024 に直接触れる。**

```
club-bot/docs/DASHBOARD_TASKS.md の D2-1 を実装する前に、設計だけ立てて。

- 既存ギルドの DB を壊さないマイグレーション手順
  （v番号・migrations/NNN の内容・_migrate_vNN_*() の中身・既存値の扱い）
  ※ v16〜v19 は IMPROVEMENT_TASKS.md が G3-4 / G4-1 / G4-2 / G4-4 に予約済み。
    着手時点の utils/db.py の SCHEMA_VERSION を読み直して番号を決めること
- 3ケースの扱い: (a) GUILD_NAME だけ (b) 両方ある (c) どちらも無い
- 旧キー GUILD_NAME の行を消すか残すか。消さない場合、次に読む人が
  「どちらが正か」を判断できる形になっているか
- ADR 0024（既定値で既存データを動かさない）を読み、
  この変更が「覆す条件」に当たるか、当たらないなら例外扱いの根拠
- テストで固定する不変条件

実装はまだしない。この方針で良いか確認してから進める。
```

---

## D. デザインタスク（D3）の回し方

D3 は「テストが緑」だけでは終わらない。**見た目の確認が受入基準に含まれる。**

```
/clear
```

```
/acm-bot-loop

club-bot/docs/DASHBOARD_TASKS.md の <タスクID> を実装して。

このタスクは見た目を変える。手順を次の順で進めること:

1. DASHBOARD_TASKS.md の §デザイン方針の表を読み、
   このタスクで守るべき行（角丸・影・アクセントの面積・モーション・タイポ）を書き出す
2. dashboard/static/preview.html で先に形を決める。
   本番の画面（index.html / app.js）にはまだ触らない
3. preview.html を 320px / 768px / 1440px の3幅で確認し、
   ライト／ダーク両方でスクリーンショットを取る
4. 形が決まってから本番画面へ適用する
5. python -m ruff check . && python -m pytest tests/ -q -rs と node --test を回す
6. before（docs/img/dashboard-before/）と after を並べて完了ログに書く

色を変えた場合は、変えた組み合わせすべてのコントラスト比を計算して
完了ログに表で残すこと（本文 / ミュート文字 / アクセント上の白文字。AA = 4.5:1）。
計算をせずに「AA を満たしています」と書かない。

外部 CDN・Web フォント・npm パッケージは足さない。
```

---

## E. 実装後の他人視点レビュー（Opus 5 を活かす）

同じ文脈で自己レビューさせても粗が残る。**別コンテキスト**で見せる。
**D0-2 / D1-2 / D1-3 / D2-1 / D2-6 / D3-1 の後は必ず回す。**

```
Task ツールで general-purpose エージェントを1つ立て、次を調べさせて:

「club-bot の未コミットの変更（git diff）をレビューせよ。

 (1) AGENTS.md の絶対ルール違反:
     guild_id スコープ漏れ / スキーマ変更に対するマイグレーション漏れ /
     サークル固有値のハードコード / README.md・docs/ と実装の矛盾

 (2) ダッシュボード固有の不変条件が壊れていないか:
     - ハンドラが ScopedGuild / EditorGuild / AdminGuild 以外で guild_id を受けていないか
       （dashboard/security.py の GuildScope を迂回していないか）
     - テーブル名・列名・並び順・検索対象がホワイトリスト経由か。
       リクエスト由来の文字列を SQL へ連結していないか
     - innerHTML を使っていないか（現行はゼロ件。textContent のみ）
     - 配信物に外部 CDN / Web フォント / npm パッケージが増えていないか

 (3) SQLite と PostgreSQL の差:
     LIKE の大文字小文字、NULL の並び順、型親和性（'5' と 5）。
     SQLite だけで検証を済ませていないか。
     CLUB_TEST_PG_DSN 条件付きのテストが追加されているか

 (4) テストが嘘をついていないか:
     skip されているテストが無いか（-rs で確認）。
     テストが実装ではなくヘルパを検査していないか
     （gotcha: test-asserts-permission-but-decorator-missing と同じ形）。
     node --test が CI で実際に失敗しうる形で組まれているか

 (5) 設計判断との衝突:
     C:\Users\yoshi\ClaudeVault\ClaudeVault\projects\acm_bot\decisions\_index.md を読み、
     特に 0006 / 0008 / 0016 / 0024 に反していないかを確認せよ。

 該当箇所をファイル:行番号で列挙し、無ければ『違反なし』と答えること。実装は変更しない。」

報告を受けてから、指摘があれば私に見せて。
```

---

## F. 完了後に ClaudeVault へ記録する

**セッションの最後に必ず1回。** 記録しないと次のセッションが同じ調査からやり直す。

```
このセッションで確定した内容を ClaudeVault へ書く材料をまとめて。
Vault のファイルは私が書くので、次の形で出力するだけでよい。

1. 【ADR が必要か】
   このタスクで新しい設計判断をしたか。したなら、既存 ADR のテンプレート
   （decisions/0023-silent-when-nothing-is-wrong.md の構成:
   文脈 / 選択肢 / 決定 / 理由 / 却下した案とその理由 / 影響範囲 / 覆す条件 / 根拠）
   に沿って草案を書く。番号は decisions/_index.md の最大値 + 1。
   既存 ADR を更新・失効させる場合は supersedes / superseded_by を明記する

   ※ この表で ADR になりやすいもの:
     - フロントの検証を Node 標準テストランナーだけで行う（D0-2）
     - ダッシュボードの見た目の規約（トークン体系・角丸・影・アクセントの面積）（D0-3）
     - 設定キーの改名で既存データを移す条件（D2-1。ADR 0024 との関係）
     - マイグレーションの排他制御に advisory lock を使う（D2-6）

2. 【gotcha が必要か】
   実装中に踏んだ罠のうち、次に同じ症状を見たら思い出したいものがあるか。
   あれば gotchas/ のテンプレート（症状 / 環境 / 原因 / 対処 / 再発防止 / 参考）で草案を書く。
   **原因ではなく「何が見えたか」を症状に書く**

   ※ この表で出やすい症状:
     - 「検索がローカルでは効くのに本番で効かない」（LIKE の大文字小文字）
     - 「✓ を押すと編集が取り消される」（blur と mousedown の順序）
     - 「sticky ヘッダが効かない」（.grid-wrap に高さ制限が無い）
     - 「デプロイ時だけマイグレーションが二重に走る」（advisory lock 不在）

3. 【unfixed を外せる gotcha】
   このタスクで解消した既知の gotcha 名（あれば）

4. 【_index.md の「未処理」の更新差分】
   消せる項目・追加すべき項目
```

---

## G. 再開 / 表のレビュー

**前回の続きから:**

```
/acm-bot-loop

git status と現在のブランチ、club-bot/docs/DASHBOARD_TASKS.md の完了ログを確認して、
前回どこまで進んだかを把握してから続きを1タスク実装して。
未コミットの作業が残っていれば、新しいタスクに入る前にそれを完成させる。

git status が 100 ファイル以上を変更ありと報告する場合は CRLF 汚染が再発している。
実装に入らず、その旨だけ報告して止まって（docs/IMPROVEMENT_LOOP_PROMPT.md §0-1）。

docs/IMPROVEMENT_TASKS.md（G0〜G4）のタスクには手を出さない。別トラック。
```

**表そのものを見直す:**

```
club-bot/docs/DASHBOARD_TASKS.md の Phase D<N> の受入基準を、実際のコード
（dashboard/ と repositories/table_repository.py）と
ClaudeVault の decisions/ ・ gotchas/ と突き合わせてレビューして。

- 既に実装済み・不要になった項目
- ADR と衝突していて、そのままでは実装できない項目
- 二重定義・依存順序の誤り
- docs/IMPROVEMENT_REPORT.md の P1-12 の表と、この表の対応が今も正しいか

を指摘して、表の修正案を出す。実装はまだしない。
```

---

## H. 無人で連続実行（headless）

**git worktree の中でだけ**やること。D0 を終えてから。

```bash
cd /c/Users/yoshi/acm_bot
git worktree add ../acm_bot_dash -b feat/dashboard-tasks
cd ../acm_bot_dash

for i in $(seq 1 4); do
  echo "=== iteration $i ==="
  claude -p --model opus --permission-mode acceptEdits \
    --add-dir /c/Users/yoshi/ClaudeVault/ClaudeVault/projects/acm_bot \
    "/acm-bot-loop club-bot/docs/DASHBOARD_TASKS.md の未完了で最も若いタスク（【人間タスク】は除く）を
     1つだけ実装し、ruff と pytest（-rs 付き）が全パスするまで自走して。
     ClaudeVault の decisions/_index.md と gotchas/_index.md を先に読み、ADR に反する実装をしない。
     配信物に外部依存を足さない。完了したら表のチェックと完了ログを更新する。コミットはしない。
     停止条件に当たったら STOPPED: と理由を出力して終了する。" \
    || break
done
```

- `--permission-mode acceptEdits` は編集のみ自動承認。`--dangerously-skip-permissions` は使わない
- **D0-2 / D2-1 / D3 のタスクは無人で回さない**
  （依存追加の判断・既存データの移行・見た目の確認が human-in-the-loop を要する）
- 実行後は必ず `git diff` を人間が読む。無人実行の結果をレビューなしでマージしない

---

## 運用メモ

- **1タスク = 1セッション**。終わったら `/clear`。`/compact` に頼ると受入基準がぼやける
- **G の表と D の表を同じセッションで混ぜない。** 受入基準が違う（D には
  「外部依存を足さない」「PG テスト必須」「見た目の確認」が加わる）
- `/context` で残りを確認し、20% を切ったら**そのタスクを完成させることだけ**に集中させる
- 検証は `bash scripts/loop_check.sh`（`club-bot/` から）。ただし **skip 数を必ず見る**
- Windows の pytest は `venv/Scripts/python.exe -m pytest tests/ -q -rs`
- ダッシュボードの手元起動:
  `venv/Scripts/python.exe -m uvicorn dashboard.main:app --host 127.0.0.1 --port 8000`
- `.env` の実値は Claude Code に読ませない（`deny` 設定）
- **セッションの最後に §F を必ず回す。** ClaudeVault が腐ると、
  「なぜそう決めたか」を次の代が誰も説明できなくなる
