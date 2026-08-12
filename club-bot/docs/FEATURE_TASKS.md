# 新機能タスク管理（/help・データ削除・重量管理・大会逆算・年度替わり）

`docs/PUBLIC_RELEASE_TASKS.md` に続く第2の管理表。公開配布の準備が一巡した後に着手する
5機能を、`/acm-bot-loop` で1タスクずつ回すために分解したもの。

## 運用ルール

- 実装は **必ず `/acm-bot-loop` の手順**（受入基準確定 → 最小実装 → ruff + pytest → 自己修正）で回す。
  「実装したので確認してください」で止めない。全パスして初めて完了
- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
  （ユーザーの明示指示があればフェーズ単位でまとめて実施する）
- タスクごとに `feat/<タスクID小文字>` ブランチを切る（例 `feat/f1-1`）
- 完了時にチェックを入れ、末尾の完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
- **フェーズ順序を守る**（F0 → F1 → F2 → F3 → F4 → F5）。破壊的変更の小さい順に並べてある
- 【人間タスク】と記されたものはエージェントは飛ばす
- コミット・push は**ユーザーから明示的に指示されたときのみ**

## 全タスク共通の受入基準（AGENTS.md より。各タスクで再掲しない）

- 新規データ・新規設定はすべて `guild_id` スコープ。ギルド別設定は `config.for_guild(guild_id)` 経由
- コマンドは `interaction.guild.id` でスコープし、DM 実行は `ensure_guild()` で拒否する
- Discord API 呼び出しは `discord.HTTPException` を捕捉する
- スキーマ変更は **同じイテレーション内で** `migrations/NNN_*.sql` と `utils/db.py` の
  `_migrate_vNN_*()` ＋ `SCHEMA_VERSION` 更新をセットで行う（既存ギルドの DB を壊さない）
- 班名・チャンネル・ロール・機体名・桁構成をコードに埋め込まない。
  鳥人間ドメイン語（桁巻き・積層・主桁・機体）はそのまま書いてよい
- 実装とドキュメント（`README.md` / `docs/`）が矛盾したら両方直す
- `ruff check .` と `python -m pytest tests/ -q` がフルセットで緑

## スキーマバージョンの割り当て（衝突防止）

現行は `SCHEMA_VERSION = 10`（`migrations/009_progress_nodes.sql` まで）。

| 版 | migration | フェーズ |
|---|---|---|
| v11 | `010_guild_lifecycle.sql` | F2（退出・削除） |
| v12 | `011_progress_weight.sql` | F3（重量管理） |
| v13 | `012_progress_milestones.sql` | F4（大会逆算） |
| v14 | `013_seasons.sql` | F5（年度替わり） |

---

## Phase F0: 足場

- [x] **F0-1** `scripts/loop_check.sh` / `scripts/loop_check.ps1` をリポジトリの `club-bot/scripts/` へ配置する。
      現状これらは `/acm-bot-loop` スキルに同梱されているだけで、リポジトリ内には無いため
      `bash scripts/loop_check.sh` が `club-bot/` から叩けない。
      - **受入**: `club-bot/` で `bash scripts/loop_check.sh` / `--fast` / `-k progress` が動作し、
        ruff と pytest の結果を要約表示する。`.gitignore` に引っかからないこと
      - **検証**: 手動実行で PASS / FAIL の両方を確認（テストは不要）
      - **注意**: スキーマ変更なし。最初の1周をこれで慣らす

---

## Phase F1: `/help` — コマンドカタログ

**背景**: `bot.py` は `help_command=None`、`cogs/core.py` は `/ping` と `/health` のみ。
コマンドが約80個あるのに入口が無く、招待直後のサークルが `docs/GUIDE.md` に辿り着けない。

