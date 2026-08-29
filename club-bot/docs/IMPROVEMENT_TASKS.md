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

現行は `SCHEMA_VERSION = 20`（`migrations/019_tools.sql` まで）。

| 版 | migration | タスク |
|---|---|---|
| v16 | `015_layer_session_layer_num_text.sql` | 適用済み（`/layer start` の PG DataError 修正） |
| v17 | `016_schedule_confirmed.sql` | **G3-3（`deleted_flag`）＋ G3-4（`confirmed_option_id`）。1版に2列をまとめてある**（適用済み） |
| v18 | `017_progress_snapshots.sql` | G4-7（進捗履歴。適用済み） |
| v19 | `018_stock.sql` | G4-8（在庫。適用済み）。**G4-9 の工具は同じ版に追加しないこと**（下記の注記） |
| v20 | `019_tools.sql` | G4-9（工具の貸出）。**v19 には足さない**（`_migrate_versioned()` の早期 return で既存 DB に届かないため） |
| v21 | `020_incidents.sql` | G4-10（ヒヤリハット）。v20 が埋まったので1つずらした |

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
        `docs/OPERATION.md` §6）は未実施。申し送りから **G3-7** として再起票する（G3-7 で実施済み）
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

- [x] **G2-6** `/progress edit` の進捗率を検証する。
      `services/progress_tree.py:82-107` の `parse_progress` は解釈不能なら `None` を返し、
      `cogs/progress.py:977-979` がそれをそのまま `manual_progress` に代入する。
      `/progress edit node:主桁 progress:半分` で**既存の進捗率が消え**、緑の成功 Embed が出る。
      - **受入**: コマンド側で `None` を弾き「`0.5` `50%` `50` の形式で指定してください」と返す
      - **検証**: `tests/test_progress_ui.py` にケースを追加
      - **注意**: `parse_progress` の「解釈不能は None」仕様は移行スクリプト用なので**変えない**。
        G0-2 で取り込む `8b9c0f4` がダッシュボード側に同じ検証を入れているので、
        **解釈規則をそちらと揃える**

- [x] **G2-7** Todoist の同期失敗を利用者に見せる。
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

- [x] **G3-1** `/setup` で班長ロールを設定できるようにし、`/set_role` に削除を追加する。
      `cogs/setup_wizard.py:44-47` に `LEADER_ROLE_IDS` が無く、L2 判定の唯一の根拠なのに
      ウィザードから設定できない。`cogs/settings.py:288-295` は**追記専用**で重複チェックも無く、
      1つ外すには全消しするしかない（その間 全班長が L1 に降格）。
      `services/season_service.py:71` の rollover も `members.is_leader` しかリセットせず、
      **毎年ロールIDが積み上がる**。
      - **受入**: `ROLE_SETTINGS` に `LEADER_ROLE_IDS` を追加（`RoleSelect(max_values=25)` で複数選択、
        追記ではなく上書き）。`/set_role` に `action: add|remove` を追加し重複を除去する。
        （**当初 `max_values=5` と書いていたが 25 へ改めた。** 上書き保存なので 5 だと
        班長ロールを6件以上運用しているギルドで L2 判定の根拠を黙って切り捨てる。完了ログの設計判断2）
        `/season rollover` の結果 Embed に「班長ロールの見直し」を促す一文を追加
      - **検証**: `tests/test_setup_wizard.py` に複数ロール保存と重複除去のケースを追加
      - **注意**: 既存の `LEADER_ROLE_IDS`（カンマ区切り）を壊さない。
        重複除去は**保存時のみ**行い、既存値の一括正規化はマイグレーションでやらない（ADR 0024）

- [x] **G3-2** 未回答判定を `members` 台帳の現役メンバーへ寄せる。
      現状 bot 側（`cogs/schedule.py:670`）はロール基準、ダッシュボード側（ADR 0025）は台帳基準で、
      **同じ「未回答」が2つの定義で動いている**。ロール未設定の予定では bot が完全に沈黙する（G2-3）。
      - **受入**: `notify_unanswered` が「`target_role_id` があれば**ロール保持者から
        台帳で退部・休止と分かっている人を除いたもの**、無ければ現役メンバー全員」を対象にする。
        ダッシュボードのピボット表と母集団は、**ロール未設定の予定で台帳の現役が全員
        ギルドに在籍している場合に一致する**
        （**当初「ロール ∩ 現役メンバー」「母集団が一致する」と書いていたが、どちらも改めた。**
        積集合にすると `/member register` が進んでいないギルド — ロール保持者20名・登録済み3名 —
        で対象が0名になり、いま届いている DM が止まる。ダッシュボードは Bot トークンを持たず
        ロールを解決できない（ADR 0015）ため、ロール限定の予定では原理的に一致しない。
        完了ログの設計判断1・3）
      - **検証**: `tests/test_schedule_unanswered.py`（新規）で、ロールあり／なし・
        退部者（`status='alumni'`）が除外されることを検査
      - **注意**: **ADR 0025 を更新する。** 完了ログに新 ADR の草案を書く。
        Bot トークンを Web 層に置かない方針（ADR 0015）は維持する

- [x] **G3-3** `/schedule delete` を論理削除にする。
      現状は投票メッセージを削除してから DB を CASCADE 削除しており、票データが完全消失する。
      `/team-remove` `/skill-remove` `/layer keta-remove` は既に論理削除方式なので方針統一にもなる。
      - **受入**: `schedules` に `deleted_flag` を追加（マイグレーション必要。v17 は G3-4 が使うので
        **同じマイグレーションにまとめる**）。一覧・集計から除外し、`/schedule restore` で戻せる
      - **検証**: `tests/test_schedule_delete.py`（新規）
      - **注意**: 既存の CASCADE 削除に依存しているテストがあれば併せて直す

- [x] **G3-4** `/schedule confirm` — 確定日程の登録と当日リマインド。
      `finalize_schedule`（`cogs/schedule.py:704`）は集計サマリーを投稿して終わりで、
      **「結局いつに決まったのか」がどこにも残らない**。前日・当日のリマインドも無い。
      - **⚠️ `confirmed_option_id` は G3-3 で追加済み（v17）。新しい migration を作らないこと。**
        `_migrate_versioned()` は `version >= SCHEMA_VERSION` で早期 return するため、
        v17 済みの DB は二度と v17 の処理を通らない。後から `_migrate_v17_*` へ ALTER を足しても
        **新規 DB にだけ列がある**状態になり、本番だけ「column does not exist」で落ちる
        （gotcha `bot-wont-start-undefined-column`）
      - **受入**: スキーマ v17（`016_schedule_confirmed.sql`。G3-3 で適用済み）の
        `schedules.confirmed_option_id TEXT NULL` を使う。`/schedule confirm schedule_id option_id`（L2）で
        確定を保存し対象ロールへ告知。`/schedule list` **と `/schedule list-closed`** に確定日を表示。
        前日20時と当日朝に「本日 18:00 ◯◯（場所）」を通知
        （**当初「`/schedule list` に表示」とだけ書いていたが、締切済み一覧を足した。**
        通常フローは締切 → 集計 → 確定で `closed_flag = 1` になるため、
        開催中一覧だけでは確定日が実質どこにも出ない。完了ログの設計判断1）
      - **検証**: `tests/test_schedule_confirm.py`（新規）。リマインドは `reminders_log` の
        日付キーで二重送信を防ぐ
      - **申し送り**: `.ics` 添付（標準ライブラリのみ・外部依存ゼロ）は次イテレーションで。
        Google カレンダー連携は ADR 0013 に反するのでやらない

- [x] **G3-5** 招待直後の案内を確実に届ける。
      （実装は `fix/g3-5`。2026-08-22 に venv を作成して検証済み:
      ruff パス / pytest 870 passed・10 skipped（skip は `CLUB_TEST_PG_DSN` 未設定の
      PostgreSQL ライブテストのみ））
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

- [x] **G3-6** 新入生オンボーディング（`on_member_join` → 班のセルフ選択）。
      新歓期に30〜50人が入るが、`on_member_join` は名前キャッシュを更新するだけ
      （`cogs/name_cache.py:157`）。**bot は新入生の存在を知らず、`/member register` を
      幹部が1人ずつ手打ちしている**。名簿に載らない人には班別通知も出欠催促も届かない。
      - **受入**: ギルド別設定 `WELCOME_ENABLED`（既定 OFF）が ON のとき、参加者へ
        「ようこそ」Embed ＋「班を選ぶ」ボタンを送る。押すと班セレクト →
        `members` へ登録 ＋ `teams.member_role_id` のロール付与。
        DM 拒否時は指定チャンネルでメンション。`/setup` に ON/OFF を追加
      - **検証**: `tests/test_welcome.py`（新規）。OFF のとき何も起きないことを必ず検査
      - **注意**: **既定は OFF**（ADR 0024「既定値で何も起きない状態から始める」）。
        （**`/member setup` は「班選択ウィザード」ではない。** autocomplete 付きの
        L3 スラッシュコマンドで、流用できるのは `MemberRepository.list_teams` と
        `_sync_roles` だけ。Select View は新規に書いた。完了ログの設計判断5）

- [x] **G3-7** ドキュメントを実装に合わせ、GUIDE.md を回帰テストの対象にする。
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

- [x] **G4-1** `/layer stats` — 積層記録の集計。
      `layer_records`（`utils/db.py:256`）に「誰が・どの桁の・何層目を・何分」が全部あるのに、
      **人間が読める形で出すコマンドがゼロ**。`/progress` に出るのは率だけで時間情報が捨てられている。
      - **受入**: `/layer stats [keta] [period:今週|今月|全期間]`（L1）が、桁別
        （完了層数/目標・合計時間・1層あたり平均分・最終作業日）と人別（層数・時間）を表示する。
        目標層数は `progress_spar_links.target_layers`、名前解決は `discord_name_cache`
      - **検証**: `tests/test_layer_stats.py`（新規）。集計は `services/` の純関数に切り出して単体テストする
      - **注意**: 新規テーブル不要。桁引数は既存の `_keta_autocomplete` を再利用

- [x] **G4-2** `/layer cancel` と押し忘れ検知。
      `/layer start` したまま帰宅すると `/layer end` が「1200分」を記録し、
      完了層数が増えるので**進捗率まで水増しされる**。打ち間違えて start した場合の
      取り消し手段も無い（`end` するしかなくゴミ行が残る）。
      - **受入**: `/layer cancel`（L1）が進行中セッションを記録を残さず破棄する。
        経過が `LAYER_SESSION_ALERT_MINUTES`（ギルド別設定・既定240分）を超えたら本人へ DM。
        さらに `LAYER_SESSION_AUTO_CANCEL_MINUTES`（既定720分）で自動 cancel し通知する
      - **検証**: `tests/test_layer_session_alert.py`（新規）
      - **注意**: 通知は `cogs/reminders.py:128` の5分ループに相乗りし、
        送信済み管理は `reminders_log` を使う。**1ギルドの失敗が他ギルドを止めないこと**

- [x] **G4-3** `/report changes` — 監査ログの閲覧と export への追加。
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

- [x] **G4-4** `/me` — 個人サマリー。
      部員視点の入口が無い。自分のタスク・未回答の投票・積層実績・担当ノードがそれぞれ別コマンドで、
      `/task list` は全体を返す。**新入生が「今日自分は何をすればいいか」を1コマンドで確認できない**。
      - **受入**: `/me`（L1・ephemeral）が 未完了タスク（期限順・上位5）／未回答の投票／
        今月の積層時間と層数／担当中の進捗ノード を表示する。
        `user` 引数は L2 以上のみ指定可
      - **検証**: `tests/test_me.py`（新規）。**新規テーブル不要**（既存クエリの合成のみ）

- [x] **G4-5** `/report weekly` の公開版と自動投稿。
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

- [x] **G4-6** `/report member-attendance` — メンバー軸の出欠。
      `/report attendance-rate`（`cogs/reports.py:193`）は投票ごとの ok 率で、
      **「最近来ていない人」が特定できない**。「3回連続で未回答」は退部のほぼ確実な予兆。
      - **受入**: `/report member-attendance [months:3]`（L2 以上・**ephemeral 固定**）が、
        締切済み投票についてメンバー別の回答率・ok 率・連続未回答数を回答率の低い順に表示する
      - **検証**: `tests/test_member_attendance.py`（新規）
      - **注意**: 晒しにならないよう公開オプションを付けない。母集団は G3-2 と揃える

- [x] **G4-7** `progress_snapshots` — 進捗の履歴とバーンダウン。
      `services/milestone_service.py:9-14` が自ら書いているとおり、履歴が無いため
      ペースが「作成日→最終更新日の平均」でしか出せず判定不能が多発する。
      「先週から何%進んだか」も分からない。
      - **受入**: スキーマ v18（`017_progress_snapshots.sql`）で
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

- [x] **G4-8** `/stock` — 資材・消耗品の在庫と発注アラート。
      人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
      カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。
      発注判断は「残量が閾値を割った」という bot が自動で見張れる条件。
      - **受入**: スキーマ v19（`018_stock.sql`）で `stock_items` / `stock_movements`。
        `/stock list`（閾値割れを強調）/ `/stock add`（L2）/ `/stock use`（L1）/
        `/stock set-threshold`（L2）。閾値を割ったら即1回通知し、以降は朝の通知に含める
      - **検証**: `tests/test_stock.py`（新規）
      - **注意**: マスタ管理は `layer_ketas`（有効フラグ・オートコンプリート付き）と同型にする。
        **品目名の初期値をコードに持たない**（サークルごとに違う）

- [x] **G4-9** `/tool` — 工具・機材の貸出管理。
      `/layer start` → `/layer end` とまったく同じ「開始→進行中→終了」モデル。
      - **受入**: `/tool list|borrow|return|add|remove`。返却予定日超過で本人へ DM
      - **注意**: G4-8 と**同じ Cog**（`cogs/inventory.py`）にまとめる。
        督促ロジックは G4-2 の押し忘れ検知を共用する

- [ ] **G4-10** `/incident` — ヒヤリハット・事故報告。
      工房での切削・溶剤・高所作業・機体運搬・テストフライトと危険度が高く、
      大学から安全管理体制の提示を求められることもある。今は雑談に流れて消える。
      - **受入**: スキーマ **v21**（`020_incidents.sql`。**当初 v20 / `019_incidents.sql` と書いていたが、G4-9 の工具が v20 を使ったので1つずらした**）。`/incident report`（L1）が Modal
        （発生日時 / 場所 / 何が起きたか / けがの有無 / 再発防止案）を開き、幹部ロールへ通知する。
        `/incident list`（L3）。**匿名フラグ**を持ち、報告者IDは DB に保持するが表示しない
      - **検証**: `tests/test_incident.py`（新規）。匿名時に報告者名が Embed に出ないことを検査
      - **注意**: `docs/PRIVACY.md` に収集項目を追記する。
        Modal は `cogs/todoist_admin.py:39` の実装を流用

- [ ] **G4-11** `log_to_channel` の送信先を同一ギルドに限定する。
      `bot.py` の `log_to_channel` は `BOT_LOG_CHANNEL_ID` を `self.get_channel`
      （bot 全体のチャンネルキャッシュ）で解決し、`guild_id` 未指定時は
      `config.bot_log_channel_id`（env 由来。**全ギルドの GuildConfig に配られる**）へも
      フォールバックする。あるギルドの設定が別ギルドのチャンネル ID を指していると、
      **他テナントの運用ログがそのサーバーへ流れる**。
      G3-5 で追加した `send_guild_notice` は `guild.get_channel` で同一ギルド内に限定済みなので、
      同じ守り方へ揃える。
      - **受入**: 送信先を「その `guild_id` に属するチャンネル」に限定する
        （`guild.get_channel` で解決、または `channel.guild.id` を検査）。
        env フォールバックは対象ギルドに実在するチャンネルのときだけ使う
      - **検証**: `tests/test_multi_tenant.py` に、他ギルドのチャンネル ID を設定した状態で
        送信先が0件になることを検査するケースを追加
      - **注意**: レガシー単一ギルド運用（`GUILD_ID` 指定）の後方互換を壊さない。
        起源は G3-5 のプラン審査（acm-plan-reviewer）で指摘された既存の穴

- [ ] **G4-12** 投票メッセージの「未回答者数」を催促の母集団へ揃える。
      G3-2 で催促（`notify_unanswered`）の母集団は「ロール保持者 − 台帳の退部者」または
      「台帳の現役」になったが、`services/schedule_service.py` の `build_option_embed` が出す
      **「未回答者数」はロール基準のまま**で、しかも候補単位で数えている。
      部員が最初に見る数字はこちらなので、実際に DM が飛ぶ相手と食い違う
      （`target_role` 未設定なら表示は `-` のまま DM は飛ぶ）。
      - **受入**: 投票メッセージの未回答者数が `select_unanswered_targets` と同じ母集団・
        同じ単位（**予定単位**）で出る
      - **注意**: `build_option_embed` / `build_summary_embed` に台帳を渡すには引数追加が必要で、
        これは **ADR 0009 の「完了条件2」**（`guild_id` を明示引数で受ける形へ改修し、
        `cogs/schedule.py` が `for_guild` プロキシを渡さなくなる）そのもの。
        ADR 0009 の未実装分と**同じイテレーションで**扱う
      - **出どころ**: G3-2 のゲート1・ゲート2（`build_option_embed` を同じ差分に混ぜると
        ADR 0014 に反するため分離した）

