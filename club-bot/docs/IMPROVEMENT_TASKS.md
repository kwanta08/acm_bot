# 改善タスク管理（G0〜G4）

`docs/PUBLIC_RELEASE_TASKS.md` → `docs/FEATURE_TASKS.md` に続く第3の管理表。
根拠は `docs/IMPROVEMENT_REPORT.md`（2026-08-20 の全コード分析）。
`/acm-bot-loop` で1タスクずつ回すために分解してある。

## 運用ルール

- 実装は **必ず `/acm-bot-loop` の手順**（受入基準確定 → 最小実装 → ruff + pytest → 自己修正）で回す。
  「実装したので確認してください」で止めない。全パスして初めて完了
- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
- タスクごとに `fix/<タスクID小文字>` または `feat/<タスクID小文字>` ブランチを切る（ADR 0014: 1タスク＝1ブランチ＝1PR）
- 完了時にチェックを入れ、末尾の完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
- **フェーズ順序を守る**（G0 → G1 → G2 → G3 → G4）。G0 は全部の前提
- 【人間タスク】はエージェントが飛ばす
- コミット・push は**ユーザーから明示指示があったときのみ**

## 全タスク共通の受入基準（AGENTS.md より。各タスクで再掲しない）

- 新規データ・新規設定はすべて `guild_id` スコープ。ギルド別設定は `config.for_guild(guild_id)` 経由
- コマンドは `interaction.guild.id` でスコープし、DM 実行は `ensure_guild()` で拒否する
- Discord API 呼び出しは `discord.HTTPException` を捕捉する
- スキーマ変更は **同じイテレーション内で** `migrations/NNN_*.sql` と `utils/db.py` の
  `_migrate_vNN_*()` ＋ `SCHEMA_VERSION` 更新をセットで行う
- 班名・チャンネル・ロール・機体名・桁構成をコードに埋め込まない
- 実装とドキュメント（`README.md` / `docs/`）が矛盾したら両方直す
- `ruff check .` と `python -m pytest tests/ -q` がフルセットで緑

## この表に固有の受入基準

- **ADR に反する変更をしない。** 既存の設計判断（ClaudeVault `projects/acm_bot/decisions/`）と
  衝突する場合は、実装せず完了ログに「ADR NNNN と衝突。判断を仰ぐ」と書いて止まる
- **ADR を更新・失効させるタスクには、そのことをタスク本文に明記してある。**
  該当タスクの完了ログには、新しい ADR の草案（文脈 / 選択肢 / 決定 / 理由 / 影響範囲 / 覆す条件）を書く
- 修正した不具合が ClaudeVault の gotcha に載っている場合、完了ログにノート名を書く（人間が `unfixed` タグを外す）

## スキーマバージョンの割り当て（衝突防止）

現行は `SCHEMA_VERSION = 15`（`migrations/014_discord_name_cache.sql` まで）。

| 版 | migration | タスク |
|---|---|---|
| v16 | `015_schedule_confirmed.sql` | G3-4（確定日程） |
| v17 | `016_progress_snapshots.sql` | G4-1（進捗履歴） |
| v18 | `017_stock.sql` | G4-2（在庫・工具） |
| v19 | `018_incidents.sql` | G4-4（ヒヤリハット） |

---

## Phase G0: 足場を整える（実装の前提。ここを飛ばすと以降の diff が読めない）

- [x] **G0-1** 【人間タスク】作業ツリーの CRLF 汚染を解消する。
      現状 `git status` が 161 ファイルを変更ありと報告するが、`git diff -w --stat` では
      `CLAUDE.md` の7行追加だけ。**全ファイルが CRLF に書き換わっているだけ**で実質差分はない。
      `core.autocrlf` は未設定、`.gitattributes` は `*.sh text eol=lf` の1行のみ。
      - **手順**: `CLAUDE.md` の実差分を退避 → `git config core.autocrlf false` →
        `.gitattributes` に `* text=auto eol=lf` を追加 → `git checkout -- .` で戻す →
        `CLAUDE.md` の差分を戻す → `git status` が clean になることを確認
      - **受入**: `git status --porcelain` が `CLAUDE.md` 以外を出さない
      - **注意**: エージェントにやらせない。`git checkout -- .` は取り返しがつかない

- [x] **G0-2** `fix/code-audit-v2` を main（または現行ブランチ）へ取り込む。
      未マージのまま放置されており、**ClaudeVault の gotcha 3件が「いま踏むと未修正」のままになっている**。
      現行コードで未適用であることを確認済み（`descendant_ids` 不在 / `InvalidValueError` 不在 /
      `cogs/progress.py:1106` が `@require(Level.L2)` のまま）。
      - 含まれるコミット: `8b9c0f4`（ダッシュボードの値検証と CSV 出力）/ `1b741d1`（`/progress edit` の子孫親付け防止）/
        `2d044ce`（`/progress setup` を Manage Server でも実行可に）/ `a3b97e4`（定期通知ループの失敗を1ギルドに閉じ込め）/
        `d9996e7`（絵文字IDを `_clean` 経由で読む）
      - **受入**: マージ後に `ruff check .` と `pytest tests/ -q` がフルセットで緑。
        `grep -n "descendant_ids" services/progress_tree.py` がヒットする。
        `cogs/progress.py` の `progress_setup` が `@require_manage_guild_or(...)` になっている
      - **検証**: `tests/test_permissions.py` が「ヘルパ」ではなく `bot.tree` を走査してコマンドの
        権限を検査していること（`2d044ce` の回帰テスト）を目視で確認する
      - **申し送り**: 取り込み後、`docs/IMPROVEMENT_REPORT.md` の P1-12「値の検証がサーバー側に無い」と
        「CSV が500行で切り捨て」が解消済みか再確認し、残っていれば G1 に起票する
      - **gotcha**: `progress-subtree-disappears` / `progress-stops-after-dashboard-edit` /
        `test-asserts-permission-but-decorator-missing`（いずれも `unfixed` タグを外せる）

- [x]  G0-3 【人間タスク】PostgreSQL でダッシュボードの編集経路を検証する。 結果: 落ちた。 VPS の clubbot_test（PostgreSQL 16）で scripts/check_g0_3_pg.py を実行。
      [1] asyncpg へ直接 str を渡す: NG
          asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
          ('str' object cannot be interpreted as an integer)
      [2] 本物のコード経路（Database → TableRepository.get_row）: NG
          asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
          ('str' object cannot be interpreted as an integer)
  - **本物のコード経路でも落ちている**ので、本番のダッシュボードは実際に壊れている
  - 当初 `CLUB_TEST_PG_DSN=... pytest tests/test_dashboard_edit.py` で検証すると書いていたが、
    **このテストは `CLUB_TEST_PG_DSN` を一度も読まず SQLite 決め打ち**のため、
    実行しても PG 経路は1行も通らない（緑が出ても「測れていない」だけ）。検証方法を差し替えた
  - `docs/IMPROVEMENT_REPORT.md` の P0-8 は【要検証】から**確定**へ格上げ
  - 申し送り: G1-0 として起票

- [x] **G0-4** `ruff check .` の既存エラー13件を解消する。
      **この項目は当初この表に無く、G0-2 の作業中に発見して追記した。**
      CI は `ruff check .` を最新版で回すが、**main 時点で E741 が9件・E402 が4件**出ており、
      「ruff が緑」というループの前提が最初から成立していなかった。
      - **受入**: `ruff check .` が `All checks passed!` になる
      - **内容**: E741 は内包表記の `l` を `link` / `label` / `row` へ改名（ロジック不変。
        `cogs/reminders.py` / `cogs/tasks.py` / `services/todoist_service.py` /
        `tests/test_guild_foundation.py`）。E402 は `tests/test_dashboard_app.py` の
        `fastapi = pytest.importorskip(...)` から代入をやめ、他のダッシュボードテストと同じ形にそろえた

---

## Phase G1: 沈黙障害と権限（全ギルドに効く。すべて小さい）

- [x] **G1-0** ダッシュボードの行 ID を主キーの型へ変換する（**最優先**。G0-3 で実測確定）。
      ルータは URL の `row_id` を `str` で受け、`utils/db.py:827` の `_prepare()` は
      `?` → `$n` の書き換えしかしないため、asyncpg へ str が bigint 引数として渡る。
      **本番（PostgreSQL）のダッシュボードは編集経路が実際に壊れている。**
      SQLite は型親和性で `'5'` を 5 と読むため CI では出ない。
      - **受入**: `TableSpec` が主キーの型（`pk_type`）を持ち、`get_row` / `update_row` が
        それに沿って正規化する。**ルータ側で `int()` しない**（型は表の定義側が持つ）。
        変換できない `row_id` は 404（500 にしない）。PostgreSQL で PATCH が通る
      - **検証**: `tests/test_table_row_id_coercion.py`（新規）で型変換と拒否を検査。
        SQLite では再現しないため「行が引けたか」ではなく**ドライバへ渡る値そのもの**を見る。
        PG 結合は `tests/test_db_postgres.py` に `CLUB_TEST_PG_DSN` 条件付きで追加
      - **注意**: 変換の失敗は `get_row`（`update_row` より前）で起こす。
        部分書き込みが発生しない現状の性質を壊さない

- [x] **G1-1** Embed fields の25個上限を超える一覧を修正する。
      `cogs/schedule.py:384`（`/schedule list-closed`）、`cogs/schedule.py:338`（`/schedule list`）、
      `cogs/layer_tracking.py:201`（`/layer status`）が無制限に `add_field` している。
      `cogs/tasks.py:801` と `cogs/progress.py:262` は `[:25]` で正しく切っており、単なる漏れ。
      - **受入**: 30件のデータを入れても `discord.HTTPException` にならず、
        「他 N 件」が本文に出る。`ScheduleRepository.list_closed_schedules` に `limit` 引数を追加する
      - **検証**: `tests/test_schedule_limits.py`（新規）で、リポジトリが30件返す状況を作り
        `len(embed.fields) <= 25` と残件表示を検査する
      - **参考**: 打ち切り告知は `cogs/help.py:135` の `_join_within` が既に正しい実装なので、
        `utils/embeds.py` へ移して共用する（G1-5 と統合してよい）

- [x] **G1-2** 進捗の通知先解決を ADR 0023 の記述どおりに直す。
      ADR 0023「影響範囲」は「通知先は `resolve_default_channel_id()`（紐付け →
      `PROGRESS_DEFAULT_CHANNEL_ID` → ギルド既定）に乗せた」と書いているが、
      `services/progress_sync_service.py:195-198` の実装は `PROGRESS_DEFAULT_CHANNEL_ID` しか読まず
      **ギルド既定へのフォールバックが無い**。`cogs/reminders.py:293-298` は `gconf` を直前で
      取得しているのに使っていない。結果、週次マイルストーン警告が `/setup` しかしていない
      サーバーへ永久に届かない（`/setup` が書くのは別綴りの `DEFAULT_PROGRESS_CHANNEL_ID`）。
      - **受入**: `resolve_default_channel_id()` が `PROGRESS_DEFAULT_CHANNEL_ID` →
        `DEFAULT_PROGRESS_CHANNEL_ID` → `DEFAULT_TASK_CHANNEL_ID` の順に解決する。
        どれも無いギルドでは ADR 0023 のとおり**沈黙してよい**が、
        `bot.log_to_channel` に理由を1行出す
      - **検証**: `tests/test_settings_notify.py` に、旧キーのみ／新キーのみ／両方無しの3ケースを追加
      - **注意**: **ADR 0023 は覆さない。** 実装を ADR に合わせる方向。
        `cogs/progress.py:851-859` は既に3段フォールバックを持っているので、そちらに揃える
      - **申し送り**: キーの一本化（`DEFAULT_PROGRESS_CHANNEL_ID` へ寄せる）は破壊的なので G3-1 で扱う