- [ ] **F1-1** `/help` を `cogs/core.py`（または新規 `cogs/help.py`）に実装する。
      **コマンド一覧はハードコードせず** `bot.tree.walk_commands()` から動的生成する
      （手書きの一覧はコマンド追加のたびに腐るため）。
      - **変更ファイル（推定）**: `cogs/help.py`(新規) or `cogs/core.py`, `bot.py`(cog 登録),
        `utils/embeds.py`, `tests/test_help.py`(新規)
      - **受入**:
        - `/help` でカテゴリ選択メニュー（日程調整 / タスク / 班・メンバー / 桁巻き / 機体進捗 /
          レポート / 設定）を表示し、選ぶと該当グループのサブコマンド一覧を Embed で返す
        - `/help command:<名前>` で個別コマンドの説明・引数・必要権限を表示（オートコンプリート付き）
        - 実行者の権限レベル（`utils/permissions.get_level`）より上のコマンドには
          「L3 以上」等のバッジを付ける（**非表示にはしない**。何ができる bot かは全員に見せる）
        - 応答は `ephemeral=True`（チャンネルを汚さない）
        - カテゴリは Cog 名かコマンドグループ名から導出し、サークル固有語を含まない
      - **検証**: `tests/test_help.py` — (1) 登録済み全コマンドが `/help` のどれかのカテゴリに現れる
        （**新コマンドを足すと落ちる回帰テスト**にする）、(2) 権限バッジが `Level` と一致する、
        (3) Embed が Discord の 6000 文字 / 25 field 制限に収まる
      - **注意**: スキーマ変更なし

- [ ] **F1-2** `/help setup-status` — 初期設定の未完了チェックを追加する。
      - **受入**: 通知チャンネル・ログチャンネル・管理者ロール・班・桁が未設定なら
        「未設定」と該当コマンド（`/setup`, `/team-add`, `/layer keta-add`）を案内する。
        すべて設定済みなら ✅ を返す。判定は `config.for_guild()` と各リポジトリの件数のみで行い、
        設定値の初期値をコードに持たない
      - **検証**: `tests/test_help.py` に追記 — 空ギルドで全項目「未設定」、
        投入後に全項目 ✅ になること
      - **注意**: `/health`（Bot と連携サービスの状態）とは責務を分ける。こちらは**サーバー側の設定**

---

## Phase F2: データのエクスポートと削除（公開 bot の必須要件）

**背景**: `on_guild_remove` ハンドラが存在せず、`docs/PRIVACY.md` にも
「キックしてもデータは消えない。削除は運営者へ連絡」と明記されている状態。
公開配布で最もリスクの高い箇所。

- [ ] **F2-1** スキーマ v11: ギルドのライフサイクルを記録する。
      - **変更ファイル**: `migrations/010_guild_lifecycle.sql`, `utils/db.py`(`SCHEMA_VERSION`=11,
        `_migrate_v11_guild_lifecycle()`), `repositories/guild_repository.py`, `bot.py`,
        `tests/test_guild_lifecycle.py`(新規)
      - **受入**:
        - `guilds` テーブルに `left_at TEXT NULL` / `purge_after TEXT NULL` を追加
        - `on_guild_remove` で `left_at` と `purge_after`（既定 = 退出 + 30日。ギルド別設定
          `DATA_RETENTION_DAYS` で上書き可）を記録する。**この時点ではデータを消さない**
        - 再招待（`on_guild_join`）で `left_at` / `purge_after` を NULL に戻し、
          既存データがそのまま復活する
        - 既存ギルドの DB がマイグレーションで壊れない（列は NULL 許容で追加）
      - **検証**: `tests/test_guild_lifecycle.py` — 退出 → 記録、再参加 → クリア、
        既存 v10 DB からのマイグレーションで既存行が保持されること