- [ ] **G4-13** オートコンプリートの登録検査が空振りしている。
      `tests/test_autocomplete.py:233` と `:309` の
      `assert param is not None and param.autocomplete is not None` は、
      discord.py の公開 API の `autocomplete` が **`bool` を返すプロパティ**なので
      `False is not None` → `True` となり、**補完が1つも登録されていなくても必ず通る**。
      G2-2 で追加した「オートコンプリートが正しいコマンドに付いていること」の2テストが
      現在なにも担保していない（実測で確認済み。`/schedule create` の `title` で
      `public=False` → 判定 `True`）。
      - **受入**: `Command._params["<name>"].autocomplete.__name__` を検査する形へ書き換え、
        登録行を消すと落ちることを実測する（G3-3 の
        `test_restore_autocomplete_is_registered_on_the_command` が正しい書き方）
      - **注意**: private 属性 `_params` に依存するが、公開 API では
        「どのコールバックが束ねられているか」を取れない。消えたときは
        `AttributeError` / `KeyError` で大きな音を立てて落ちるので、テスト専用の依存として許容する
      - **出どころ**: G3-3 のゲート2（差分監査）

- [ ] **G4-14** `/schedule remind` に締切済みのガードが無い。
      `cogs/schedule.py` の `remind` は `closed_flag` を見ないため、L2 が ID を直打ちすれば
      **締切済み・復元済みの予定でも未回答者へ DM が飛ぶ**（オートコンプリートは開催中のみなので
      踏みにくい）。`edit-deadline` は既に `closed_flag` を見て断っている。
      - **受入**: `remind` が締切済みを断る。文言は `edit-deadline` と揃える
      - **注意**: 締切済み全般の挙動変更なので G3-3 には混ぜなかった（ADR 0014）
      - **出どころ**: G3-3 のゲート2（差分監査）

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
| ~~G3-7~~ | 「restart 前に `pg_dump -Fc` を取る」を `docs/DASHBOARD_SETUP.md` §11 と `docs/OPERATION.md` **§6** へ追記（マイグレーションに down が無い） | G1-8 の残件。**G3-7 で実施済み**（当初 G3-6 と書いていたが、その枠はオンボーディングで埋まっていた） |
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

---

### 2026-08-22 — G2-6: `/progress edit` の進捗率検証（ブランチ `fix/g2-6`）

`parse_progress` は解釈不能なら None を返し（移行スクリプト用の仕様。
**変えていない**）、コマンド側がそれをそのまま `manual_progress` へ代入して
いた。`/progress edit node:主桁 progress:半分` で**既存の進捗率が消え**、
緑の成功 Embed が出る。

`Progress._parse_progress_input()` を挟み、(成立したか, 値) に分けて
コマンド側で弾く。エラーは
「進捗率「{入力}」を解釈できません。`0.5` `50%` `50` の形式で指定してください。」

#### 解釈規則（ダッシュボード側と揃えた）

G0-2 で取り込んだ `8b9c0f4` がダッシュボード側
（`repositories/table_repository.py` の progress 列検証）に入れた規則と同一:

| 入力 | 結果 |
|---|---|
| `0.5` / `50%` / `50` / `１００％` | 受理（1 より大きい数値は % とみなす） |
| 空・空白のみ | クリア（None を保存） |
| `半分` など解釈不能 | エラー（既存値は変更しない） |

#### スコープ判断

**`/progress add` にも同じ検証を入れた。** タスク本文は edit を名指しするが、
add も同じ `parse_progress(progress)` 素通しで「progress:半分 が黙って
未入力になり緑が出る」形（既存値が無いぶん実害が軽いだけで、原因は同一行）。
edit だけ直すと2つの入口で挙動が食い違う。

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **836 passed, 9 skipped**（skip は PG ライブ9件）
- 実装を戻す（progress.py を checkout）と新テスト2件が赤
  （既存値の保持・add のエラー化）

#### 申し送り

- エラー文言はダッシュボード側（「には 0.5 または 50% の形式で入力して
  ください。」）と**基調は同じだが完全一致ではない**（コマンド側は入力値の
  エコーと `50` の例示がある）。文言まで一本化したくなったら
  `_invalid_progress_embed` とテーブル側 `InvalidValueError` を共通化する
- gotcha `progress-stops-after-dashboard-edit` とは別件（あちらは同期停止）

---

### 2026-08-22 — G2-7: Todoist の同期失敗を利用者に見せる（ブランチ `fix/g2-7`）

`/task done`・`/task delete` は Todoist 側の操作が `except TodoistError: pass`
で握りつぶされ、`log.warning` すら出ていなかった。`/task done` は必ず
「完了にしました」と返るのに Todoist 側は未完了のまま残り、翌朝の通知に
出続ける（gotcha `todoist-completed-tasks-not-detected` の同期の片方向性と関連）。

#### 変更

- **ローカルの完了・削除は従来どおり維持**（受入基準どおり。Todoist が
  落ちていても Discord 側の運用は止めない）
- 成功 Embed に同期結果を明記:
  「⚠️ Todoist 側の完了に失敗しました。Todoist 上で直接完了にしてください。」
  （delete は「直接削除してください」）
- `log.warning` に guild_id・task_id・todoist_task_id を出す
- 3つ目の握りつぶし（`/task section-link` のセクション名解決）にも
  `log.warning` を追加（名前解決だけの失敗なので紐付けは続行。
  利用者向けの文言は変えない）

#### 検証

- `ruff check .`: パス
- `pytest tests/ -q -rs`: **839 passed, 9 skipped**（skip は PG ライブ9件）
- `tests/test_todoist_guild_scope.py` に3ケース追加（受入基準の指定どおり）。
  実装を戻すと2件が赤（成功側の「⚠️ が出ない」は戻しても緑＝回帰検出は
  失敗側の2件が担う）
- テスト前提の訂正: `/task delete` は物理削除ではなく **archived への論理削除**
  だったため、テストの検証を `status == "archived"` に合わせた

#### 申し送り

- gotcha `todoist-completed-tasks-not-detected` は**未解消**（あちらは
  Todoist→bot 方向の同期の話。今回は bot→Todoist 方向の失敗可視化）
- `/task add` 系の TodoistError は元からエラー表示があり触っていない。
  定期同期（`cogs/progress.py` の periodic_sync）の失敗は #bot-log へ
  通知される既存実装がある

### 2026-08-22 — G3-5: 招待直後の案内を確実に届ける（ブランチ `fix/g3-5`）

ADR 0017 の最小権限招待では `manage_channels` を要求しないため、README の
招待URLで入れたサーバーには `#bot-log` が作られない。それなのに案内は
`log_to_channel`（bot-log 限定）で送られており、**ほとんどの新規サーバーで
誰にも届かないまま破棄されていた**。さらに権限不足で何も作れなくても
`AUTO_SETUP_DONE` を立てていたため、GUIDE.md の「権限を付けて再招待」という
復旧手順が効かなかった（settings は退出後も猶予期間だけ残るため）。

#### 変更

- `bot.py`
  - `build_setup_guidance(auto_setup_ok)` を追加。`/setup` `/setup-status` `/help`
    を明記する。自動作成できていないときだけ、確実に効く順（①自分で作って
    `/setup` で指定 ②Manage 系権限を付ける。同名があると作られない旨つき）で
    手順を添える。**最小権限招待では未作成が既定の経路**なので「失敗」調にはしない
  - `send_guild_notice()` / `_notice_channels()` を追加。bot-log →
    `guild.system_channel` → 送信可能な最初のテキストチャンネル、の順で
    **1箇所にだけ**送る。試行は `MAX_NOTICE_CHANNEL_ATTEMPTS`（5件）まで
  - `on_guild_join` の案内を `log_to_channel` から `send_guild_notice` へ差し替え
  - `_ensure_guild_setup` を `-> bool` にし、マーカーを
    **`AUTO_SETUP_COMPLETED_AT`（新キー）へ移した**。全部揃ったときだけ立て、
    立っていたら自動作成をやり直さない。**旧 `AUTO_SETUP_DONE` は読まない**
    （旧実装が権限不足のギルドにも立てていたため。互換のため書き込みは残した）
  - `_auto_create_roles` / `_auto_create_log_channel` を `-> bool` 化し、
    「設定済みか」の判定を settings 行ではなく**実効設定**
    （`config.for_guild`。環境変数フォールバックを含む）＋
    **そのギルドに実在するか**で行う
- `cogs/help.py` — `collect_setup_status()` に「幹部ロール」(`EXEC_ROLE_ID`) を追加。
  案内が `/setup-status` を入口に指すのに、L3 判定の実体である `EXEC_ROLE_ID` を
  見ていなかった（「すべて設定済み」と出たサーバーで幹部が L3 を使えない形）
- `README.md`（招待直後に何が起きるか・`/setup` の項目）/ `docs/GUIDE.md` Step 1 /
  `docs/MULTI_TENANT_MIGRATION.md` §5 を実装に合わせた。GUIDE の復旧手順は
  案内文と同じ順序・同じ但し書きに揃えている

#### 設計判断

- **冪等性を二段構えにした。** (1) 新マーカー `AUTO_SETUP_COMPLETED_AT`
  （揃ったときだけ立てる）があればやり直さない。(2) マーカーが無くても、
  実効設定に ID があるものは作らない。旧 `AUTO_SETUP_DONE` を読まないので、
  誤って立っている既存ギルドも復旧できる。マーカーを完全に捨てなかったのは、
  管理者が `/settings_delete`・ダッシュボードの空値 PATCH で消した設定を
  次の起動で復活させないため（ADR 0024「明示的な操作でだけ変える」）
- **「設定済みか」は settings 行ではなく実効設定で判定する。** `docs/SETUP.md`
  は今も `.env` に `EXEC_ROLE_ID` 等を書く手順を案内しており、解決順は
  ギルド別 DB 設定 > 環境変数。settings 行だけを見ると、env で運用している
  ギルドに空の `幹部` ロールを作って保存し、**実効設定を誰も持たないロールへ
  差し替えて幹部が L3 を失う**。さらに ID がそのギルドに実在するかまで見る
  （解決できないときは作り直さない。`set_if_absent` では古い行を直せず、
  毎起動ロールを作り続けるため）
- **同名ロールには ID を紐付けない。** 名前が一致するだけのロールを
  `EXEC_ROLE_ID` にすると、そのロールを持つ人へ黙って L3 権限を配ることになる
  （ADR 0024 の「既定で何も起きない」に反する）。作成をスキップして未設定のまま残し、
  `/setup` で明示指定させる（`/setup-status` が未設定として拾う）
- **同名 `#bot-log` は採用する。** チャンネルは権限を配らないため。ただし
  `permissions_for(guild.me).send_messages` を確認してから採用する
- **`log_to_channel` の仕様は据え置き。** 運用ログの宛先を一般チャンネルへ
  広げると、他の運用ログまで部員の目に流れる。案内だけを別関数に分けた
- ログの粒度: 権限が無いだけ・同名があるだけ（定常状態）は `log.info`、
  API 失敗と ID 保存失敗は `log.warning` / `log.error`。最小権限招待では
  未作成が正常なので、毎起動 WARNING を全ギルド分吐かせない

#### 既存ギルドで次回起動時に起きる変化（DB は書き換えていない）

- 誤って `AUTO_SETUP_DONE` が立っている既存ギルドでも、**新マーカーが無いので
  自動セットアップが1度だけ再試行される**（旧値は読まない・消さない）
- その結果、`manage_roles` / `manage_channels` を持つ既存ギルドで
  `EXEC_ROLE_ID` 等が settings にも env にも無く、同名ロールも無ければ、
  **`幹部` / `Bot管理者` が新規作成される**
- `bot-log` という名前のチャンネルがある既存ギルドは `BOT_LOG_CHANNEL_ID` が
  自動で入り、運用ログがそこへ流れ始める
- 揃ったギルドには `AUTO_SETUP_COMPLETED_AT` が立ち、以降はやり直さない

#### 検証

- **`ruff check .` / `pytest tests/ -q -rs` を実行できていない。**
  この worktree（`acm_bot_auto`）に `club-bot/venv` が無く、セッションの権限設定で
  `python -m venv` / `pip install` が実行できなかった（システム Python 3.14 には
  ruff も pytest も未導入）。次で環境を作ってから実行すること:
  `python -m venv venv` →
  `venv\Scripts\python.exe -m pip install -r requirements.txt -r dashboard/requirements.txt ruff pytest` →
  `venv\Scripts\python.exe -m pytest tests/ -q -rs`
- `tests/test_guild_foundation.py` に23ケース追加（受入基準の指定ファイル。
  bot-log 無し・権限無しの2ケースを含む）。`__main__` の手動実行リストにも追加した
- `tests/test_help.py` は「幹部ロール」追加に伴う既存2ケースを更新
- ゲート2の test-adversary は**実測できず静的分析**。差し戻し9件のうち8件は
  赤くなる見込みで、検出できない穴として指摘された
  `config.invalidate_guild` / 成功時の文面 / on_ready 経路 / 候補の重複排除 /
  `guild.me is None` を、テスト側を直して塞いだ（キャッシュを温めてから実行、
  `on_ready` を実際に通す、など）
- ゲート2の diff-auditor の指摘7件を反映（消した設定の復活・env 設定の上書き・
  案内文の実現性・README の齟齬・`send` を持たないチャンネル・
  MULTI_TENANT_MIGRATION の古い記述・未追跡ファイルをコミットに混ぜない）

#### 申し送り

- **検証済み（2026-08-22）。** venv を作成し ruff / pytest を実行、
  870 passed・10 skipped（PG ライブテストの想定 skip）で緑。
  ruff が ISC004 ×3（`build_setup_guidance` 内の暗黙文字列連結）を検出したため、
  意図どおりの連結であることを確認のうえ括弧で明示した
- 素の Windows 環境では `zoneinfo` に **`tzdata` パッケージが必要**だが
  requirements.txt に無く、fresh venv では全テストが collection で落ちる
  （`ZoneInfoNotFoundError: Asia/Tokyo`）。今回は venv へ手動インストールで対応。
  requirements への追加（`tzdata; sys_platform == "win32"`）は別途検討
- `log_to_channel` は `self.get_channel` と env フォールバックで**他ギルドの
  チャンネルへ運用ログを送りうる**（今回追加した `send_guild_notice` は
  `guild.get_channel` で同一ギルドに限定済み）。**G4-11 として起票した**
- IMPROVEMENT_REPORT P1-19 の後半「`/setup` に『不足しているものを今すぐ作る』
  ボタン」は未実装（受入基準に無いためスコープ外）
- スキーマ変更・マイグレーションは無し。PostgreSQL 固有の新規 SQL も無い

### 2026-08-28 — G3-1: `/setup` に班長ロール、`/set_role` に削除（ブランチ `fix/g3-1`）

L2 判定の唯一の根拠である `LEADER_ROLE_IDS` が `/setup` から設定できず、`/set_role` は
追記専用で重複チェックも無かった。1つ外すには全消しするしかなく、
**その間は全班長が L1 に降格**していた。

- ruff: `All checks passed!`
- pytest: **895 passed, 10 skipped**（着手前は 870 passed, 10 skipped。
  skip は `CLUB_TEST_PG_DSN` 未設定の PostgreSQL ライブテストのみで件数は据え置き）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `config.py` | `MULTI_ROLE_KEYS`（複数値ロール設定キー）を追加。`/setup` と `/set_role` が同じ定義を見るよう1箇所に置いた |
| `cogs/setup_wizard.py` | `ROLE_SETTINGS` に班長ロールを追加。`build_setup_embed(gconf, selected_key, notice)` が `list[int]` を複数メンションで描画し、**空リストを「未設定」として数える**。`select_item` を `edit_message` 化。上限超過ギルドでは選ばせる前に断る。`select_role` は複数値をカンマ結合で上書き保存し、単数キーへの複数選択を拒否 |
| `cogs/settings.py` | `split_role_tokens()` / `merge_role_ids()` / `stale_role_warning()`（純関数）を追加。`/set_role` に `action: add|remove`。変更が無いときは保存しない。保存後に実効設定を解決し直して警告する |
| `cogs/season.py` | `rollover_result_embed` に班長ロールの見直しを促す一文（ロールIDは自動で消さない） |
| `cogs/help.py` | `/setup-status` の班長ロールの hint を `/setup` 優先へ |
| `docs/` | `GUIDE.md` / `OPERATION.md` / `SETUP.md` を実装に合わせた |

新規テスト `tests/test_settings_role.py`（16件）＋ `tests/test_setup_wizard.py` に7件、
`tests/test_seasons.py` に1件を追加した。

#### 設計判断

**1. `select_item` を `edit_message` にしたのが本質。**
最初の方針は「`self.select_role.max_values` を切り替える」だけだった。だが従来の `select_item` は
`interaction.response.send_message` で**別の ephemeral を送るだけ**で親メッセージの View を
送り直していない。Python 側の属性を変えてもクライアントが持つコンポーネント定義は
`max_values=1` のままなので、**複数選択が一生発生しない**。
しかも「`view.select_role.max_values == 25` を見るテスト」は緑になる。
gotcha `test-asserts-permission-but-decorator-missing` と同型なので、テストは
`edit_message` に渡された View まで見る形にした。

**2. `max_values` は受入基準の 5 ではなく 25（RoleSelect の上限）。**
`/setup` は**上書き**保存なので、5 にすると班長ロールを6件以上運用しているギルドで
L2 判定の根拠を黙って切り捨てる。25 でも超えるギルドはありうるので、
`select_item` の時点で `disabled` にして `/set_role action:remove` へ誘導し、
保存側にも同じガードを置いた（**選ばせてから捨てない**）。受入基準の本文も 25 に直した。

