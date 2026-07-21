"""既存の平文 Todoist トークンを暗号化して todoist_configs へ移行する
一回限りの移行スクリプト。

使い方:
    venv/bin/python scripts/migrate_todoist_token.py            # dry-run（件数のみ表示）
    venv/bin/python scripts/migrate_todoist_token.py --apply    # 実際に移行

移行対象（見つかったものだけ処理）:
    1. settings テーブルの TODOIST_API_TOKEN / TODOIST_PROJECT_ID（ギルド別）
    2. 環境変数 TODOIST_API_TOKEN / TODOIST_PROJECT_ID
       （GUILD_ID 環境変数で指定されたレガシーギルドに紐付ける）

処理内容（--apply 時）:
    - トークンを Fernet（ENCRYPTION_KEY）で暗号化して todoist_configs へ upsert
    - settings の TODOIST_API_TOKEN / TODOIST_PROJECT_ID を削除（平文を残さない）

注意:
    - トークンは一切表示しない（件数とギルド ID のみ出力）
    - 事前に data/club.db のバックアップを推奨
    - 冪等: 2回実行しても同じ状態になる（settings キーが無ければ何もしない）
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.todoist_config_repository import TodoistConfigRepository  # noqa: E402
from utils import crypto  # noqa: E402
from utils.db import Database, legacy_guild_id  # noqa: E402

LEGACY_KEYS = ("TODOIST_API_TOKEN", "TODOIST_PROJECT_ID")
DEFAULT_LABEL = "今日やること"


async def main(apply: bool) -> None:
    if not crypto.is_encryption_ready():
        print("ERROR: ENCRYPTION_KEY が未設定または不正です。.env を確認してください。")
        sys.exit(1)

    db_path = (os.getenv("DB_PATH") or "./data/club.db").strip()
    db = Database(db_path)
    await db.connect()
    repo = TodoistConfigRepository(db)

    try:
        # (1) settings テーブルのギルド別トークンを収集
        rows = await db.fetchall(
            "SELECT guild_id, setting_key, setting_value FROM settings"
            " WHERE setting_key IN ('TODOIST_API_TOKEN', 'TODOIST_PROJECT_ID')"
            " AND guild_id > 0")
        by_guild: dict[int, dict[str, str]] = {}
        for r in rows:
            by_guild.setdefault(int(r["guild_id"]), {})[r["setting_key"]] = r["setting_value"]

        # (2) 環境変数のトークン（レガシーギルドに紐付け）
        env_token = (os.getenv("TODOIST_API_TOKEN") or "").strip()
        env_project = (os.getenv("TODOIST_PROJECT_ID") or "").strip()
        legacy_gid = legacy_guild_id()
        if env_token and legacy_gid:
            by_guild.setdefault(legacy_gid, {})
            by_guild[legacy_gid].setdefault("TODOIST_API_TOKEN", env_token)
            if env_project:
                by_guild[legacy_gid].setdefault("TODOIST_PROJECT_ID", env_project)
        elif env_token:
            print("警告: TODOIST_API_TOKEN が環境変数にありますが GUILD_ID 未設定の"
                  " ため紐付け先を決定できません。GUILD_ID を設定して再実行してください。")

        if not by_guild:
            print("移行対象のトークンは見つかりませんでした（settings ・環境変数ともに無し）。")
            return

        print(f"移行対象: {len(by_guild)} ギルド")
        for gid, values in sorted(by_guild.items()):
            has_token = bool(values.get("TODOIST_API_TOKEN"))
            existing = await repo.get(gid)
            print(f"  guild_id={gid}: トークン={'あり' if has_token else 'なし'}"
                  f" / 既存の暗号化設定={'あり' if existing else 'なし'}")
            if not has_token:
                continue
            if apply:
                encrypted = crypto.encrypt_token(values["TODOIST_API_TOKEN"])
                await repo.upsert(
                    gid, encrypted,
                    values.get("TODOIST_PROJECT_ID") or None,
                    DEFAULT_LABEL, actor_id="migrate_todoist_token.py")
                # 平文の settings キーを削除
                for key in LEGACY_KEYS:
                    await db.execute(
                        "DELETE FROM settings WHERE guild_id = ? AND setting_key = ?",
                        (gid, key))

        if apply:
            # 平文を物理的にも除去する（SQLite は DELETE 後も free page に
            # データを残すため、WAL 切り詰めと VACUUM で上書きする）
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.execute("VACUUM")
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            print("移行が完了しました。settings の平文トークンは削除し、"
                  "VACUUM で物理除去しました。")
            print("環境変数 TODOIST_API_TOKEN / TODOIST_PROJECT_ID は .env から"
                  " 手動で削除してください。")
            print("移行前の DB バックアップ（.db.bak 等）には平文が残るため、"
                  "不要になったバックアップは削除してください。")
        else:
            print("dry-run のため変更していません。実行するには --apply を付けてください。")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="実際に移行を実行する（既定は dry-run）")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