- [ ] **F2-2** `/data export` — サーバー管理者が自サーバーの全データを ZIP（CSV 群）で受け取る。
      - **変更ファイル**: `cogs/data.py`(新規), `repositories/table_repository.py`(再利用),
        `bot.py`, `tests/test_data_export.py`(新規)
      - **受入**:
        - L4（または Manage Server）のみ。`ephemeral` で ZIP を添付
        - 対象は `table_repository` のホワイトリスト（tasks / members / teams / schedules /
          schedule_votes / layer_records / progress_nodes …）。ダッシュボードの CSV 出力ロジックを再利用する
        - **`guild_id` 列と Todoist トークン等の機密列は出力しない**
        - CSV は BOM 付き UTF-8（Excel でそのまま開ける。ダッシュボードと同仕様）
        - 8MB を超える場合は分割せず「ダッシュボードから取得してください」と案内する
        - `audit_log` に `data.export` を記録する
      - **検証**: `tests/test_data_export.py` — (1) 他ギルドの行が1件も混入しない
        （B大学のデータを件数を変えて配置して検出）、(2) 機密列が ZIP 内に現れない、
        (3) 権限不足で拒否される
      - **注意**: 新規依存を足さない（`zipfile` / `csv` は標準ライブラリ）

- [ ] **F2-3** `/data delete` — サーバー管理者が自サーバーのデータを自分で削除する。
      - **受入**:
        - L4 のみ。Modal で**サーバー名を打たせて確認**する（ボタン1つで消えない）
        - 実行すると `purge_after` を即時（= 現在時刻）に設定し、F2-4 のジョブで削除される。
          猶予期間中は `/data delete cancel` で取り消せる
        - 削除前に F2-2 のエクスポートを自動で添付し、「これが最後のバックアップ」と明示する
        - `audit_log` に `data.delete.requested` / `data.delete.cancelled` を記録する
      - **検証**: `tests/test_data_delete.py` — 確認文字列の不一致で中止、
        取り消しで `purge_after` が NULL に戻ること
      - **注意**: この時点では**実削除を行わない**（実削除は F2-4）。1周を小さく保つ

- [ ] **F2-4** 自動パージジョブと文書の更新。
      - **変更ファイル**: `cogs/reminders.py`(日次ループへ追加), `repositories/guild_repository.py`,
        `docs/PRIVACY.md`, `docs/TERMS.md`, `README.md`, `tests/test_data_purge.py`(新規)
      - **受入**:
        - 日次ループで `purge_after <= now` のギルドを検出し、全テーブルから当該 `guild_id` の
          行を削除する（削除件数を `bot-log` と `utils/logger` に残す）
        - 削除対象テーブルは**ホワイトリストではなく `TABLE_DDL` の全テーブルから導出**する
          （テーブルを足したときに消し漏れが出ないようにする）。テストで全テーブル網羅を検証する
        - 1ギルドの削除失敗が他ギルドの処理を止めない
        - `docs/PRIVACY.md` / `docs/TERMS.md` / `README.md` の「キックしても消えない・
          運営者へ連絡」の記述を、新しい挙動（退出後30日で自動削除・`/data delete` で自己削除）へ更新する
      - **検証**: `tests/test_data_purge.py` — (1) 期限切れギルドの行が全テーブルから消える、
        (2) **他ギルドの行が1件も減らない**、(3) `TABLE_DDL` に新テーブルを足すと
        網羅テストが落ちる（消し漏れ検出）
      - **注意**: ここは唯一の**破壊的処理**。テストを先に書いてから実装する

---

## Phase F3: 重量管理（`/weight`）

**背景**: 人力飛行機は重量が競技成績に直結するのに、`progress_nodes` は進捗率しか持たない。
既存の木構造と再帰集計（`services/progress_tree.py`）をそのまま重量の積み上げに使える。

**設計判断（先に固定する）**: 独立テーブルを作らず **`progress_nodes` に列を追加**する。
理由 —(1) 機体→パーツ→部品の木は進捗と重量で完全に同一、(2) `/progress` のツリー
オートコンプリートと `load_tree()` をそのまま流用できる、(3) 別テーブルにすると
ノード削除時の整合管理が増える。