**3. 保存が実効設定に反映されないことがあるので、成功と言い切らない。**
`config.for_guild()` は DB 値が空のときグローバル（env 由来 / 起動時に読んだ値）へ
フォールバックする（`config.py:315-317` の `if leader_ids:`）。最後の1件を外すと
**DB は空なのに L2 は残る**。G2-7 と同型の嘘になるため、保存後に `force_reload=True` で
解決し直し、**保存値に無いロールが有効なら**警告を出す。

判定を「外した ID が残っているか」にすると穴が残る（差分監査で実測）。
DB=`111` / env=`222` の状態で `111` を外すと、**保存していない `222` が L2 を得る**のに無言だった。
「解決結果のうち保存値に無いもの」へ広げ、回帰テストを付けた。

文面は原因で分けた。**env が設定されている場合しか「`.env` を直せ」と言わない。**
`GUILD_ID` 指定のレガシーギルドでは起動時に読んだ値がプロセス内に残るだけなので
（`config.load_from_db` は `if not self.leader_role_ids:` で一度入った値を減らさない）、
その場合は「再起動で反映される」と案内する。存在しない `.env` の行を直せと言うのは、
このタスクが潰そうとしている失敗と同型。

**4. 保存は成功させ、DB は巻き戻さない。** 3 の警告時に保存自体を中止すると、
`.env` を直して再起動しても何も戻ってこない。G2-7 の「ローカル完了は維持したまま
同期結果を明記する」と同じ形にした。

**5. 変更が無いときは `settings.set()` を呼ばない。**
既存値の非数値トークンは保存時に除かれる（`get_int_list` が元から無視するので実効挙動は不変）が、
**何も変えていない操作で消える**のは ADR 0024 の「明示的な操作でだけ変える」に反する。

**6. `/season rollover` は `LEADER_ROLE_IDS` に触らない。**
勝手に消すと新体制が設定するまで全班長が L1 に降格する（ADR 0024）。
`services/season_service.py` は不変のまま、結果 Embed で見直しを促すだけにした。

**7. 受入基準からの逸脱（すべて意図的）。**
- `max_values` 25（設計判断2）
- テストファイルは指定の `tests/test_setup_wizard.py` に加えて **`tests/test_settings_role.py` を新設**。
  `/set_role` の実体は別 Cog（`cogs/settings.py`）で、ウィザード側のテストだけでは
  コマンドがヘルパを通っていることを測れないため

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE ×2 → 「織り込めば APPROVE 相当」 | 1回目に7件（env フォールバックで remove が効かない／既存テストの直し方／コマンド経路のテスト／`edit_message` 化の3条件／`max_values` の付帯条件／黙って正規化しない／ドキュメント追随）、2回目に3件（警告文の原因別分岐・no-op で保存しない・権限テストの形） |
| ゲート2（acm-diff-auditor） | FINDINGS → **CLEAN** | 6件（残留警告の判定漏れ／SETUP.md の記述／OPERATION.md の未エスケープ `\|`／テストの後始末が `finally` の外／`MULTI_ROLE_KEYS` の二重定義／`ruff format` の回帰）を修正 |
| ゲート2（acm-test-adversary） | **EFFECTIVE** | 16項目すべてで、戻すと赤くなることを実測 |

**ゲート2は同時ではなく逐次で回した。** test-adversary は実装を一時的に戻す手法なので、
同時に走らせると diff-auditor が中途半端な `git diff` を読むため。ループ手順からの意図的な逸脱。

#### test-adversary の実測（抜粋）

| 戻した実装 | 落ちたテスト |
|---|---|
| `ROLE_SETTINGS` から班長ロールを削除 | 5 |
| `build_setup_embed` の複数値分岐 | 3 |
| `select_item` を `send_message` へ戻す | 2 |
| `max_values` の切り替えだけ削除 | 1 |
| 上限超過ガード | 1 |
| `select_role` の複数値分岐 | 1 |
| 単数キーへの複数選択拒否 | 1 |
| `/set_role` を HEAD の実装へ全戻し | 9 |
| `merge_role_ids` の重複判定 | 1 |
| `changed` を常に True | 3 |
| `stale_role_warning` の呼び出し | 3 |
| **`leftover` を `merge.removed` ベースへ戻す** | **1**（差分監査で見つけた穴の回帰テスト） |
| 警告文の原因別出し分け | 1 |
| `split_role_tokens` の非数値検出 | 3 |
| `rollover_result_embed` の一文 | 1 |
| `@app_commands.check(is_admin)` を外す | 1 |

**adversary が見つけた「戻しても緑のまま」の穴3件は、その場で塞いだ**（塞いだ後に
戻して落ちることも実測済み）:

| 穴 | 意味 | 対応 |
|---|---|---|
| `is_admin`(L4) → `@require(Level.L2)` への**差し替え**が無検出 | ロール無しメンバーはどのレベルでも拒否されるので素通りする。`/set_role` は L4/L2 判定の根拠そのものを書き換えるコマンドなので、**班長が自分を管理者へ昇格できる** | `command_required_level(Settings.set_role) == Level.L4` を追加 |
| `MAX_MULTI_ROLE_VALUES` を 5 に戻しても無検出 | テストがシンボル参照だけで、意図（25 を選んだ理由）が固定されていなかった | 値そのものを assert |
| `_set_multi_role` の `_after_change` 削除が無検出 | 保存後のキャッシュ破棄とグローバル再読込が消えても気づけない | 実効設定が更新されることを検査するケースを追加 |

#### 次タスクへの申し送り

- **`config.py` の解決規則そのものは変えていない。** 「settings に行があるなら空でも正」への変更は
  L2 の解決規則の変更で、G3-1 の範囲（ADR 0014）を超える。現状は警告で運用を支えている。
  **別タスクとして起票が要る**
- レガシーギルド（`GUILD_ID` 指定）のグローバル値は `config.for_guild()` の種付け（`config.py:284`）
  経由で**同一プロセスの他ギルドにも配られる**。G4-11（`log_to_channel` の同一ギルド限定）と
  同じ根から来ている
- `cogs/settings.py` には監査ログ（`AuditLogRepository`）の記録が**一切ない**。
  L2 判定の根拠を書き換えるコマンドなのに、復元材料が ephemeral メッセージしかない。別タスク候補
- `RoleSelect(default_values=...)`（discord.py 2.4+）を使うと「上書き」が
  「今の集合を編集する」操作になり、上限超過の扱いも素直になる。
  `requirements.txt` の下限は `>=2.3.0` なので今回は見送った。
  **G3-6 で `DynamicItem`（2.4+）のために下限を上げる予定なので、その後なら採用できる**
- **PostgreSQL 実機では検証していない**（`CLUB_TEST_PG_DSN` 未設定・Docker なし）。
  SQL の追加・変更はゼロで、保存先は既存の `settings.setting_value TEXT NOT NULL`。
  ただし**全消し時に空文字 `""` を書く経路は新規**で、ここだけは PG 未確認
- 採番の食い違い: G1-2 の申し送りは「通知キーの一本化（`DEFAULT_PROGRESS_CHANNEL_ID` へ寄せる）は
  G3-1 で扱う」と書いているが、G3-1 は班長ロールで埋まっている。**未起票**（破壊的変更なので別途起票が要る）
- ブランチは `fix/g3-5` から分岐した（ADR 0014 の1タスク1コミットは保っているが、
  **マージは順番どおりに行う必要がある**）。以降の G3 も連鎖する

### 2026-08-28 — G3-2: 未回答判定を members 台帳へ（ブランチ `fix/g3-2`）

bot 側（ロール基準）とダッシュボード側（ADR 0025 の台帳基準）で「未回答」が二重定義になっており、
対象ロール未設定の予定では bot が完全に沈黙していた。

- ruff: `All checks passed!`
- pytest: **914 passed, 10 skipped**（着手前は 895 passed, 10 skipped。skip 件数は据え置き）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `services/schedule_service.py` | 純関数 `select_unanswered_targets()` を追加。ID は TEXT 列（名簿）と int（`Member.id`）が混ざるため関数内で `str()` 正規化する |
| `cogs/schedule.py` | `notify_unanswered` が上を使う。`_roster_ids()` は既存クエリ2回の差で現役・退部を作る。`_member_of()` は非数値 `user_id` を警告してスキップ。`/schedule remind` は 0 名を成功と別扱いに |
| `cogs/reminders.py` | `count == 0` でも `mark_reminder_sent` を打たない。reason の断定を外した |
| `docs/` `dashboard/README.md` | 母集団の決まり方とズレを明記 |

新規テスト `tests/test_schedule_unanswered.py`（16件）＋ `tests/test_schedule_notify.py` に3件。

#### 設計判断

**1. 積集合ではなく差集合。受入基準を書き換えた。**
受入基準は「ロール ∩ 現役メンバー」だったが、そのまま実装すると
**`/member register` が進んでいないギルドで今日届いている DM が止まる**。
ロール保持者20名・登録済み3名は導入から数週間のギルドで普通に起きる状態で、
∩ を採ると対象0名になる。しかも `0` は「対象は特定できた」を意味するので
`mark_reminder_sent` が打たれ、**誰にも DM が飛ばないまま「成功・0名」**になる
（G2-3 が塞いだ不具合の再発）。

採ったのは「ロール保持者から**台帳で退部・休止と分かっている人だけ**を差し引く」形。
名簿に無い人は「退部か未登録か区別できない」ので残す（ADR 0021 / 0024）。
受入基準の「`status='alumni'` が除外される」は満たしている。

**2. `count == 0` でも送信済みにしない（`cogs/reminders.py` を触った理由）。**
受入基準に無い変更だが、**入れないと G3-2 が新しい永久沈黙を作る**。
「ロールにメンバーが0人」を `0` と返すか `None` と返すかは、
偽警報と永久沈黙のトレードオフに見えていた。だが損害の実体は戻り値ではなく、
`0` を受けた定期リマインドが送信済みフラグを立てることだった。
G2-3 の原理「送っていないなら送信済みにしない」を `count == 0` にも適用すると両方消える。

副作用は締切前1時間の再評価が増えることだけ（`list_reminder_candidates` の窓は
`[now, now+1h]` なので tick は最大12回で有限）。
おまけに「全員回答済みで送信済み → その後リアクションを外した人が居ても二度と催促されない」
という既存の穴も塞がった。

**3. ダッシュボードとの一致は原理的に無理。受入基準を書き換えた。**
ダッシュボードは Bot トークンを持たない（ADR 0015）のでロールを解決できず、
さらに**ギルド在籍を検査していない**（`dashboard/routers/tables.py:167`）。
ズレは1軸から2軸になったので、`dashboard/README.md` に両方を明記した。

**4. ロール保持者が1人も見えないときは `None`。名簿へフォールバックしない。**
当初は `0` を返す方針だった（誰も付けていないロールは正常な状態で、
そこで赤いエラーを出すのは偽警報になるため）。だが差分監査の指摘で覆した。
誰も付けていないロールとメンバーキャッシュの欠落は区別できないので、
「全員回答済み」とは主張しない。**名簿へフォールバックすると、
班限定の予定の催促がサークル全員へ飛ぶ**ので、そちらも避けた。

これに伴い `tests/test_schedule_notify.py` の
`test_notify_unanswered_returns_zero_when_everyone_answered` のフィクスチャを差し替えた。
元は `members=[]` のロールで「0名」を作っており、それは「全員回答済み」ではなく
「ロールに誰も居ない」状態で、**テスト名と中身が食い違っていた**。
ロール保持者1名が回答済み、という本来の意味に直した（G2-3 の契約は維持）。

**5. `build_option_embed` の「未回答者数」は揃えていない。**
引数追加＝呼び出し4〜5箇所の変更は ADR 0009 の完了条件2そのもので、
G3-2 に混ぜると ADR 0014 に反する。**G4-12 として起票した。**

#### ADR 0025 の更新草案

> ## 0025（改訂案）. 出欠の「未回答」の母集団
>
> **文脈の追加**: 2026-08-28（G3-2）まで、bot の催促はロール基準、ダッシュボードは
> 台帳基準で、同じ「未回答」が2つの定義で動いていた。対象ロール未設定の予定では
> bot が完全に沈黙していた（ロールが無いと対象を特定できないため）。
>
> **決定（改訂）**: 母集団を次のように定める。
> - **bot（催促）**: `target_role_id` があれば**ロール保持者 − 台帳で退部・休止と
>   分かっている人**。無ければ**台帳の現役 ∩ ギルド在籍**
> - **ダッシュボード（ピボット表）**: 従来どおり台帳の現役（`active_flag=1` かつ
>   `status='active'`）
>
> **理由**: bot を「台帳基準」に揃えなかったのは、台帳が部分的にしか埋まっていない
> ギルドで催促が止まるため。台帳に無い人は「退部」ではなく「未登録」かもしれず、
> **分からないものを数字にしない**（0021 / 0022）。台帳は**除外にのみ**使う。
>
> **影響範囲（残るズレは3軸）**:
> - (a) ロール限定の予定では、ダッシュボードだけ母集団が広い（ロールを解決できない）
> - (b) ダッシュボードはギルド在籍を検査しないので、台帳に残る退出者も未回答に出る
> - (c) **投票メッセージの「未回答者数」はロール基準・候補単位のまま**（G4-12 で扱う）
>
> **覆す条件**: 変更なし（ロール基準へ寄せたいときは、Bot トークンを Web 層へ置くのではなく
> `discord_name_cache` に `role_member` 相当の同期を bot 側で足す）。

**ClaudeVault の ADR 本文は編集していない。** 表の運用ルール（草案を完了ログに書く）に従った。

#### 既存ギルドで何が変わるか

- **`target_role` 未指定の開催中の予定は、締切1時間前に名簿の現役メンバー全員へ DM が飛ぶ**
  （これまでは完全に沈黙していた）。**既定 ON で、設定での opt-out は無い**。
  受入基準がそう定めているが、既存ギルドの体感が黙って変わる経路なので GUIDE.md にも明記した
- 名簿が空のギルドは従来どおり沈黙する（`None`）
- ロール限定の予定は、**退部者・休止者が対象から外れる**ぶんだけ対象が減る
- 未回答0名の予定は送信済みフラグが立たなくなり、締切までの1時間は5分ごとに再評価される

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE ×2 → 「1〜4を反映すれば APPROVE」 | 1回目に8件（**部分登録の崖**／解決不能と0人の区別／文言とドキュメント／型の正規化／検証が実装に効いていない／受入基準は達成できない／`build_option_embed` は別タスク／`int()` ガード）、2回目に4件（`count == 0` で送信済みにしない／受入基準の `∩` も直す／ADR 草案の文言／解決できないときのログ） |
| ゲート2（acm-diff-auditor） | FINDINGS ×2 → **CLEAN 相当** | 1回目に5件（ロール0名で `0` を返す／`/schedule remind` の嘘の成功／reason の断定／GUIDE.md 未反映／受入基準）、2回目に3件（新ケースへの誤案内／コードフェンス内の `**`／`ruff format` の回帰） |
| ゲート2（acm-test-adversary） | **EFFECTIVE** | 13項目すべてで戻すと赤くなることを実測 |

**ゲート2は同時ではなく逐次で回した**（test-adversary が実装を一時的に戻すため、
同時だと diff-auditor が中途半端な `git diff` を読む）。G3-1 と同じ意図的な逸脱。

#### test-adversary の実測（抜粋）

| 戻した実装 | 落ちたテスト |
|---|---|
| **差集合を積集合へ** | **2** |
| 退部者の除外 | 3 |
| 「ロールなし × 名簿空 → None」 | 4 |
| `str()` 正規化 | 1 |
| `notify_unanswered` を HEAD へ全戻し | 4 |
| ロール保持者0名 → `None` | 1 |
| 「1人も解決できない → None」 | 1 |
| bot 除外 | 1 |
| **reminders の `count == 0` で送信済みにしない** | **1** |
| reminders の `count is None` | 2 |
| `/schedule remind` の 0 名分岐 | 1 |
| 回答済みの除外 | 5 |
| 申告件数と実送信数の乖離 | 1 |

**adversary が見つけた「戻しても緑のまま」の穴3件は、その場で塞いだ**（塞いだ後に
戻して落ちることも実測済み）:

| 穴 | 意味 | 対応 |
|---|---|---|
| ロール0名のとき**名簿へフォールバック**する改変が無検出 | コメントで明示的に禁じているのにテストが無い。次の担当者が「ロールが空なら名簿で代替」と直すと、**班限定の予定の DM がサークル全員へ飛ぶ** | 名簿に現役を入れたうえで「1通も飛ばない」ことを見るケースを追加 |
| `/schedule remind` の**正常系**（`count > 0` で成功）が無検出 | 「嘘の成功を出さない」側だけ固めていて、成功 Embed を到達不能にする改変が通る | 送ったときに `再通知しました` と件数が出ることを検査 |
| `_member_of` の `int()` ガードが無検出 | 名簿に数字でない `user_id` が1件あると、そのギルドの催促がまるごと止まる | リポジトリを通さず壊れた行を直接 INSERT し、他の人への送信が続くことを検査 |

#### 次タスクへの申し送り

- **G4-12 を起票した**（投票メッセージの未回答者数。ADR 0009 の完了条件2と同時にやる）
- 一斉 DM は `dm_each_with_channel_fallback` が逐次 `member.send` するため、
  名簿が数百人規模だとレート制限で分単位ブロックする。`schedule_tick` はギルドを直列に
  回すので、同 tick の後続ギルドの処理が遅れる（ループは止まらない）。
  **名簿の規模が大きいギルドが出てきたら、送信の分割・間引きを検討する**