- [x] **G1-3** `/setup-status` の検査項目を実効設定に差し替える。
      現状 `cogs/help.py:222-234` が検査する `DEFAULT_ANNOUNCE_CHANNEL_ID` は
      **送信に一度も使われていない**（`grep` で確認済み。`config.py` に読み込まれるだけ）。
      逆に、実際に通知が飛ぶ `DEFAULT_TASK_CHANNEL_ID` と、L2 判定の唯一の根拠である
      `LEADER_ROLE_IDS`（`utils/permissions.py:74-75`）は検査対象外。
      - **受入**: `collect_setup_status()` が タスク通知チャンネル / ログチャンネル / 管理者ロール /
        **班長ロール** / 班 / 桁 / 大会日 を検査する。未設定項目には具体的なコマンド名を hint に出す
      - **検証**: `tests/test_help.py` に「班長ロール未設定なら done=False」のケースを追加
      - **注意**: 「あるべき初期値をコードに持たない」方針（件数が0かどうかだけ見る）は維持する

- [x] **G1-4** 権限エラーに「誰に頼めばいいか」を出す。
      `utils/permissions.py:110-115` の `PermissionDenied` は `この操作には L2 以上の権限が必要です。`
      としか言わない。L1〜L4 の意味は**ソースのコメントにしか存在しない**（`grep "一般メンバー" cogs/` は0件）。
      `gconf` には `admin_role_id` / `exec_role_id` / `leader_role_ids` が揃っている。
      - **受入**: エラーが「この操作は**班長以上**が実行できます（あなたは一般メンバー）。
        依頼先: @班長 @幹部」の形になる。該当ロールが未設定なら
        「班長ロールが未設定です。管理者に `/setup` を依頼してください」を出す
      - **検証**: `tests/test_permissions.py` にレベル→ラベルの写像と、
        ロール未設定時の文面を検査するケースを追加
      - **注意**: `cogs/help.py:126,190` の `f"L{int(required)} 以上"` も同じラベルに揃える。
        ロールメンションは `allowed_mentions=discord.AllowedMentions.none()` で通知を飛ばさない

- [x] **G1-5** 一覧の打ち切り告知と内部名表示を統一する。
      告知なしで切っている箇所: `cogs/teams.py:181`、`cogs/progress.py:1416`、`cogs/members.py:536`、
      **`cogs/reports.py:206`（`/report attendance-rate` は26件目以降が集計から落ちるので数字が誤る）**。
      加えて `cogs/progress.py:991-996` の `/progress edit` は DB のカラム名をそのまま表示し
      （`- manual_progress: None` / `- parent_id: n_3f9a01b2c4`）、`cogs/reports.py:186` は
      生の18桁ユーザーIDを出す（`discord_name_cache` があるのに使っていない）。
      - **受入**: `utils/embeds.py` に `add_truncation_note()` と表示ラベル写像を追加し、
        上記4箇所＋`/progress edit`＋`/report audit` が使う。
        `/report attendance-rate` は**打ち切らずに全件集計**する（表示だけ絞る）
      - **検証**: `tests/test_reports.py`（新規）で30件の投票から正しい出席率が出ることを検査
      - **⚠️ 一部未実施**: 打ち切り告知と `/report attendance-rate` の全件集計は完了したが、
        **内部名表示の統一（`/progress edit` の DB カラム名・`/report audit` の生ユーザーID）は
        手付かず**。完了ログの申し送りから G2-8 として再起票する

- [x] **G1-6** ダッシュボードで班のロールIDを編集できないようにする。
      `repositories/table_repository.py:137-139` の `leader_role_id` / `member_role_id` /
      `secondary_role_id` が `editable=True` で、必要権限は L2（`dashboard/security.py:134`）。
      一方 Discord の `/team-role` は管理者限定（`cogs/teams.py:212`）。
      `cogs/members.py:83-113` の `_sync_roles` が `member_role_id` をそのまま `add_roles()` に使い、
      これを叩く `/member assign-team` も L2（`cogs/members.py:201`）なので、
      **班のロールIDを管理者ロールのIDに書き換えてから自分に割り当てると L4 へ昇格できる**。
      - **受入**: 上記3列と `members.is_leader`（`table_repository.py:120`）が `editable=False` になる。
        ダッシュボードから PATCH しても 400（`UnknownColumnError`）で拒否される
      - **検証**: `tests/test_dashboard_edit.py` に、3列への PATCH が拒否されることを検査するケースを追加
      - **注意**: ADR 0016（ホワイトリスト）の徹底であって方針変更ではない。
        `dashboard/routers/settings.py:65-82` の設定読み取りが L1 で `ADMIN_ROLE_ID` を返す点も
        同時に絞る（`scope.level >= Level.L4` のときだけ値を返す）

- [x] **G1-7** 他人のタスクを勝手に完了・削除できないようにする。
      `cogs/tasks.py:279-292` の `/task done` は `@require(Level.L1)` で、担当者・作成者との照合が無い。
      ID を打ち間違えると他班のタスクが完了扱いになり Todoist からも消える（`:296`）。
      `cogs/members.py:442-486` の `/member skill add|remove` も L1 で他人に付け外しできる。
      - **受入**: `/task done` `/task delete` `/task priority` は「担当者本人 or 作成者 or L2 以上」でのみ実行可。
        拒否時は担当者をメンションして「本人か班長に依頼してください」と案内する。
        `/member skill add|remove` の `user` 指定は L2 以上に限定
      - **検証**: `tests/test_tasks_permission.py`（新規）で、第三者・担当者・班長の3パターンを検査

- [ ] **G1-8** ~~`deploy.yml` にダッシュボードの更新を追加する。~~
      **🚫 取り下げ（2026-08-21）。`origin/main` の `4dc33c9`（2026-08-13）で解決済みだった。**
      指摘の根拠にした `deploy.yml` は手元の `feat/dashboard-ui-fixes` にあった `fd86fb7`（08-06 / 30行）で、
      `origin/main` 版（43行）には `dashboard/requirements.txt` の install、
      `club-bot-dashboard.service` の restart、`ReadWritePaths` 用ディレクトリ作成まで入っている。
      **分析前に `git fetch` していなかったのが原因**（`docs/IMPROVEMENT_REPORT.md` の P0-7 も取り下げ）。
      - **残る作業**: 「restart 前に `pg_dump -Fc` を取る」の追記（`docs/DASHBOARD_SETUP.md` §11 /
        `docs/OPERATION.md` §8.2）は未実施。申し送りから G3-6 として再起票する
      - 以下は取り下げた元の記述:
      `.github/workflows/deploy.yml:24-30` は bot しか restart していないのに、
      `deploy/club-bot-dashboard.service:9-11` は「deploy.yml も同じ前提でデプロイします」と書いている。
      bot 側の再起動でスキーマだけ新版になり、**旧コードのダッシュボードが新スキーマを触る**状態が続く。
      - **受入**: `pip install -r requirements.txt -r dashboard/requirements.txt` と
        `systemctl restart club-bot.service club-bot-dashboard.service` と
        `curl -fsS http://127.0.0.1:8000/healthz` のスモークテストが入る
      - **検証**: 【人間タスク】実際に push して両ユニットが再起動することを確認。
        `sudoers` にダッシュボードのユニットが許可されているかを先に確認する
      - **注意**: `docs/DASHBOARD_SETUP.md` §11 と `docs/OPERATION.md` §8.2 に
        「restart 前に `pg_dump -Fc` を取る」を追記する（マイグレーションは down が無い）

- [x] **G1-9** `_coerce()` が INTEGER 列に float を返す（G1-0 と同じ失敗の書き込み側）。
      `repositories/table_repository.py` の `number` 分岐は `int(text)` → 失敗したら
      `float(text)` の順に試すため、INTEGER 列にも float が入る。さらに
      `isinstance(value, (int, float)): return value` により、JSON ボディの
      `{"priority": 2.7}` は変換すら経ずに素通りする。
      asyncpg は int8 の引数に float を渡しても `DataError` になるため、
      **G1-0 とまったく同じ形で本番だけ 500 になる**（SQLite では保存できてしまい、
      そのあと bot 側の読み取りが壊れる — gotcha `progress-stops-after-dashboard-edit` と同型）。

      **`number` の既定を int にはできない。** 編集できる `number` 列は整数と実数が混在する:

      | 列 | DDL | 受けるべき型 |
      |---|---|---|
      | `tasks.priority` | INTEGER | int のみ |
      | `layer_records.minutes` | INTEGER | int のみ |
      | `progress.sort_order` | **REAL** | float 可 |
      | `progress.target_weight_g` | **REAL** | float 可 |
      | `progress.actual_weight_g` | **REAL** | float 可 |

      - **受入**: `Column` に列型（int / real）を持たせ `_coerce()` が従う。
        INTEGER 列に小数が来たら 400（`InvalidValueError`）で「整数で入力してください」。**丸めない**。
        `isinstance(value, (int, float)) → return value` の素通りを塞ぐ。
        宣言と DDL のずれを検出するテスト。PG 実機で `tasks.priority` に `"2.7"` → 400
      - **注意**: `progress` 型（`manual_progress`）は `parse_progress` が別に扱うので対象外
      - **追撃（2026-08-22 完了）**: 型は揃ったが**大きさ**が未検査だった。`"9" * 30` / `"1e20"` が
        int4 を超えて本番だけ 500 になる穴と、REAL 列の inf / NaN を同ブランチで塞いだ。
        範囲は BIGINT ではなく **int4**（編集できる INTEGER 列は PG でも `INTEGER`）。完了ログ参照

- [ ] **G1-10** CI に PostgreSQL を追加し、PG 経路を回す。
      G1-0 の不具合は「CI が全部緑なのに本番だけ壊れている」形で残った。
      ADR 0006（本番は PostgreSQL / SQLite は開発・テスト専用）を踏まえると、
      **SQLite だけの CI は本番を代表していない。**
      - **受入**: `.github/workflows/ci.yml` に `services: postgres:16` を追加し `CLUB_TEST_PG_DSN` を渡す。
        **DB 名に `test` を含めること**（含めないと `_guarded_dsn()` が skip し、追加したのに1件も走らない）。
        PG ジョブで skip が 0 件であることを `-rs` で確認する。
        マトリクス3版すべては高コストなので **PG は 3.12 のみ**に絞ってよい（判断を完了ログに残す）
      - **検証**: 意図的に G1-0 / G1-9 の修正を戻した PR で CI が赤になること
      - **注意**: ADR 0014（1タスク＝1ブランチ）に従い、他の変更と混ぜない
      - **現状**: G1-9 時点で PG テストは7件（`test_db_postgres.py` 5 / `test_dashboard_edit.py` 2）。
        すべて `CLUB_TEST_PG_DSN` 未設定で skip している

---

## Phase G2: 事故を防ぐ / 迷わせない（全89コマンドに効く共通改善）

- [x] **G2-1** 破壊的操作に共通の確認ステップを入れる。
      確認があるのは `/data delete`・`/season rollover`・`/team-remove` の3つだけ。
      無いもの: `cogs/progress.py:1001-1011`（`/progress remove` は**配下ごと**削除し、
      実行後に件数を報告するだけ）、`cogs/schedule.py:446-473`（`/schedule delete` は
      投票メッセージを全削除してから DB を CASCADE 削除。票データ完全消失）、
      `cogs/season.py:170-197`（`/season new` は現年度を即終了）。
      - **受入**: `cogs/season.py:59-127` の `RolloverView` を `utils/views.py` の
        汎用 `ConfirmView(owner_id, preview_embed, on_confirm)` に切り出し、上記3コマンドに適用する。
        `/progress remove` は削除前に配下の件数をプレビューする（`ProgressRepository.count_subtree` を追加）
      - **検証**: `tests/test_confirm_view.py`（新規）で、他人の interaction が拒否されること・
        確定前は削除が走らないことを検査
      - **注意**: ADR 0018 / 0024 の「既存データを動かさない」軸に沿う。
        `/schedule delete` の論理削除化は破壊的なので **このタスクには含めない**（G3-3 で扱う）

