"""タスク・技能タグを「他人の分まで勝手に触れない」ことのテスト。

/task done と /task priority は L1 のまま（自分のタスクは自分で閉じられる）
だが、ID を打ち間違えたときに他班のタスクを完了扱いにしてしまうと、
Todoist 側からも消えるうえ audit_log にも残らない。

/member skill add|remove も L1 でありながら user 引数で他人を指定でき、
「◯◯君は溶接できる」を勝手に登録できてしまう。

いずれも「本人 or 作成者 or 班長以上」に絞る。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord

from cogs.tasks import Tasks
from config import GuildConfig
from repositories.task_repository import TaskRepository
from utils import permissions
from utils.db import Database

G1 = 111
OWNER = 501
OTHER = 502
LEADER_ROLE = 900


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _member(user_id: int, role_ids: tuple[int, ...] = ()):
    """isinstance(x, discord.Member) を満たすスタブ。"""
    stub = mock.MagicMock(spec=discord.Member)
    stub.id = user_id
    stub.display_name = f"user{user_id}"
    stub.roles = [SimpleNamespace(id=r) for r in role_ids]
    stub.guild = SimpleNamespace(id=G1, owner_id=999)
    stub.guild_permissions = SimpleNamespace(administrator=False, manage_guild=False)
    return stub


class _Interaction:
    def __init__(self, member):
        self.user = member
        self.guild = SimpleNamespace(id=G1)
        self.sent: list = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_description(self) -> str:
        return self.sent[-1]["embed"].description or ""


class _DisabledTodoist:
    async def for_guild(self, guild_id):
        return SimpleNamespace(enabled=False)


async def _make_cog():
    db = Database(_tmp_db_path())
    await db.connect()
    bot = SimpleNamespace(db=db, guilds=[], todoist_manager=_DisabledTodoist())
    return Tasks(bot), db, TaskRepository(db)


async def _seed_task(repo: TaskRepository, *, assignee: int | None, creator: int) -> int:
    return await repo.create_task(
        G1,
        title="主桁の積層",
        created_by=str(creator),
        assignee_id=str(assignee) if assignee else None,
        priority=3,
    )


def _with_gconf(gconf: GuildConfig):
    """permissions.config.for_guild を差し替えるコンテキストマネージャ。"""

    class _Ctx:
        def __enter__(self):
            self._original = permissions.config.for_guild

            async def _fake(guild_id):
                return gconf

            permissions.config.for_guild = _fake
            return self

        def __exit__(self, *exc):
            permissions.config.for_guild = self._original
            return False

    return _Ctx()


# ---------------------------------------------------------------------
# /task done
# ---------------------------------------------------------------------
def test_stranger_cannot_complete_someone_elses_task():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            task_id = await _seed_task(repo, assignee=OWNER, creator=OWNER)
            interaction = _Interaction(_member(OTHER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Tasks.done.callback(cog, interaction, task_id=task_id)

            assert "担当" in interaction.last_description
            assert (await repo.get_task(G1, task_id))["status"] == "open"
        finally:
            await db.close()

    run(_main())


def test_assignee_can_complete_own_task():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            task_id = await _seed_task(repo, assignee=OWNER, creator=OTHER)
            interaction = _Interaction(_member(OWNER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Tasks.done.callback(cog, interaction, task_id=task_id)

            assert (await repo.get_task(G1, task_id))["status"] != "open"
        finally:
            await db.close()

    run(_main())


def test_creator_can_complete_task_assigned_to_someone_else():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            task_id = await _seed_task(repo, assignee=OWNER, creator=OTHER)
            interaction = _Interaction(_member(OTHER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Tasks.done.callback(cog, interaction, task_id=task_id)

            assert (await repo.get_task(G1, task_id))["status"] != "open"
        finally:
            await db.close()

    run(_main())


def test_leader_can_complete_any_task():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            task_id = await _seed_task(repo, assignee=OWNER, creator=OWNER)
            interaction = _Interaction(_member(OTHER, role_ids=(LEADER_ROLE,)))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Tasks.done.callback(cog, interaction, task_id=task_id)

            assert (await repo.get_task(G1, task_id))["status"] != "open"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /task priority
# ---------------------------------------------------------------------
def test_stranger_cannot_change_priority():
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            task_id = await _seed_task(repo, assignee=OWNER, creator=OWNER)
            interaction = _Interaction(_member(OTHER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Tasks.priority.callback(cog, interaction, task_id=task_id, priority=1)

            assert "担当" in interaction.last_description
            assert (await repo.get_task(G1, task_id))["priority"] == 3
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /member skill add|remove
# ---------------------------------------------------------------------
def test_plain_member_cannot_tag_someone_else():
    from cogs.members import Members
    from repositories.member_repository import MemberRepository
    from repositories.skill_tag_repository import SkillTagRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SkillTagRepository(db).add(G1, "溶接", "tester")
            cog = Members(SimpleNamespace(db=db, guilds=[]))
            victim = _member(OWNER)
            interaction = _Interaction(_member(OTHER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Members.skill_add.callback(cog, interaction, skill="溶接", user=victim)

            assert "班長以上" in interaction.last_description
            row = await MemberRepository(db).get_member(G1, str(OWNER))
            assert row is None or "溶接" not in (row["skills"] or "")
        finally:
            await db.close()

    run(_main())


def test_member_can_tag_self():
    from cogs.members import Members
    from repositories.member_repository import MemberRepository
    from repositories.skill_tag_repository import SkillTagRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SkillTagRepository(db).add(G1, "溶接", "tester")
            cog = Members(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(_member(OWNER))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Members.skill_add.callback(cog, interaction, skill="溶接", user=None)

            row = await MemberRepository(db).get_member(G1, str(OWNER))
            assert "溶接" in (row["skills"] or "")
        finally:
            await db.close()

    run(_main())


def test_leader_can_tag_someone_else():
    from cogs.members import Members
    from repositories.member_repository import MemberRepository
    from repositories.skill_tag_repository import SkillTagRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await SkillTagRepository(db).add(G1, "溶接", "tester")
            cog = Members(SimpleNamespace(db=db, guilds=[]))
            victim = _member(OWNER)
            interaction = _Interaction(_member(OTHER, role_ids=(LEADER_ROLE,)))
            with _with_gconf(GuildConfig(guild_id=G1, leader_role_ids=[LEADER_ROLE])):
                await Members.skill_add.callback(cog, interaction, skill="溶接", user=victim)

            row = await MemberRepository(db).get_member(G1, str(OWNER))
            assert "溶接" in (row["skills"] or "")
        finally:
            await db.close()

    run(_main())
