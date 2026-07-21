"""DB 基盤強化（スキーマ v2: guilds 台帳 / audit_log / user_version）の単体テスト。

- 新規 DB に guilds / audit_log が作成され user_version=2 になること
- v1 相当 DB からの自動マイグレーション（台帳バックフィル）が冪等であること
- 2ギルドの監査ログ・通知ログがリポジトリ経由で混ざらないこと

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402

from repositories.audit_log_repository import AuditLogRepository  # noqa: E402
from repositories.guild_repository import GuildRepository  # noqa: E402
from repositories.reminders_log_repository import RemindersLogRepository  # noqa: E402
from repositories.schedule_repository import ScheduleRepository  # noqa: E402
from utils.db import SCHEMA_VERSION, TABLE_DDL, Database  # noqa: E402

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2

# v1 相当（guilds / audit_log / skill_tags / todoist_configs 導入前）のテーブル群
V1_TABLES = [t for t in TABLE_DDL
             if t not in ("guilds", "audit_log", "skill_tags", "todoist_configs")]


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # connect() に作成させる
    return path


def run(coro):
    return asyncio.run(coro)


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_fresh_schema_is_v2():
    async def _main():
        db = await _connected_db()
        try:
            # guilds / audit_log が存在し、期待するカラムを持つ
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(guilds)")}
            assert {"guild_id", "guild_name", "joined_at", "setup_version"} <= cols
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(audit_log)")}
            assert {"audit_id", "guild_id", "actor_id", "action",
                    "target", "detail", "created_at"} <= cols
            # スキーマバージョンが最新
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            # busy_timeout が設定されている（外部ツール同時アクセス対策）
            row = await db.fetchone("PRAGMA busy_timeout")
            assert row[0] == 5000
        finally:
            await db.close()
    run(_main())


def test_guild_registry_ensure():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "ギルド1")
            g = await repo.get(G1)
            assert g["guild_name"] == "ギルド1"
            assert g["setup_version"] == 2
            # 再登録は冪等で名称のみ更新される
            await repo.ensure(G1, "ギルド1改")
            assert (await repo.get(G1))["guild_name"] == "ギルド1改"
            assert len(await repo.list_all()) == 1
            # guild_id=0 は CHECK 制約で拒否される（レガシー sentinel を台帳に登録しない）
            raised = False
            try:
                await repo.ensure(0, "legacy")
            except Exception:
                raised = True
            assert raised, "guild_id=0 の登録が拒否されませんでした"
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# v1 -> v2 マイグレーション
# ---------------------------------------------------------------------
def test_v1_to_v2_migration_backfills_guilds():
    async def _main():
        path = _tmp_db_path()
        # v1 相当の DB を準備（guilds / audit_log 無し、user_version=0）
        conn = await aiosqlite.connect(path)
        for table in V1_TABLES:
            await conn.executescript(TABLE_DDL[table])
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'GUILD_NAME', '移行ギルド')", (G1,))
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'TZ', 'Asia/Tokyo')", (G2,))
        # guild_id=0（レガシー sentinel）は台帳に登録されないことを確認するための行
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (0, 'TZ', 'Asia/Tokyo')")
        await conn.commit()
        await conn.close()

        db = Database(path)
        await db.connect()
        try:
            # guilds / audit_log が作成された
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(audit_log)")}
            assert "guild_id" in cols
            # 台帳バックフィル: G1 は GUILD_NAME から、G2 は '(unknown)'
            repo = GuildRepository(db)
            assert (await repo.get(G1))["guild_name"] == "移行ギルド"
            assert (await repo.get(G2))["guild_name"] == "(unknown)"
            assert await repo.get(0) is None
            # バージョン更新
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()

        # 再接続しても冪等（バージョン据え置き・データ不変）
        db2 = Database(path)
        await db2.connect()
        try:
            row = await db2.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            assert len(await GuildRepository(db2).list_all()) == 2
        finally:
            await db2.close()
    run(_main())


# ---------------------------------------------------------------------
# ギルド分離
# ---------------------------------------------------------------------
def test_audit_log_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = AuditLogRepository(db)
            id1 = await repo.record(G1, "u1", "team.add", target="design",
                                    detail="班を追加")
            await repo.record(G2, "u2", "todoist.setup")
            assert id1 >= 1
            logs1 = await repo.list_recent(G1)
            logs2 = await repo.list_recent(G2)
            assert len(logs1) == 1 and len(logs2) == 1
            assert logs1[0]["action"] == "team.add"
            assert logs2[0]["action"] == "todoist.setup"
            # 他ギルドのログが混入しない
            assert all(l["guild_id"] == G1 for l in logs1)
            assert all(l["guild_id"] == G2 for l in logs2)
        finally:
            await db.close()
    run(_main())


def test_reminders_log_repository_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = RemindersLogRepository(db)
            for i in range(3):
                await repo.add(G1, "task_overdue", f"t{i}", None, "ch1", "success")
            await repo.add(G2, "schedule_unanswered", "s1", None, "ch2", "failed", "err")
            logs1 = await repo.list_recent(G1, limit=10)
            logs2 = await repo.list_recent(G2, limit=10)
            assert len(logs1) == 3
            assert len(logs2) == 1
            # 新しい順
            assert logs1[0]["target_id"] == "t2"
            # limit が効く
            assert len(await repo.list_recent(G1, limit=2)) == 2
            # 失敗理由も保存される
            assert logs2[0]["error_message"] == "err"
        finally:
            await db.close()
    run(_main())


def test_schedule_list_all_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = ScheduleRepository(db)
            for gid, sid in ((G1, "sch1"), (G2, "sch2")):
                await repo.create_schedule(
                    gid, schedule_id=sid, title=f"title-{sid}", description=None,
                    place=None, target_role_id=None, deadline_iso="2099-01-01T00:00:00",
                    created_by="u1", channel_id="ch")
            await repo.close_schedule(G1, "sch1")
            # list_all はクローズ済みも含む
            assert [s["schedule_id"] for s in await repo.list_all(G1)] == ["sch1"]
            assert [s["schedule_id"] for s in await repo.list_all(G2)] == ["sch2"]
            # 他ギルドのスケジュールは見えない
            assert all(s["guild_id"] == G1 for s in await repo.list_all(G1))
        finally:
            await db.close()
    run(_main())


if __name__ == "__main__":
    test_fresh_schema_is_v2()
    print("test_fresh_schema_is_v2: OK")
    test_guild_registry_ensure()
    print("test_guild_registry_ensure: OK")
    test_v1_to_v2_migration_backfills_guilds()
    print("test_v1_to_v2_migration_backfills_guilds: OK")
    test_audit_log_isolation()
    print("test_audit_log_isolation: OK")
    test_reminders_log_repository_isolation()
    print("test_reminders_log_repository_isolation: OK")
    test_schedule_list_all_isolation()
    print("test_schedule_list_all_isolation: OK")
    print("全テスト成功")