- `guild.get_member` が**一部だけ** `None` を返すと、解決できた人にだけ送って
  `mark_reminder_sent` が立つ（全員解決できない場合だけ `None` で守られる）。
  ロール基準時代からある性質だが、母集団が名簿へ寄ったぶん露出が増えた
- **PostgreSQL 実機では検証していない**（`CLUB_TEST_PG_DSN` 未設定・Docker なし）。
  SQL の追加・変更はゼロで `members.user_id` / `schedule_votes.user_id` は
  どちらのバックエンドでも TEXT。ただし **`MemberRepository.list_members` は
  PG ライブテストで一度も呼ばれていない**（既存の PG テストは `upsert_member` /
  `get_member` / `set_leader` のみ）。G3-2 は DM 送信経路をこれに2回依存させた
- `cogs/schedule.py:86-88`（`schedule_choices` のシグネチャ）は `ruff format` 未適合だが
  **HEAD 時点から存在する既存差分**で、この差分の責任ではない

### 2026-08-29 — G3-3: `/schedule delete` を論理削除に（ブランチ `fix/g3-3`・スキーマ v17）

投票メッセージを削除してから DB を CASCADE 削除しており、**票データが完全に消えていた**。
誰がいつ参加と答えたかは、Discord 側のメッセージを消した時点で他のどこにも残らない。

- ruff: `All checks passed!`
- pytest: **943 passed, 11 skipped**（着手前は 914 passed, 10 skipped）。
  **skip が1件増えたのは PG ライブテストを1本追加したため**で、既存テストが skip に落ちたのではない。
  内訳は `tests/test_dashboard_edit.py` 4件・`tests/test_db_postgres.py` 7件、
  いずれも `CLUB_TEST_PG_DSN` 未設定

#### 完了内容

| ファイル | 内容 |
|---|---|
| `utils/db.py` / `migrations/016_schedule_confirmed.sql` | スキーマ v17。`deleted_flag INTEGER NOT NULL DEFAULT 0` と `confirmed_option_id TEXT` を**同じ版で**追加。`_table_columns` で存在確認してから ALTER |
| `repositories/schedule_repository.py` | `soft_delete_schedule` / `restore_schedule` / `list_deleted_schedules`。全一覧に `AND deleted_flag = 0`。`get_schedule` に `include_deleted`（既定 False）。物理削除 `delete_schedule` は**置き換えて削除**した |
| `cogs/schedule.py` | `/schedule restore`（L3）＋削除済みのみの候補（`[削除済み]` 表示）。削除できなかったメッセージの件数を報告。「見つかりません」を `_find_schedule` に集約 |
| `repositories/table_repository.py` | `schedules` に `deleted_flag`（編集不可）。出欠回答のタブから削除済みを除外 |
| `docs/` | `PRIVACY.md`（**公開ポリシーの変更**）/ `GUIDE.md` / `OPERATION.md` / `FEATURE_TASKS.md` |

新規テスト `tests/test_schedule_delete.py`（29件）＋ `tests/test_db_postgres.py` に PG ライブ1件。

#### 公開ポリシー（`docs/PRIVACY.md` §8.3）の変更前後

| | 記述 |
|---|---|
| 変更前 | `| /schedule delete | 日程調整と、それに紐づく出欠回答 |` |
| 変更後 | `| /schedule delete | 日程調整（無効化）。**出欠回答は残ります**（/schedule restore で戻せます） |` |

あわせて「無効化した日程調整の出欠回答を**完全に消す**には、`/data delete`（8.1）で
サーバー全体のデータを削除するか、8.2 の個人からの請求として運営者へご連絡ください。」を追記した。
`/data delete` の purge は `TABLE_DDL` 由来で全行を消すので、8.1 の記述は従来どおり成立する。

#### 設計判断

**1. 削除時に `closed_flag` も立てる（ゲート1で方針を変えた）。**
当初は「`restore` が `reminder_sent_flag = 1` を立てて催促を再開させない」案だった。却下の理由:
- G2-3 が `cogs/reminders.py` に「**送っていないなら送信済みにしない**」という不変条件を
  コメント付きで立てたばかりで、1通も送らずに 1 を立てるのは同じフラグへ逆向きの嘘を書く行為
- `update_deadline` が `reminder_sent_flag` をリセットするので、抑止が
  「誰も締切を触らない」という規律に依存する（ADR 0008 / 0016 の軸に反する）
- `list_open_schedules` が `closed_flag = 0` で拾うため、投票メッセージが無い予定が
  「開催中」に居座り、`close` / `remind` / `edit-deadline` の候補にも出続ける

代わりに `soft_delete_schedule` を `deleted_flag = 1, closed_flag = 1` の1文にした。
投票メッセージを消した時点で投票は現実に終わっているので**嘘ではなく**、
自動催促・自動締切・開催中一覧が**既存の条件式だけで**止まる。
`restore_schedule` は `deleted_flag = 0` だけを書くので、「復元は元の状態に戻す」が文字どおり成立する。

**2. Discord の投票メッセージは従来どおり削除する。**
タスクの目的は票データの保全であって画面の復元ではない。`edit` で「削除されました」に
差し替える案は、削除したはずの投稿が残り続ける点で管理者の期待から外れる。
ただし**削除できなかった件数を報告する**（残ったメッセージのリアクションは無反応になるため）。

**3. `_migrate_versioned` の早期 return が、2列を1版にまとめる理由。**
`version >= SCHEMA_VERSION` で return するので、G3-3 が v17 を切ったあとに G3-4 が
同じ版へ ALTER を足しても**既存 DB では二度と実行されない**。
規律に頼らないよう、(a) v16→v17 昇格テストで**両方の列**の存在を assert し、
(b) G3-4 の本文に「新しい migration を作らない」を明記した。

**4. `TableSpec.extra_where` は使わず、列を見せる側にした。**
`extra_where`（`repositories/table_repository.py:90`）は「論理削除の除外」を想定した機構だが
1箇所も使われていない。使わなかった理由: `/data export` の ZIP は `list_all_rows`
（sheet 指定なし）なので、隠すと**保持されている行が export から消える**。
`docs/PRIVACY.md` が export を持ち出し手段として案内しているため、
「残っているものは出す。ただし削除済みと分かる」ほうが一貫する。
`members.active_flag` / `teams.active_flag` も同じ扱い（行は隠さず列で示す）。

ただし `list_sheets` からは削除済みを外したので**タブは消える**。結果として
**ダッシュボード UI の CSV（シート単位）からは削除済み予定の票へ到達できない**が、
`/data export` の ZIP には残る、という経路差がある。

**5. `deleted_flag` を編集不可にした根拠は権限。**
ダッシュボードの編集認可は L2 だが `/schedule delete` `/schedule restore` は L3。
editable にすると **L2 が L3 の操作を取り消せる**（`members.is_leader` / `teams.leader_role_id` と同じ理由）。

**6. `v_attendance` ビューは直していない（DB 面の残存）。**
`utils/db.py` の `v_attendance` は `JOIN schedules` で `deleted_flag` を見ないため、
削除済みが集計に残る。直さない理由: bot もダッシュボードも読んでいない
（参照は `tests/`・`scripts/cleanup_test_pg.py`・NocoDB 系ドキュメントのみ）、
`_VIEW_BODIES` だけ直すと**新規 DB とだけ挙動が変わる**、既存 DB へ反映するには
v17 から `_migrate_v5_views()` を呼び直す必要があり無関係なビュー3つを作り直す副作用が出る。
受入基準「集計から除外」に対する**既知の例外**として記録する。

**7. `idx_schedules_guild` は拡張していない。**
`(guild_id, closed_flag, deadline)` のままで `deleted_flag` には効かないが、
日程調整は数百行規模なので走査コストが問題にならない。

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE ×2 → APPROVE 相当 | 1回目に A-1〜A-5・B-1〜B-7（**PRIVACY.md が嘘になる**／**既定除外で既存テストが素通りする**／メッセージ削除の失敗が無報告・無検証／復元後に自動処理が走る／v17 共有を規律に依存させない ほか）、2回目に A-4 の実装手段（`reminder_sent_flag` 案の却下と代替案） |
| ゲート2（acm-diff-auditor） | FINDINGS → **CLEAN** ×2 | 1回目に4件（`FEATURE_TASKS.md` の割当表が v16 のまま／L1 に L3 のコマンドを案内／restore の文言が守れない約束／候補が全部 `[終了]`）。テスト書き直し後の再監査でも CLEAN |
| ゲート2（acm-test-adversary） | **INEFFECTIVE → EFFECTIVE** | 詳細は下記 |

**ゲート2は同時ではなく逐次で回した**（G3-1 / G3-2 と同じ意図的な逸脱）。

#### test-adversary が見つけた「テストが何も担保していない」問題

1巡目は **INEFFECTIVE**。`list_open_schedules` / `list_due_schedules` /
`list_reminder_candidates` の `AND deleted_flag = 0` を外しても**1件も落ちなかった**。
原因は `soft_delete_schedule` が必ず `closed_flag = 1` を立てるため、
テストが削除経路でしか削除済み行を作らず、**`deleted_flag = 1 かつ closed_flag = 0` の行が
一度も生まれない**こと。既存の assert は closed_flag の再確認にしかなっていなかった。
`_handle_reaction` の削除済みガードも同じ理由で無検証だった。

**この状態は机上の話ではない。** ダッシュボードの `schedules` 表は `list_rows` が
guild_id しか絞らないので削除済みの行も並び、`closed_flag` は `editable=True`（L2）なので
「締切済み」を外せる（差分監査が `update_row` で実際に到達可能なことを実測）。
ガードが無ければ、投票メッセージを消した予定が開催中一覧へ復活し、自動締切と自動催促が動き出す。

`_unclose_deleted()` でその状態を作るケースを4件足し、2巡目で **EFFECTIVE**（8項目すべて赤）。
さらに2巡目で見つかった穴2件もその場で塞いだ:

| 穴 | 対応 |
|---|---|
| `soft_delete_schedule` から `WHERE guild_id = ?` を外しても緑（`restore` 側は担保済みで**非対称**） | 「持ち主でないギルドから消しにいく」向きのテストを追加。同じ `schedule_id` を2ギルドに置く形では検査できない（`schedule_id` は PRIMARY KEY で schema 上作れない） |
| `_schedule_ac_all` に削除済みを混ぜても緑（docstring は「含まない」と書いている） | `/schedule status` `/schedule delete` の候補から削除済みが消えることを検査 |

#### 次タスクへの申し送り

- **G3-4 は新しい migration を作らないこと**（`confirmed_option_id` は v17 で追加済み）。
  G3-4 の本文にも明記した
- **G4-13 / G4-14 を起票した**（オートコンプリート登録検査の空振り／`/schedule remind` の締切済みガード）
- **PostgreSQL 実機で確認済み**（test-adversary が `postgres:16` の使い捨てコンテナで実行）。
  DSN 付きのフルスイートは **945 passed, 0 skipped**。ただし
  **v17 のマイグレーション経路は PG では検出できない**——新規 PG DB は `CREATE TABLE` 側から
  両列を得るため、`_migrate_v17_*` を一度も通らない。「v17 に2列」を守っているのは
  SQLite の `test_v17_adds_both_columns_to_an_existing_db` **だけ**。
  v16 で止まっている既存 PG 本番 DB に対する昇格は未検証
- 「見つかりません」の重複は **5件**（6ではない）。集約後は `_find_schedule` と
  `restore`（`include_deleted=True` が要るため意図的に非集約）の2箇所
- 適用前に `pg_dump -Fc` を取ること（down が無い）。手順の docs への追記は G3-7 で行う

### 2026-08-29 — G3-4: `/schedule confirm` 確定日程と当日リマインド（ブランチ `fix/g3-4`）

`finalize_schedule` は集計サマリーを投稿して終わりで、**「結局いつに決まったのか」が
どこにも残らなかった**。前日・当日のリマインドも無い。

- ruff: `All checks passed!`
- pytest: **972 passed, 12 skipped**（着手前は 943 passed, 11 skipped）。
  **skip が1件増えたのは PG ライブテストを1本追加したため。**
  内訳は `tests/test_dashboard_edit.py` 4件・`tests/test_db_postgres.py` 8件で、
  いずれも `CLUB_TEST_PG_DSN` 未設定。**PG ライブテストは今回実行していない（skip のまま）**

#### 完了内容

| ファイル | 内容 |
|---|---|
| `repositories/schedule_repository.py` | `set_confirmed_option`（**SQL の `EXISTS` で対象候補を担保**）/ `clear_confirmed_option` / `list_confirmed_between`。一覧2つを LEFT JOIN 化し確定候補の `start_at` を同じ行で返す |
| `cogs/schedule.py` | `/schedule confirm`（L2）/ `/schedule unconfirm`（L2）。`option_id` の候補は選ばれた予定のものだけ。一覧に確定日を表示 |
| `cogs/reminders.py` | 前日 20:00 / 当日 08:30 の新ループ。`confirmed_schedule_reminders` |
| `services/schedule_service.py` | 集計サマリーに確定日（`description` へ追記） |
| `docs/` | `OPERATION.md`（コマンド表・自動ジョブ表）/ `GUIDE.md`（通知表・使い方） |

**マイグレーションは無し。** `confirmed_option_id` は G3-3 の v17 で追加済み。

新規テスト `tests/test_schedule_confirm.py`（29件）＋ `tests/test_db_postgres.py` に PG ライブ1件。

#### 設計判断

**1. 受入基準の「`/schedule list` に確定日を表示」だけでは機能が死ぬ。**
`/schedule list` は `closed_flag = 0` しか出さないが、確定作業の通常フロー
（締切 → 集計サマリー → 確定）を通ると `finalize_schedule` が `close_schedule` を呼ぶので、
その予定は二度と `/schedule list` に現れない。**`/schedule list-closed` にも出す**ように直し、
受入基準の本文も更新した。

**2. 確定日の表示に候補の `label` を使わない。**
`label` は利用者が `/schedule create` に打った生文字列で、`utils/parser.SHORT_FORMATS` により
`7/20 18:00`（年なし）や `2026-07-20`（時刻なし）も通る。これを確定日として出すと、
一覧は「7/20」なのに当日通知は「本日 00:00」になる。**正規化済みの `start_at` に統一**した。

**3. 対象外の候補を弾くのは Cog の if ではなく SQL。**
`set_confirmed_option` を1文の UPDATE にし、`EXISTS (SELECT 1 FROM schedule_options
WHERE guild_id = ? AND option_id = ? AND schedule_id = ?)` を条件に入れた（ADR 0008 / 0010）。
Cog の分岐は UX のためで、担保はリポジトリ側。テストも Cog を通さず直接叩いて固定した。

**4. 失敗を `reminders_log` に書かない。**
`RemindersLogRepository.exists()` は `status` を見ないので、失敗を同じキーで書くと
**その日の通知が二度と飛ばない**（G2-3 が潰した「送っていないのに送信済み」と同じ形）。
失敗は `log.warning` と `bot.log_to_channel` にだけ出す。この不変条件を
`CONFIRMED_REMINDER_TYPE` の定義箇所に明記した。
`exists()` に `status` フィルタを足す案は、共有述語の変更で `milestone_alert` の挙動も
変わるため採らなかった（ADR 0014。必要になったら別タスク）。

**5. 締切前の確定を許可する。ただし自動で締め切らない。**
「先に決まる」は実運用で起きる。断ると `/schedule close`（＝公開サマリー投稿）を
強制することになる。逆に confirm が `finalize_schedule` を呼ぶのは、別コマンドの副作用で
公開投稿が飛ぶ設計で ADR 0024 に反する。保存したうえで ephemeral に
「まだ投票受付中です。締め切るには `/schedule close`」を添える形にした。

**6. 告知の本文は Embed の `description` に渡す。**
`utils/embeds._base` は `title[:100]` で無条件に切るため、本文を title に入れると
**イベント名が長いギルドで日時や場所が黙って消える**（このコマンドの目的そのものが落ちる）。
差分監査の指摘で発見。イベント名90文字の回帰テストを置いた。

**7. チャンネルは `guild.get_channel_or_thread`。**
`guild.get_channel` はスレッドを解決しない。`/schedule create` は `channel` 未指定時に
`interaction.channel` の ID を保存するので、**スレッド内で作られた予定は `channel_id` が
スレッド ID になる**。`get_channel` だと告知が出ず、リマインドは毎日2回
「チャンネルが見つかりません」を運用者ログへ流し続ける。既存経路は `bot.get_channel`
（スレッドも解決する）なので、同一ギルド限定にした今回の2箇所だけが踏む形だった。

**8. 集計サマリーの確定日は `add_field` ではなく `description`。**
候補数に上限が無い（`svc.parse_options` は分割するだけ）ので、field を1つ増やすと
上限25に当たる閾値が下がる。`finalize_schedule` は `HTTPException` を握り潰すため、
**超えた瞬間に集計サマリーが無言で投稿されなくなる**。

**9. 未確定のサマリーに1行の案内を残した（意図的な逸脱）。**
差分監査は「確定済みのときだけ出す」を勧めたが、(a) `description` の1行なので上限の問題は
解消している、(b) このタスクの起票理由は「結局いつに決まったのかが残らない」ことで、
締切サマリーは**その必要が生じるまさにその瞬間**に出る唯一の接点、
(c) ADR 0024 が禁じているのはマイグレーションや既定値による**状態**の変化で、
公開投稿の文面1行はそれに当たらない、として残した。再監査で許容判定。
公開投稿なので主語を書く形（「班長以上が `/schedule confirm` で…」）にし、
L1 の部員に実行できないコマンドを命令していない。

