"""/data delete（F2-3）のテスト。

**この段階では実削除を行わない。** 削除予定（purge_after）を立てるだけで、
実際に消すのは F2-4 の日次ジョブ。ここで検証するのは:

- 確認入力がサーバー名と一致しなければ中止すること
- 予約すると purge_after が入り、取り消すと NULL に戻ること
- 予約した時点ではデータが1行も減っていないこと
- 監査ログに data.delete.requested / data.delete.cancelled が残ること
- 権限（L4 または Manage Server）が要ること
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord.ext import commands

from cogs.data import Data, confirmation_matches
from config import GuildConfig
from repositories.audit_log_repository import AuditLogRepository
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from utils.db import Database
from utils.permissions import (
    Level,
    command_required_level,
    has_manage_guild_or_level,
)

GA = 100000000000000001
GB = 200000000000000002


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ---------------------------------------------------------------------
# 確認文字列
# ---------------------------------------------------------------------
def test_confirmation_requires_exact_server_name():
    assert confirmation_matches("A大学 鳥人間サークル", "A大学 鳥人間サークル")
    # 前後の空白だけは許す（コピペ時の事故を防ぐ）
    assert confirmation_matches("A大学", "  A大学  ")


def test_confirmation_rejects_mismatch():
    assert not confirmation_matches("A大学 鳥人間サークル", "A大学")
    assert not confirmation_matches("A大学", "a大学")
    assert not confirmation_matches("A大学", "")
    assert not confirmation_matches("A大学", "はい")
    assert not confirmation_matches("A大学", None)


# ---------------------------------------------------------------------
# 予約と取り消し
# ---------------------------------------------------------------------
def test_request_purge_sets_purge_after_without_left_at():
    """自己申告の削除では left_at を立てない（まだ参加中のため）。"""
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")

            purge_at = await repo.request_purge(GA)

            row = await repo.get(GA)
            assert row["purge_after"] == purge_at
            assert row["left_at"] is None
        finally:
            await db.close()

    run(_main())


def test_request_purge_does_not_delete_anything():
    """予約した時点ではデータが1行も減らない（実削除は F2-4）。"""
    async def _main():
        db = await _connected_db()
        try:
            await GuildRepository(db).ensure(GA, "A大学")
            await MemberRepository(db).upsert_team(GA, "struct", "構造")

            await GuildRepository(db).request_purge(GA)

            teams = await MemberRepository(db).list_teams(GA)
            assert len(teams) == 1, "予約だけで消えてはいけない"
        finally:
            await db.close()

    run(_main())


def test_cancel_purge_restores_null():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.request_purge(GA)

            assert await repo.cancel_purge(GA) is True

            assert (await repo.get(GA))["purge_after"] is None
        finally:
            await db.close()

    run(_main())


def test_cancel_purge_without_request_returns_false():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            assert await repo.cancel_purge(GA) is False
        finally:
            await db.close()

    run(_main())


def test_purge_request_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.ensure(GB, "B大学")

            await repo.request_purge(GA)

            assert (await repo.get(GA))["purge_after"] is not None
            assert (await repo.get(GB))["purge_after"] is None, \
                "他サーバーを巻き込んで削除予約しない"
        finally:
            await db.close()

    run(_main())


def test_cancel_purge_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.ensure(GB, "B大学")
            await repo.request_purge(GA)
            await repo.request_purge(GB)

            await repo.cancel_purge(GA)

            assert (await repo.get(GA))["purge_after"] is None
            assert (await repo.get(GB))["purge_after"] is not None
        finally:
            await db.close()

    run(_main())


def test_cancel_does_not_revive_a_left_guild_schedule():
    """退出により立った削除予定は、取り消しても left_at は残る。

    「参加中に自分で予約した削除」と「退出による削除予定」は別物なので、
    取り消しで退出の事実まで消さない。
    """
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(GA, "A大学")
            await repo.mark_left(GA, 30)

            await repo.cancel_purge(GA)

            row = await repo.get(GA)
            assert row["purge_after"] is None
            assert row["left_at"] is not None
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------
def test_audit_actions_are_recorded():
    async def _main():
        db = await _connected_db()
        try:
            repo = AuditLogRepository(db)
            await repo.record(GA, actor_id="1", action="data.delete.requested",
                              target="all", detail="purge_after=...")
            await repo.record(GA, actor_id="1", action="data.delete.cancelled",
                              target="all", detail="取り消した")

            actions = [r["action"] for r in await repo.list_recent(GA)]
            assert "data.delete.requested" in actions
            assert "data.delete.cancelled" in actions
            # 他サーバーのログには出ない
            assert await repo.list_recent(GB) == []
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 権限
# ---------------------------------------------------------------------
def _member(*, role_ids=(), manage_guild: bool = False):
    return SimpleNamespace(
        id=1,
        guild=SimpleNamespace(id=GA, owner_id=42),
        roles=[SimpleNamespace(id=r) for r in role_ids],
        guild_permissions=SimpleNamespace(administrator=False,
                                          manage_guild=manage_guild),
    )


def _command(name: str):
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    for cmd in Data(bot).walk_app_commands():
        if cmd.name == name:
            return cmd
    raise AssertionError(f"/data {name} が見つからない")


def test_delete_commands_require_admin_level():
    assert command_required_level(_command("delete")) == Level.L4
    assert command_required_level(_command("delete-cancel")) == Level.L4


def test_leaders_cannot_delete():
    gconf = GuildConfig(guild_id=GA, leader_role_ids=[500], exec_role_id=600)
    assert not has_manage_guild_or_level(
        _member(role_ids=(500,)), gconf, Level.L4)
    assert not has_manage_guild_or_level(
        _member(role_ids=(600,)), gconf, Level.L4)


def test_manage_guild_can_delete():
    assert has_manage_guild_or_level(
        _member(manage_guild=True), GuildConfig(guild_id=GA), Level.L4)
