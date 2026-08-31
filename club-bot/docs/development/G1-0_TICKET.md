# G1-0: ダッシュボードの `row_id` を主キーの型へ変換する

> **内部の作業用ドキュメントです（[development/README.md](README.md)）。**
> 書かれた時点のスナップショットで、現在のコードとは食い違う記述を含みます。
> **現状の仕様の根拠には使えません。** 使い方は [`../GUIDE.md`](../GUIDE.md)、
> 運用は [`../OPERATION.md`](../OPERATION.md) を参照してください。

`docs/development/IMPROVEMENT_TASKS.md` の **Phase G1 の先頭**に入れてください（G1-1 より前）。
G0-3 の検証で **落ちることが実測で確定**したため、優先度は最上位です。

---

## G0-3 の完了ログ（そのまま追記できます）

- [x] **G0-3** 【人間タスク】PostgreSQL でダッシュボードの編集経路を検証する。
      **結果: 落ちた。** VPS の `clubbot_test`（PostgreSQL 16）で `scripts/check_g0_3_pg.py` を実行。

      ```
      [1] asyncpg へ直接 str を渡す: NG
          asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
          ('str' object cannot be interpreted as an integer)
      [2] 本物のコード経路（Database → TableRepository.get_row）: NG
          asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
          ('str' object cannot be interpreted as an integer)
      ```

      - **本物のコード経路でも落ちている**ので、本番のダッシュボードは実際に壊れている
      - 当初 `CLUB_TEST_PG_DSN=... pytest tests/test_dashboard_edit.py` で検証すると書いていたが、
        **このテストは `CLUB_TEST_PG_DSN` を一度も読まず SQLite 決め打ち**のため、
        実行しても PG 経路は1行も通らない（緑が出ても「測れていない」だけ）。検証方法を差し替えた
      - `docs/development/IMPROVEMENT_REPORT.md` の P0-8 は【要検証】から**確定**へ格上げ
      - 申し送り: G1-0 として起票

---

## タスク本体

- [ ] **G1-0** ダッシュボードの行編集で `row_id` を主キーの型へ変換する。

      `dashboard/routers/tables.py:255` は `row_id` を **str** で受け取り、
      `repositories/table_repository.py:489,498` がそのままバインド値にする。
      `utils/db.py:827-839` の `_prepare()` は「`?` → `$N`」の書き換えだけで型変換をせず、
      `utils/db.py:735` の `asyncpg.create_pool()` にも `init=` が無い（型コーデック未登録）。
      その結果 asyncpg が Bind の時点で `DataError` を投げる。

      ホワイトリスト7表のうち **6表が BIGINT 主キー**（`INTEGER PRIMARY KEY AUTOINCREMENT`
      → `to_pg_ddl` で `BIGINT GENERATED ...`）。TEXT なのは `schedules` のみ。

      | 表 | pk | PG 型 | 現状 |
      |---|---|---|---|
      | members | `member_id` | BIGINT | ✗ |
      | tasks | `local_task_id` | BIGINT | ✗ |
      | teams | `team_id` | BIGINT | ✗ |
      | schedule_votes | `vote_id` | BIGINT | ✗ |
      | layer_records | `record_id` | BIGINT | ✗ |
      | progress | `progress_node_id` | BIGINT | ✗ |
      | schedules | `schedule_id` | TEXT | ✓ |

      落ちる位置は `update_row` ではなく、先に呼ばれる `get_row`
      （`tables.py:271` の `before = await repo.get_row(...)`）。
      **部分書き込みは起きず 500 になる**——これは維持すること。

      - **受入**:
        1. `TableSpec` に主キーの型を持たせ（例 `pk_type: str = "int"`）、
           `get_row` / `update_row` の入口で正規化する。
           **ルータ側で `int()` する対症療法にしない**——ホワイトリストで型を定義する
           既存方針（ADR 0016）と一貫させる
        2. 数値に変換できない `row_id`（`"abc"` 等）は **404**（`UnknownRowError`）にする。
           「その行が無い」が意味として正しく、500 にしない
        3. **PostgreSQL で回る編集テストを追加する。** `DashboardConfig` は
           `database_url` を受け取れる（`dashboard/config.py:63`）ので構造上は可能。
           現状 `tests/test_dashboard_edit.py` は `_config(db_path)` で SQLite 決め打ちのため、
           **直しても再発を検出できない**。`CLUB_TEST_PG_DSN` があるときだけ走る形にする
        4. `scripts/check_g0_3_pg.py` が「通った」を返す
        5. SQLite 経路の既存テストが従来どおり緑（663 passed / 4 skipped を下回らない）
      - **検証**:
        - `python -m pytest tests/ -q -rs`（SQLite。skip が増えていないこと）
        - `CLUB_TEST_PG_DSN=postgresql://.../clubbot_test python -m pytest tests/ -q -rs`
        - VPS で `./venv/bin/python scripts/check_g0_3_pg.py <DSN>`
      - **注意**:
        - `row_id` 以外にも str のまま int 列へ渡している経路が無いか、
          `_sheet_where`（`table_repository.py`）と `list_rows` の `sheet_id` を確認する
          （`schedule_id` と桁名はどちらも TEXT なので現状は問題ないはず。**確認して報告**）
        - CI に postgres service を足して PG 経路を回すのが望ましい（別タスクでも可）

