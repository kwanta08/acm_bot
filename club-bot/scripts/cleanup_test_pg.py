"""テスト用 PostgreSQL DB から、テストが作成したオブジェクトを安全に削除する。

tests/test_db_postgres.py のライブテスト（CLUB_TEST_PG_DSN）が失敗途中に
作成したテーブル・ビュー・schema_meta を削除し、テストの独立性を保つ。

安全装置:
- 接続先のデータベース名に "test" が含まれない場合は実行を拒否する
  （本番の clubdb 等を誤って初期化しないため）
- 削除対象は本プロジェクトのスキーマ定義にあるテーブル・ビューのみ
- データの中身・接続情報は一切出力しない（オブジェクト名と件数のみ出力）

使い方:
    CLUB_TEST_PG_DSN=postgresql://user:pass@host:5432/clubdb_test \
        venv/bin/python scripts/cleanup_test_pg.py
    # または
    venv/bin/python scripts/cleanup_test_pg.py \
        --dsn postgresql://user:pass@host:5432/clubdb_test
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import TABLE_DDL, Database  # noqa: E402

VIEWS = ["v_todoist_status", "v_attendance", "v_team_summary"]
# ドロップ順（FK 参照のない順。CASCADE も付ける）
TABLES = list(TABLE_DDL.keys()) + ["schema_meta"]


async def main(args: argparse.Namespace) -> None:
    dsn = args.dsn or (os.getenv("CLUB_TEST_PG_DSN") or "").strip()
    if not dsn:
        print("ERROR: --dsn または環境変数 CLUB_TEST_PG_DSN を指定してください。")
        sys.exit(1)

    db = Database("./unused.db", database_url=dsn)
    try:
        await db.connect()
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 接続に失敗しました: {type(e).__name__}")
        sys.exit(1)

    try:
        row = await db.fetchone("SELECT current_database() AS db")
        db_name = row["db"]
        # 安全装置: テスト用 DB 名であることを確認（本番 clubdb を守る）
        if "test" not in db_name.lower():
            print(f"ERROR: データベース名 '{db_name}' に 'test' が含まれません。")
            print("本番 DB を誤って初期化しないよう、実行を中止しました。")
            sys.exit(1)
        print(f"対象データベース: {db_name}（テスト用と判断）")

        for view in VIEWS:
            await db.execute(f"DROP VIEW IF EXISTS {view}")
            print(f"  DROP VIEW {view}")
        for table in TABLES:
            row = await db.fetchone(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = ?) AS e", (table,))
            if not row["e"]:
                continue
            cnt = await db.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            await db.execute(f"DROP TABLE {table} CASCADE")
            print(f"  DROP TABLE {table}（{cnt['c']} 行を削除）")
        print("クリーンアップが完了しました。")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None,
                        help="テスト用 PostgreSQL の接続 URL（既定: CLUB_TEST_PG_DSN）")
    asyncio.run(main(parser.parse_args()))
