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

現行は `SCHEMA_VERSION = 15`（`migrations/014_discord_name_cache.sql` まで）。

| 版 | migration | フェーズ |
|---|---|---|
| v11 | `010_guild_lifecycle.sql` | F2（退出・削除） |
| v12 | `011_progress_weight.sql` | F3（重量管理） |
| v13 | `012_progress_milestones.sql` | F4（大会逆算） |
| v14 | `013_seasons.sql` | F5（年度替わり） |
| v15 | `014_discord_name_cache.sql` | F6（ダッシュボード改修） |

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

- [x] **F1-1** `/help` を `cogs/core.py`（または新規 `cogs/help.py`）に実装する。
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

- [x] **F1-2** `/help setup-status` — 初期設定の未完了チェックを追加する。
      （実装は `/setup-status`。理由は完了ログを参照）
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

- [x] **F2-1** スキーマ v11: ギルドのライフサイクルを記録する。
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

- [x] **F2-2** `/data export` — サーバー管理者が自サーバーの全データを ZIP（CSV 群）で受け取る。
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

- [x] **F2-3** `/data delete` — サーバー管理者が自サーバーのデータを自分で削除する。
      - **受入**:
        - L4 のみ。Modal で**サーバー名を打たせて確認**する（ボタン1つで消えない）
        - 実行すると `purge_after` を即時（= 現在時刻）に設定し、F2-4 のジョブで削除される。
          猶予期間中は `/data delete cancel` で取り消せる
        - 削除前に F2-2 のエクスポートを自動で添付し、「これが最後のバックアップ」と明示する
        - `audit_log` に `data.delete.requested` / `data.delete.cancelled` を記録する
      - **検証**: `tests/test_data_delete.py` — 確認文字列の不一致で中止、
        取り消しで `purge_after` が NULL に戻ること
      - **注意**: この時点では**実削除を行わない**（実削除は F2-4）。1周を小さく保つ

- [x] **F2-4** 自動パージジョブと文書の更新。
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

- [x] **F3-1** スキーマ v12: `progress_nodes` に `target_weight_g REAL NULL` /
      `actual_weight_g REAL NULL` を追加する。
      - **変更ファイル**: `migrations/011_progress_weight.sql`, `utils/db.py`(v12),
        `repositories/progress_repository.py`(更新許可列のホワイトリストに追加),
        `tests/test_progress_weight.py`(新規)
      - **受入**: 単位は **g 固定**（`_g` サフィックスで明示。単位設定は作らない）。
        既存行は NULL のまま壊れない。PostgreSQL では `DOUBLE PRECISION` になること
        （`to_pg_ddl` の REAL 変換が効いているか確認する）
      - **検証**: v11 DB からのマイグレーションで既存 `progress_nodes` が保持されること

- [x] **F3-2** `services/progress_tree.py` に重量集計を追加する。
      - **受入**:
        - 集計規則: **実測値が入っていればそれを採用**、無ければ子ノードの実測合計を積み上げる
          （進捗率の `aggregated` と同じ再帰の中で計算する）
        - 目標重量も同じ規則で集計する
        - 「実測が入っているノードの割合」も返す（見積もりの確度を示すため）
        - 孤児・循環ノードは既存と同じく除外され、無限ループしない
      - **検証**: `tests/test_progress_weight.py` — (1) 葉のみ実測 → 親が合計になる、
        (2) 親に実測がある → 子の合計ではなく親の実測が勝つ、(3) 循環データで停止する

- [x] **F3-3** `/weight` コマンド群と `/progress view` への表示。
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

- [x] **F3-4** Web ダッシュボードの progress グリッドへ重量列を追加する。
      - **受入**: `repositories/table_repository.py` のホワイトリストに 2 列を追加し、
        班長以上は編集可・CSV にも出力される。`guild_id` スコープの検証は既存の仕組みに乗る
      - **検証**: `tests/test_dashboard_tables.py` に追記（`dashboard/requirements.txt` 未導入だと
        skip される点に注意）

---

## Phase F4: 大会からの逆算アラート