- [x] **G2-2** ID を手で写させるのをやめる（オートコンプリート追加）。
      `cogs/schedule.py:347,393,416,441,482` の5コマンドが素の `schedule_id: str`、
      `cogs/tasks.py:280,311,342,369` が素の `task_id: int`。
      一方 `cogs/progress.py:1482-1491` は10箇所に階層字下げ付きオートコンプリートを一括登録している。
      - **受入**: `/schedule close|remind|edit-deadline` は開催中のみ、`/schedule status|delete` は
        締切済みも含めて候補に出す。候補名は「イベント名（〜締切）」。
        `/task done|delete|assign|priority` は `app_commands.Choice[int]` で未完了タスクを出す
      - **検証**: `tests/test_autocomplete.py`（新規）で、候補が25件以内・guild_id でスコープされることを検査

- [x] **G2-3** 通知の抜けを塞ぐ（3件まとめて1タスク）。
      1. `cogs/schedule.py:670-678` — `target_role_id` が無いと `return 0` なのに、
         呼び出し側（`:430-438`）が緑の成功 Embed で「対象: 0 名」と表示する。
         さらに `cogs/reminders.py:157-164` は 0 名でも `mark_reminder_sent` を打つので、
         **後から対象ロールを付けても永久に再送されない**
      2. `cogs/schedule.py:194` — 作成時に投票メッセージへロールメンションを付けていない
      3. `cogs/tasks.py:198-262` — 担当者に DM もメンションも送っていない
      - **受入**: (1) 対象ロール未設定なら成功ではなくエラーで返し、`reminders_log` の status を
        `skipped` にする。(2) `content=f"{target_role.mention} ..."` を付ける。
        (3) 担当者へ DM、`discord.Forbidden` なら班チャンネルへメンション
      - **検証**: `tests/test_schedule_notify.py` / `tests/test_tasks_notify.py`（新規）
      - **注意**: (1) の恒久解は ADR 0025 の「覆す条件」に沿って**未回答判定を `members` 台帳の
        現役メンバーに寄せる**こと。ただし Bot トークンを Web 層に置かない方針は維持する。
        台帳ベースへの切り替えは影響が大きいので、このタスクでは**エラー表示までに留め**、
        台帳ベース化は G3-2 で扱う
      - (3) の DM→チャンネルのフォールバックは `cogs/schedule.py:689-701` に既存実装があるので
        `utils/` のヘルパに切り出して共用する

- [x] **G2-4** タイムアウトした View を画面に反映する。
      `cogs/progress.py:478-480` ほか5箇所の `on_timeout` は `item.disabled = True` するだけで
      `message.edit(view=self)` を呼んでいない。`cogs/season.py` の `RolloverView` には
      `on_timeout` すら無い。`/season rollover` は選択途中で5分経つと確定ボタンが無反応になる。
      - **受入**: `utils/views.py` に `TimeoutAwareView` を作り、6箇所が継承する。
        タイムアウト時に「時間切れです。もう一度実行してください」へ差し替える。
        `RolloverView` の timeout を 300 → 900 に延ばす
      - **検証**: `tests/test_views_timeout.py`（新規）で `on_timeout` が `message.edit` を呼ぶことを検査

- [x] **G2-5** 空状態に「次の1コマンド」を必ず添える。
      良い例（`cogs/progress.py:75-78` / `cogs/teams.py:165-169` / `cogs/season.py:147-155`）と
      悪い例（`cogs/tasks.py:794` / `cogs/schedule.py:331` / `cogs/layer_tracking.py:101` /
      `cogs/reports.py:180,202`）が混在している。
      特に `/report weekly`（`cogs/reports.py:46-74`）は新規サーバーで「未完了 0 / 超過 0 / 投票 0」と
      表示され、**健全に運用できている状態と見分けが付かない**。
      - **受入**: `utils/embeds.py` に `empty_state_embed(title, situation, next_command)` を追加し、
        上記5箇所が使う。`/report weekly` はタスクも投票も0件なら
        「まだデータがありません。`/task add` `/schedule create` から始めてください」に切り替える
      - **検証**: `tests/test_empty_states.py`（新規）で、各コマンドの空状態にコマンド名が含まれることを検査

- [ ] **G2-6** `/progress edit` の進捗率を検証する。
      `services/progress_tree.py:82-107` の `parse_progress` は解釈不能なら `None` を返し、
      `cogs/progress.py:977-979` がそれをそのまま `manual_progress` に代入する。
      `/progress edit node:主桁 progress:半分` で**既存の進捗率が消え**、緑の成功 Embed が出る。
      - **受入**: コマンド側で `None` を弾き「`0.5` `50%` `50` の形式で指定してください」と返す
      - **検証**: `tests/test_progress_ui.py` にケースを追加
      - **注意**: `parse_progress` の「解釈不能は None」仕様は移行スクリプト用なので**変えない**。
        G0-2 で取り込む `8b9c0f4` がダッシュボード側に同じ検証を入れているので、
        **解釈規則をそちらと揃える**

- [ ] **G2-7** Todoist の同期失敗を利用者に見せる。
      `cogs/tasks.py:293-299` / `:324-330` / `:572-573` が `except TodoistError: pass` で、
      `log.warning` すら出していない。`/task done` は必ず「完了にしました」と返るのに
      Todoist 側は未完了のまま残り、翌朝の通知に出続ける。
      - **受入**: ローカル完了は維持したまま、成功メッセージに同期結果を明記する
        （`⚠️ Todoist 側の完了に失敗しました。Todoist 上で直接完了にしてください`）。
        `log.warning` に guild_id と task_id を出す
      - **検証**: `tests/test_todoist_guild_scope.py` に `TodoistError` を送出するケースを追加
      - **gotcha**: `todoist-completed-tasks-not-detected` と関連（同期の片方向性）

---

## Phase G3: 導入と定着（破壊的変更を含む。プランモードを挟む）

- [ ] **G3-1** `/setup` で班長ロールを設定できるようにし、`/set_role` に削除を追加する。
      `cogs/setup_wizard.py:44-47` に `LEADER_ROLE_IDS` が無く、L2 判定の唯一の根拠なのに
      ウィザードから設定できない。`cogs/settings.py:288-295` は**追記専用**で重複チェックも無く、
      1つ外すには全消しするしかない（その間 全班長が L1 に降格）。
      `services/season_service.py:71` の rollover も `members.is_leader` しかリセットせず、
      **毎年ロールIDが積み上がる**。
      - **受入**: `ROLE_SETTINGS` に `LEADER_ROLE_IDS` を追加（`RoleSelect(max_values=5)` で複数選択、
        追記ではなく上書き）。`/set_role` に `action: add|remove` を追加し重複を除去する。
        `/season rollover` の結果 Embed に「班長ロールの見直し」を促す一文を追加
      - **検証**: `tests/test_setup_wizard.py` に複数ロール保存と重複除去のケースを追加
      - **注意**: 既存の `LEADER_ROLE_IDS`（カンマ区切り）を壊さない。
        重複除去は**保存時のみ**行い、既存値の一括正規化はマイグレーションでやらない（ADR 0024）

- [ ] **G3-2** 未回答判定を `members` 台帳の現役メンバーへ寄せる。
      現状 bot 側（`cogs/schedule.py:670`）はロール基準、ダッシュボード側（ADR 0025）は台帳基準で、
      **同じ「未回答」が2つの定義で動いている**。ロール未設定の予定では bot が完全に沈黙する（G2-3）。
      - **受入**: `notify_unanswered` が「`target_role_id` があればロール ∩ 現役メンバー、
        無ければ現役メンバー全員」を対象にする。ダッシュボードのピボット表と母集団が一致する
      - **検証**: `tests/test_schedule_unanswered.py`（新規）で、ロールあり／なし・
        退部者（`status='alumni'`）が除外されることを検査
      - **注意**: **ADR 0025 を更新する。** 完了ログに新 ADR の草案を書く。
        Bot トークンを Web 層に置かない方針（ADR 0015）は維持する

- [ ] **G3-3** `/schedule delete` を論理削除にする。
      現状は投票メッセージを削除してから DB を CASCADE 削除しており、票データが完全消失する。
      `/team-remove` `/skill-remove` `/layer keta-remove` は既に論理削除方式なので方針統一にもなる。
      - **受入**: `schedules` に `deleted_flag` を追加（マイグレーション必要。v16 は G3-4 が使うので
        **同じマイグレーションにまとめる**）。一覧・集計から除外し、`/schedule restore` で戻せる
      - **検証**: `tests/test_schedule_delete.py`（新規）
      - **注意**: 既存の CASCADE 削除に依存しているテストがあれば併せて直す

- [ ] **G3-4** `/schedule confirm` — 確定日程の登録と当日リマインド。
      `finalize_schedule`（`cogs/schedule.py:704`）は集計サマリーを投稿して終わりで、
      **「結局いつに決まったのか」がどこにも残らない**。前日・当日のリマインドも無い。
      - **受入**: スキーマ v16（`015_schedule_confirmed.sql`。G3-3 の `deleted_flag` と同じ版にまとめる）で
        `schedules.confirmed_option_id TEXT NULL` を追加。`/schedule confirm schedule_id option_id`（L2）で
        確定を保存し対象ロールへ告知。`/schedule list` に確定日を表示。
        前日20時と当日朝に「本日 18:00 ◯◯（場所）」を通知
      - **検証**: `tests/test_schedule_confirm.py`（新規）。リマインドは `reminders_log` の
        日付キーで二重送信を防ぐ
      - **申し送り**: `.ics` 添付（標準ライブラリのみ・外部依存ゼロ）は次イテレーションで。
        Google カレンダー連携は ADR 0013 に反するのでやらない

- [ ] **G3-5** 招待直後の案内を確実に届ける。
      `bot.py:331-336` の「次のステップ: `/setup`」は `log_to_channel` 経由で、
      `bot.py:363-370` は `BOT_LOG_CHANNEL_ID` 未設定なら**無言で破棄**する。
      `bot.py:69-76` の `INVITE_PERMISSIONS` は `manage_channels` を含まないので、
      README の招待URLで入れた場合 `#bot-log` は作られない。
      さらに `bot.py:209-227` は**権限不足で何も作らなかった場合でも `AUTO_SETUP_DONE` を立てる**ため、
      GUIDE.md:59-61 の「権限を付けて再招待」という復旧手順が効かない。
      - **受入**: 案内を「bot-log →（無ければ）`guild.system_channel` →（無ければ）送信可能な
        最初のテキストチャンネル」の順で送る。文面に `/setup` `/setup-status` `/help` を明記。
        `AUTO_SETUP_DONE` は**作成に成功したときだけ**立てる
      - **検証**: `tests/test_guild_foundation.py` に、bot-log 無し・権限無しの2ケースを追加
      - **注意**: ADR 0017（最小権限）は維持する。権限を増やす方向で解決しない

- [ ] **G3-6** 新入生オンボーディング（`on_member_join` → 班のセルフ選択）。
      新歓期に30〜50人が入るが、`on_member_join` は名前キャッシュを更新するだけ
      （`cogs/name_cache.py:157`）。**bot は新入生の存在を知らず、`/member register` を
      幹部が1人ずつ手打ちしている**。名簿に載らない人には班別通知も出欠催促も届かない。
      - **受入**: ギルド別設定 `WELCOME_ENABLED`（既定 OFF）が ON のとき、参加者へ
        「ようこそ」Embed ＋「班を選ぶ」ボタンを送る。押すと班セレクト →
        `members` へ登録 ＋ `teams.member_role_id` のロール付与。
        DM 拒否時は指定チャンネルでメンション。`/setup` に ON/OFF を追加
      - **検証**: `tests/test_welcome.py`（新規）。OFF のとき何も起きないことを必ず検査
      - **注意**: **既定は OFF**（ADR 0024「既定値で何も起きない状態から始める」）。
        UI は `cogs/members.py:287` の `/member setup` の班選択ウィザードを流用する

