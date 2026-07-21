"""Sheets -> DB 移行スクリプト（scripts/migrate_sheets_to_db.py）のテスト。

gspread を使わず、インポータ関数にシート行（list[list]）を直接渡して検証する。
- 各行種別の移行・スキップ・エラー集計
- dry-run（apply=False）で DB が変わらないこと
- 再実行で重複が増えないこと（冪等性）

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import migrate_sheets_to_db as mig  # noqa: E402
from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.schedule_repository import ScheduleRepository  # noqa: E402
from utils.db import Database  # noqa: E402

G1 = 100000000000000001


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


async def _seed_base(db: Database) -> None:
    """解決に必要な基礎データ（班・メンバー・スケジュール）を投入。"""
    repo = MemberRepository(db)
    await repo.upsert_team(G1, "wing", "翼")
    await repo.upsert_member(G1, "111", "Taro", "wing")
    srepo = ScheduleRepository(db)
    await srepo.create_schedule(G1, schedule_id="sch1", title="部会", description=None,
                                place=None, target_role_id=None,
                                deadline_iso="2099-01-01T00:00:00",
                                created_by="111", channel_id="ch")
    await srepo.add_option(G1, "opt1", "sch1", "7/3(木)", "2099-01-01", None, "msg")


# ---------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------
def test_import_tasks_dedup_and_dry_run():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_base(db)  # 班「翼」の解決に必要
            rows = [
                ["1", "td1", "既存タスク", "Taro", "翼", "", "3", "open", "111", "2026-01-01", ""],
                ["2", "", "新規タスク", "Taro", "翼", "2026-07-05", "2", "open", "111", "2026-01-02", ""],
                ["", "", "IDなしはスキップ", "", "", "", "", "open", "", "", ""],
            ]
            # dry-run: DB は変わらないが集計は出る
            stats = await mig.import_tasks(db, G1, rows, apply=False)
            assert stats.input_rows == 3
            assert stats.migrated == 2  # id=1,2 が移行対象
            assert stats.skipped == 1
            row = await db.fetchone("SELECT COUNT(*) AS c FROM tasks WHERE guild_id = ?", (G1,))
            assert row["c"] == 0  # dry-run では書き込まれない

            # apply → 2件移行
            stats = await mig.import_tasks(db, G1, rows, apply=True)
            assert stats.migrated == 2
            row = await db.fetchone(
                "SELECT title, team_key FROM tasks WHERE guild_id = ? AND local_task_id = 2",
                (G1,))
            assert row["title"] == "新規タスク"
            assert row["team_key"] == "wing"  # 班名→キー解決

            # 再実行（冪等）: 既存 ID はスキップされ増えない
            stats = await mig.import_tasks(db, G1, rows, apply=True)
            assert stats.migrated == 0
            assert stats.skipped == 3
            row = await db.fetchone("SELECT COUNT(*) AS c FROM tasks WHERE guild_id = ?", (G1,))
            assert row["c"] == 2
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# members
# ---------------------------------------------------------------------
def test_import_members_and_skill_registration():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_base(db)  # 111=Taro 既存
            rows = [
                ["111", "Taro", "翼", "", "○", "CAD", "2026-01-01", "在籍"],      # 既存 → skip
                ["222", "Jiro", "翼", "", "", "はんだ、CAD", "2026-01-02", "在籍"],  # 新規
                ["abc", "Bad", "", "", "", "", "", ""],                              # ID 非数値 → skip
            ]
            stats = await mig.import_members(db, G1, rows, apply=True)
            assert stats.input_rows == 3
            assert stats.migrated == 1
            assert stats.skipped == 2

            repo = MemberRepository(db)
            m = await repo.get_member(G1, "222")
            assert m["display_name"] == "Jiro"
            assert m["primary_team"] == "wing"
            assert sorted(m["skills"]) == ["CAD", "はんだ"]
            # 技能タグがギルドのマスタに自動登録されている
            rows = await db.fetchall(
                "SELECT skill_name FROM skill_tags WHERE guild_id = ? ORDER BY skill_name",
                (G1,))
            assert [r["skill_name"] for r in rows] == ["CAD", "はんだ"]

            # 再実行（冪等）
            stats = await mig.import_members(db, G1, rows, apply=True)
            assert stats.migrated == 0
            row = await db.fetchone(
                "SELECT COUNT(*) AS c FROM members WHERE guild_id = ?", (G1,))
            assert row["c"] == 2
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# attendance
# ---------------------------------------------------------------------
def test_import_attendance_resolution_and_dedup():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_base(db)
            rows = [
                ["sch1", "部会", "7/3(木)", "Taro", "ok", "2026-07-03", "2026-07-03 12:00"],
                ["sch1", "部会", "7/3(木)", "Taro", "yes", "", ""],       # yes→ok だが重複
                ["sch1", "部会", "7/3(木)", "不明な人", "ok", "", ""],     # ユーザー解決不可
                ["schX", "?", "7/3(木)", "Taro", "ok", "", ""],            # schedule 無し
            ]
            stats = await mig.import_attendance(db, G1, rows, apply=True)
            assert stats.input_rows == 4
            assert stats.migrated == 1
            assert stats.skipped == 3
            row = await db.fetchone(
                "SELECT status FROM schedule_votes WHERE guild_id = ? AND option_id = 'opt1'",
                (G1,))
            assert row["status"] == "ok"

            # 再実行（冪等）
            stats = await mig.import_attendance(db, G1, rows, apply=True)
            assert stats.migrated == 0
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# layer
# ---------------------------------------------------------------------
def test_import_layer_rows_dedup():
    async def _main():
        db = await _connected_db()
        try:
            await _seed_base(db)
            rows = [
                ["3", "Taro", "2026-07-01 10:00", "2026-07-01 12:00", "120"],
                ["4", "Taro", "2026-07-02 10:00", "2026-07-02 11:00", "60"],
                ["", "Taro", "", "", ""],  # 不正行
            ]
            stats = await mig.import_layer_rows(db, G1, "主翼前桁", rows, apply=True)
            assert stats.input_rows == 3
            assert stats.migrated == 2
            assert stats.skipped == 1
            # 桁名マスタに登録される
            row = await db.fetchone(
                "SELECT 1 FROM layer_keta WHERE guild_id = ? AND keta_name = '主翼前桁'",
                (G1,))
            assert row is not None
            # synced_flag=1 で保存される（Sheets 側に既に存在した記録のため）
            row = await db.fetchone(
                "SELECT synced_flag FROM layer_records WHERE guild_id = ?", (G1,))
            assert row["synced_flag"] == 1

            # 再実行（冪等）
            stats = await mig.import_layer_rows(db, G1, "主翼前桁", rows, apply=True)
            assert stats.migrated == 0
            row = await db.fetchone(
                "SELECT COUNT(*) AS c FROM layer_records WHERE guild_id = ?", (G1,))
            assert row["c"] == 2
        finally:
            await db.close()
    run(_main())


if __name__ == "__main__":
    test_import_tasks_dedup_and_dry_run()
    print("test_import_tasks_dedup_and_dry_run: OK")
    test_import_members_and_skill_registration()
    print("test_import_members_and_skill_registration: OK")
    test_import_attendance_resolution_and_dedup()
    print("test_import_attendance_resolution_and_dedup: OK")
    test_import_layer_rows_dedup()
    print("test_import_layer_rows_dedup: OK")
    print("全テスト成功")