**10. `/schedule unconfirm` を足した（受入基準に無い）。**
取り消し手段が無いと、誤確定を止める唯一の方法が `/schedule delete`（L3・投票メッセージも
消える）になり代償が釣り合わない。`option_id` 省略で解除する案は、Discord の UI で
任意引数の省略が最も起きやすいため却下した。`clear_confirmed_option` が
**呼び出し元の無いデッドコードになる**という指摘も決め手。

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE ×2 → 「反映すれば実装に入ってよい」 | 1回目に10件（**確定日が表示されない**／`exists()` が status を見ない／ループ登録テストの空振り／Cog の if ではなく SQL で守る／`guild.get_channel` へ揃える／公開投稿で命令しない／本文と時刻／PG の境界値／docs／作業ツリー確認）、2回目に3件（失敗を `reminders_log` に書かない／`list_option_labels` では確定「日」を出せない／`unconfirm` の補完と権限テスト） |
| ゲート2（acm-diff-auditor） | FINDINGS → **CLEAN** | 6件（**title 100文字切り**／**`get_channel` がスレッドを解決しない**／告知失敗が伝わらない／field が増える／`int()` が try の外／`OPERATION.md` の自動ジョブ表） |
| ゲート2（acm-test-adversary） | **EFFECTIVE** | 23項目すべてで戻すと赤くなることを実測 |

**ゲート2は同時ではなく逐次で回した**（G3-1〜G3-3 と同じ意図的な逸脱）。

#### ループ登録テストの実測（ゲート1の指定）

既存の `tests/test_data_purge.py` の `test_purge_loop_is_registered` は `hasattr` を見るだけで、
**`cog_load` から `start()` の行を消しても通る**形だった。今回は挙動で見る形にし、
実際に行を消して落ちることを確認した:

| 消した行 | 結果 |
|---|---|
| `cog_load` の `self.confirmed_schedule_reminders.start()` | `test_every_loop_is_started_and_cancelled_by_the_cog` が **1 件失敗** |
| `cog_unload` の `self.confirmed_schedule_reminders.cancel()` | 同じテストが **1 件失敗** |

#### test-adversary が見つけた「戻しても緑」の穴（その場で塞いだ）

| 穴 | 意味 | 対応 |
|---|---|---|
| `schedule_list_value` から確定日の3行を消しても緑 | **受入基準の中核**（一覧に確定日を表示）が cog 側で無検証だった。リポジトリの JOIN は担保されていたが、それを Embed に出す側を見るテストが無かった | `Schedule.list_cmd` / `list_closed_cmd` を実際に呼び、field の value に確定日時が入ることを検査（戻すと落ちることを実測） |
| PG ライブテストが「他予定の**実在する** option_id」を見ていなかった | 現実的な誤操作（他予定の候補を選ぶ）が PG 側で無検証 | 2つ目の予定と候補を作って False を確認するケースを追加（**DSN 未設定のため未実行**） |

#### 次タスクへの申し送り

- **`.ics` 添付は次イテレーション**（受入基準の申し送りどおり）。Google カレンダー連携は ADR 0013 に反するのでやらない
- **PG ライブテストは skip のまま**（`CLUB_TEST_PG_DSN` 未設定）。今回追加した
  `test_pg_live_confirmed_schedule_join` は、`+09:00` 付き ISO 文字列の範囲比較で
  **ちょうど 00:00:00 の候補**を取りこぼさないことを見る。実行するときはここを注視すること
- `config.tz` に**夏時間のあるゾーン**を設定すると、`start_at` のオフセットが変動して
  文字列の辞書順＝時刻順が崩れる。既存の `deadline` 比較と同じ前提なので今回は触っていない
- 08:30 に3つのループ（`daily_morning` / `weekly_milestone_alert` /
  `confirmed_schedule_reminders`）が並走する。いずれもギルド単位で例外を握るので
  相互には影響しないが、ギルド数が増えたら実行時刻の分散を検討する

### 2026-08-29 — G3-6: 新入生オンボーディング（ブランチ `fix/g3-6`）

新歓期に30〜50人が入るのに bot は新入生の存在を知らず、幹部が `/member register` を
1人ずつ手打ちしていた。名簿に載らない人には班別通知も出欠催促も届かない。

- ruff: `All checks passed!`
- pytest: **1013 passed, 12 skipped**（着手前は 972 passed, 12 skipped。skip は据え置き）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `cogs/welcome.py`（新規） | `on_member_join` で「班を選ぶ」ボタンを DM。押すと班 Select → 名簿登録＋ロール付与 |
| `config.py` | `welcome_enabled: bool`（既定 False）/ `welcome_channel_id` |
| `repositories/settings_repository.py` | `get_bool()`（保存は `"1"/"0"`、解釈は大文字小文字を無視、不正値は既定） |
| `cogs/setup_wizard.py` | ON/OFF トグル。`CONDITIONAL_KEYS` で OFF のギルドは未設定に数えない |
| `cogs/help.py` | `/setup-status` も ON のときだけ案内チャンネルを検査 |
| `bot.py` | `COGS` 追加、`add_dynamic_items` の登録（**失敗を握る**） |
| `requirements.txt` | discord.py の下限を `>=2.4.0` へ |
| `docs/` | `GUIDE.md` Step 4.5 / `PRIVACY.md`（**公開ポリシーの変更**）/ `OPERATION.md` |

新規テスト `tests/test_welcome.py`（38件）。**マイグレーションは無し**（`settings` の行のみ）。

#### 設計判断

**1. 参加しただけでは `members` に登録しない。** 登録は班を選んだときだけ。
`on_member_join` は**再参加でも発火**するので、ここで `upsert_member` すると
訪問者や OB まで台帳に入る。台帳は G3-2 で**未回答催促の母集団**にしたばかりなので、
誤爆が直接「誤送信」になる。`status` / `active_flag` にも触れない
（再参加した卒業生が自動で現役に戻る経路を作らない）。

**2. DM 拒否時の送り先は `WELCOME_CHANNEL_ID` だけ。未設定なら何もしない。**
G3-5 の `send_guild_notice` は招待直後の案内用で、候補順が
`#bot-log` → `system_channel` → 送信可能な最初のテキストチャンネル、
権限チェックも Bot 側だけ。新入生が読めない `#bot-log` に落ちても「送信しました」と
成功扱いになる。受入基準の語も「**指定**チャンネル」。
Bot の送信権限と**本人の可視性**の両方を検査し、カテゴリ・フォーラムも弾く。

**3. ボタンは `DynamicItem`。`custom_id` に guild_id と user_id の両方を埋める。**
user_id を埋めないと、チャンネルへ落ちたボタンが**誰でも押せる班ロール自販機**になる
（L2 の `/member register` を迂回する）。`DynamicItem` にしたのは、新歓期の再起動で
新入生のボタンが死なないようにするため。これに伴い `requirements.txt` の下限を
`>=2.4.0` へ上げた（2.3 環境では import が落ちるが、venv は 2.7.1 なので
**テストでは絶対に検出できない**。ソース走査テストで下限を守る）。

**4. ロール付与の前提は2つ。どちらが欠けても登録は通す。**
Manage Roles は最小権限の招待（ADR 0017）に含まれず、`teams.member_role_id` は
ロールの自動作成が成功したギルドにしか入っていない。**後者は新規ギルドの既定に近い状態**なので、
`_sync_roles` の戻り値だけを見ると「付いた」と誤解させる。`member_role_id` の有無を
先に判定して文面を分けた。権限を増やす方向では解決しない。

**5. `/member setup` は「班選択ウィザード」ではなかった。**
受入基準の「UI は `/member setup` の班選択ウィザードを流用する」は現物と食い違っており、
実体は autocomplete 付きの L3 スラッシュコマンド。流用できたのは
`MemberRepository.list_teams` と `_sync_roles` だけで、**Select View は新規に書いた**。
受入基準の本文にもその旨を追記した。

**6. `/setup` と `/setup-status` は ON のときだけ案内チャンネルを数える。**
OFF のギルドではこの設定はどこからも参照されないので、常時カウントすると
オンボーディングを使わないサークルに毎回「未設定 N 件」を突きつけることになる
（ADR 0023 / 0024）。逆に ON なら**必ず**数える——未設定だと DM を拒否している
新入生が丸ごと取りこぼされるため。ON にしたその場でも警告する
（後で `/setup-status` を見に行かせる設計にしない）。

**7. `add_dynamic_items` の登録失敗を握る。**
`load_extension` は失敗を握るのに、直後の裸の `from cogs.welcome import ...` を
try の外に置いていた。ここで例外が出ると `setup_hook` ごと落ちて
**全ギルドの bot が起動しない**。1ギルドの機能欠落で済むはずが全テナント停止に化ける。

#### 公開ポリシー（`docs/PRIVACY.md`）の変更

- §2 冒頭に「ボタン・選択メニューの操作」を追加し、
  「名簿に登録されるのは班を選んでいただいたときだけ」を明記
- §2.1 に「表示名キャッシュ」の行を追加。**この差分以前から欠落していた項目**で、
  `cogs/name_cache.py` は設定に関わらず**起動のたびに全メンバー分**を更新する
  （当初「サーバー参加時」とだけ書いてしまい、差分監査に2度指摘された）
- §2.4 の「スラッシュコマンドとリアクションのみで動作し」を実装に合わせた

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE ×2 → 実装許可 | 1回目に13件（**フォールバック先が受入基準の「指定チャンネル」でない**／**再参加で既存データが動く**／`_sync_roles` が例外を捕捉していない／Select の25件上限／下限引き上げを構造で守る／`get_bool` の往復／`/member setup` の事実誤認 ほか）、2回目に4件（`get_channel_or_thread` がカテゴリを返す／Bot 側権限を落とさない／バージョン判定を文字列一致で書かない／初回押下でボタンを消さない） |
| ゲート2（acm-diff-auditor） | FINDINGS ×2 → 残1件 | 1回目に6件（**PRIVACY.md に事実でない記述**／`/setup` が無条件にカウント／**Cog 1本の import 失敗で起動不能**／ロール未紐付けで「登録しました」だけ返る／テストが効いていない2箇所／`open_team_picker` だけ在籍を見ていない）、2回目に3件（表示名キャッシュの記録タイミング／`ruff format` による無関係な整形の混入／picker の在籍ガードに回帰テストが無い） |
| ゲート2（acm-test-adversary） | **INEFFECTIVE → 修正済み** | 下記 |

#### test-adversary の実測（27項目）と、見つかった7つの穴

23項目は戻すと赤くなった。**緑のままだったのは7件**で、いずれもその場で塞ぎ、
戻すと落ちることを実測した:

| 穴 | 意味 |
|---|---|
| `WELCOME_CHANNEL_ID` の判定を消して `text_channels[0]` へ落としても緑 | テストの `_Guild` に `text_channels` / `system_channel` が無く、**受入基準の「指定チャンネル」を守れていなかった**。ダブルに実物と同じ入口を持たせた |
| `open_team_picker` の在籍検査を消しても緑 | picker を呼ぶ既存4テストが全員メンバーを渡す形だった |
| `get_bool` を `raw == "1"` だけにしても緑 | docstring が主張する「大文字小文字を無視」が未検証 |
| ON 時の警告を消しても緑 | assert が**トートロジー**だった（`未設定` の行は notice の有無に関係なく出る） |
| `add_dynamic_items` の呼び出しだけ無効化しても緑 | ソースの文字列 grep だけだったので、コメントアウトでも通る |
| `callback` が `custom_id` でなく押下者の ID を使っても緑 | `callback` を通すテストが1件も無かった |
| DM のボタンに user_id を埋めなくても緑 | 参加イベント → `custom_id` への埋め込みの**配線**が無検査だった |

#### 次タスクへの申し送り

- `cogs/settings.py` の `channel_keys` / `/set_channel` の choices に `WELCOME_CHANNEL_ID` が無い。
  `/settings_list` で「その他」に分類されるだけの**表示上の問題**なので、このタスクでは触っていない
- **採番の食い違い（再掲）**: `IMPROVEMENT_TASKS.md` の申し送り表は
  「`pg_dump -Fc` の docs 追記」を "G3-6" に割り当てているが、G3-6 の枠は
  オンボーディングで埋まっている。**この追記は G3-7 で行う**（G3-3 の完了ログにも記録済み）
- **PostgreSQL 実測はしていない。** ただし G3-6 は新しい SQL を1文も追加しておらず
  （`get_bool` は既存の `get` を再利用、他も既存経路）、`config.for_guild` に SELECT が
  1回増えるだけなので、G1-0 / G1-9 型のリスクはこの差分には無いと判断した
- **作業手順の反省**: ゲート2の test-adversary が live worktree に変異を当てている最中に
  コミットしてしまい、G3-4 の実装コミットに変異が混ざった（`77f38bd` で復旧）。
  以降は「フルセット緑」だけを根拠にせず、**戻した項目それぞれを実装側の grep で確認**してから
  コミットする。adversary 側もサンドボックス複製で実測する運用に変えた

### 2026-08-29 — G3-7: ドキュメントの整合と GUIDE.md の回帰テスト化（ブランチ `fix/g3-7`）

GUIDE.md の通知表は「毎朝 08:00」と書いていたが実装は 08:30。早見表には
`/close` `/remind` のような**実在しないコマンド名**が並び、`/status` は
`/schedule status` と `/layer status` を同じ表記で指していた。

- ruff: `All checks passed!`
- pytest: **1017 passed, 12 skipped**（着手前は 1013 passed, 12 skipped。skip は据え置き）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `docs/GUIDE.md` | §5 通知表を実装の8ループと1対1に。付録の早見表を**全92コマンドの完全名**へ |
| `tests/test_docs_commands.py` | 付録セクションの**双方向**検査（+4テスト）。`len(TABLES)` との突き合わせ |
| `docs/OPERATION.md` | ジョブ表と本文の 08:00 → 08:30（計7箇所）、`pg_dump -Fc`、`/health` の DB 表記 |
| `docs/{PRIVACY,DASHBOARD_SETUP}.md` `README.md` `cogs/{data,season}.py` | `/data export` の「全データ」→「主要7テーブル」、`pg_dump` 手順 |

#### 設計判断

**1. 早見表は全件必須。除外リストを作らない。**
「よく使うものだけ」に絞る案は、(a) その役割は既に §3「役割別・最初に覚えること」が
担っており第3の層ができる、(b) 付録は「全コマンドの詳細は OPERATION.md」と自称している、
(c) 行数の増分は12 → 18行程度（既存12行が既に約70件を詰めていた）、という理由で採らなかった。
決め手は構造で、**除外リストは逃げ道**になる——将来テストが赤くなったときの最小手が
「ドキュメントに書く」ではなく「除外リストに1行足す」になる（ADR 0008 / 0016）。

**2. 検査対象は付録セクションのみ。ファイル全体ではない。**
全体を対象にすると、`/schedule restore` は3章の幹部向け一覧にも書かれているので
**付録から抜けていても緑になる**。見出しで切り出し、見出しが見つからなければ
「抽出0件」ではなく「見出しが無い」で落ちるようにした。

**3. 完全名への書き換えは検査を通すための改変ではなく、欠陥修正。**
`/close` `/remind` `/delete` `/edit-deadline` `/show` `/reset` はどれも実在せず、
読者がそのまま打てない。「完全名の行だけ検査する」折衷案は、付録の大半を
検査対象外にしたうえで緑を返す形になるので採らなかった。

**4. `/data delete` の「全データ」は正しいので変えていない。**
`purge_target_tables()` は `TABLE_DDL` の全テーブルから導出されるので本当に全データ。
ここを「主要7テーブル」に書き換えると**プライバシーポリシーで削除範囲を過小に記載する**
ことになり、G3-6 で直したのと逆向きの虚偽になる。直したのは `/data export` 側だけ。

**5. 数字は直書きしない。** `len(TABLES)` と突き合わせるテストを入れた。
docs 4ファイルに加えて `cogs/data.py` と `cogs/season.py` の docstring も対象に含める
（数字が散っているので、片方だけ直して片方が静かに古いまま残る形を防ぐ）。
G4-3 で `audit_log` 等が加わったときにテスト失敗として現れる。

#### 空振り確認（実測）

新しい検査が本当に効くことを、実際に改変して確かめた:

| 一時的な改変 | 結果 |
|---|---|
| 付録から `/countdown` を消す | `test_every_command_is_in_the_guide_appendix` が失敗 |
| 付録に `/nonexistent` を足す | `test_the_guide_appendix_has_no_stale_commands` が失敗 |
| 付録から `/schedule restore` **だけ**消す | missing 方向が失敗（**3章に記載があっても緑にならない**） |
| 同一セルに `/bogus` を足す | stale 方向が失敗 |
| `docs/PRIVACY.md` を「全データ」へ差し戻す | `test_the_export_table_count_matches_the_whitelist` が失敗 |
| `cogs/data.py` を「主要8テーブル」に | 同上が失敗（**.py 側も検査範囲に入っている**） |

差分監査も独自に8種の変異（typo・表の全削除・同一セル中間への挿入など）を当て、
すべて検出されることを確認している。

#### ゲートの判定

| ゲート | 判定 | 経過 |
|---|---|---|
| ゲート1（acm-plan-reviewer） | REVISE → **APPROVE** | **`/weight` は実装に存在する**（トップレベル群 `weight set` / `weight view` / `weight top`。受入基準の「全体で0ヒット」は GUIDE.md 内で0件の意味）という私の事実誤認のほか、不足コマンドが7件では足りない／検査は双方向／`/data delete` を変えるな／`push_section_tasks` の漏れ／`pg_dump` の追記先は §8.2 ではなく §6、など13件 |
| ゲート2（acm-diff-auditor） | FINDINGS ×3 → 残2件も対応 | 1回目に4件、2回目に6件（**OPERATION.md 本文の 08:00 が4箇所残っていた**／`cogs/reminders.py` の docstring ／**新テストの guard が「全データ」への差し戻しを素通りさせていた**／`cogs/data.py` の docstring ほか）、3回目に2件 |

