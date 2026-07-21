"""NocoDB 表示用ビュー（v_attendance / v_team_summary / v_todoist_status）のテスト。

- 旧 Google Sheets の attendance / team_summary シート相当データが
  ビューから guild_id 単位で取得できること
- スキーマバージョンが 5 に更新されること

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.schedule_repository import ScheduleRepository  # noqa: E402
from utils.db import SCHEMA_VERSION, Database  # noqa: E402

G1 = 100000000000000001
G2 = 200000000000000002


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def test_schema_is_v5():
    async def _main():
        db = await _connected_db()
        try:
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION == 5
            for view in ("v_attendance", "v_team_summary", "v_todoist_status"):
                rows = await db.fetchall(
                    "SELECT name FROM sqlite_master WHERE type='view' AND name = ?",
                    (view,))
                assert rows, f"{view} がありません"
        finally:
            await db.close()
    run(_main())


def test_attendance_view_guild_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = ScheduleRepository(db)
            for gid, sid in ((G1, "sch1"), (G2, "sch2")):
                await repo.create_schedule(
                    gid, schedule_id=sid, title=f"title-{sid}", description=None,
                    place=None, target_role_id=None,
                    deadline_iso="2099-01-01T00:00:00",
                    created_by="u1", channel_id="ch")
                await repo.add_option(gid, f"opt-{sid}", sid, "候補A",
                                      "2099-01-01", None, "msg")
            await repo.set_vote(G1, "opt-sch1", "u1", "ok")
            await repo.set_vote(G1, "opt-sch1", "u2", "ng")
            await repo.set_vote(G2, "opt-sch2", "u1", "maybe")

            rows1 = await db.fetchall(
                "SELECT * FROM v_attendance WHERE guild_id = ?", (G1,))
            rows2 = await db.fetchall(
                "SELECT * FROM v_attendance WHERE guild_id = ?", (G2,))
            assert len(rows1) == 2
            assert len(rows2) == 1
            # attendance シート相当の列構成
            r = dict(rows1[0])
            assert {"guild_id", "schedule_id", "event_title", "option_label",
                    "user_id", "status", "updated_at", "deadline"} <= set(r)
            assert r["event_title"] == "title-sch1"
            assert r["option_label"] == "候補A"
            # 他ギルドの投票が混入しない
            assert all(dict(x)["schedule_id"] == "sch1" for x in rows1)
            assert dict(rows2[0])["status"] == "maybe"
        finally:
            await db.close()
    run(_main())


def test_team_summary_view_guild_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_team(G1, "design", "設計")
            await repo.upsert_team(G1, "wing", "翼")
            await repo.upsert_team(G2, "design", "設計")
            await repo.upsert_member(G1, "u1", "Taro", "design")
            await repo.upsert_member(G1, "u2", "Jiro", "design")
            await repo.set_leader(G1, "u1", True)
            await repo.upsert_member(G1, "u3", "Sabu", "wing")
            await repo.upsert_member(G2, "u1", "Taro", "design")

            rows = await db.fetchall(
                "SELECT * FROM v_team_summary WHERE guild_id = ?"
                " ORDER BY team_key", (G1,))
            assert len(rows) == 2
            design = dict(rows[0])
            assert design["team_key"] == "design"
            assert design["member_count"] == 2
            assert design["leader_count"] == 1
            wing = dict(rows[1])
            assert wing["member_count"] == 1

            rows2 = await db.fetchall(
                "SELECT * FROM v_team_summary WHERE guild_id = ?", (G2,))
            assert len(rows2) == 1
            assert dict(rows2[0])["member_count"] == 1
        finally:
            await db.close()
    run(_main())


if __name__ == "__main__":
    test_schema_is_v5()
    print("test_schema_is_v5: OK")
    test_attendance_view_guild_isolation()
    print("test_attendance_view_guild_isolation: OK")
    test_team_summary_view_guild_isolation()
    print("test_team_summary_view_guild_isolation: OK")
    print("全テスト成功")