- [ ] **F3-1** スキーマ v12: `progress_nodes` に `target_weight_g REAL NULL` /
      `actual_weight_g REAL NULL` を追加する。
      - **変更ファイル**: `migrations/011_progress_weight.sql`, `utils/db.py`(v12),
        `repositories/progress_repository.py`(更新許可列のホワイトリストに追加),
        `tests/test_progress_weight.py`(新規)
      - **受入**: 単位は **g 固定**（`_g` サフィックスで明示。単位設定は作らない）。
        既存行は NULL のまま壊れない。PostgreSQL では `DOUBLE PRECISION` になること
        （`to_pg_ddl` の REAL 変換が効いているか確認する）
      - **検証**: v11 DB からのマイグレーションで既存 `progress_nodes` が保持されること

- [ ] **F3-2** `services/progress_tree.py` に重量集計を追加する。
      - **受入**:
        - 集計規則: **実測値が入っていればそれを採用**、無ければ子ノードの実測合計を積み上げる
          （進捗率の `aggregated` と同じ再帰の中で計算する）
        - 目標重量も同じ規則で集計する
        - 「実測が入っているノードの割合」も返す（見積もりの確度を示すため）
        - 孤児・循環ノードは既存と同じく除外され、無限ループしない
      - **検証**: `tests/test_progress_weight.py` — (1) 葉のみ実測 → 親が合計になる、
        (2) 親に実測がある → 子の合計ではなく親の実測が勝つ、(3) 循環データで停止する

- [ ] **F3-3** `/weight` コマンド群と `/progress view` への表示。
      - **受入**:
        - `/weight set node:<ツリー補完> actual:<g> [target:<g>]`（L2 班長以上）
        - `/weight view [node]` — 集計重量 / 目標との差分 / 実測入力率を Embed 表示
        - `/weight top` — **目標超過の大きい順**にノードを並べる（減量の着手先が分かる）
        - `/progress view` の Embed に「重量: 実測 1,240g / 目標 1,100g（+140g）」の行を追加。
          重量未設定のノードでは行ごと出さない（既存表示を汚さない）
        - 変更は `audit_log` に記録する
      - **検証**: `tests/test_progress_weight.py` に追記 — 権限、超過ランキングの並び順、
        重量未設定時に `/progress view` の表示が従来どおりであること
      - **注意**: 画像を作らない（`utils/progress_bar.py` と同じくテキスト表現。
        matplotlib 再混入は P3-1 のテストが検出する）

- [ ] **F3-4** Web ダッシュボードの progress グリッドへ重量列を追加する。
      - **受入**: `repositories/table_repository.py` のホワイトリストに 2 列を追加し、
        班長以上は編集可・CSV にも出力される。`guild_id` スコープの検証は既存の仕組みに乗る
      - **検証**: `tests/test_dashboard_tables.py` に追記（`dashboard/requirements.txt` 未導入だと
        skip される点に注意）

---

## Phase F4: 大会からの逆算アラート

**背景**: 進捗率は見えるが「間に合うのか」が見えない。大会日とマイルストーンから
必要ペースを逆算し、遅延を先に知らせる。

- [ ] **F4-1** スキーマ v13: `progress_milestones` テーブルと大会日の設定。
      - **変更ファイル**: `migrations/012_progress_milestones.sql`, `utils/db.py`(v13),
        `repositories/progress_repository.py`, `tests/test_milestones.py`(新規)
      - **受入**:
        - `progress_milestones(milestone_id, guild_id, node_id, name, due_date, created_at, updated_at)`、
          `UNIQUE (guild_id, node_id, name)`
        - 大会日はギルド別設定キー `COMPETITION_DATE`（`YYYY-MM-DD`）。**既定値を持たない**
          （大会も日程もサークルごとに違う）
        - `node_id` は `progress_nodes` と同じく FK を張らない（既存方針に合わせる）。
          存在しないノードを指すマイルストーンは表示から除外する
      - **検証**: マイグレーション後に既存データが保持されること、`guild_id` スコープ