---

## Claude Code への実装プロンプト

```
/clear
```

```
/acm-bot-loop

club-bot/docs/development/IMPROVEMENT_TASKS.md の G1-0 を実装して。
G0-3 の検証で PostgreSQL では落ちることが実測で確定している（完了ログ参照）。

## 事前に読むもの

1. IMPROVEMENT_TASKS.md の G1-0 の受入基準・検証・注意
2. 開発ノート の decisions/_index.md（/add-dir 済み）。
   特に 0016（外部へ出すテーブル・列はホワイトリストで定義する）と
   0006（本番は PostgreSQL、SQLite は開発・テスト専用）
3. gotchas/dashboard-tests-silently-skipped.md
   （PG のテストを追加するとき、skip を「緑」と数えない）

## 実測された失敗

  asyncpg.exceptions.DataError: invalid input for query argument $2: '5'
  ('str' object cannot be interpreted as an integer)

raw asyncpg と、本物の Database → TableRepository.get_row の両方で再現。

## 手順

1. 受入基準を1行ずつ書き出す
2. fix/g1-0 ブランチを切る
3. **先に失敗するテストを書く。** ただし SQLite では再現しないことに注意——
   型変換の単体テスト（TableSpec の pk_type に沿って正規化されること、
   変換できない値が UnknownRowError になること）は SQLite 環境でも書ける。
   PG 結合テストは CLUB_TEST_PG_DSN があるときだけ走る形にする
4. 最小の実装 → ruff + pytest → 自己修正を全パスまで
5. 実装を元に戻すと新しいテストが落ちることを確認する
6. 表のチェックと完了ログを更新して報告する

## 制約

- ルータ側で int() する対症療法にしない。TableSpec に型を持たせる
- 変換できない row_id は 404。500 にしない
- 落ちる位置が get_row（update_row より前）である性質は維持する
  ——部分書き込みが起きない設計を壊さない
- ADR に反する実装をしない。衝突したら実装せず報告する
- コミットはしない。skip を「緑」と数えない（-rs 必須）

## 報告に含めること

- row_id 以外に str のまま int 列へ渡している経路が無いかの確認結果
  （_sheet_where / list_rows の sheet_id）
- PG 結合テストを CI で回すべきかの判断（postgres service の追加要否）
```

---

## 実装後の確認

VPS で:

```bash
cd ~/club-bot/club-bot
git fetch origin && git checkout <ブランチ>
./venv/bin/python scripts/check_g0_3_pg.py postgresql://<USER>:<PW>@127.0.0.1:5432/clubbot_test
```

「通った → G0-3 に『PostgreSQL でも緑』と記録してクローズ」と出れば完了です。