- [ ] **G3-7** ドキュメントを実装に合わせ、GUIDE.md を回帰テストの対象にする。
      齟齬: GUIDE.md:364-365 は「毎朝 **08:00**」だが実装は **08:30**（`cogs/reminders.py:193`）。
      通知表（GUIDE.md:360-368）に `weekly_milestone_alert` / `daily_purge` /
      `cogs/progress.py:803` が無い。早見表（GUIDE.md:484-509）に `/help` `/setup-status`
      `/countdown` `/weight` `/milestone` `/season` `/data` が**全部無い**（`/weight` は全体で0ヒット）。
      `docs/IMPROVEMENT_REPORT.md` の「reminders 定期ジョブ一覧」がそのまま使える。
      - **受入**: GUIDE.md の通知表と早見表が実装と一致する。
        `tests/test_docs_commands.py`（現状 `docs/OPERATION.md` のみ検査）の対象に
        GUIDE.md の付録セクションを追加する
      - **検証**: `pytest tests/test_docs_commands.py -q` が緑
      - **注意**: `/data export` と `/season rollover` の「全データ」表記は G4-3 の完了までは
        「主要7テーブル」に直す（誇張表記の解消）

---

## Phase G4: 溜まっているデータを見せる / ドメイン拡張

- [ ] **G4-1** `/layer stats` — 積層記録の集計。
      `layer_records`（`utils/db.py:256`）に「誰が・どの桁の・何層目を・何分」が全部あるのに、
      **人間が読める形で出すコマンドがゼロ**。`/progress` に出るのは率だけで時間情報が捨てられている。
      - **受入**: `/layer stats [keta] [period:今週|今月|全期間]`（L1）が、桁別
        （完了層数/目標・合計時間・1層あたり平均分・最終作業日）と人別（層数・時間）を表示する。
        目標層数は `progress_spar_links.target_layers`、名前解決は `discord_name_cache`
      - **検証**: `tests/test_layer_stats.py`（新規）。集計は `services/` の純関数に切り出して単体テストする
      - **注意**: 新規テーブル不要。桁引数は既存の `_keta_autocomplete` を再利用

- [ ] **G4-2** `/layer cancel` と押し忘れ検知。
      `/layer start` したまま帰宅すると `/layer end` が「1200分」を記録し、
      完了層数が増えるので**進捗率まで水増しされる**。打ち間違えて start した場合の
      取り消し手段も無い（`end` するしかなくゴミ行が残る）。
      - **受入**: `/layer cancel`（L1）が進行中セッションを記録を残さず破棄する。
        経過が `LAYER_SESSION_ALERT_MINUTES`（ギルド別設定・既定240分）を超えたら本人へ DM。
        さらに `LAYER_SESSION_AUTO_CANCEL_MINUTES`（既定720分）で自動 cancel し通知する
      - **検証**: `tests/test_layer_session_alert.py`（新規）
      - **注意**: 通知は `cogs/reminders.py:128` の5分ループに相乗りし、
        送信済み管理は `reminders_log` を使う。**1ギルドの失敗が他ギルドを止めないこと**

- [ ] **G4-3** `/report changes` — 監査ログの閲覧と export への追加。
      `AuditLogRepository.list_recent`（`repositories/audit_log_repository.py:38`）を呼ぶコードが
      bot 側に1箇所も無い。`/report audit`（`cogs/reports.py:38,178`）が読んでいるのは
      `reminders_log` で別物。`audit_log` には `/setup`・班マスタ変更・年度替わり・
      **ダッシュボードのセル編集**が記録され続けている。
      `/data export` も `TABLES` の7テーブルのみで `audit_log` / `seasons` /
      `progress_milestones` / `layer_ketas` / `skill_tags` / `settings` を持ち出せない。
      - **受入**: `/report changes [limit] [actor]`（L3）が `audit_log` を表示し、
        actor_id / target_id を表示名に解決する。既存の `/report audit` は
        `/report notifications` へ改名する。`TABLES` に読み取り専用の TableSpec を追加し、
        export とダッシュボードに同時に効かせる
      - **検証**: `tests/test_data_export.py` に新テーブルが ZIP に含まれることを検査するケースを追加
      - **注意**: `settings` は**秘密情報を含む列を除外**する（Todoist トークンは
        `todoist_configs` にあり `TABLES` に無いが、念のため）。
        `docs/PRIVACY.md` の記載と export 内容を一致させる

- [ ] **G4-4** `/me` — 個人サマリー。
      部員視点の入口が無い。自分のタスク・未回答の投票・積層実績・担当ノードがそれぞれ別コマンドで、
      `/task list` は全体を返す。**新入生が「今日自分は何をすればいいか」を1コマンドで確認できない**。
      - **受入**: `/me`（L1・ephemeral）が 未完了タスク（期限順・上位5）／未回答の投票／
        今月の積層時間と層数／担当中の進捗ノード を表示する。
        `user` 引数は L2 以上のみ指定可
      - **検証**: `tests/test_me.py`（新規）。**新規テーブル不要**（既存クエリの合成のみ）

- [ ] **G4-5** `/report weekly` の公開版と自動投稿。
      現状 `/report weekly` は L2 以上・ephemeral 固定で、部員には
      「今週サークル全体で何が進んだか」が見えない。
      - **受入**: `/report weekly` に `public: bool = False` を追加。
        ギルド別設定 `WEEKLY_DIGEST_ENABLED`（既定 OFF）と曜日設定で、
        月曜朝に同じ内容を公開チャンネルへ自動投稿する。
        内容に「先週の積層層数・時間・参加人数・完了タスク数」を含める（G4-1 の集計を再利用）
      - **検証**: `tests/test_weekly_digest.py`（新規）。OFF のとき何も送らないことを必ず検査
      - **注意**: **ADR 0023 は覆さない。** 0023 の「覆す条件」に
        「『今週も問題なし』を明示的に求められたとき（週報 `/report weekly` への統合で
        代替できるかを先に検討する）」とあり、本タスクはまさにその代替案。
        マイルストーン警告（遅延時のみ）とダイジェスト（週次の実績報告）は**別物**として共存させ、
        ダイジェスト側に「遅延はありません」の定型文を入れない。
        既定 OFF なので、既存ギルドの通知量は変わらない

- [ ] **G4-6** `/report member-attendance` — メンバー軸の出欠。
      `/report attendance-rate`（`cogs/reports.py:193`）は投票ごとの ok 率で、
      **「最近来ていない人」が特定できない**。「3回連続で未回答」は退部のほぼ確実な予兆。
      - **受入**: `/report member-attendance [months:3]`（L2 以上・**ephemeral 固定**）が、
        締切済み投票についてメンバー別の回答率・ok 率・連続未回答数を回答率の低い順に表示する
      - **検証**: `tests/test_member_attendance.py`（新規）
      - **注意**: 晒しにならないよう公開オプションを付けない。母集団は G3-2 と揃える

- [ ] **G4-7** `progress_snapshots` — 進捗の履歴とバーンダウン。
      `services/milestone_service.py:9-14` が自ら書いているとおり、履歴が無いため
      ペースが「作成日→最終更新日の平均」でしか出せず判定不能が多発する。
      「先週から何%進んだか」も分からない。
      - **受入**: スキーマ v17（`016_progress_snapshots.sql`）で
        `progress_snapshots(guild_id, node_id, snapshot_date, aggregated, actual_weight_g)`、
        UNIQUE `(guild_id, node_id, snapshot_date)`。
        `cogs/progress.py:776` の20分同期ループ末尾で、その日まだ書いていなければ1回だけ保存する。
        `/progress history [node] [days:60]`（L1）でテキストのスパークラインと直近7日の伸びを表示
      - **検証**: `tests/test_progress_snapshots.py`（新規）。1日1行しか書かれないことを検査
      - **注意**: **ADR 0022（ペースは作成日→更新日で定義し、判定不能なら予測を出さない）を
        更新する。** 完了ログに新 ADR の草案を書く。
        スナップショットが十分に溜まるまでは従来の推定にフォールバックし、
        **履歴が無い期間について予測を出さない**という 0022 の核は維持する
      - **申し送り**: 溜まったら G4-5 のダイジェストが「主桁 62%→68%」を言えるようになる

- [ ] **G4-8** `/stock` — 資材・消耗品の在庫と発注アラート。
      人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
      カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。
      発注判断は「残量が閾値を割った」という bot が自動で見張れる条件。
      - **受入**: スキーマ v18（`017_stock.sql`）で `stock_items` / `stock_movements`。
        `/stock list`（閾値割れを強調）/ `/stock add`（L2）/ `/stock use`（L1）/
        `/stock set-threshold`（L2）。閾値を割ったら即1回通知し、以降は朝の通知に含める
      - **検証**: `tests/test_stock.py`（新規）
      - **注意**: マスタ管理は `layer_ketas`（有効フラグ・オートコンプリート付き）と同型にする。
        **品目名の初期値をコードに持たない**（サークルごとに違う）

- [ ] **G4-9** `/tool` — 工具・機材の貸出管理。
      `/layer start` → `/layer end` とまったく同じ「開始→進行中→終了」モデル。
      - **受入**: `/tool list|borrow|return|add|remove`。返却予定日超過で本人へ DM
      - **注意**: G4-8 と**同じ Cog**（`cogs/inventory.py`）にまとめる。
        督促ロジックは G4-2 の押し忘れ検知を共用する

- [ ] **G4-10** `/incident` — ヒヤリハット・事故報告。
      工房での切削・溶剤・高所作業・機体運搬・テストフライトと危険度が高く、
      大学から安全管理体制の提示を求められることもある。今は雑談に流れて消える。
      - **受入**: スキーマ v19（`018_incidents.sql`）。`/incident report`（L1）が Modal
        （発生日時 / 場所 / 何が起きたか / けがの有無 / 再発防止案）を開き、幹部ロールへ通知する。
        `/incident list`（L3）。**匿名フラグ**を持ち、報告者IDは DB に保持するが表示しない
      - **検証**: `tests/test_incident.py`（新規）。匿名時に報告者名が Embed に出ないことを検査
      - **注意**: `docs/PRIVACY.md` に収集項目を追記する。
        Modal は `cogs/todoist_admin.py:39` の実装を流用

---

## この表に入れなかったもの（再提案しないための記録）

| 案 | 理由 |
|---|---|
| 天候 API 連携で活動可否を自動判断 | 全テナント分の API キー・費用を運営者が負担する構造になる。何より **bot の判断が安全判断の根拠にされるのは責任上危険**。ADR 0022 の「分からないものを数字にしない」とも不整合 |
| 大会エントリー書類の期限管理（専用機能） | `/task add`（期限付き）＋ 朝の7日以内通知 ＋ 夜の超過通知 ＋ `/milestone add` でほぼ賄える。専用テーブルを足すと「期限」が3系統に分裂する。GUIDE.md への運用例追記が正解 |
| Google カレンダー同期 | ADR 0013 で gspread / google-auth を撤去し、`tests/test_progress_no_sheets.py` で再混入を検出している。明確な方針逆行。`.ics` 添付（依存ゼロ）で代替 → G3-4 の申し送り |
| 部費・立替精算・会計管理 | 金銭記録の正本を bot が持つと欠損時の責任が運営者個人に及ぶ。実際の入出金は銀行・大学の会計ルール側にあり二重記帳になる。G4-8 の在庫から CSV を出す形が現実的 |
| 機体設計値の汎用スペック管理 | 「設計値 vs 実測値」は `/weight` で実装済み。他の物理量へ広げると `progress_nodes` の列が際限なく増えるか汎用 EAV になり、ADR 0021（グラム固定・単位設定は作らない）に反する |
| 汎用リアクションロール | G3-6 に完全に包含される。`teams.member_role_id` を使う限り別機能にする理由がない |
| ダッシュボードのページング・検索・ソート・モバイル対応 | 効果は大きいがフロントの作り替えに近く、1タスクに割れない。G4 完了後に**別表**として起こす（`docs/IMPROVEMENT_REPORT.md` P1-12 の表がそのまま候補一覧） |
| ダッシュボードのセッション短縮・ログ設定・advisory lock | 運用側の改善で、`/acm-bot-loop` のテスト駆動と相性が悪い。G0-3 の結果と併せて **人間が運用手順として** 対応する |