**test-adversary は回していない。** 実装コードの変更が docstring 2行のみで、
戻して赤くなるかを見る対象が無いため。代わりに**ドキュメント側の変異6種を自分で当てて実測**し、
差分監査にも独自の変異検査を依頼した。

#### 差分監査が見つけた「自分の修正が中途半端だった」箇所

- ジョブ表だけ 08:30 に直し、**同一ファイルの本文4箇所は 08:00 のまま**残していた
  （同じファイル内で併記された状態になり、受入基準を満たしていなかった）
- 呼び出し側（`cogs/season.py`）の「丸ごと」は直したのに、
  **エクスポート本体（`cogs/data.py`）の docstring が「全データ」のまま**だった
- 新テストに `if "主要" not in text: continue` の guard を置いたため、
  **「全データ」へ差し戻す回帰が素通り**していた（数字を8に変える改ざんは検出できるが、
  表記ごと戻す回帰は検出できない、という非対称）

#### 次タスクへの申し送り

- **`/set_sheet` `/sheet_sync` の記述が矛盾している。** `docs/OPERATION.md:139` は
  「撤去しました」、`:182` と `:704` は「利用できます」と書いており、**実装にはどちらも無い**。
  既存の `test_documented_commands_still_exist` は**表の行しか見ない**ので、
  本文のこの矛盾は永久に検出されない。G3-7 の受入対象外なので直していない
- **付録の権限列は機械検査されていない。** 同じコマンドを権限の違う2行に重複掲載しても
  `>=` 比較で緑のまま（現状は重複なし）
- **GUIDE.md の3章・4章のコマンド例はコードブロック内**なので、今回の検査の死角。
  現時点で齟齬が無いことは目視で確認済み（差分監査も全件突き合わせ済み）
- `cogs/data.py` の `EXPORT_README`（ZIP に同梱され利用者が読む文面）は範囲を限定していない。
  ファイル一覧は併記されているので実害は小さいが、将来 PRIVACY と揃えるなら候補

---

### 2026-08-29 — G4-1: `/layer stats` 積層記録の集計（ブランチ `feat/g4-1`）

`layer_records` には「誰が・どの桁の・何層目を・何分」が全部入っているのに、
読める形で出すコマンドが1つも無かった。`/progress` に出るのは率だけで、
**時間情報（合計・1層あたり平均・最終作業日）はどこにも出ていなかった**。

- ruff: `All checks passed!`
- pytest: **1046 passed, 12 skipped**（着手前は 1017 passed, 12 skipped。skip は据え置き）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `services/layer_stats_service.py`（新規） | `period_start()` と `aggregate_layer_stats()`。DB を触らない純関数 |
| `repositories/layer_session_repository.py` | `list_records(guild_id, keta=None)` を追加（集計用の取得） |
| `cogs/layer_tracking.py` | `/layer stats [keta] [period]`（L1）。桁別＋作業者別の Embed |
| `tests/test_layer_stats.py`（新規） | 29件。期間境界・数え方・ギルド境界・分母の無い桁 |
| `docs/OPERATION.md` `docs/GUIDE.md` | コマンド表と早見表へ追記（G3-7 の回帰テストが要求する） |

#### 設計判断

**1. 完了層数の数え方は `count_completed_layers` と揃えた**（層番号の種類数。
巻き直しは1層）。ここだけ「記録の件数」で数えると、同じ桁の層数が `/progress`
（進捗率の分子）と `/layer stats` で食い違い、**画面ごとに数字が違う**という
一番たちの悪い形になる。テストで固定した。

**2. 目標層数が無い桁に分母を作らない**（ADR 0021）。`progress_spar_links` に
紐付けが無い桁は `target=None` として「N 層（目標未設定）」と出す。
`0` にすると「0 層中 N 層」「達成率 ∞」のどちらかになり、どちらも嘘になる。

**3. 期間の絞り込みは SQL ではなく純関数側**。`ended_at` は ISO 文字列で、
オフセット表記が混ざると `>=` の文字列比較が黙って壊れる（`+09:00` と `Z` は
辞書順で逆転する）。`from_iso()` で datetime に直してから比較している。
副作用として境界の単体テストが DB 無しで書ける。

**4. 週は月曜始まり、月は1日始まり。** 部活の週の区切りに合わせた。
境界ちょうど（月曜 0:00）の記録を含めることをテストで固定している
（`>` にすると「記録が消えた」という報告になる）。

**5. 表示は ephemeral、桁別・人別とも各10件まで。** `/layer` の他コマンドが
すべて ephemeral なので揃えた。件数制限は Embed の 25 field 上限を
**桁10 + 続き1 + 作業者1 = 12** に収めるため（超えると送信自体が
HTTPException で落ち、利用者には「予期せぬエラー」としか出ない）。
打ち切りは件数と絞り込み手段（`keta` 引数）を添えて明示する。

**6. 名前解決はギルドキャッシュ → `discord_name_cache` → ID の3段。**
退部者や bot 再起動直後でも ID が生で出ないようにした。

#### 空振り確認（実測）

実装を一時的に改変してテストが赤くなることを確かめた。

| 一時的な改変 | 結果 |
|---|---|
| 完了層数を「記録の件数」で数える | 数え方と平均分の2件が失敗 |
| 期間の境界を `>=` から `>` へ | 境界テストが失敗 |
| 目標が無い桁の `target` を `0` に | ADR 0021 のテストが失敗 |
| `list_records` から `guild_id` 条件を外す | ギルド境界の3件が失敗 |
| Cog の `keta=` 絞り込みを外す | 絞り込みテストが失敗 |
| `discord_name_cache` の解決をやめる | 名前解決テストが失敗 |

#### 次タスクへの申し送り

- **G4-5（週次ダイジェスト）は `aggregate_layer_stats` をそのまま再利用できる。**
  「先週の積層層数・時間・参加人数」は `LayerStats.records` /
  `total_minutes` / `len(members)` で出せる
- `/layer stats` は**進行中セッションを含めない**（`layer_records` のみ）。
  G4-2 の押し忘れ検知で `layer_sessions` を見るときに、
  「集計に出ないのに進行中」という状態が生まれることに注意

---

### 2026-08-29 — G4-2: `/layer cancel` と押し忘れ検知（ブランチ `feat/g4-2`）

`/layer start` したまま帰宅すると `/layer end` が「1200分」を記録し、
**完了層数が増えるので `/progress` の進捗率まで水増しされる**。
打ち間違えた場合の取り消し手段も無く、`end` するしかなかった。

- ruff: `All checks passed!`
- pytest: **1072 passed, 12 skipped**（着手前は 1046 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `services/layer_tracking_service.py` | `LayerTrackingService.cancel()`（記録を残さない）と `classify_stale_sessions()`（純関数） |
| `config.py` | `LAYER_SESSION_ALERT_MINUTES`（既定240）/ `LAYER_SESSION_AUTO_CANCEL_MINUTES`（既定720）をギルド別設定に |
| `cogs/layer_tracking.py` | `/layer cancel`（L1） |
| `cogs/reminders.py` | 5分ループに `_process_layer_sessions()` を相乗り。`reminders_log` で二重通知を防ぐ |
| `tests/test_layer_session_alert.py`（新規） | 26件 |
| `docs/OPERATION.md` `docs/GUIDE.md` | コマンド表・早見表・通知表・トラブル表 |

#### 設計判断

**1. `cancel` は `end` の別名ではない。記録を1行も書かない。**
`end` で閉じると押し忘れの分数が `layer_records` に入り、
`count_completed_layers` が数える層番号の種類数が増えて進捗率が上がる。
これがこのタスクの発端そのものなので、テストで
「`list_records` が空のまま」を固定した。

**2. DM の失敗を「拒否（Forbidden）」と「一時障害（HTTPException）」で分けた。**
`reminders_log.exists()` は status を見ないので、書けば再試行は止まる。

- Forbidden → `failed` で**記録する**。次の tick でも直らないので、
  5分ごとに永久に叩き続けないため
- HTTPException → **記録しない**。次の tick で再試行する（G2-3 の
  「送っていないなら送信済みにしない」）

G2-3 の原則をそのまま全部に当てると、DM を閉じている部員1人のために
セッション中ずっと5分おきに Forbidden を叩くことになる。
**「直らない失敗」と「直るかもしれない失敗」を分けた**のがこのタスクの判断。

**3. 自動取り消しは DM より先に実行する。**
通知が届かなくても水増しは止める。DM は付随であって本体ではない。
メンバーがキャッシュに無い（退部済み・キャッシュ欠落）場合も取り消しは進む。

**4. 催促と自動取り消しは排他。** 閾値を両方超えたセッションは
自動取り消しだけを行い、催促には入れない（同じ tick で2通届く）。
`classify_stale_sessions` の `elif` がその担保で、
`if` に変えるとテストが落ちる。

**5. `reminders_log` のキーは `layer_session:<session_id>`。**
`session_id` は AUTOINCREMENT なので「1セッションにつき1回」を素直に表せる。
ユーザー単位にすると、次に始めたセッションで催促が飛ばなくなる。

**6. 0 でその機能だけ無効。** 「催促は要らないが自動取り消しは欲しい」
（またはその逆）が選べる。既定値を変えていないので、既存ギルドの挙動は
4時間・12時間で**新しく**動きだす——ここは ADR 0024 の「既定値で既存データを
動かさない」に照らして迷ったが、(a) データを書き換えるのではなく
セッション行を消すだけ、(b) 押し忘れセッションを残すこと自体が
進捗率を壊している状態、(c) 受入基準が既定値を明示している、の3点で
**既定 ON のまま**とした。無効化の手段（0）を用意し、ドキュメントに書いた。

**7. 5分ループでは日程調整とは別の `try` にした。**
同じ `try` に入れると、日程調整側が落ちたギルドで押し忘れの自動取り消しまで
止まり、進捗率の水増しが残り続ける（gotcha `all-guilds-stop-getting-notifications`
と同じ形の、ジョブ間版）。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| `cancel` が `add_record` するようにする | 記録なしの2件が失敗 |
| 自動取り消しの境界を `>=` から `>` へ | 自動取り消し系3件が失敗 |
| 催促の分岐を `elif` から `if` へ | 二重通知の2件が失敗 |
| Forbidden を一時障害扱い（`None` を返す）に | 再試行しないことのテストが失敗 |

#### 次タスクへの申し送り

- **G4-9（工具の貸出）は `classify_stale_sessions` を再利用できる。**
  「開始 → 進行中 → 終了」が同型で、返却予定日超過の督促は
  閾値を「予定日からの経過」に読み替えるだけ
- `/layer stats` は `layer_records` だけを見るので、自動取り消しされた
  セッションは集計に一切現れない（これは意図どおり）

---

### 2026-08-29 — G4-3: `/report changes` と読み取り専用テーブルの追加（ブランチ `feat/g4-3`）

`AuditLogRepository.list_recent` を呼ぶコードが bot 側に1つも無く、
`audit_log` は**書かれ続けているのに誰も読めない**状態だった。
`/report audit` が読んでいたのは `reminders_log`（bot が送った通知）で別物。
`/data export` も `TABLES` の7表だけで、年度・節目・桁マスタ・技能タグ・設定を持ち出せなかった。

- ruff: `All checks passed!`
- pytest: **1101 passed, 12 skipped**（着手前は 1072 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `repositories/table_repository.py` | 読み取り専用の6表（`audit_log` / `seasons` / `progress_milestones` / `layer_keta` / `skill_tags` / `settings`）と `TableSpec.min_level` |
| `repositories/audit_log_repository.py` | `list_recent` に `actor_id` 絞り込み、`list_actors()`（補完用） |
| `cogs/reports.py` | `/report changes`（L3・actor 補完付き）。`/report audit` → `/report notifications` へ改名 |
| `dashboard/routers/tables.py` | `_visible_spec()` で `min_level` を一覧・取得・CSV・PATCH の4経路に効かせる |
| `tests/test_report_changes.py`（新規） | 26件 |
| `tests/test_dashboard_tables.py` ほか | 既存テストの前提更新＋レベル制御の3件を追加 |
| `docs/*` `README.md` `cogs/{data,season}.py` | 「主要7テーブル」→「主要13テーブル」（8箇所）、コマンド表・早見表 |

#### 設計判断

**1. 表ごとの閲覧レベルを `TableSpec` に持たせた（`min_level`）。**
`GET /settings` はロール ID の実値を **L4 にだけ**返している（G1-6）。
`settings` を素で `TABLES` に足すと、**同じ値が表グリッド経由で L1 に見える**——
G1-6 の修正が丸ごと無効になる。ルータ側の `if table_key == "settings"` で守る案は、
表を足すときに書き忘れれば素通りするので採らなかった（ADR 0008 / 0016 の
「規律ではなく構造で守る」）。既定は 1 なので、既存の7表の挙動は変わらない。

割り当て: `settings` = L4（`GET /settings` と同じ）、`audit_log` = L3
（`/report changes` と同じ。画面と Discord で食い違わせない）、残りは L1。
`/data export` は元から L4 なので、この値の影響を受けない。

**2. 新しい6表は編集可能な列をゼロにした。** 正本の入口は Discord コマンド側
（`/season new` `/milestone add` `/layer keta-add` `/skill-add` `/setup`）にあり、
表グリッドから直せると入口が二重になる。とくに `settings` を編集可にすると
**ダッシュボードから権限ロールを差し替えられる**（`teams.leader_role_id` を
編集不可にしたのと同じ理由。G1-6）。

**3. 「秘密情報を含む列の除外」を、列名の検査に置き換えた。**
`settings` の3列（`setting_key` / `setting_value` / `updated_at`）に
秘密専用の列は無く、Todoist トークンは `todoist_configs`（`TABLES` 外）にある。
そこで「この表のこの列を外す」ではなく、**全表を横断して
`token` / `secret` / `password` / `credential` / `api_key` / `encrypted` を
含む列名が1つも無いこと**をテストにした。次に表を足す人にも効く。

**4. 表キーは `layer_ketas` ではなく `layer_keta`。**
実テーブル名が `layer_keta`（単数）。タスク本文の表記に合わせて
キーだけ複数形にすると、CSV のファイル名（`layer_ketas.csv`）と
DB のテーブル名が食い違って混乱する。

**5. `/report audit` は削除して改名した（別名を残していない）。**
`test_the_old_audit_command_is_gone` で `hasattr` を検査している。
残すと「audit なのに audit_log を読まない」という元の混乱がそのまま残る。
早見表・OPERATION.md も同時に直した（`test_docs_commands.py` が両方向で検査する）。

**6. 実行者の解決はギルドキャッシュ → `discord_name_cache` → ID の3段。**
解決できないものは**伏せずに ID のまま出す**（伏せると追跡できなくなる）。
`target` も同じ解決を通す（ロール ID・ユーザー ID が入ることがある）。
これは G2-8 として起票されている「`/report audit` が生の18桁 ID を出す」の
`changes` 側での解消にあたる。

**7. actor の補完候補は「実際にログへ出てくる人」だけ。**
ギルドの全メンバーを並べても、そのほとんどは1度も操作していない。
`list_actors()` が `GROUP BY actor_id ORDER BY MAX(audit_id) DESC` で出す。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| ルータの `scope.require(min_level)` を消す | ダッシュボードのレベル検査が失敗 |
| 一覧の `min_level` フィルタだけ消す | 同上（一覧に L4 限定の表が出る） |
| `settings` の `min_level=4` を消す | 2件が失敗 |
| `list_recent` の `actor_id` 条件を外す | 絞り込み3件が失敗 |
| `/report changes` が `reminders_log` を読むようにする | 3件が失敗 |
| 名前解決をやめて ID を返す | 2件が失敗 |
| `audit_log.action` を `editable=True` に | 読み取り専用の2件が失敗 |

#### 次タスクへの申し送り

- **G4-7（`progress_snapshots`）を足したら `TABLES` にも読み取り専用で加える。**
  そのとき「主要13テーブル」の数字が `test_the_export_table_count_matches_the_whitelist`
  で自動的に赤くなる（docs 4ファイル ＋ `cogs/{data,season}.py`）
- `/report changes` の `target` は `members#1` のような複合文字列も入る。
  `_resolve_actor` は数字でないものをそのまま返すので壊れないが、
  表示名へ解決したいなら別の解決器が要る
- G2-8（`/progress edit` が DB カラム名をそのまま出す）は**未着手のまま**。
  `changes` 側は解消したが `progress.py` 側は手を付けていない

---

### 2026-08-29 — G4-4: `/me` 個人サマリー（ブランチ `feat/g4-4`）

部員視点の入口が無かった。自分のタスク・未回答の投票・積層実績・担当ノードが
それぞれ別コマンドで、`/task list` は全体を返す。
**新入生が「今日自分は何をすればいいか」を1コマンドで確認できなかった。**

- ruff: `All checks passed!`
- pytest: **1120 passed, 12 skipped**（着手前は 1101 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `cogs/me.py`（新規） | `/me [user]`（L1・ephemeral）。集計は `unanswered_schedules` / `assigned_nodes` / `layer_summary` の3メソッドに分けた |
| `bot.py` `cogs/help.py` | Cog の登録と `/help` のカテゴリ（「基本」） |
| `tests/test_me.py`（新規） | 19件 |
| `docs/OPERATION.md` `docs/GUIDE.md` | コマンド表・早見表・役割別の一覧 |