- [ ] **F4-2** `/milestone add|remove|list` と `/countdown`。
      - **受入**:
        - `/milestone add node:<ツリー補完> name:<名前> due:<日付>`（L2 以上）。
          日付は `utils/parser.py` の既存パーサを使う（独自実装しない）
        - `/countdown` — 大会まで残り日数、マイルストーンごとに
          「期限まで N 日 / 進捗 X% / **必要ペース vs 実績ペース**」を表示
        - 遅延判定: 期限までの残り日数 × 直近の平均進捗速度 < 残り進捗 なら ⚠️。
          実績ペースは `progress_nodes.updated_at` と `layer_records` の履歴から算出し、
          履歴が足りないノードは「判定不能」と明示する（嘘の予測を出さない）
        - `COMPETITION_DATE` 未設定なら `/countdown` は設定方法を案内して終わる
      - **検証**: `tests/test_milestones.py` — 遅延あり / 余裕あり / 判定不能の3ケースで
        分類が正しいこと、日付境界（当日・過去日）で例外を出さないこと

- [ ] **F4-3** 週次アラートと `/report weekly` への統合。
      - **受入**:
        - `cogs/reminders.py` に週次ループを追加（既定は月曜 8:30 / `TZ` は既存定数を使う）。
          遅延マイルストーンがあるときだけ通知する（無いときは沈黙。通知疲れを避ける）
        - 通知先は既存の解決順（紐付け → `PROGRESS_DEFAULT_CHANNEL_ID` → ギルド既定）に従う
        - `/report weekly` に「大会まで N 日 / 遅延 M 件」の行を追加する
        - 1ギルドの失敗が他ギルドを止めない（`_safe_send` と同じ扱い）
        - `reminders_log` に記録し、同じ週に二重送信しない
      - **検証**: `tests/test_reminders_resilience.py` の方式に合わせ、
        送信失敗ギルドがあっても他ギルドへ送られること・二重送信しないこと

---

## Phase F5: 年度替わり（世代交代）

**背景**: サークルなら毎年必ず来るのに `archive` 相当の処理が一切無い。
`docs/GUIDE.md` に「年度替わりの引き継ぎ」の章がある以上、コマンド化する価値が高い。

**設計判断（先に固定する）**: **全テーブルに `season_id` を張らない**。
理由 —(1) 既存の全テーブルへの列追加と全クエリ改修は AGENTS.md 4（後方互換）に対する
リスクが大きすぎる、(2) 記録には `created_at` があり年度での絞り込みは日付範囲で足りる。
本フェーズで持たせるのは **`seasons`（年度の境界）と `members.status`（在籍状態）だけ**にする。

- [ ] **F5-1** スキーマ v14: `seasons` テーブルと `members` の在籍状態。
      - **変更ファイル**: `migrations/013_seasons.sql`, `utils/db.py`(v14),
        `repositories/season_repository.py`(新規), `repositories/member_repository.py`,
        `tests/test_seasons.py`(新規)
      - **受入**:
        - `seasons(season_id, guild_id, name, started_at, ended_at NULL, created_at)`、
          `UNIQUE (guild_id, name)`。「現役の年度」は `ended_at IS NULL` の最新1件
        - `members` に `status TEXT NOT NULL DEFAULT 'active'`（`active` / `alumni` / `inactive`）と
          `left_season TEXT NULL` を追加。**既存行はすべて `active` になる**
        - 年度名に既定値を持たない（`2026年度` も `第30代` もサークル次第）
      - **検証**: v13 からのマイグレーションで既存メンバーが `active` として保持されること

- [ ] **F5-2** `/season` コマンド群。
      - **受入**:
        - `/season list` / `/season new name:<名前>`（L4）。`new` は現年度に `ended_at` を打ってから作る
        - `/season rollover`（L4）— ウィザードで**継続 / 卒業を仕分ける**。
          Select で卒業者を選び、確認後に `status='alumni'` と `left_season` を設定、
          **班長フラグ（`is_leader`）を全員リセット**する
        - 卒業者は削除しない（過去の作業記録の担当者名が壊れるため）
        - 既定の一覧・検索（`/member profile` の候補、`/member support` など）は
          `status='active'` のみを対象にする。`include_alumni` 引数で明示的に含められる
        - 実行内容を `audit_log` に記録し、`bot-log` へ要約を投稿する
      - **検証**: `tests/test_seasons.py` — 仕分け後に active/alumni が正しい、
        班長フラグが全リセットされる、alumni が既定の検索から外れる、
        既存の `tests/test_teams_skills.py` / `test_multi_tenant.py` が落ちないこと
      - **注意**: **既存メンバーの status を勝手に変えない**（明示的な rollover のときだけ変える）

