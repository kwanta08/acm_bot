# G1-9 / G1-10 起票内容

`docs/IMPROVEMENT_TASKS.md` の Phase G1 に追加してください。
G1-0 の完了ログ（申し送り B / C）から起票したものです。

---

- [ ] **G1-9** `_coerce()` が INTEGER 列に float を返す（G1-0 と同じ失敗の書き込み側）。

      `repositories/table_repository.py:317-332` の `number` 分岐は
      `int(text)` → 失敗したら `float(text)` の順に試すため、INTEGER 列にも float が入る。
      さらに `:320-321` の `isinstance(value, (int, float)): return value` により、
      JSON ボディで `{"priority": 2.7}` が来た場合は変換すら経ずに素通りする。

      asyncpg は int8 の引数に float を渡しても `DataError` になるため、
      **G1-0 とまったく同じ形で本番だけ 500 になる**（SQLite では保存できてしまい、
      その後 bot 側の読み取りが壊れる — gotcha `progress-stops-after-dashboard-edit` と同型）。

      **`number` の既定を int にはできない。** 編集可能な `number` 列は整数と実数が混在する:

      | 列 | DDL | 受けるべき型 |
      |---|---|---|
      | `tasks.priority` | INTEGER | int のみ |
      | `layer_records.minutes` | INTEGER | int のみ |
      | `progress.sort_order` | **REAL** | float 可 |
      | `progress.target_weight_g` | **REAL** | float 可 |
      | `progress.actual_weight_g` | **REAL** | float 可 |

      - **受入**:
        1. `Column` に列型（integer / real）を持たせ、`_coerce()` がそれに従う。
           **G1-0 の `pk_type` と同じく、安全な既定値が無いことを構造で示す**
           （既定 int なら重量列が壊れ、既定 float なら本件が新しい列で再発する）
        2. INTEGER 列に小数が来たら 400（`InvalidValueError`）で
           「整数で入力してください」と返す。丸めない
        3. `isinstance(value, (int, float)) → return value` の素通りを塞ぐ
           （JSON 由来の float も同じ検査を通す）
        4. 宣言と DDL のずれを検出するテスト（G1-0 で `pk_type` に対して書いたものと同じ形）
        5. PG 実機で `tasks.priority` に `"2.7"` を PATCH → 400 になることを検査する
           （`CLUB_TEST_PG_DSN` があるときだけ走る形）
      - **注意**: `progress` 型（`manual_progress`）は `parse_progress` が別に扱うので対象外

---

- [ ] **G1-10** CI に PostgreSQL を追加し、PG 経路を回す。

      G1-0 の不具合は「CI が全部緑なのに本番だけ壊れている」形で残った。
      ADR 0006（本番は PostgreSQL / SQLite は開発・テスト専用）を踏まえると、
      **SQLite だけの CI は本番を代表していない。**

      - **受入**:
        1. `.github/workflows/ci.yml` に `services: postgres:16` を追加し、
           `CLUB_TEST_PG_DSN` を渡す
        2. **DB 名に `test` を含めること**。含めないと
           `tests/test_db_postgres.py` の `_guarded_dsn()` が skip する
           （= 追加したのに1件も走らない、という最悪の形になる）
        3. PG ジョブで skip が 0 件であることを確認する（`-rs` で出力）
        4. マトリクス3版すべてで回すとコストが増えるので、
           **PG は 3.12 のみ**に絞ってよい（判断を完了ログに残す）
      - **検証**: 意図的に G1-0 の修正を戻した PR で CI が赤になること
      - **注意**: ADR 0014（1タスク＝1ブランチ）に従い、他の変更と混ぜない

---

## 受入基準の訂正（G1-0）

当初の受入基準4「`scripts/check_g0_3_pg.py` が『通った』を返す」は**誤りでした**。

同スクリプトのチェック [1] は asyncpg へ直接 `str` を渡す探針で、club-bot のコードが
1行も関与しません。asyncpg が int8 引数に `str` を受け付けないというドライバ仕様そのものを
測っているため、アプリを修正しても永久に NG のままです。

スクリプト側を修正しました:

- [1] を「ドライバ仕様の確認（NG が正常）」の情報行へ格下げ
- 判定は [2]（`Database` → `TableRepository.get_row`）のみに基づく
- 接続先 DB 名に `test` が含まれず [2] がスキップされた場合は
  「判定不能」で終了コード 2（**スキップを「通った」と誤判定しない**）

回帰の担保はテスト側（`tests/test_dashboard_edit.py` / `test_db_postgres.py` の PG 版）に
置かれているので、そちらが本命です。