---

## 実施順の理由

- **G0 が全部の前提。** 作業ツリーが CRLF で 161 ファイル分汚れている状態で実装を始めると、
  diff が読めずレビューもできない。未マージの `fix/code-audit-v2` を先に入れないと、
  G2-6 やダッシュボードの値検証で**同じ修正を二重に書く**
- **G1 は「動いていない」を直す。** 利用者から見て最も分かりにくい沈黙障害と、
  1関数の修正で全89コマンドに効く権限エラーの改善を先に置いた
- **G2 は事故を防ぐ。** 破壊的操作の確認とオートコンプリートは、
  新しい機能を足す前に入れておくほど累積効果が大きい
- **G3 は破壊的変更とスキーマ変更を含む**ので、G1 / G2 で足場を固めた後に置いた。
  ADR を更新するタスク（G3-2）もここ
- **G4 は新機能。** 上位4件（G4-1 / G4-2 / G4-4 / G4-5）は**新規テーブル不要**で、
  既に DB に溜まっているのに見せていないデータを価値化するもの

## 停止条件（`/acm-bot-loop` §4 の再掲。特に本表で起きやすいもの）

- **ADR と衝突する実装になったとき。** 勝手に ADR を覆さない。
  「ADR NNNN と衝突。◯◯という理由で覆すべきだと考える」と書いて止まる
- 同じテストが3周直しても緑にならない
- 既存ギルドのデータを壊す破壊的変更が避けられない（G3-1 / G3-3 / G4-7 で起きやすい）
- `fix/code-audit-v2` のマージでコンフリクトが解決できない（G0-2）
- 秘密情報や本番 DB に触る必要が出た

---

## 完了ログ

<!-- 各タスク完了時に「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する -->

### 2026-08-21 — G0-2 / G0-4 / G1-1〜G1-7（ブランチ `fix/improvement-g0-g1`）

`origin/main`（`eaade27` = PR #18 マージ後）を基点に、別セッションが作ったパッチ13枚を
レビューしてから `git am` し（`04111dd`〜`4cbba02`）、そのうえで G1-6 の積み残しを
1件追加実装した（`016f245`）。

- ruff: `All checks passed!`
- pytest: **675 passed, 4 skipped**（main 時点は 580 passed）。
  skip は `CLUB_TEST_PG_DSN` 未設定の PostgreSQL テスト4件のみ

#### 完了内容

| タスク | コミット | 内容 |
|---|---|---|
| G0-2 | `04111dd` `871d9c1` `9c2739f` `d5d47d9` | 未マージだった `fix/code-audit-v2` の監査修正3件を取り込み |
| G0-4 | `ee5eb07` | ruff の既存エラー13件（E741×9 / E402×4）を解消 |
| G1-1 | `bc792eb` | `utils/embeds.MAX_EMBED_FIELDS` と `add_truncation_note()` を追加し、`/schedule list` `/schedule list-closed` `/layer status` を25件で打ち切り |
| G1-2 | `241dd48` | `resolve_default_channel_id()` を `PROGRESS_DEFAULT_CHANNEL_ID` → `DEFAULT_PROGRESS_CHANNEL_ID` → `DEFAULT_TASK_CHANNEL_ID` の3段に |
| G1-3 | `4e77b22` | `/setup-status` の検査項目を実効設定（タスク通知ch / ログch / 管理者ロール / 班長ロール / 班 / 桁 / 大会日）へ差し替え |
| G1-4 | `b7c1a65` | `LEVEL_LABELS` / `roles_for_level()` / `denial_message()` を追加し、権限エラーに依頼先ロールを出す |
| G1-5 | `4cbba02` | 打ち切り告知を4箇所へ展開。`/report attendance-rate` を**全件集計・表示だけ25件**に（**内部名表示は未実施**） |
| G1-6 | `b469e54` ＋ `016f245` | `teams` のロールID3列と `members.is_leader` を `editable=False`。`GET /settings` のロールID実値は L4 のみ。`016f245` で `/team-role` に `role_type:leader` を新設（設計判断3） |
| （P1-12 の残件） | `cfb42a8` | `export.csv` の500行打ち切りを解消（`list_all_rows`）。`?sheet=` にも対応 |
| G1-7 | `9e6a0db` | `is_self_or_level()` を追加し、`/task done` `/task priority` `/member skill add\|remove` を「担当者 or 作成者 or L2以上」に |

テストは 1,330 行追加（新規4ファイル: `test_embed_limits.py` / `test_tasks_owner_guard.py` /
`test_table_value_coercion.py` / `test_team_role_command.py`）。

#### 設計判断

**1. ADR は1つも覆していない。** G1-2 は ADR 0023 の「影響範囲」に書かれた3段フォールバックが
実装されていなかっただけで、**沈黙の条件は変えていない**。`#bot-log` への1行は
「遅延があるのに送信先が無い」場合にのみ、月曜に1回だけ出る（`if not behind: return 0` の後に到達）。
部員向けの通知は従来どおり沈黙する。
→ **ADR 0023 の「影響範囲」に `#bot-log` への例外を1行追記すべき**（申し送り）。

**2. `csv_safe` は main 側を採用し、ブランチ側の `_csv_safe` を捨てた。**
両実装を同一入力で突き合わせて確認した結果:
- エスケープ対象文字は完全に同一 — `("=", "+", "-", "@", "\t", "\r")`
- BOM・`None → ""` も同等
- main は非文字列も str 化してからエスケープするため**適用範囲が広い**（緩い方向の差はゼロ）
- main は `display.export_rows()` で ID→表示名を解決した**後**に適用するので、
  ニックネームを `=HYPERLINK(...)` にされてもエスケープされる

これに伴い `tests/test_table_value_coercion.py` の CSV テスト1件を main の契約
（`csv_safe(None) == ""` / `csv_safe(42) == "42"`）へ書き換えた。**テストを実装に合わせる操作**なので
根拠を残す: (a) 変わったのは1関数だけでエスケープ検証6件は無傷、(b) 観測可能な CSV 出力は
両契約で同一、(c) **main には書き換え前から同じ契約のテストが存在した**
（`tests/test_data_export.py:196-201`）。さらに `rows_to_csv` から `csv_safe` の呼び出しだけ外して
フルセットを回し、`test_injected_task_title_is_escaped_in_export` が落ちることで
**配線も担保されている**ことを確認した。

**3. G1-6 は「代替手段の無い操作を塞ぐ」直前だった。**
`teams.leader_role_id` を `editable=False` にした時点で、この列は**どこからも書けなくなっていた**
（`set_team_roles()` に引数が無く、`/team-role` の choices も primary / secondary だけ、
`upsert_team(leader_role_id=...)` を渡す呼び出しも存在しない）。`/team-list` は表示している。
また `_sync_roles()` が `add_roles()` に渡すのは `member_role_id` と `secondary_role_id` だけなので、
**この列には権限昇格の根拠が成立しない**。
→ `set_team_roles()` に `leader_role_id` を追加し、`/team-role` に `role_type:leader` を新設した（`016f245`）。
Web は3列とも読み取り専用のまま、設定手段は L4 限定の Discord 側へ一本化した。

**4. `teams.leader_role_id` と settings の `LEADER_ROLE_IDS` は別物。**
前者は `/team-list` の表示専用で、L2 判定にも自動付与にも使われない。そのまま `leader` を
追加すると「班長ロールを設定したのに班長が何もできない」という新しい罠になるため、
選択肢名（`leader（班長・表示用）`）・成功メッセージ・docs の3箇所で `/set_role role_type:リーダー`
へ誘導している。

**5. 受入基準からの逸脱（すべて意図的）。**
- G1-1: `ScheduleRepository.list_closed_schedules` への `limit` 追加はしていない。
  Embed 側の責務なので cog でスライスした（リポジトリの契約を変えない）
- G1-4: `allowed_mentions=AllowedMentions.none()` は付けていない。`PermissionDenied` は
  `bot.py:409` で `error_embed` に包まれ ephemeral で送られるため、**Embed 内のロールメンションは
  通知を飛ばさない**。ただし将来 `content` へ移されると壊れる暗黙の前提なので、
  明示的に付けるほうが安全（申し送り）
- G1-7: `/task delete` には所有者チェックを入れていない。元から `@require(Level.L2)` で、
  受入基準（担当者 or 作成者 or L2以上）より**厳しい**状態が既に成立しているため
- テストファイル名は指定（`test_schedule_limits.py` / `test_settings_notify.py` /
  `test_reports.py` / `test_tasks_permission.py`）と異なる。既存ファイルへの追記または
  論点単位のファイルにまとめた

**6. 「テストが本当に落ちること」を全件確認した。**
gotcha `test-asserts-permission-but-decorator-missing` と同型の嘘を防ぐため、
**実装ファイルだけを reverse-apply**（テストは残す）して失敗することを確かめた:

| 戻した実装 | 落ちたテスト |
|---|---|
| 0001（`@require_manage_guild_or`） | 2 |
| 0006（cog の slice。ヘルパは残置） | 3 |
| 0007（3段フォールバック） | 3 |
| 0010（`editable=False` と L4 マスク） | 5 |
| 0011（`_may_modify` / `is_self_or_level`） | 3 |
| 0013（全件集計） | 1 |
| G1-6 追加分（`set_team_roles` / cog 分岐 / choices） | 4 / 5 / 1 |

最後の `choices` の1件が重要で、**ハンドラが `leader` を処理できても `app_commands.Choice` に
無ければ利用者は選べない**。コールバックを直接呼ぶテストだけでは見逃すため、
`Teams.team_role.parameters` の choices を検査するテストを別に置いた。

#### 次タスクへの申し送り

**A. G1-8（`deploy.yml`）は取り下げ。`origin/main` の `4dc33c9`（2026-08-13）で解決済みだった。**
指摘の根拠にした `deploy.yml` は手元の `feat/dashboard-ui-fixes` にあった `fd86fb7`（08-06 / 30行）。
`origin/main` 版（43行）には `dashboard/requirements.txt` の install、
`club-bot-dashboard.service` の restart、`ReadWritePaths` 用ディレクトリ作成まで入っている。
**`docs/IMPROVEMENT_REPORT.md` の P0-7 も取り下げる。**
原因は分析前に `git fetch origin` していなかったこと。
→ **今後 IMPROVEMENT_REPORT.md 系の分析を始める前に必ず `git fetch origin` し、
`origin/main` を基点にすること。** 手元のブランチが main より古いと、修正済みの問題を再指摘する。

**B. ClaudeVault の gotcha 3件は `unfixed` タグを外せる。**
0001〜0003 で解消した。`gotchas/_index.md` の「いま踏むと未修正のもの（3件）」の節ごと削除できる。

| ノート | 解消したコミット |
|---|---|
| `progress-subtree-disappears` | `871d9c1`（`descendant_ids()` で子孫親付けを拒否） |
| `progress-stops-after-dashboard-edit` | `9c2739f`（`_coerce` / `InvalidValueError` → HTTP 400） |
| `test-asserts-permission-but-decorator-missing` | `04111dd`（デコレータ適用＋`checks` を走査する回帰テスト） |

`decisions/_index.md` の「未処理」から「`fix/code-audit-v2` の監査修正3件が未マージ」も消せる。

**C. `fix/code-audit-v2` の残り3コミットは取り込んでいない。main に反映済みのため。**

| コミット | 内容 | main 側の根拠 |
|---|---|---|
| `13f5451` | revert（ブランチ内部の往復） | 打ち消しコミットなので取り込む対象が無い |
| `d9996e7` | 絵文字IDを `config._clean` 経由で読む | `tests/test_config_env_robustness.py` が main に存在し、`_clean` も使われている |
| `a3b97e4` | 定期通知ループの失敗を1ギルドに閉じ込め | `tests/test_reminders_resilience.py` が main に存在する |

→ **`fix/code-audit-v2` はこれで役目を終えた。ローカルにしか無く GitHub へ push もされていないので、
このブランチのマージ後に削除してよい。**

