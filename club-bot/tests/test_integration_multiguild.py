"""複数ギルド統合テスト。

2つのギルドに全エンティティ（teams / skill_tags / members / tasks /
schedules+attendance / settings / todoist_configs / audit_log /
reminders_log / layer 系）を混在させ、Repository・ビュー経由で
相互に取得・更新・削除できないことを包括的に検証する。

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet  # noqa: E402

from repositories.audit_log_repository import AuditLogRepository  # noqa: E402
from repositories.guild_repository import GuildRepository  # noqa: E402
from repositories.layer_keta_repository import LayerKetaRepository  # noqa: E402
from repositories.layer_session_repository import LayerSessionRepository  # noqa: E402
from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.reminders_log_repository import RemindersLogRepository  # noqa: E402
from repositories.schedule_repository import ScheduleRepository  # noqa: E402
from repositories.settings_repository import SettingsRepository  # noqa: E402
from repositories.skill_tag_repository import SkillTagRepository  # noqa: E402
from repositories.task_repository import TaskRepository  # noqa: E402
from repositories.todoist_config_repository import TodoistConfigRepository  # noqa: E402
from utils import crypto  # noqa: E402
from utils.db import Database  # noqa: E402

G1 = 100000000000000001
G2 = 200000000000000002


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


def test_full_multiguild_isolation():
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    crypto.reset_cache()

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            guilds = GuildRepository(db)
            settings = SettingsRepository(db)
            members = MemberRepository(db)
            skills = SkillTagRepository(db)
            tasks = TaskRepository(db)
            schedules = ScheduleRepository(db)
            todoist = TodoistConfigRepository(db)
            audit = AuditLogRepository(db)
            rlog = RemindersLogRepository(db)
            keta = LayerKetaRepository(db)
            sessions = LayerSessionRepository(db)
            task_ids: dict[int, int] = {}

            # ---- 両ギルドにデータを投入 ----
            for gid, suffix in ((G1, "A"), (G2, "B")):
                await guilds.ensure(gid, f"ギルド{suffix}")
                await settings.set(gid, "DEFAULT_TASK_CHANNEL_ID", f"ch-{suffix}")
                await members.upsert_team(gid, "wing", f"翼{suffix}")
                await members.set_team_roles(gid, "wing", member_role_id=f"role-{suffix}")
                await skills.add(gid, f"技能{suffix}", "admin")
                await members.upsert_member(gid, "u1", f"太郎{suffix}", "wing")
                await members.add_skill(gid, "u1", f"技能{suffix}")
                task_ids[gid] = await tasks.create_task(gid, f"タスク{suffix}", created_by="u1")
                await schedules.create_schedule(
                    gid, schedule_id=f"sch-{suffix}", title=f"部会{suffix}",
                    description=None, place=None, target_role_id=None,
                    deadline_iso="2099-01-01T00:00:00",
                    created_by="u1", channel_id="ch")
                await schedules.add_option(gid, f"opt-{suffix}", f"sch-{suffix}",
                                           "候補", "2099-01-01", None, "msg")
                await schedules.set_vote(gid, f"opt-{suffix}", "u1", "ok")
                await todoist.upsert(gid, crypto.encrypt_token(f"token-{suffix}"),
                                     f"proj-{suffix}", "今日やること", "admin")
                await audit.record(gid, "admin", "team.add", target="wing")
                await rlog.add(gid, "task_overdue", "t1", None, "ch", "success")
                await keta.add(gid, f"桁{suffix}", "u1", "2026-01-01")
                await sessions.start(gid, "u1", f"桁{suffix}", "1", "2026-01-01T00:00:00")

            # ---- 相互に見えないこと（全エンティティ横断） ----
            assert (await guilds.get(G1))["guild_name"] == "ギルドA"
            assert (await guilds.get(G2))["guild_name"] == "ギルドB"

            assert await settings.get(G1, "DEFAULT_TASK_CHANNEL_ID") == "ch-A"
            assert await settings.get(G2, "DEFAULT_TASK_CHANNEL_ID") == "ch-B"

            t1 = await members.get_team(G1, "wing")
            t2 = await members.get_team(G2, "wing")
            assert t1["team_name"] == "翼A" and t2["team_name"] == "翼B"
            assert t1["member_role_id"] == "role-A" and t2["member_role_id"] == "role-B"

            assert await skills.exists_active(G1, "技能A") is True
            assert await skills.exists_active(G1, "技能B") is False
            assert await skills.exists_active(G2, "技能B") is True

            m1 = await members.get_member(G1, "u1")
            m2 = await members.get_member(G2, "u1")
            assert m1["display_name"] == "太郎A" and m2["display_name"] == "太郎B"
            assert m1["skills"] == ["技能A"] and m2["skills"] == ["技能B"]

            assert [t["title"] for t in await tasks.list_tasks(G1)] == ["タスクA"]
            assert [t["title"] for t in await tasks.list_tasks(G2)] == ["タスクB"]

            assert await schedules.get_schedule(G1, "sch-B") is None
            assert await schedules.get_schedule(G2, "sch-A") is None
            att1 = await db.fetchall("SELECT * FROM v_attendance WHERE guild_id = ?", (G1,))
            att2 = await db.fetchall("SELECT * FROM v_attendance WHERE guild_id = ?", (G2,))
            assert len(att1) == 1 and len(att2) == 1
            assert dict(att1[0])["event_title"] == "部会A"
            assert dict(att2[0])["event_title"] == "部会B"

            cfg1 = await todoist.get(G1)
            cfg2 = await todoist.get(G2)
            assert crypto.decrypt_token(cfg1["api_token_encrypted"]) == "token-A"
            assert crypto.decrypt_token(cfg2["api_token_encrypted"]) == "token-B"
            assert cfg1["api_token_encrypted"] != cfg2["api_token_encrypted"]

            assert len(await audit.list_recent(G1)) == 1
            assert (await audit.list_recent(G1))[0]["target"] == "wing"
            assert len(await rlog.list_recent(G1)) == 1
            assert len(await rlog.list_recent(G2)) == 1

            assert await keta.exists_active(G1, "桁A") is True
            assert await keta.exists_active(G1, "桁B") is False
            assert (await sessions.get_by_user(G1, "u1"))["keta"] == "桁A"
            assert (await sessions.get_by_user(G2, "u1"))["keta"] == "桁B"

            sum1 = await db.fetchall("SELECT * FROM v_team_summary WHERE guild_id = ?", (G1,))
            assert dict(sum1[0])["team_name"] == "翼A"
            assert dict(sum1[0])["member_count"] == 1

            # ---- 片方の変更・削除が他方に波及しないこと ----
            await tasks.delete_task(G1, task_ids[G1])
            assert (await tasks.get_task(G1, task_ids[G1]))["status"] == "archived"
            assert (await tasks.get_task(G2, task_ids[G2]))["status"] == "open"

            await settings.delete(G1, "DEFAULT_TASK_CHANNEL_ID")
            assert await settings.get(G1, "DEFAULT_TASK_CHANNEL_ID") is None
            assert await settings.get(G2, "DEFAULT_TASK_CHANNEL_ID") == "ch-B"

            await todoist.delete(G1)
            assert await todoist.get(G1) is None
            assert await todoist.get(G2) is not None

            await schedules.delete_schedule(G1, "sch-A")
            assert await schedules.get_schedule(G1, "sch-A") is None
            assert await schedules.get_schedule(G2, "sch-B") is not None
        finally:
            await db.close()
    run(_main())
    crypto.reset_cache()
    os.environ.pop("ENCRYPTION_KEY", None)


if __name__ == "__main__":
    test_full_multiguild_isolation()
    print("test_full_multiguild_isolation: OK")