**新しいテーブルもマイグレーションも無い**（受入基準どおり、既存クエリの合成のみ）。
`test_no_new_table_was_added_for_me` で「表を足していないこと」を固定した。

#### 設計判断

**1. 未回答は投票「単位」で数える**（候補単位ではない）。G3-2 で
`notify_unanswered` を予定単位にしたのと同じ定義に揃えた。候補単位で数えると
「3候補のうち1つに答えた人」が `/me` では未回答、DM では回答済みになり、
**画面ごとに違う数字**という一番たちの悪い形になる。
テストの種データは、わざと候補2つのうち1つだけ回答した予定を含めてある。

**2. 担当ノードは名前で照合する（ID ではない）。**
`progress_nodes.assignee` は `/progress add assignee:` に手入力される
**自由記述の名前**で、Discord ユーザー ID を持たない。ID で引く実装は
書けるが常に0件になる。Discord の表示名と `members.display_name` の
**両方**で照合し、改名した人でも台帳側で拾えるようにした。
それでも取りこぼす場合があることは OPERATION.md に明記した。
（`assignee` を ID 列にする改修は `/progress` 全体に波及するので別タスク。）

**3. 権限判定を `Me.may_view()` に切り出した。**
当初はコマンド本体から `is_self_or_level` を直接呼び、テストは
`mock.patch("cogs.me.is_self_or_level", ...)` で差し替えていた。
**これは単体では緑、フルセットでだけ赤になった。**
`tests/test_docs_commands.py` が全 Cog を `load_extension` →
`unload_extension` するとき `sys.modules["cogs.me"]` が消え、
次の import で**別のモジュールオブジェクト**ができる。
テストが import 済みのクラスが見ているのは古い方のモジュール辞書なので、
新しい方へ当てたパッチは効かない。
インスタンス属性（`cog.may_view = ...`）の差し替えならこの影響を受けない。

必要レベルは `VIEW_OTHERS_LEVEL = Level.L2` というモジュール定数にし、
**その値自体をテストで固定**した（差し替えたテストが「L2 であること」を
検査しなくなるのを防ぐため）。

**4. 判定できないときは通さない。** `may_view` は自分自身なら無条件 True、
それ以外は `is_self_or_level`。`is_self_or_level` は `discord.Member` で
なければ False を返すので、DM や解決できない実行者は**通らない**側に倒れる。

**5. 各セクション5件まで。** 「今日何をすればいいか」を1画面で見るための
コマンドなので、全件を出す意味がない。超過分は件数と、全部見るための
コマンド（`/task list` / `/progress view`）を添える。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| 未回答を候補単位で数える | 4件が失敗 |
| 完了ノードも担当中に含める | 2件が失敗 |
| タスクの `assignee_id` 絞り込みを外す | 1件が失敗 |
| `VIEW_OTHERS_LEVEL` を L1 に下げる | レベル固定のテストが失敗 |
| 権限判定そのものを外す | 2件が失敗 |
| 台帳の `display_name` を照合に使わない | 1件が失敗 |
| `bot.py` の Cog 登録を外す | 登録検査が失敗 |
| 積層集計の期間指定（今月）を外す | 1件が失敗 |

#### 次タスクへの申し送り

- **`progress_nodes.assignee` が ID を持たない**のは `/me` に限らない問題。
  `/report member-attendance`（G4-6）は `schedule_votes.user_id` を使うので
  影響しないが、「担当者で絞る」系の機能を足すときは同じ壁に当たる
- **フルセットでだけ赤くなるテストの型を1つ踏んだ。**
  `unload_extension` を通る `tests/test_docs_commands.py` があるので、
  **Cog のモジュール変数へ `mock.patch` を当てるテストは書かない**。
  差し替えるならインスタンス属性にする（ClaudeVault の gotcha 候補）
- `/me` は `aggregate_layer_stats`（G4-1）を再利用している。
  G4-5 のダイジェストも同じ関数を使う予定

---

### 2026-08-29 — G4-5: `/report weekly` の公開版と週次ダイジェスト（ブランチ `feat/g4-5`）

`/report weekly` は L2 以上・ephemeral 固定で、部員には
「今週サークル全体で何が進んだか」が見えなかった。

- ruff: `All checks passed!`
- pytest: **1148 passed, 12 skipped**（着手前は 1120 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `services/weekly_digest_service.py`（新規） | `last_week_range()` / `count_completed_between()` / `week_label()`（純関数） |
| `services/layer_stats_service.py` | `aggregate_layer_stats` に `until`（半開区間 `[since, until)`） |
| `repositories/task_repository.py` | `list_completed()`（完了タスクの取得） |
| `cogs/reports.py` | `build_weekly_embed()` を切り出し、`/report weekly` に `public: bool = False` |
| `cogs/reminders.py` | `weekly_digest` ループ（指定曜日 08:30・既定 OFF） |
| `config.py` | `WEEKLY_DIGEST_ENABLED`（既定 OFF）/ `WEEKLY_DIGEST_WEEKDAY`（既定 0＝月曜） |
| `tests/test_weekly_digest.py`（新規） | 28件 |
| `docs/OPERATION.md` `docs/GUIDE.md` | 設定表・通知表・コマンド表 |

#### 設計判断

**1. ADR 0023 は覆していない。0023 の「覆す条件」に沿った代替案として実装した。**
0023 が却下したのは「遅延が無い週にも『問題ありません』を送る」こと。
今回のダイジェストは**実績の報告**であって「異常なし」の通知ではない。
線引きを実装で保つために、次の3つをテストで固定した:

- ダイジェストの本文に「問題ありません / 遅延はありません / 遅れはありません /
  異常なし」が**現れないこと**（現れたら 0023 が却下したものと同じになる）
- マイルストーン警告とは**別のジョブ・別の `reminder_type`**であること
- **既定 OFF** であること（既存ギルドの通知量は変わらない。ADR 0024）

**2. `/report weekly` と自動投稿は同じ `build_weekly_embed()` を使う。**
別々に組み立てると、同じ「今週」の数字が画面ごとに食い違う
（G3-2 の「未回答が2つの定義で動いていた」と同じ形）。
`Reminders` は既存の `bot.get_cog("Schedule")` と同じやり方で
`bot.get_cog("Reports")` から呼ぶ。

**3. 「先週」は半開区間 `[前の月曜 0:00, 今週の月曜 0:00)`。当日を含めない。**
含めると「今日の朝までの実績」が混ざり、翌週の集計と二重になる。
`aggregate_layer_stats` に `until` を足したのも同じ理由で、
週をつなげても境界の記録が2回数えられない。

**4. 「何も無い週」は送らない。** `build_weekly_embed()` が `None` を返す
（未完了0・超過0・投票0・先週の完了0・先週の積層0）。
0/0/0 のダイジェストは「うまくいっている」ではなく
「まだ始まっていない」で、送っても読む人に何も伝えない。
これは 0023 の考え方（言うことが無い週は黙る）と同じ方向。

**5. 空状態は `public:true` でも公開しない。** 「まだデータがありません」を
チャンネルへ流す意味がなく、部員には bot の不調に見える。

**6. 投稿先はお知らせチャンネル →（無ければ）`resolve_default_channel_id()`。**
解決は `guild.get_channel` 経由（`_guild_channel`）で、**他ギルドの
チャンネルへは出せない**。送信先が無いときは部員には沈黙し、
運用者には `log_to_channel` で残す（マイルストーン警告と同じ作法）。

**7. 送信失敗を `reminders_log` に書かない。** `exists()` は status を見ないので、
書くとその週は二度と送られない（G2-3）。G4-2 の DM とは事情が違う——
チャンネル送信は次の週まで再試行の機会がないので、
「直らない失敗」として殺す理由がない。

**8. 曜日の不正値は既定に落とす（例外を投げない）。**
`for_guild()` で例外が出るとそのギルドの全コマンドが死ぬ
（gotcha `one-guild-loses-all-features`）。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| 既定を ON にする | 既定 OFF のテストが失敗 |
| `weekly_digest_enabled` の判定を消す | OFF のテストが失敗（**下の注記を参照**） |
| 曜日の判定を消す | 曜日テストが失敗 |
| 二重送信の防止を消す | 同週2回のテストが失敗 |
| 送信失敗を `failed` で記録する | 再送テストが失敗 |
| 本文に「遅延はありません」を足す | 定型文検査が失敗 |
| `until` の判定を消す | 先週の数字テストが失敗 |
| 「先週」に当日以降を含める | 5件が失敗 |
| 空のダイジェストも送るようにする | 0件ギルドのテストが失敗 |
| `public` を無視して常に ephemeral | 公開テストが失敗 |
| `weekly_digest.start()` を消す | ループ登録テストが失敗 |

**注記: 最初は「OFF」の検査が空振りしていた。**
`weekly_digest_enabled` の判定を丸ごと消しても緑のままだった——
テストが投稿先チャンネルを設定しておらず、
**「チャンネルが無いから送れなかった」で同じ結果になっていた**ため。
チャンネルと曜日を揃え「OFF だけが送信を止めている」状態にし、
対になる ON のケース（`test_turning_it_on_is_the_only_difference`）を
足して裏付けた。既定 OFF は ADR 0024 の核なので、ここが空振りしたままでは
このタスクの受入基準を満たしていない。

#### 次タスクへの申し送り

- **G4-7（`progress_snapshots`）が溜まったら、ダイジェストに
  「主桁 62%→68%」を足せる。** `build_weekly_embed()` の
  「先週の実績」フィールドが差し込み先
- `WEEKLY_DIGEST_ENABLED` は `/setup` のボタンにしていない
  （`/settings_set` から設定する）。`/setup` に足すなら
  G3-6 の `WELCOME_ENABLED` トグルと同じ形にできる
- `/report weekly` の `public:true` は**実行したチャンネル**へ出る。
  ダイジェストの自動投稿先（お知らせチャンネル）とは別なので、
  「公開したのに違う場所に出た」という混同は起きない

---

### 2026-08-29 — G4-6: `/report member-attendance` メンバー軸の出欠（ブランチ `feat/g4-6`）

`/report attendance-rate` は投票ごとの ok 率で、**「最近来ていない人」が
特定できない**。「3回連続で未回答」は退部のほぼ確実な予兆。

- ruff: `All checks passed!`
- pytest: **1171 passed, 12 skipped**（着手前は 1148 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `services/attendance_service.py`（新規） | `ScheduleAnswers` / `MemberAttendance` / `aggregate_member_attendance()`（純関数） |
| `cogs/reports.py` | `collect_member_attendance()` と `/report member-attendance [months]`（L2・ephemeral 固定） |
| `tests/test_member_attendance.py`（新規） | 23件 |
| `docs/OPERATION.md` `docs/GUIDE.md` | コマンド表・早見表・レポート例 |

#### 設計判断

**1. 母集団は `select_unanswered_targets` をそのまま呼んで作る。**
自前の条件式で書き直すと、DM が飛ぶ相手とこの表の対象がずれ、
G3-2 が潰した「未回答が2つの定義で動く」形に戻る。
`answered_ids=set()` を渡して「差し引く前の対象そのもの」を得ている。
呼んでいること自体を `inspect.getsource` で検査するテストを置いた
（条件式に書き換えたら落ちる）。

**2. ロールを解決できない予定は、集計から丸ごと外す。**
「0名」として数えると全員が未回答扱いになり、**実在しない連続未回答**が
出る。ADR 0021 / 0022 の「分からないものを数字にしない」をここにも当てた。
`/schedule remind` が同じ状況で `None`（特定できない）を返すのと同じ判断。

**3. 2つの率で分母を変えた。**
- 回答率 = 回答した回数 ÷ **対象になった回数**
- ok率 = 参加と答えた回数 ÷ **回答した回数**

ok率の分母も「対象回数」にすると回答率との積になり、
「答えてはいるが来られない人」と「そもそも答えない人」が同じ数字に潰れる。
分けたので Embed の本文に定義を明記した（率だけ見せて解釈を委ねない）。

**4. 連続未回答は直近から数え、対象外だった回は飛ばす。**
`aggregate_member_attendance` は**新しい順**の入力を前提にする
（`list_closed_schedules` が `deadline DESC` なのでそのまま渡せる）。
順序を取り違えると「昔サボっていて最近は来ている人」が
要注意人物として上がる——このコマンドの目的と正反対になる。
順序依存はドキュメント文字列に書き、逆順を渡す変異でテストが落ちることを実測した。

**5. ephemeral 固定。公開オプションを付けない。**
「誰が来ていないか」を並べた表なので、公開できると晒しになる。
`_params` に `public` が無いことをテストで固定した
（あとから追加されたら落ちる）。

**6. 期間は 30日 × months の近似。** 月末の境界を厳密にしても
「最近来ていない人」の判断は変わらないので、
`dateutil` のような依存を増やさない（ADR 0013 と同じ方向）。

**7. ロールは「現在の」保持者しか分からない。**
過去の予定について当時の保持者を復元する手段が無いので近似になる。
これは実装の都合ではなくデータの限界なので、docstring に明記した。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| 連続未回答を古い順に数える | 2件が失敗 |
| 回答しても連続が切れないようにする | 2件が失敗 |
| 対象外の回でも連続を数える | 2件が失敗 |
| ok 率の分母を対象回数にする | 2件が失敗 |
| 並び順を回答率の高い順にする | 1件が失敗 |
| ロール解決失敗を「名簿全員」に落とす | 1件が失敗 |
| 退部者を差し引かない | 1件が失敗 |
| 期間の絞り込みを外す | 1件が失敗 |
| ephemeral を外す | 1件が失敗 |
| 母集団を自前の条件式に置き換える | 2件が失敗 |

#### 次タスクへの申し送り

- **`/report attendance-rate` と `/report member-attendance` は目的が違う。**
  前者は「その予定にどれだけ集まったか」、後者は「誰が来ていないか」。
  統合の提案が出たら、母集団（前者は票数、後者は対象者）が
  そもそも別物であることを先に確認すること
- G4-12（投票メッセージの未回答者数を催促と揃える）も
  `select_unanswered_targets` を使う。**3箇所目**になるので、
  そのときに「呼び出し側が毎回 role_member_ids を組み立てている」
  重複をヘルパへまとめるかを検討する

---

### 2026-08-29 — G4-7: `progress_snapshots` 進捗の履歴（ブランチ `feat/g4-7`・スキーマ v18）

`services/milestone_service.py` が自ら書いていたとおり、履歴が無いため
ペースが「作成日→最終更新日の平均」でしか出せず、**停滞期間を含まない**
近似だった。「先週から何%進んだか」も分からなかった。

- ruff: `All checks passed!`
- pytest: **1212 passed, 12 skipped**（着手前は 1171 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `utils/db.py` | `progress_snapshots` の DDL・索引・`SCHEMA_VERSION = 18`・`_migrate_v18_progress_snapshots()` |
| `migrations/017_progress_snapshots.sql`（新規） | PostgreSQL 手動適用用の参照定義 |
| `repositories/progress_repository.py` | `has_snapshot` / `save_snapshots` / `list_snapshots` / `snapshot_node_ids` / `latest_snapshot_dates` |
| `services/milestone_service.py` | `snapshot_pace()` / `recent_gain()` / `sparkline()` と、ペースの出どころ3段の説明 |
| `cogs/progress.py` | 20分ループ末尾の `save_daily_snapshot()`、`pace_overrides()` の優先順位、`/progress history [node] [days]`（L1） |
| `repositories/table_repository.py` | 読み取り専用の TableSpec（`/data export` に含める。G4-3 の申し送り） |
| `tests/test_progress_snapshots.py`（新規） | 41件 |
| `tests/test_data_purge.py` ほか | 新テーブルぶんの前提更新（3ファイル） |
| `docs/*` `README.md` `cogs/{data,season}.py` | 「主要13テーブル」→「主要14テーブル」、コマンド表・早見表 |

#### 新しい ADR の草案（0035）

**ADR 0022 の「覆す条件」に沿った移行なので、0022 を失効させるのではなく
更新する。** 0022 の核（履歴が無い期間について予測を出さない）は残る。

---

**0035. 進捗の履歴を日次スナップショットで持つ**
`supersedes:` なし / `updates: 0022`

**文脈** — 0022 は「履歴テーブル（案 A）は正確だが、この機能のためだけに
スキーマと書き込み経路を増やすべきではない。近似（案 B）で足りるかを先に見る」
と決め、覆す条件に「**進捗の履歴が必要な別機能が出てきたとき。
その時点で A に移行し、ペースも正確化する**」を挙げていた。
`/progress history`（G4-7）と週次ダイジェストの「先週から何%」（G4-5）が
それにあたる。近似では原理的に出せない。

**選択肢**

| 案 | 内容 | 欠点 |
|---|---|---|
| A. 全書き込み経路に履歴を記録する | 変化の瞬間まで正確 | 経路が7つあり、1つ漏らすと静かに歪む |
| B. 定期ジョブが1日1回スナップショットを撮る | 書き込み経路を1つも触らない | 日内の変化は残らない |
| C. 近似のまま（0022 を維持） | 変更ゼロ | `/progress history` が作れない |

**決定** — **B。20分ごとの既存同期ループの末尾で、その日まだ書いていなければ
1回だけ全ノードのスナップショットを保存する。**

**理由**
- 進捗は日単位で語られる（「先週から何%」「大会まであと N 日」）ので、
  日内の解像度は要らない。**必要な精度の下限で止める**