**背景**: 進捗率は見えるが「間に合うのか」が見えない。大会日とマイルストーンから
必要ペースを逆算し、遅延を先に知らせる。

- [x] **F4-1** スキーマ v13: `progress_milestones` テーブルと大会日の設定。
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

- [x] **F4-2** `/milestone add|remove|list` と `/countdown`。
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

- [x] **F4-3** 週次アラートと `/report weekly` への統合。
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

- [x] **F5-1** スキーマ v14: `seasons` テーブルと `members` の在籍状態。
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

- [x] **F5-2** `/season` コマンド群。
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

- [x] **F5-3** 引き継ぎパッケージとドキュメント更新。
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

## Phase F6: ダッシュボード改修（シートタブ・ID 表示廃止・JST 秒表示）

**背景**: 表グリッドは全予定の出欠・全桁の記録が1枚に混ざり、人物・チャンネルは
Discord の ID が生のまま、日時は ISO 文字列のまま表示されていた。

- [x] **F6-1** ダッシュボードのシート切替と表示解決を実装する。
      - **変更ファイル**: `dashboard/display.py`(新規), `dashboard/routers/tables.py`,
        `dashboard/static/app.js` / `style.css`, `repositories/table_repository.py`,
        `repositories/schedule_repository.py`, `repositories/name_cache_repository.py`(新規),
        `cogs/name_cache.py`(新規), `bot.py`, `migrations/014_discord_name_cache.sql`,
        `utils/db.py`(v15), `tests/test_dashboard_display.py` /
        `test_dashboard_views.py` / `test_name_cache.py`(すべて新規)
      - **受入**:
        - 出欠回答はタブ1つ = 予定1件、桁巻き記録はタブ1つ = 桁1つ。
          タブは共通コンポーネント（`renderSheetTabs` / `.sheetbar`）で、
          リロード無しで切り替わり、開催日時の降順・横スクロール・
          選択中の明示・0 件時の空状態を備える
        - 人物は「名前キャッシュ（ニックネーム → グローバル表示名 →
          ユーザー名）→ members 台帳 → ID フォールバック」で解決し、
          チャンネルは `#名前`（削除済みはフォールバック）で表示する。
          DB は従来どおり ID を保持し、解決は表示層（`_display`）で行う
        - すべての日時は Asia/Tokyo・秒単位で表示（naive な既存値は
          保存規約どおりローカル TZ として解釈。DB の書き換えはしない）
        - 名前の解決はギルド単位の一括読みで N+1 を作らない。
          キャッシュは bot がギルドイベントから同期する（スキーマ v15）
      - **検証**: `tests/test_dashboard_views.py`（シート絞り込み・
        ギルドスコープ・空状態・表示解決・CSV・PATCH 応答）、
        `tests/test_dashboard_display.py`（JST 秒・日付跨ぎ・フォールバック）、
        `tests/test_name_cache.py`（同期・優先順位・guild_id スコープ）

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
| F1-1 | `cogs/help.py` を新設し `/help` を実装。一覧は `bot.tree.walk_commands()` から動的生成し、カテゴリは Cog 名から導出する（`CATEGORY_BY_COG`）。**設計判断**: (1) 必要権限が `require()` のクロージャに閉じていて外から読めなかったため、`utils/permissions.py` に `REQUIRED_LEVEL_ATTR` と `command_required_level()` を追加し、`require()` / `require_manage_guild_or()` / `is_admin` が必要レベルを属性として持つようにした（属性を足すだけなので既存の権限判定の挙動は不変）。(2) コマンド一覧は Embed の field ではなく description に列挙する — コマンドが増えても 25 field 制限に当たらないため。溢れる場合は「ほか N 件」に畳む。(3) カテゴリ未登録の Cog は「その他」へ落ち、`test_no_uncategorized_command_remains` と `test_every_loaded_cog_with_commands_is_mapped` が落ちる（実際に `Tasks` を外して2件落ちることを確認済み）。**申し送り**: 現在 75 コマンド / 12 Cog。新しい Cog を足したら `CATEGORY_BY_COG` への登録が要る |
| F1-2 | 初期設定チェックを **`/setup-status`** として実装（表の `/help setup-status` から名前を変更）。**設計判断**: Discord のアプリコマンドは、サブコマンドを持つグループ自身を実行できない。`setup-status` を `/help` のサブコマンドにすると `/help` 単独実行ができなくなり、F1-1 の受入基準「`/help` でカテゴリ選択メニューを表示」と両立しない。Phase F1 の目的が「約80コマンドへの入口を作る」ことなので、入口である `/help` を単独コマンドとして残し、設定チェックを独立コマンドへ分けた。`/help` の冒頭に `/setup-status` への導線を置いてある。判定は `config.for_guild()` と `list_teams()` / `list_active()` の件数のみで、あるべき初期値をコードに持たない。**申し送り**: `README.md` の機能表と `docs/OPERATION.md` の全コマンド一覧への追記は **F5-3 の担当**なので本イテレーションでは行っていない |
| F2-1 | スキーマ v11。`guilds` に `left_at` / `purge_after` を追加し、`on_guild_remove` で記録する。**設計判断**: 退出しただけでは消さない（誤キックや一時的な離脱から再招待で復帰できるようにする）。クリアは `on_guild_join` ではなく `_ensure_guild_setup()` に置いた — 起動時の `on_ready` からも通るため、Bot 停止中に退出→再参加した場合も復旧する。猶予日数は `DEFAULT_DATA_RETENTION_DAYS = 30`、ギルド別設定 `DATA_RETENTION_DAYS` で上書き（負値は 0 に丸める）。**申し送り**: 既存ギルドは列追加後も `left_at IS NULL` = 参加中のままで、マイグレーションで削除対象にならないことをテストで固定した |
| F2-2 | `/data export`。**設計判断**: 出力対象を `table_repository.TABLES` のホワイトリストに限ったので、`guild_id` 列も Todoist トークンも**構造的に**出てこない（除外リストを手で持たない）。CSV 生成は `rows_to_csv()` に切り出し、BOM 付与と数式インジェクション対策（先頭 `=+-@` にシングルクォート）をここに集約した。全件取得には `list_all_rows()` を追加（`list_rows` は表示用に 500 件上限のため）。**申し送り**: 未マージの `fix/code-audit-v2` がダッシュボード側に同等の `_csv_safe` を持つ。マージ時に `rows_to_csv` へ寄せて重複を解消すること |
| F2-3 | `/data delete` と **`/data delete-cancel`**（表の `/data delete cancel` から変更）。**設計判断**: F1-2 と同じ Discord の制約 — サブコマンドを持つグループ自身は実行できないため、`/data delete` を実行可能にしたまま `cancel` をサブコマンドにはできない。確認は Modal でサーバー名の完全一致を要求する。実削除はせず `purge_after` を現在時刻にするだけで、確認が通った時点で最後のバックアップ ZIP を添付する。`request_purge` は `left_at` を立てない（参加したまま削除を申告した状態と、退出による削除予定を区別するため） |
| F2-4 | 日次パージジョブ（毎日 04:00）とドキュメント更新。**設計判断**: 削除対象は `TABLE_DDL` から導出し、**順序は TABLE_DDL の逆順**（`guilds` だけ最後）。`schedule_options → schedules`、`schedule_votes → schedule_options` に `ON DELETE CASCADE` の外部キーがあり、親を先に消すと子が連鎖削除されて `DELETE` の rowcount に現れず、削除件数のログが実際より少なくなるため。期限判定は ISO 文字列の辞書順ではなく `from_iso()` で行う（タイムゾーン表記が混ざると誤るため）。解釈できない `purge_after` は対象外にする（消さない側に倒す）。削除後は `config.invalidate_guild()` でキャッシュを捨てる。通知先は設定ごと消えるので**削除前に**解決しておく。**検証**: `TABLE_DDL` に仮のテーブルを足すと網羅テスト2件が実際に落ちることを確認済み。**申し送り**: `docs/PRIVACY.md` / `docs/TERMS.md` / `README.md` の「キックしても消えない・運営者へ連絡」を全面的に書き換えた（運営者への連絡は不要になった） |
| F3-1 | スキーマ v12。`progress_nodes` に `target_weight_g` / `actual_weight_g` を追加（migrations/011）。**設計判断**: `ALTER TABLE ... ADD COLUMN` は `to_pg_ddl()` を通らないため、マイグレーション側でドライバ別に型を指定する（PostgreSQL の `REAL` は 4 バイトで SQLite の `REAL` より精度が低い）。新規 DB は `TABLE_DDL_PG` 経由なので変換が効く。**申し送り**: `NODE_COLUMNS`（`SELECT *` を使わない取得列の定数）への追加を忘れると、DB に値が入っていてもツリーまで流れてこない。実際に取りこぼして 1 周使った |
| F3-2 | `services/progress_tree.py` に重量集計を追加。**設計判断**: (1) 集計は進捗率と同じ後行順ループの中で行う（木を2回歩かない）。(2) **未計測は `None` のままにし `0.0` へ丸めない** — 「0 g」と「未計測」を混同すると合計が過小に出るため。(3) 自ノードに値があれば子の合計より優先する（実測のほうが見積もりより信用できる）。(4) 確度を示すため `WeightSummary.fill_rate`（実測が入っているノードの割合）を返す。孤児・循環は既存の `build_tree` が除外するので重量集計にも自動的に効く |
| F3-3 | `/weight set|view|top` と `/progress view` への重量行。**設計判断**: 新しい Cog を作らず `cogs/progress.py` に置いた — ツリーのオートコンプリート（`_node_autocomplete`）と `load_tree()` をそのまま共有できるため。重量が未設定のサーバーでは `weight_line()` が空文字を返し、`/progress view` の表示は従来どおりになる（回帰テストで固定）。`/weight top` は親ノードも対象に含まれる（機体全体としての超過が最上位に出る）。画像は作らずテキスト表現のみ |
| F3-4 | ダッシュボードの progress グリッドに重量2列を追加。ホワイトリストに足すだけで、閲覧・編集・CSV 出力・`guild_id` スコープはすべて既存の仕組みに乗る。**申し送り**: `test_dashboard_*` は `dashboard/requirements.txt` が未導入だと丸ごと skip される。導入済みの環境で回すこと |
| F4-1 | スキーマ v13。`progress_milestones` を追加（migrations/012）。大会日はテーブルに持たずギルド別設定 `COMPETITION_DATE` に置き、**既定値を持たない**。`node_id` に外部キーを張らないのは `progress_nodes` と同じ方針で、存在しないノードを指す行は表示側で除外する。**申し送り**: テーブルを足したことで F2-4 の網羅テスト（`test_seed_covers_every_purge_target`）が実際に落ちた。削除処理は `TABLE_DDL` から導出しているので実装の変更は不要で、テストの seed に1行足すだけで済んだ — 消し漏れ検出が設計どおり働いている |
| F4-2 | `services/milestone_service.py`（純関数）＋ `/milestone add\|remove\|list` と `/countdown`。**ペース算出の定義（停止条件だった箇所）**: 進捗の履歴テーブルが無く `progress_nodes` は現在値と `created_at` / `updated_at` しか持たないため、実績ペース = 集計進捗 ÷（更新日 − 作成日）とした。「作られてから最後に動くまでの平均」であり停滞期間は含まない。判定不能は (1) 作成日と更新日が同じ、(2) 日時が記録されていない、の2つだけに絞ったので実運用のツリーではほぼ判定できる（`test_most_nodes_are_judgeable_in_a_realistic_tree` で固定）。桁巻きに紐付いたノードは `layer_records` に作業日が残るため、そちらを優先して実際の作業ペースを使う。判定できないものは必要ペースだけを示し、**予測は出さない**。日付境界は当日未完 = 遅延、過去日 = 期限超過、進捗100% = 期限に関係なく完了 |
| F4-3 | 週次アラート（月曜 8:30）と `/report weekly` への統合。**設計判断**: `tasks.loop(time=...)` は毎日発火するので曜日で絞る。**遅延が無い週は沈黙する** — 毎週「問題ありません」を送ると通知が読まれなくなるため。二重送信の防止は `reminders_log` に ISO 週キー（`milestone:2026-W33`）を記録して判定する。通知先は既存の `resolve_default_channel_id()`（紐付け → `PROGRESS_DEFAULT_CHANNEL_ID` → ギルド既定）に乗せた。1ギルドの送信失敗が他ギルドを止めないことをテストで固定 |
| F5-1 | スキーマ v14。`seasons` と `members.status` / `left_season`（migrations/013）。**後方互換がこのフェーズの主眼**: `status` は `NOT NULL DEFAULT 'active'` で追加するため、**既存メンバーは全員そのまま active** になり、移行で誰も勝手に卒業扱いにならない（`test_existing_members_all_become_active` と、他の全列が保持されることを `test_existing_member_columns_are_preserved` で固定）。表の設計判断どおり全テーブルに `season_id` は張っていない |
| F5-2 | `/season list\|new\|rollover` と在籍状態の反映。**設計判断**: (1) `list_members()` に `include_alumni`（既定 False）を足し、既定の一覧・検索から卒業者を外した。`search_support()` はこれを経由するので自動的に効く。既存テスト（`test_teams_skills` / `test_multi_tenant` 含む）が全て通ることを確認済み。(2) rollover の確定処理は `services/season_service.perform_rollover()` に切り出して Discord なしでテストできるようにした。(3) **選ばれなかったメンバーの status には触れない**（`test_rollover_does_not_touch_unselected_members`）。(4) 卒業者選択は `discord.ui.UserSelect`（上限25名）。**申し送り**: 一度に26名以上を卒業させる場合はコマンドを複数回実行する必要がある |
| F5-3 | 年度スナップショットとドキュメント更新。`/season rollover` の完了時に `cogs/data.build_export_zip()` を**共有**して ZIP を添付する（エクスポートを再実装しない）。`docs/GUIDE.md` の年度替わりの章を `/season rollover` 前提へ全面的に書き換え、`README.md` の機能表に `/help` `/weight` `/countdown` `/season` `/data` を追加、`docs/OPERATION.md` に不足していた **26 コマンド**を追記した（新機能15件のほか、`/set_*` `/settings_*` `/member setup` `/schedule edit-deadline` など既存の記載漏れ11件も含む）。**回帰テスト**: `tests/test_docs_commands.py` が `bot.tree` の全89コマンドと `OPERATION.md` を突き合わせ、記載漏れと逆に実装から消えたコマンドの両方を検出する |
| F6-1 | ダッシュボードのシートタブ・ID 表示廃止・JST 秒表示（スキーマ v15）。**設計判断**: (1) ダッシュボードは設計上 Bot トークンを持たないため、名前解決は Discord API ではなく **bot が同期する `discord_name_cache` テーブル**で行う（`cogs/name_cache.py` が起動時全同期＋イベント差分。ユーザー行は退会後も残し「最後に知られた名前」を出す。チャンネル行は削除で消しフォールバック表示に落とす）。(2) シート切替は新規 API を作らず既存 `/tables/{key}` に `?sheet=` を足し、絞り込み条件はリポジトリ側で固定（votes は options 経由の副問い合わせ、桁は `keta = ?`。編集 PATCH・CSV・監査は既存の仕組みへ相乗り）。(3) 行の生値（ID・ISO）は変えず `_display` を添える方式にし、編集入力は従来どおり生値で行う。(4) 予定タブの「開催日時」は最初の候補日（無ければ締切）。(5) naive な既存日時は保存規約（utils/parser.TZ）の壁時計として解釈し、**DB の書き換えマイグレーションはしない**。(6) ダッシュボード CSV は画面と同じ表示値で出す（生値の完全バックアップは `/data export` が担当）。**申し送り**: ロール ID（班長ロール等）は未解決のまま（キャッシュ対象は user/channel のみ）。設定画面 API（settings ルーター）はフロント未実装のため対象外。キャッシュが空の期間（v15 適用直後〜bot 初回同期まで）は members 台帳名で表示される |