**D. 新規に起票すべきタスク。**

| 仮ID | 内容 | 出どころ |
|---|---|---|
| G2-8 | 内部名表示の統一（`/progress edit` が DB カラム名をそのまま出す / `/report audit` が生の18桁ユーザーIDを出す。`discord_name_cache` があるのに使っていない） | G1-5 の未実施分 |
| G3-6 | 「restart 前に `pg_dump -Fc` を取る」を `docs/DASHBOARD_SETUP.md` §11 と `docs/OPERATION.md` §8.2 へ追記（マイグレーションに down が無い） | G1-8 の残件 |
| — | ADR 0023 の「影響範囲」に `#bot-log` への例外を1行追記 | 設計判断1 |
| — | `bot.py:426-428` に `allowed_mentions=discord.AllowedMentions.none()` を明示 | 設計判断5 |
| — | `tests/test_table_value_coercion.py:21` の `pytest.importorskip("fastapi")` を削除（書き換えで dashboard 依存が消えたのに残っている。無いと `_coerce` の全テストが不要に skip される＝gotcha `dashboard-tests-silently-skipped` と同型） | レビュー時に発見 |
| — | テスト欠落: `/member skill remove` の所有者ガード、`/team-list`・`/member support`・`/milestone list` の打ち切り告知 | レビュー時に発見 |

**E. G0-1 / G0-3 は【人間タスク】で未実施。**
G0-3（PostgreSQL でのダッシュボード編集テスト）は `dashboard/routers/tables.py` の
`row_id: str` が BIGINT 主キーへ渡る疑いの検証で、**5分で白黒がつく**うえ
落ちた場合は G1-0 として最優先になる。G2 に入る前に回しておくのが望ましい。

**F. G2 へ進む前提はそろっている。** ruff 緑・pytest 675 緑・ADR との衝突なし。
G2（破壊的操作の確認 / ID のオートコンプリート / 通知の抜け / View のタイムアウト /
空状態 / Todoist の同期失敗表示）から再開できる。

### 2026-08-21 — G1-0（ブランチ `fix/g1-0`）

`fix/improvement-g0-g1` の `016f245` から分岐。`table_repository.py` を触る
`0003` / `0010` / `0012` が `origin/main` に未マージのため、そちらを基点にした。
**未コミット。**

- ruff: `All checks passed!`
- pytest: **701 passed, 5 skipped**（G1-0 前は 696 passed / 4 skipped）。
  skip は `CLUB_TEST_PG_DSN` 未設定の PG テストのみで、**4 → 5 に増えたのが
  今回追加した PG 結合テスト**（skip を緑と数えない: gotcha `dashboard-tests-silently-skipped`）

#### 完了内容

| ファイル | 変更 |
|---|---|
| `repositories/table_repository.py` | `TableSpec.pk_type` を**既定値なしの必須フィールド**として追加。`UnknownRowError` と `coerce_row_id()` を新設し、`get_row` / `update_row` の両方で正規化 |
| `dashboard/routers/tables.py` | `UnknownRowError` を捕捉して 404。**ルータ側に `int()` は書かない** |
| `tests/test_table_row_id_coercion.py`（新規・21件） | 型変換、拒否、DDL との整合、ドライバへ渡る値の検査 |
| `tests/test_dashboard_edit.py`（+6件） | HTTP で 404 になること・書き込みへ進まないこと。**`_config` / `_seed` / `_client` に `database_url` を通し、PG 実機で HTTP 経路を1往復するテストを追加**（DSN 条件付き） |
| `tests/test_db_postgres.py`（+1件） | PG 実機でのリポジトリ層 str row_id 読み書き（DSN 条件付き） |

`TABLES` 7表の内訳: `int` が6表（tasks / members / teams / schedule_votes /
layer_records / progress）、`text` が1表（schedules）。

#### 設計判断

**1. 型は `TableSpec` に持たせ、ルータでは `int()` しない。**
ADR 0016 の「通る門が1つなら守る場所も1つ」に合わせた。列の定義とその型が同じ場所に
あるので、表を足すときに片方だけ直し忘れることがない。

**2. `pk_type` に既定値を置かなかった。**
既定値を `"int"` にすると TEXT 主キーの表を足したときに壊れ、`"text"` にすると
今回と同じ不具合が新しい表で再発する。**どちらの既定値も安全ではない**ので、
`dataclass` の必須フィールドにして「決めずには書けない」形にした（ADR 0016 の
「規律ではなく構造で守る」）。加えて宣言と DDL のずれを検出するテストを置いた。

**3. `int()` に頼らず ASCII 数字だけを受ける。**
Python の `int()` は全角「５」・アラビア数字「٥」・桁区切り `"5_000"` も通す。
そのままだと**同じ行を指す URL の綴りが複数できる**。監査ログには URL の生値が
残るため、「`tasks#٥` を編集」と記録されて実際は 5 行目、というずれが起きる。
`text.isascii() and text.isdigit()` で綴りを1つに固定した。
（この1件だけはテストを先に書いた時点で実装が通ってしまい、**実装側を直した**。
　テストを緩める方向には倒していない）

**4. 変換は `get_row` の中で行う。**
ルータは `get_row` → `update_row` の順に呼ぶので、変換の失敗は `UPDATE` に到達しない。
部分書き込みが起きない現状の性質を維持している（`update_row` 側でも冒頭で変換するが、
これは直接呼ばれたときの防御であって順序の担保ではない）。

**5. SQLite では再現しない不具合をどうテストしたか。**
SQLite は型親和性で `'5'` を 5 として扱うため、「行が引けたか」では修正を検証できない。
`Database` を継承した `_SpyDatabase` で **`fetchone` に渡ったバインド値そのもの**を
捕まえ、`isinstance(bound_row_id, int)` を検査している。
HTTP レベルのテスト（404 になること）は SQLite では修正前でも通るため、
**それだけでは担保にならない**ことをテストのコメントに明記した。

ADR との衝突なし。スキーマ変更なしのためマイグレーション不要。

#### 実装を戻すとテストが落ちること

| 戻した実装 | 落ちたテスト |
|---|---|
| `get_row` / `update_row` の `coerce_row_id()` 呼び出し（関数本体は残置） | 3 |
| ルータの `except UnknownRowError` → 404 | 5（すべて 500 になる） |
| `tasks` の `pk_type` を `"text"` に改ざん | 16（DDL 整合テストを含む） |

#### 次タスクへの申し送り

**A. `row_id` 以外に str のまま int 列へ渡している経路は無い。**
`table_repository.py` 内で `= ?` 比較している列を機械的に洗い出した結果:

| 列 | 渡る値 | 判定 |
|---|---|---|
| `guild_id` | `dashboard/security.py:88` が `Annotated[int, Path(ge=1)]` で受ける | ✅ int 確定 |
| `keta`（`_sheet_where` の `layer_records`） | `?sheet=` の str | ✅ `keta` は TEXT 列 |
| `schedule_id`（`_sheet_where` の副問い合わせ） | `?sheet=` の str | ✅ `schedule_options.schedule_id` は TEXT 列 |
| `{spec.pk}` | URL の str | ✅ 今回 `coerce_row_id()` で修正 |

`limit` / `offset` は `list_rows` が `int()` 済み。他の INTEGER 列
（`priority` / `is_leader` / `active_flag` / `minutes` / `closed_flag`）は
比較には使われず、書き込みのみで `_coerce()` を通る。

**B. ただし `_coerce()` が INTEGER 列に float を返す経路が残っている（別件・未修正）。**
`_coerce()` は `type == "number"` のとき `int()` → 失敗したら `float()` の順に試すため、
`minutes`（INTEGER）に `"2.5"` を PATCH すると **2.5（float）** が返る。
実測: `_coerce(minutes_col, "2.5") -> 2.5 <float>` / `_coerce(priority_col, "2.7") -> 2.7 <float>`。
asyncpg は int8 引数に float を渡しても `DataError` になるため、**G1-0 と同じ失敗の
書き込み側**が残っている。列型が INTEGER か REAL かを `Column` に持たせれば直せる
（`pk_type` と同じ考え方）。**G1-9 として起票を推奨。**

**C. PG 結合テストを CI で回すべきか → 回すべき。ただし本タスクでは追加していない。**
現状 `.github/workflows/ci.yml` は SQLite だけで、`CLUB_TEST_PG_DSN` を設定していない。
**今回の不具合は「CI が全部緑なのに本番だけ壊れている」形で3週間以上残った**もので、
ADR 0006（本番は PostgreSQL）を踏まえると SQLite だけの CI は本番を代表していない。

- **やること**: `test` ジョブに `services: postgres:16`（`ports: 5432:5432`、
  `--health-cmd pg_isready`）を足し、`CLUB_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/clubbot_test`
  を env に置く。DB 名に `test` を含めないと `_guarded_dsn()` が skip する点に注意
- **コスト**: サービスコンテナの起動が1ジョブあたり十数秒。マトリクスが3版なので
  3回起動する。PG 版は1つで十分なら `matrix` を分けて PG は 3.12 のみにする手もある
- **注意**: 追加したら **`-rs` で skip が消えたことを確認する**。
  設定しても `_guarded_dsn()` が skip し続けていれば、CI は緑のまま何も測っていない
  （gotcha `dashboard-tests-silently-skipped` と同じ形）
- 別タスクにするのは、CI の変更は失敗時の切り分け単位が違うため（ADR 0014）。
  **G1-10 として起票を推奨**

**D. 受入基準4「`scripts/check_g0_3_pg.py` が『通った』を返す」は、スクリプトを直さない限り
達成できない（実装の不備ではない）。** 判定行が

    if not ok_raw or ok_real is False:   # scripts/check_g0_3_pg.py:170

で、`ok_raw` は `check_raw_asyncpg()` ——`asyncpg.connect()` で**直に**
`fetchrow(..., 111, "5")` を呼ぶ探針——の結果。ここには club-bot のコードが1行も
関与しないため、**アプリ側を何をどう直しても `ok_raw` は False のまま**になる
（asyncpg のクライアント側エンコーダが int8 引数に str を受け付けない、という
ドライバの仕様そのものを測っている）。プール側に `init=` で型コーデックを
登録する案も、この探針は独立した接続を張るので効かない。

`[2] check_real_code_path()` は `TableRepository.get_row("members", "5")` を呼ぶので、
**今回の修正で OK になる**（`coerce_row_id()` が int へ正規化してからバインドする）。

- スクリプトは**不具合の有無を測る診断ツール**として書かれており、修正後の回帰確認用ではない。
  勝手に判定の意味を変えたくないので**触っていない**
- 修正後の確認に使いたい場合の最小変更: `[1]` を「ドライバの仕様確認（NG が正常）」の
  情報行に格下げし、判定を `if ok_real is False:` にする
- **代替として、回帰の担保はテスト側に置いた** — `tests/test_dashboard_edit.py` の
  `test_pg_live_dashboard_edit_accepts_string_row_id` が HTTP 経路を PG で1往復する。
  受入基準3が求めていたのはこちらなので、実質的な担保はできている

**E. `docs/IMPROVEMENT_REPORT.md` の P0-8 は解消済みにできる。**

### 2026-08-21 — G1-9（ブランチ `fix/g1-9`）

`fix/g1-0` の `e93c949` から分岐。同じ `table_repository.py` を触るので
G1-0 の `coerce_row_id` と同居させた。**未コミット。**

- ruff: `All checks passed!`
- pytest: **729 passed, 7 skipped**（G1-9 前は 701 passed / 6 skipped）。
  skip は `CLUB_TEST_PG_DSN` 未設定の PG テストのみで、**6 → 7 の増分が今回追加した PG 結合テスト**

#### 完了内容

| ファイル | 変更 |
|---|---|
| `repositories/table_repository.py` | `Column.number_type`（`"int"` / `"real"`）を追加し、`__post_init__` で宣言を強制。`_coerce()` の number 分岐を `_coerce_number()` へ切り出し、列の型に従わせた |
| `tests/test_number_column_types.py`（新規・26件） | 型変換、小数の拒否、DDL との整合、宣言し忘れの検出、ドライバへ渡る値 |
| `tests/test_dashboard_edit.py`（+3件） | HTTP で 400 になること、REAL 列は小数を受けること、PG 実機（DSN 条件付き） |