- 案 A は `/progress edit`・Todoist 同期・桁巻き反映・ダッシュボード編集・
  `/weight set`・再集計・移行スクリプトの**全部**に記録を足すことになり、
  1つ漏らすと履歴が静かに歪む。歪んだ履歴は近似より悪い
- 集計後の値（`aggregated`）を撮るので、子の変更が親に伝わった結果が残る

**却下した案とその理由**
- **A** = 上記。書き込み経路を7箇所触る変更を、日次の解像度で足りる要件のために入れない
- **C** = `/progress history` が作れない。0022 自身が「そのときは A に移行する」と書いている

**影響範囲**
- スキーマ v18。`UNIQUE (guild_id, node_id, snapshot_date)` が「1日1行」を
  **構造で**保証する（ADR 0008 / 0016）。アプリ側の早期 return は
  無駄な書き込みを省くための最適化にすぎない
- `aggregated` / `actual_weight_g` は NULL 許容。未集計を 0.0 に丸めない（ADR 0021）
- `node_id` に外部キーを張らない（ADR 0019 と同じ方針）。ノードが消えても履歴は残る
- ペースの出どころは **snapshots > layer_records > node** の3段。
  **履歴が足りないノードは snapshots を使わない**（`MIN_SNAPSHOTS_FOR_PACE = 3`、
  `MIN_SNAPSHOT_SPAN_DAYS = 3`）。ここが 0022 の核をそのまま引き継ぐ部分
- 保存されるのは「その日の最初の同期時点の値」＝実質は前日終了時点の状態
- 既存 DB には**空のテーブルが増えるだけ**。導入直後は従来の推定のまま動く（ADR 0024）

**覆す条件**
- 日内の変化を追う必要が出たとき（例: 作業日の時間帯別の進み方）。
  そのときは案 A（書き込み経路への記録）へ移す
- ノード数 × 日数が実運用で問題になる規模に育ったとき（保持期間の設計が必要になる）

**根拠** — `docs/IMPROVEMENT_TASKS.md` G4-7、`migrations/017_progress_snapshots.sql`、
`services/milestone_service.py`（`snapshot_pace` の docstring に根拠を併記）

---

#### 設計判断（上記 ADR 草案に含まれないもの）

**1. 同期がエラーを報告したギルドでもスナップショットは撮る。**
Todoist 連携が壊れている間だけ履歴が抜けると、復旧後にペースが狂う
（「その期間まったく進んでいない」ように見える）。同期の成否と切り離した。

**2. `/progress history` は「伸び」を出せないときに 0% と書かない。**
「比較できる記録がまだありません」と出す。記録が1点しか無い状態と
「まったく進んでいない」は別物（ADR 0021）。
一方で **`snapshot_pace` は伸び 0 を判定不能にしない**——
十分な期間の実測で伸びが0なのは「分からない」ではなく
「このままでは間に合わない」という情報だから。この2つは意図的に扱いが違う。

**3. スパークラインは系列ごとに正規化しない。** 常に 0〜1 で描く。
正規化すると 5%→6% の変化が満杯のグラフに見える。
未集計は空白で、`▁`（0%）と区別する。

**4. `/data export` の対象に入れた**（G4-3 の申し送りどおり）。
新テーブルを足すたびに「持ち出せないデータ」が増えるのを防ぐ。
「主要13テーブル」→「主要14テーブル」は
`test_the_export_table_count_matches_the_whitelist` が自動で赤くしてくれた。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| `ON CONFLICT DO NOTHING` を `DO UPDATE` に | 1日1行のテストが失敗 |
| DDL から `UNIQUE` を外す | 15件が失敗 |
| 値の列を `NOT NULL DEFAULT 0` に | 15件が失敗 |
| `has_snapshot` の早期 return を消す | 効率のテストが失敗（**下の注記**） |
| `MIN_SNAPSHOTS_FOR_PACE` を 2 に下げる | 件数条件のテストが失敗（**下の注記**） |
| `MIN_SNAPSHOT_SPAN_DAYS` の判定を消す | 期間条件のテストが失敗 |
| `aggregated` の None を 0.0 として数える | 1件が失敗 |
| `recent_gain` が 0.0 を返すようにする | 2件が失敗 |
| 未集計のスパークラインを `▁` にする | 1件が失敗 |
| スパークラインを系列ごとに正規化 | 1件が失敗 |
| スナップショット由来のペース上書きを消す | 1件が失敗 |
| 履歴不足のノードにもペースを入れる | 1件が失敗 |
| `list_snapshots` から `guild_id` 条件を外す | 1件が失敗 |

**注記: 最初は2つの変異が素通りした。**

- **`has_snapshot` の早期 return を消しても全部緑だった。**
  `UNIQUE` 制約があるので行は増えず、**正しさは守られていた**——
  つまりテストは正しく、消えたのは効率だけ。とはいえ
  「制約が守るから if は不要」で消される種類のコードなので、
  「2回目はツリーを読み直さない」ことをテストで固定した
- **`MIN_SNAPSHOTS_FOR_PACE` を 3→2 に下げても緑だった。**
  件数のテストが**期間の条件でも落ちる**データを使っていたため、
  件数の下限を検査できていなかった。2点を30日離した種データを足して
  件数条件だけを切り出した

#### 次タスクへの申し送り

- **G4-5 のダイジェストに「主桁 62%→68%」を足せる状態になった。**
  `recent_gain(snapshots, 7, today)` がそのまま使える。
  まだ足していない（G4-5 は完了済みで、混ぜると ADR 0014 に反する）
- **保持期間の設計はしていない。** ノード100 × 365日 = 年3.6万行。
  数年運用しても実害は無い見込みだが、`/data delete` の対象には入っている
- **日内の変化は残らない。** 「その日の最初の同期時点の値」なので、
  1日のうちに進んで戻した動きは記録されない（ADR 草案の「覆す条件」）
- ダッシュボードの表に `progress_snapshots` が出る（読み取り専用・L1）。
  行数が多いので、シート切替（`SHEET_TABLES`）に入れるかは別途検討

---

### 2026-08-29 — G4-8: `/stock` 資材・消耗品の在庫（ブランチ `feat/g4-8`・スキーマ v19）

人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。
発注判断は「残量が閾値を割った」という、bot が自動で見張れる条件。

- ruff: `All checks passed!`
- pytest: **1248 passed, 12 skipped**（着手前は 1212 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `utils/db.py` | `stock_items` / `stock_movements`・索引・`SCHEMA_VERSION = 19`・`_migrate_v19_stock()` |
| `migrations/018_stock.sql`（新規） | PostgreSQL 手動適用用の参照定義 |
| `services/stock_service.py`（新規） | `is_low` / `crossed_below` / `low_items` / `format_amount`（純関数） |
| `repositories/stock_repository.py`（新規） | 品目・増減・通知状態の CRUD |
| `cogs/inventory.py`（新規） | `/stock list\|add\|use\|set-threshold\|remove` |
| `cogs/reminders.py` | 朝 08:30 の `_notify_low_stock`（割れが無い日は送らない） |
| `utils/notify.py` | `resolve_notice_channel_id()` / `guild_channel()`（告知先の解決） |
| `repositories/table_repository.py` | 読み取り専用の TableSpec 2つ（`/data export` に含める） |
| `tests/test_stock.py`（新規） | 36件 |
| `bot.py` `cogs/help.py` | Cog 登録とカテゴリ「資材・在庫」 |
| `docs/*` `README.md` ほか | 「主要14テーブル」→「主要16テーブル」、コマンド表・早見表・通知表 |

#### 設計判断

**1. 閾値は NULL 許容。既定値を置かない**（ADR 0021）。
`threshold` に `DEFAULT 0` を置くと「在庫0でも閾値割れではない」という
嘘になる。**閾値を決めていない品目は判定そのものをしない**。
一覧では「閾値 未設定」と明示し、0 とは書かない。
一方で **`/stock set-threshold` の `0` は有効な閾値**（尽きたら知らせる）。
解除は負の値で行う——この区別をテストで固定した。

**2. 閾値は「以下」で割れ。** 「残り1本になったら発注」と設定した人は
1本になった時点で知らせてほしいのであって、0本になってからでは
納期に間に合わない。

**3. 即時通知の重複防止に `reminders_log` を使わない。**
`stock_items.low_notified_flag` を持たせ、閾値以上へ戻ったときに落とす。
`reminders_log.exists()` はキー単位で「送ったか」しか見ないので、
**同じ品目が何度も割りうる**この用途では2回目以降が永久に飛ばなくなる。
G4-2（1セッションにつき1回）とは要件が違う。

**4. 送信に成功してからフラグを立てる。**
先に立てると、送信失敗した回が「通知済み」になり、
次に割り直すまで二度と知らせられない。

**5. 在庫は負にならない（`MAX(quantity + ?, 0)`）が、履歴には申告どおりの
値を残す。** -3本という物理的にありえない値を作らず、かつ
「使いすぎの申告があった」事実も消さない。
利用者には「記録した消費は残量を上回っています」と伝える（黙って丸めない）。

**6. 品目名の初期値をコードにも DDL にも持たない。**
何を在庫管理するかはサークルごとに違う（AGENTS.md「組織構造は可変」）。
一方で **`プリプレグ`・`桁`・`積層` といったドメイン語はテストとドキュメントに
書いてよい**（対象が鳥人間サークルに限定されているため。ADR 0004）。

**7. `/stock remove` は受入基準に無かったが追加した。**
「マスタ管理は `layer_keta` と同型」という注記に従うと、
有効フラグを持つのに無効化する手段が無い状態になる。
論理削除で、増減の履歴は残す。

**8. 告知先の解決を `utils/notify.py` へ切り出した。**
「お知らせ →（無ければ）進捗 → タスク」の順。
解決した ID は必ず `guild.get_channel` で引く（`guild_channel()`）ので、
**他テナントのチャンネルへは出せない**（G4-11 が直そうとしている
`log_to_channel` の穴を、新しいコードでは最初から作らない）。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| 閾値未設定を 0 として判定する | 3件が失敗 |
| 閾値の判定を「未満」にする | 2件が失敗 |
| 割り込み判定から「直前の状態」を外す | 2件が失敗 |
| 在庫が負になれるようにする | 1件が失敗 |
| `get_item` から `guild_id` 条件を外す | 2件が失敗 |
| 無効化を物理削除にする | 2件が失敗 |
| 閾値以上へ戻ってもフラグを落とさない | 1件が失敗 |
| 送信前にフラグを立てる | 1件が失敗 |
| 朝の通知を割れが無くても送る | 2件が失敗 |
| 朝の通知を品目ごとに連投する | 1件が失敗 |
| DDL の閾値に `NOT NULL DEFAULT 0` | 12件が失敗 |
| DDL から `UNIQUE (guild_id, item_name)` を外す | 1件が失敗 |
| `set-threshold` の `0` を解除扱いにする | 1件が失敗 |

#### 次タスクへの申し送り

- **G4-9（工具の貸出）は同じ `cogs/inventory.py` に足す**（受入基準どおり）。
  **新しいマイグレーションが要る。** `_migrate_versioned()` は
  `version >= SCHEMA_VERSION` で早期 return するため、
  **v19 に後から ALTER や CREATE を足しても既存 DB では実行されない**
  （新規 DB にだけテーブルがある状態になる。gotcha
  `bot-wont-start-undefined-column`）。表の v19 の欄は
  「在庫・工具」と書かれていたが、**工具は v20 を使うこと**。
  そのぶん G4-10（ヒヤリハット）は v21 へずれる
- 督促ロジックの共用（G4-9 の注記）は、`classify_stale_sessions`
  （G4-2）の閾値を「返却予定日からの経過」に読み替える形で使える
- `/stock` に「発注済み」の状態は無い。閾値を割ったまま発注しても
  毎朝通知が出続ける。運用で困るようなら `ordered_flag` を足す余地がある

---

### 2026-08-29 — G4-9: `/tool` 工具・機材の貸出（ブランチ `feat/g4-9`・スキーマ v20）

`/layer start` → `/layer end` とまったく同じ「開始 → 進行中 → 終了」モデル。
借りたまま返らない工具は、次に使う人の作業日をそのまま潰す。

- ruff: `All checks passed!`
- pytest: **1284 passed, 12 skipped**（着手前は 1248 passed, 12 skipped）

#### 完了内容

| ファイル | 内容 |
|---|---|
| `utils/db.py` | `tools` / `tool_loans`・索引・`SCHEMA_VERSION = 20`・`_migrate_v20_tools()` |
| `migrations/019_tools.sql`（新規） | PostgreSQL 手動適用用の参照定義 |
| `services/tool_service.py`（新規） | `overdue_loans()` / `loan_status_label()`（純関数） |
| `repositories/tool_repository.py`（新規） | 工具マスタと貸出の CRUD |
| `cogs/inventory.py` | `/tool list\|borrow\|return\|add\|remove`（G4-8 と同じ Cog） |
| `cogs/reminders.py` | 朝 08:30 の `_notify_overdue_tools`（本人へ DM・1貸出1回） |
| `repositories/table_repository.py` | 読み取り専用の TableSpec 2つ |
| `tests/test_tools.py`（新規） | 36件 |
| `docs/*` `README.md` ほか | 「主要16テーブル」→「主要18テーブル」、コマンド表・早見表・通知表 |

#### 設計判断

**1. スキーマ版は v19 ではなく v20 を切った（表の記載と違う）。**
表の v19 の欄は「G4-8（在庫・工具）」と1版にまとめる想定だったが、
**G4-8 が先に v19 を切ってしまった後では、同じ版に足しても既存 DB へ届かない**。
`_migrate_versioned()` は `version >= SCHEMA_VERSION` で早期 return するため、
v19 済みの DB は二度と v19 を通らず、**新規 DB にだけテーブルがある**状態になる
（gotcha `bot-wont-start-undefined-column` と同型。G3-3 / G3-4 が
同じ罠を避けるために1版へまとめたのと**逆向きの**判断で、
理由は同じ「早期 return」）。表の割り当ても更新し、
G4-10 のヒヤリハットを v20 → **v21** へずらした。
`v19` に工具が混ざっていないことを `inspect.getsource` で検査している。

**2. 貸出中かどうかは `tool_loans.returned_at IS NULL` で表す。**
`tools` 側に `borrowed_flag` を置く案は採らなかった——
工具の行を消したときに貸出の事実まで消えるし、
「誰がいつ借りたか」を別に持つ必要がどのみち出る。
これは `/layer` が `layer_sessions`（進行中）と `layer_records`（完了）を
分けているのと同じ形。

**3. 返却予定日は NULL 許容で、未設定なら督促しない**（ADR 0021）。
「予定日を決めていない貸出」を「本日返却」とみなすのは、
分からないものを数字にすることにあたる。
**予定日当日も督促しない**——「本日中に返す」つもりの人へ朝に
「超過しています」と送るのは誤報になる。

**4. `classify_stale_sessions`（G4-2）をそのまま呼ばず、形だけ揃えた。**
受入基準は「督促ロジックは G4-2 の押し忘れ検知を共用する」だが、
あちらの閾値は**分**で `started_at` からの経過を見るのに対し、
こちらは**日**で `due_date` を過ぎたかを見る。共用するには貸出行を
偽のセッション辞書（`keta` / `layer_num` を持つ）へ詰め替えることになり、
読む人にとって意味不明になる。**共有したのは形**——
「閾値を超えたものを選び、通知済みフラグで1回に絞り、
送れたときだけフラグを立てる」——で、この3点は同じにしてある。
`tool_service.py` の docstring に理由を書いた。

**5. DM の失敗は G4-2 と同じ3分岐。**
拒否（Forbidden）は翌朝も直らないのでフラグを立てて打ち切り、
一時障害（HTTPException）は立てずに翌朝再試行、
**メンバーが見つからない場合も立てない**（退部者が戻ってきたら督促できるように）。

**6. 返却は借りた本人でなくても記録できる。**
工具は現物が戻れば返却であって、借りた人が不在のときに
記録できないと台帳が実物とずれる。誰の貸出を閉じたかは応答に出す。

#### 空振り確認（実測）

| 一時的な改変 | 結果 |
|---|---|
| 予定日未設定を「借りた日」で代用する | 2件が失敗 |
| 予定日当日を超過扱いにする | 1件が失敗 |
| 返却済みの貸出も督促する | 1件が失敗 |
| 通知済みフラグを無視する | 2件が失敗 |
| 貸出中の判定から `returned_at IS NULL` を外す | 1件が失敗 |
| `give_back` から `guild_id` 条件を外す | 1件が失敗 |
| 工具の無効化を物理削除にする | 1件が失敗 |
| 二重貸出を許す | 1件が失敗 |
| 不正な予定日を無視して記録する | 1件が失敗 |
| 返却を借用者本人に限定する | 1件が失敗 |
| 督促先が居なくてもフラグを立てる | 1件が失敗 |
| 一時障害でもフラグを立てる | 1件が失敗 |
| v19 に工具テーブルを足す | 1件が失敗 |

**注記:** 最初に当てた「予定日未設定を `today` で代用する」変異は
**等価変異**だった（`days_over` が 0 になり、当日の除外条件で同じく落ちるため）。
テストの穴ではないと確認したうえで、実際に挙動が変わる
「借りた日で代用する」に差し替えて検出を確かめた。

#### 次タスクへの申し送り

- **G4-10 のヒヤリハットは v21 / `020_incidents.sql`**（表も更新済み）
- `/tool` に予約（次に使う人の登録）は無い。「借りたい」が競合したときは
  `/tool list` で借用者に直接聞く運用になる
- 督促は1貸出につき1回。長期の未返却を繰り返し知らせたいなら、
  `overdue_notified_flag` を「最後に督促した日」へ変える余地がある
