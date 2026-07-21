"""既存の SQLite DB（data/club.db）から PostgreSQL へデータを移行する
一回限りの移行スクリプト。

使い方:
    # dry-run（既定。両側の件数を表示するだけで変更しない）
    venv/bin/python scripts/migrate_sqlite_to_pg.py \
        --dsn postgresql://clubbot:pass@127.0.0.1:5432/clubdb

    # 実行（対象が空でない場合は --force が必要。事前に pg_dump でバックアップ）
    venv/bin/python scripts/migrate_sqlite_to_pg.py \
        --dsn postgresql://clubbot:pass@127.0.0.1:5432/clubdb --apply

オプション:
    --sqlite   SQLite ファイルのパス（既定: 環境変数 DB_PATH または ./data/club.db）
    --dsn      PostgreSQL 接続 URL（既定: 環境変数 DATABASE_URL）
    --apply    実際に移行する（既定は dry-run）
    --force    対象テーブルが空でなくても TRUNCATE して上書きする

処理内容（--apply 時）:
    1. PostgreSQL 側にスキーマを作成（冪等）＋バージョン付きマイグレーション
    2. 対象テーブルが空でなければ中止（--force で TRUNCATE ... CASCADE）
    3. 全テーブルを FK 安全な順序でコピー
    4. IDENTITY シーケンスを最大値に修復
    5. テーブルごとのコピー件数を表示（秘密情報は出力しない）

ロールバック:
    --apply 前に pg_dump でバックアップを取っておくこと:
      pg_dump "$DATABASE_URL" > backup_$(date +%Y%m%d).sql
    リストアは psql "$DATABASE_URL" < backup_YYYYMMDD.sql
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import Database  # noqa: E402

# コピー順（FK 参照が壊れない順序。guilds → schedules → options → votes）
COPY_ORDER = [
    "guilds", "settings", "teams", "skill_tags", "members",
    "schedules", "schedule_options", "schedule_votes",
    "tasks", "reminders_log", "todoist_sections", "todoist_configs",
    "layer_keta", "layer_sessions", "layer_records", "audit_log",
]


async def main(args: argparse.Namespace) -> None:
    sqlite_path = args.sqlite or (os.getenv("DB_PATH") or "./data/club.db").strip()
    dsn = args.dsn or (os.getenv("DATABASE_URL") or "").strip()
    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite が見つかりません: {sqlite_path}")
        sys.exit(1)
    if not dsn:
        print("ERROR: --dsn または環境変数 DATABASE_URL を指定してください。")
        sys.exit(1)

    src = Database(sqlite_path)
    await src.connect()
    dst = Database(sqlite_path, database_url=dsn)
    try:
        await dst.connect()  # PG 側スキーマ作成＋マイグレーション（冪等）

        # 件数確認
        counts: dict[str, tuple[int, int]] = {}
        for table in COPY_ORDER:
            s = await src.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            d = await dst.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            counts[table] = (int(s["c"]), int(d["c"]))

        print("===== 移行対象（SQLite → PostgreSQL） =====")
        for table in COPY_ORDER:
            s, d = counts[table]
            print(f"  {table}: {s} 行 → 現在 {d} 行")

        non_empty = [t for t in COPY_ORDER if counts[t][1] > 0]
        if non_empty and not args.force:
            print(f"\nERROR: 対象にデータがあります: {', '.join(non_empty)}")
            print("上書きする場合は --force を付けてください"
                  "（事前に pg_dump でバックアップを推奨）。")
            sys.exit(1)

        if not args.apply:
            print("\ndry-run のため変更していません。実行するには --apply を付けてください。")
            return

        if non_empty:
            await dst.execute(
                "TRUNCATE TABLE " + ", ".join(reversed(COPY_ORDER)) + " CASCADE")
            print("対象テーブルを TRUNCATE しました。")

        # コピー（FK 安全な順序で全行転送）
        for table in COPY_ORDER:
            cols_rows = await src.fetchall(f"PRAGMA table_info({table})")
            cols = [r["name"] for r in cols_rows]
            rows = await src.fetchall(f"SELECT * FROM {table}")
            col_list = ", ".join(cols)
            placeholders = ", ".join("?" for _ in cols)
            copied = 0
            for row in rows:
                await dst.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols))
                copied += 1
            print(f"  {table}: {copied} 行をコピーしました。")

        # 明示 ID 挿入後のシーケンス修復（PK 衝突防止）
        await dst._pg_fix_sequences()
        print("IDENTITY シーケンスを修復しました。")

        # 検証
        print("\n===== 検証 =====")
        ok = True
        for table in COPY_ORDER:
            s = await src.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            d = await dst.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
            match = "OK" if s["c"] == d["c"] else "不一致"
            if s["c"] != d["c"]:
                ok = False
            print(f"  {table}: {s['c']} 行 / {d['c']} 行 [{match}]")
        print("移行が完了しました。" if ok else "警告: 件数が一致しないテーブルがあります。")
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=None, help="SQLite ファイルのパス")
    parser.add_argument("--dsn", default=None, help="PostgreSQL 接続 URL")
    parser.add_argument("--apply", action="store_true",
                        help="実際に移行を実行する（既定は dry-run）")
    parser.add_argument("--force", action="store_true",
                        help="対象が空でなくても TRUNCATE して上書きする")
    asyncio.run(main(parser.parse_args()))