対象は number 列 11本（INTEGER 8 / REAL 3）。うち編集可能なのは
`tasks.priority`・`layer_records.minutes`（int）と
`progress.sort_order`・`target_weight_g`・`actual_weight_g`（real）の5本。

#### 設計判断

**1. `number_type` に既定値を置かず、`__post_init__` で強制した。**
G1-0 の `pk_type` は `TableSpec` の必須フィールドにできたが、`Column` は
`type` / `editable` が既定値を持つため、必須の位置引数を後ろに足せない。
そこで**フィールドは `None` 既定のまま、`__post_init__` で「number なら宣言必須」を検査**する形にした。
`TABLES` はモジュール読み込み時に構築されるので、宣言し忘れると **import が失敗する**
（テストが1件落ちるのではなく、モジュール全体が読めない）。「決めずには書けない」構造は保てている。
逆向き（number 以外の列に `number_type` を書く）も弾く。効かない宣言を残さないため。

**2. 小数は丸めない。ただし `2.0` は受ける。**
`priority` に `2.7` が来たら「2 にしておく」ではなく 400 で返す（勝手に値を変えない）。
一方 `2.0` / `"2.0"` は `int(2)` として受ける。これは丸めではなく**等価変換**で、
JSON の数値リテラルが float になる言語・クライアントを弾かないため。
判定は `float.is_integer()` なので `1.0000001` は拒否される。

**3. REAL 列は int で来ても float に寄せる。**
`sort_order` に `7` が来たら `7.0` にする。値は変わらないが、
「列の型に合わせる」という契約を int / real の両方向で同じにしておくため。

**4. bool の扱いは変えていない。**
`_coerce(priority, True) == 1` は従来どおり。ON/OFF を 1/0 として受ける既存の挙動で、
今回の論点（INTEGER 列に float）とは別。

**5. `progress` 型（`manual_progress`）には触っていない。**
`parse_progress` が `0.5` / `50%` / `50` を解釈して 0.0〜1.0 にクランプする別系統で、
列は REAL。今回の変更の対象外（チケットの注意どおり）。

ADR との衝突なし。スキーマ変更なしのためマイグレーション不要。

#### 実装を戻すとテストが落ちること

| 戻した実装 | 落ちたテスト |
|---|---|
| `_coerce_number()` を旧挙動へ（`isinstance` の素通り＋int→float の順試行。宣言は残置） | 11（うち HTTP 1） |
| `Column.__post_init__` の検証 | 2 |
| `tasks.priority` の `number_type` を `"real"` に改ざん | 14（DDL 整合テストを含む） |

#### 次タスクへの申し送り

**A. G1-10（CI に PostgreSQL）の優先度が上がった。**
PG でしか再現しない不具合がこれで2件（G1-0 / G1-9）になり、どちらも
**SQLite の CI は全部緑のまま**だった。現在 PG テストは7件あるが、
`CLUB_TEST_PG_DSN` 未設定で全部 skip している。**CI で回さない限り、
このブランチがマージされた後に同じ型の不具合が入っても気付けない。**

**B. 型の宣言が3系統になった。整理は不要だが、次に列を足す人向けに一言。**
`TableSpec.pk_type`（主キー）/ `Column.type`（表示・入力の扱い）/
`Column.number_type`（number の下位型）。それぞれ DDL との整合テストがあるので、
宣言を間違えれば落ちる。**新しい表・列を足すときは DDL を見てから宣言する**、で足りる。

**C. 同じ形の穴が他に無いかは未調査。**
今回は `number` 列だけを見た。`bool` 列（INTEGER）は `_coerce` が 1/0 の int を返すので安全、
`datetime` / `text` 列は TEXT なので対象外。ただし **`type` の宣言自体が DDL とずれている列**が
あれば別の話になる（例: TEXT 列を `"number"` と宣言している）。
`Column.type` と DDL の整合を機械的に検査するテストは**まだ無い**ので、
気になるなら G2 以降で足す価値がある。

---

### 2026-08-22 — G1-9 追撃: number 列の範囲チェック（ブランチ `fix/g1-9`）

`_coerce_number()` は型（int / real）は揃えるが**大きさを見ていない**ため、
変換が通っても DB の型に収まらない値がドライバへ渡っていた。
G1-0 / G1-9 と同じ「SQLite は保存でき、**PostgreSQL だけ 500**」の形。

#### 塞いだ入口

| 入口 | 変換の経路 | 変換結果 |
|---|---|---|
| `"1e20"` | `int()` 失敗 → `float()` → `is_integer()` True → `int()` | 10^20 |
| `"9" * 30` | `int()` が**直接成功**する（Python は任意精度整数） | 10^30 - 1 |
| `{"priority": 10**30}` | JSON ボディの数値。文字列を経ないので素通り | 10^30 |
| `"9" * 400`（REAL 列） | `int()` 成功 → `float()` が **OverflowError**（未捕捉＝500） | — |

`priority` / `minutes` に CHECK 制約は無いので DB 側では止まらない。
範囲外は丸めず・切り詰めず `InvalidValueError`（→ 400）。

#### 受入基準の訂正: 範囲は BIGINT ではなく **int4**

チケットは「BIGINT の範囲（±2^63）を超えると asyncpg が投げる」としていたが、
**編集できる INTEGER 列は PostgreSQL では int4** だった。
`to_pg_ddl()` が BIGINT へ寄せるのは**主キーと `guild_id` だけ**で、
`tasks.priority` / `layer_records.minutes` は `INTEGER` のまま出る。

int8 の範囲で判定すると `3000000000` が素通りし、**本番だけ**
`OverflowError: value out of int32 range` になる。PG 実機で確認済み:

    # チケット記載どおり int8 で判定した場合
    asyncpg.exceptions.DataError: invalid input for query argument $1:
        2147483648 (value out of int32 range)

そのため判定は `_INT32_MIN` / `_INT32_MAX`。
「編集できる int 列が本当に int4 か」は
`test_editable_integer_columns_are_int4_in_postgres` が PG の DDL と
突き合わせて固定する（BIGINT へ広げたら落ちて、範囲判定の見直しを促す）。

SQLite の INTEGER は 64bit なので開発環境では通ってしまうが、
**ADR 0006（本番は PostgreSQL）に合わせて厳しい側へ揃える**。
開発で通って本番で落ちる、が今回の不具合そのものだった。

#### REAL 列の inf / NaN も同じコミットで塞いだ（スコープ判断）

**一緒に塞ぐ**と判断した。理由:

1. **同じ関数の同じ分岐対**。`_coerce_number()` の末尾は
   `int` 側と `real` 側の2本しかなく、片方だけ直すのは関数を半分だけ直すこと
2. **同じ欠陥クラス**「DDL の型に入れてはいけない値を通す」。
   int 側は 500、real 側は**500 にすらならない**（float8 は Infinity / NaN を格納できる）
3. **ADR 0021 と整合する**。未計測を 0.0 に丸めず None で表すと決めている以上、
   NaN という第3の「値でない値」を通すのはその判断に反する。
   新方針の発明ではなく既存 ADR の徹底
4. **コストが小さい**。同じ `InvalidValueError` 経路に数行。
   スキーマ変更・設定追加・マイグレーションなし
5. ADR 0014 が禁じているのは**無関係な変更の混在**。
   同一関数・同一欠陥クラスの対は「無関係」ではない

放置した場合の壊れ方: `target_weight_g` / `actual_weight_g` に NaN が入ると
`services/progress_tree.py` の `_resolve_weight()` が子の合計を取る際に伝播し、
**祖先ノードの重量がすべて NaN になる**。エラーにならないぶん G1-0 より発見が遅れる。
PG 実機で「float8 は NaN を拒否しない」ことも確認済み
（`test_pg_live_float8_would_store_nan_if_we_let_it`）＝ DB 側は止めてくれない。

#### 変更したファイル

- `repositories/table_repository.py`: `_coerce_number()` に有限性チェックと
  int4 範囲チェック、REAL 側の `OverflowError` 捕捉を追加
- `tests/test_number_column_types.py`: 範囲・inf・NaN の単体テスト（+27）
- `tests/test_dashboard_edit.py`: PG 実機の HTTP 経路テスト（+2）

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`（SQLite）: **758 passed, 9 skipped**（skip は PG ライブ9件）
- `pytest tests/ -q -rs`（**PostgreSQL 16 実機**）: **767 passed, skip 0**
- 実装を戻すとテストが落ちること: 範囲チェックを無効化 → **20 failed**

#### 申し送り

**A. G1-9 の「型は宣言、大きさは？」が残っていた。**
`number_type` は int / real の別は宣言させるが、**その int がどれだけ入るか**は
DDL 側にしか無い。今回は「編集できる int 列は int4」をテストで固定して塞いだが、
将来 BIGINT 列を編集可能にするなら `number_type` に幅を持たせる設計判断が要る。

**B. 同じ「大きさ」の穴は text 列にもある（未調査）。**
`title` などの TEXT 列に長さ上限が無い。PostgreSQL の TEXT に上限は無いので
500 にはならないが、Discord の埋め込み文字数制限に当たる可能性はある。別件。

---

### 2026-08-22 — G2-1: 破壊的操作に共通の確認ステップ（ブランチ `fix/g2-1`）

確認があったのは `/data delete`・`/season rollover`・`/team-remove` の3つだけで、
同じくらい取り返しのつかない操作が確認なしで走っていた。
`utils/views.py` に `ConfirmView` を新設し、確認の作法を1箇所へ集約した。

#### 確認を付けたコマンド

| コマンド | 何が消えるか | 確認前に見せるもの |
|---|---|---|
| `/progress remove` | ノードと**配下すべて**（進捗・重量・マイルストーン） | 配下の件数と合計件数 |
| `/schedule delete` | 投票メッセージ＋票データ（CASCADE） | イベント名・候補数・回答した人数 |
| `/season new` | 現年度が**即終了** | 終了する年度名 |

`/season rollover` の `RolloverView` は `ConfirmView` のサブクラスへ切り出した
（卒業者を選ぶ `UserSelect` はそのまま。確認の作法だけを親へ移した）。

#### ConfirmView に入れた性質

- **実行者チェックを既定動作にした。** View のボタンは interaction を受け取れば
  誰でも押せうるので、所有者の一致を `_is_owner()` で毎回見る。
  「呼び出し側が毎回きちんと書く」に依存しない形にした（ADR 0008 / 0016 の
  「規律ではなく構造で守る」軸）
- **連打で二重に走らせない。** 削除が2回走ると報告する件数も嘘になるため、
  `confirmed` を立ててから処理へ入り、ボタンは即座に無効化する
- **コールバックが例外を投げても `stop()` する**（押しっぱなしにしない）

#### 設計判断

**A. 削除の「方式」は変えていない。**
`/schedule delete` の論理削除化はタスク本文どおり **G3-3 へ回した**。
ADR 0018 / 0024 の「既存データを動かさない」軸に沿い、
このタスクで足したのは**確認ステップだけ**。DB スキーマは無変更でマイグレーション不要。

**B. `count_subtree()` は `delete_subtree()` と辿り方を共有する。**
プレビューの件数と実際に消える件数がずれると、確認の意味がなくなる。
共通の `_subtree_ids()` を切り出して両者が同じ結果を返すようにし、
`test_count_subtree_matches_what_delete_subtree_removes` で固定した。
ついでに `delete_subtree()` は**存在しないノードなら空**を返すようになった
（従来は未知の ID でもその ID 自体を削除対象に数えていたが、
`delete_node()` が 0 件を返すので実害は無かった）。

**C. `on_timeout` は入れていない（G2-4 の範囲）。**
`ConfirmView` は放置されるとボタンが生きたまま無反応になる。これは
G2-4「`on_timeout` がメッセージを編集していない6箇所」と同じ論点なので、
ADR 0014（1タスク＝1ブランチ）に従い混ぜなかった。
**G2-4 は対象が6箇所から変わる**: `RolloverView` は `ConfirmView` に吸収されたので、
`ConfirmView` 1箇所を直せば `/progress remove`・`/schedule delete`・
`/season new`・`/season rollover` の4つが同時に片付く。

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **781 passed, 9 skipped**
  （skip は PG ライブテスト9件。`CLUB_TEST_PG_DSN` 未設定のため）
- 実装を戻すとテストが落ちること:

| 戻した実装 | 落ちたテスト |
|---|---|
| 3コマンドの確認ステップと `count_subtree()` | 13 |
| `ConfirmView` の所有者チェックのみ | 5 |

#### 申し送り

**A. 確認の無い破壊的操作はまだ残っている可能性がある。**
今回はタスクが名指しした3つを塞いだ。`/milestone remove`・`/layer` 系・
`/task delete` などは未調査。`ConfirmView` ができたので、追加は数行で済む。

**B. gotcha `progress-subtree-disappears` とは別件。**
あちらは親の付け替えで循環を作るとツリーから部分木が落ちる話で、
`/progress remove` の確認欠如とは原因が違う。今回の変更では解消しない。

---

### 2026-08-22 — G2-2: schedule_id / task_id のオートコンプリート（ブランチ `fix/g2-2`）

5コマンドが素の `schedule_id: str`、4コマンドが素の `task_id: int` で、
利用者は一覧の出力から ID を手で写していた。`cogs/progress.py` の一括登録の
作法（クラス定義後に `Class.command.autocomplete("param")(Class._method)`）を
そのまま踏襲した。

#### 追加した候補

| コマンド | 候補 | 表示名 |
|---|---|---|
| `/schedule close` / `remind` / `edit-deadline` | **開催中のみ** | `イベント名（〜MM/DD HH:MM）` |
| `/schedule status` / `delete` | 締切済みも含む | 締切済みは `[終了]` を前置 |
| `/task done` / `delete` / `assign` / `priority` | 未完了のみ（`Choice[int]`） | `#ID タイトル` |