- [ ] **F5-3** 引き継ぎパッケージとドキュメント更新。
      - **受入**:
        - `/season rollover` の完了時に F2-2 のエクスポート ZIP を「年度スナップショット」として添付する
          （エクスポート処理は再実装せず共有する）
        - `docs/GUIDE.md` の「年度替わりの引き継ぎ」を新コマンド前提に書き換える
        - `README.md` の機能表に `/help` `/data` `/weight` `/countdown` `/season` を追加する
        - `docs/OPERATION.md` の全コマンド一覧・権限表を更新する
      - **検証**: ドキュメント内のコマンド名が実装と一致することを確認する
        （可能なら `tests/test_docs_commands.py` として、`bot.tree` の全コマンドが
        `docs/OPERATION.md` に載っていることを検査する回帰テストにする）

---

## 実施順の理由

1. **F0** — 検証ループを速くしてから始める（以降の全タスクの周回コストが下がる）
2. **F1** — スキーマ変更なし。ループの慣らしとして最適で、効果（導入直後の離脱防止）は即座
3. **F2** — 公開 bot として最も急ぐ。破壊的処理を含むが、対象は「退出済み or 明示要求」に限定
4. **F3** — 既存の木構造の素直な拡張。他機能へ影響しない
5. **F4** — F3 と同じ `progress` 周辺を触るため、文脈が温まっているうちに続ける
6. **F5** — `members` と既存の全一覧系に影響が及ぶ。最も慎重にやるべきなので最後

## 停止条件（`/acm-bot-loop` §4 の再掲。特に本表で起きやすいもの）

- **F2-4**: 全テーブル削除の網羅性に自信が持てない → 止めて対象テーブル一覧を提示する
- **F5-2**: 既存メンバーの status 移行で既存ギルドのデータを壊す恐れがある → 止めて確認する
- **F4-2**: 遅延判定のロジックが「判定不能」ばかりになる → ペース算出の定義を提示して確認する

---

## 完了ログ

| タスク | 完了内容 / 設計判断 / 申し送り |
|---|---|
| （初期化） | 本表を作成。`/help`・データ削除・重量管理・大会逆算・年度替わりの5機能を F0〜F5 に分解。スキーマは v11〜v14 を予約済み。次は F0-1（`loop_check` スクリプトの配置）から着手する |
| F0-1 | `scripts/loop_check.sh` / `.ps1` を `club-bot/scripts/` へ配置。**設計判断**: (1) `.ps1` は UTF-8 **BOM 付き**で保存する — Windows PowerShell 5.1 は BOM 無し UTF-8 を CP932 と誤読し、日本語メッセージでパースエラーになる（スキル同梱版はこれで起動不能だった）。(2) `-PytestArgs` は名前付きではなく `ValueFromRemainingArguments` で受ける — `powershell -File` 経由では配列パラメータが `-k ,progress` に壊れるため。これで `-k progress` と sh 版の書き味が揃う。(3) `.sh` は `${PYTEST_ARGS[@]+...}` で `set -u` 下の空配列展開を保護（bash 4.3 以前対策）。**申し送り**: 本ブランチは `main`(2c15601) 基点。スキル同梱の `.claude/skills/acm-bot-loop/scripts/` も同内容へ同期した（同梱版は上記 (1)(2) の不具合を抱えており PowerShell から起動できなかったため）。ruff の既定 select は `E4/E7/E9/F` のみで `E501`（行長）は検出されない — `line-length = 100` は formatter にしか効いていないので、行長を lint で縛りたいなら明示的な `select` 追加が要る（現状 32 件 / 16 ファイルが該当） |