締切済みに close は意味がなく remind は嘘の通知になるため、開催中のみに絞る。
絞り込みは名前・ID の部分一致。候補は25件以内・表示名は100文字以内
（Discord の制約）。guild_id でスコープし、DM（guild None）では空を返す。

#### 設計判断

- 純粋関数 `schedule_choices()` / `task_choices()` に切り出し、DB 行 → 候補の
  変換を Discord なしでテストできるようにした（`node_choices()` と同型）
- 補完コールバックは例外を握りつぶして空を返す（補完の失敗で入力を妨げない。
  `_node_autocomplete` と同じ判断）
- 登録先コマンドの検査は `command.get_parameter("schedule_id").autocomplete` の
  有無で行う（gotcha `test-asserts-permission-but-decorator-missing` と同じ
  「実装ではなく登録を見る」形）

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **799 passed, 9 skipped**（skip は PG ライブ9件）
- 実装を戻す（2ファイルを checkout）と `tests/test_autocomplete.py` が
  collection error で赤になることを確認

#### 申し送り

- `/schedule remind` の嘘成功（対象0名でも緑）は **G2-3 の範囲**。
  ここでは候補に出す・出さないだけを直した
- 候補が25件を超えるサーバーでは古い投票が候補から落ちる。
  絞り込み入力で到達できるので実害は小さいが、`/schedule status` を
  日付降順で出しているのはそのため（新しいものが先に出る）

---

### 2026-08-22 — G2-3: 通知の抜け3件（ブランチ `fix/g2-3`）

#### 塞いだ抜け

**1. `/schedule remind` の嘘成功と、定期リマインドの永久沈黙。**
`notify_unanswered()` は「対象ロール未設定」と「未回答0名」をどちらも 0 で
返していたため、呼び出し側は緑の成功 Embed で「対象: 0 名」と表示し、
定期リマインドは送っていないのに `mark_reminder_sent` を打っていた
（後からロールを付けても永久に再送されない）。
戻り値を `int | None` に分離: **None = 対象を特定できない**（ロール未設定・
ロール削除済み・ギルド不可視）、0 = 特定できて0名。
- `/schedule remind`: None ならエラー Embed（target_role の指定方法を案内）
- 定期リマインド: None なら `reminders_log` に **skipped** を記録し、
  送信済みフラグは立てない（ロールを付けた次の tick で送られる）

**2. 作成時のロールメンション。**
対象ロールを指定しても投票メッセージに誰のメンションも無く、対象者は
投票の開始に気付けなかった。**先頭の1通だけ**に
`content=f"{target_role.mention} 日程調整「{title}」の投票が始まりました。"`
を付ける。候補の数だけメンションを鳴らさない（受入基準の `content=...` は
満たしつつ、5候補で5回鳴る形は避けた）。

**3. タスク担当者への通知。**
作成時に担当者を指定しても本人に何も届かなかった。DM →
`discord.Forbidden` なら班チャンネル（`teams.channel_id`）→ 無ければ
既定タスクチャンネル、の順でフォールバックする。
**`/task assign` にも同じ通知を入れた**（タスク本文が名指しするのは
作成フローだが、担当者が決まる入口は assign も同じで、抜けの原因も同じ。
ヘルパ共用で数行の差分。スコープ判断として完了ログに残す）。

#### 切り出したもの

`utils/notify.py` の `dm_each_with_channel_fallback()`。
未回答リマインドにあった「DM を1人ずつ試し、拒否された人はチャンネルで
**1通にまとめて**メンションする」実装を切り出し、`notify_unanswered()` と
タスク割り当て通知の両方が使う。戻り値 `NotifyOutcome`（dm_sent /
fell_back / failed）で「誰に届かなかったか」を呼び出し側が判断できる。

#### 留めたこと（スコープ判断）

- **未回答判定の台帳ベース化（ADR 0025 の覆す条件）は G3-2 へ。**
  ここではエラー表示と skipped 記録まで。Bot トークンを Web 層に
  置かない方針（ADR 0015）はそのまま
- skipped はウィンドウ内の tick ごとに1行記録される（送信済みフラグを
  立てない設計の帰結）。ログ量は締切前1時間 × tick 間隔だけなので許容した

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **815 passed, 9 skipped**（skip は PG ライブ9件）
- 実装を戻す（cogs 3ファイルを checkout、utils/notify.py は残す）と
  `test_schedule_notify.py` / `test_tasks_notify.py` の **8件が赤**

#### 申し送り

- `/schedule remind` の成功 Embed の「対象: N 名」は**通知を試みた人数**で、
  実際に届いた人数ではない（DM 全滅でもチャンネルにまとめて出れば届いている）。
  届いた/届かないの内訳表示は `NotifyOutcome` が持っているので、
  出したくなったら数行で足せる
- 週次の遅延通知など他のリマインダー種別は「送れなかった」を
  どう扱っているか未調査（同じ嘘成功があるかもしれない）

---

### 2026-08-22 — G2-4: タイムアウトした View を画面に反映（ブランチ `fix/g2-4`）

6箇所の `on_timeout` は `item.disabled = True` するだけで `message.edit` を
呼んでいなかった。discord.py の View はサーバー側を編集しない限り表示が
変わらないため、利用者には「ボタンはあるのに無反応」に見えていた。

`utils/views.py` に **`TimeoutAwareView`** を新設し、タイムアウト時に
「時間切れです。もう一度コマンドを実行してください。」の Embed へ
差し替える（`view=None` で押せないボタンを画面に残さない）。

#### 適用箇所

| View | コマンド | 送信側の message 捕捉 |
|---|---|---|
| `ConfirmView`（G2-1 で新設） | `/progress remove`・`/schedule delete`・`/season new`・`/season rollover` | `followup.send` の戻り値 |
| `ProgressView` | `/progress view` | 同上 |
| `ProjectSetupWizard` | `/progress setup` | 同上 |
| `SectionSelectView` | `/task add` | 同上 |
| `SetupWizardView` | `/setup` | `original_response()` |
| `TodoistSetupView` | `/todoist setup` | `original_response()` |

タスク本文の「6箇所」に対し、`RolloverView` は G2-1 で `ConfirmView` に
吸収済みのため、**基底クラス1つ＋既存5 View の継承変更**で全てが片付いた
（G2-1 完了ログの申し送りどおり）。

`RolloverView` の timeout は受入基準どおり **300 → 900 秒**
（卒業者を最大25名選ぶ操作は5分では足りない）。

#### 設計判断

- `on_timeout` の上書きを許さず、文言は `timeout_title` / `timeout_message` の
  上書きで変える。**「disabled にするだけ」の再発をテストで検出する**
  （`cls.on_timeout is TimeoutAwareView.on_timeout` を全対象クラスで検査）
- `message` を覚えさせ損ねた View は従来と同じ挙動に落ちるだけで例外にしない。
  編集失敗（メッセージ削除済み等）も握りつぶす — タイムアウト処理で
  例外を出しても誰も見ていない
- `response.send_message` は戻り値が無いので `original_response()` で取り直す
  （`/setup`・`/todoist setup` の2箇所）

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **824 passed, 9 skipped**（skip は PG ライブ9件）
- 実装を戻す（7ファイル checkout）と `tests/test_views_timeout.py` が赤
  （collection error: TimeoutAwareView が存在しない）

#### 申し送り

- `HelpView`（`cogs/help.py`）は対象に含めなかった。閲覧専用でタイムアウト
  しても実害が無く、タスク本文の6箇所にも入っていない。統一したくなったら
  継承を1行変えるだけ
- G2-1 の ConfirmView 申し送り（「放置でボタンが生きたまま無反応」）は
  これで解消

---

### 2026-08-22 — G2-5: 空状態に「次の1コマンド」（ブランチ `fix/g2-5`）

`utils/embeds.py` に `empty_state_embed(title, situation, next_command)` を追加し、
「〜はありません。」で行き止まりだった空状態に次のコマンドを添えた。

| 箇所 | 従来 | 次の1コマンド |
|---|---|---|
| `/task list`（Todoist 一覧含む） | 該当するタスクはありません。 | `/task add` |
| `/schedule list` | 現在、開催中の投票はありません。 | `/schedule create` |
| `/layer keta-list` | 登録済みの桁名はありません。 | `/layer keta-add` |
| `/report audit` | ログがありません。 | `/schedule create`（通知が発生する運用の起点） |
| `/report attendance-rate` | 集計対象の投票がありません。 | `/schedule create` |
| `/report weekly` | 未完了 0 / 超過 0 / 投票 0 | 下記 |

`/report weekly` はタスク・超過・投票がすべて0件のとき、0/0/0 のサマリーを
出す代わりに「まだデータがありません。`/task add` でタスクを、
`/schedule create` で日程調整を作成できます。」へ切り替えた
（**健全な運用と未開始を見分けられない**問題への対処）。
1件でもデータがあれば従来どおり集計を出す（テストで固定）。

#### 設計判断

- `next_command` は**1つだけ**受け取る。複数の選択肢は situation 側の文に
  書く（`/report weekly` がその形）。「次はこれ」を1つに絞るのが趣旨で、
  選択肢を並べ直すと元の迷いが戻る
- タスク本文の「悪い例」の行番号は現行コードとずれていたため、
  該当コマンドの空状態表示を実体として特定して直した
  （`tasks.py:794` は実際には `/task push` の行で空状態ではない。
  タスク一覧の空状態 `_build_task_list_embed` / `_build_todoist_task_list_embed` を対象にした）

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **833 passed, 9 skipped**（skip は PG ライブ9件）
- 実装を戻す（5ファイル checkout）と `tests/test_empty_states.py` が赤

#### 申し送り

- `/schedule list-closed`（締切済み一覧）の空状態は触っていない。
  「まだ締め切られた投票が無い」は初心者の行き止まりではないため対象外とした
- 空状態が残っている可能性のある他コマンドは未調査（`/member list` 等）。
  `empty_state_embed` ができたので追加は1行
