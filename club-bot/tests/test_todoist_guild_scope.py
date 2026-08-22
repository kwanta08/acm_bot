"""Todoist 連携のギルド別運用に関するガードテスト。

既存実装（/todoist-setup によるギルド別暗号化登録）が
将来の変更で後退しないことを確認する。

- 環境変数の Todoist トークンなしで起動できる（validate は DISCORD_TOKEN のみ必須）
- 環境変数フォールバックが復活していない（設計: フォールバックを残さない）
- 未設定ギルドでは無効サービス + 「未設定です」案内で終了する

実行: venv/bin/python -m pytest tests/
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.tasks import Tasks
from config import Config
from services.todoist_service import TodoistServiceManager
from utils.db import Database

G1 = 100000000000000001  # ギルド1


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 側で新規作成させる
    return path


def test_startup_without_todoist_env():
    """DISCORD_TOKEN だけあれば起動バリデーションを通過すること。"""
    c = Config()
    c.discord_token = "dummy-token"
    assert c.validate() == []

    # Todoist トークンの環境変数フォールバックが復活していないこと
    # （設計: 平文トークンは .env / settings に置かず /todoist-setup で登録）
    for name in ("TODOIST_API_TOKEN", "TODOIST_TOKEN"):
        assert not hasattr(c, name.lower()), f"環境変数フォールバック痕跡: {name}"


def test_unconfigured_guild_disabled_and_guided():
    """未設定ギルドでは例外なく無効サービスが返り、案内 Embed が用意されていること。"""
    db = Database(_tmp_db_path())
    run(db.connect())
    try:
        manager = TodoistServiceManager(db)
        svc = run(manager.for_guild(G1))
        assert svc.enabled is False
        assert run(manager.is_configured(G1)) is False

        embed = Tasks._todoist_unconfigured_embed()
        assert "未設定" in (embed.title or "")
        assert "/todoist-setup" in (embed.description or "")
    finally:
        run(db.close())

# ---------------------------------------------------------------------
# 同期失敗を利用者に見せる（G2-7）
#
# /task done・delete は Todoist 側の操作が失敗しても except TodoistError:
# pass で握りつぶし、必ず「完了にしました」と返していた。Todoist 側は
# 未完了のまま残り、翌朝の通知に出続ける
# （gotcha `todoist-completed-tasks-not-detected` の同期の片方向性と関連）。
# ローカル完了は維持したまま、成功メッセージに同期結果を明記する。
# ---------------------------------------------------------------------
import logging
from types import SimpleNamespace
from unittest import mock

import discord

from repositories.task_repository import TaskRepository
from services.todoist_service import TodoistError


class _SyncInteraction:
    def __init__(self):
        self.guild = SimpleNamespace(id=G1)
        self.user = mock.MagicMock(spec=discord.Member)
        self.user.id = 501
        self.user.display_name = "tester"
        self.user.roles = []
        self.user.guild = SimpleNamespace(id=G1, owner_id=501)
        self.user.guild_permissions = SimpleNamespace(administrator=True, manage_guild=True)
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return f"{embed.title or ''} {embed.description or ''}"


class _FailingTodoist:
    """close/delete が常に失敗する有効サービス。"""

    enabled = True

    async def close_task(self, todoist_task_id):
        raise TodoistError("boom")

    async def delete_task(self, todoist_task_id):
        raise TodoistError("boom")


class _WorkingTodoist:
    enabled = True

    async def close_task(self, todoist_task_id):
        return None

    async def delete_task(self, todoist_task_id):
        return None


def _cog_with_svc(db, svc):
    class _Manager:
        async def for_guild(self, guild_id):
            return svc

    return Tasks(SimpleNamespace(db=db, guilds=[], todoist_manager=_Manager(),
                                 get_channel=lambda cid: None))


async def _seed_linked_task(db) -> int:
    return await TaskRepository(db).create_task(
        G1, "主桁の積層", created_by="501", todoist_task_id="td_1"
    )


def test_done_shows_a_warning_when_todoist_close_fails(caplog):
    """ローカルは完了、ただし同期失敗を成功メッセージに明記すること。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_linked_task(db)
            cog = _cog_with_svc(db, _FailingTodoist())
            interaction = _SyncInteraction()
            with caplog.at_level(logging.WARNING, logger="tasks"):
                await Tasks.done.callback(cog, interaction, task_id=task_id)

            # ローカル完了は維持
            row = await TaskRepository(db).get_task(G1, task_id)
            assert row["status"] == "done"

            text = interaction.text()
            assert "完了にしました" in text
            assert "⚠️" in text and "Todoist 上で直接完了" in text, text

            # guild_id と task_id が warning に出ること
            warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
            assert any(str(G1) in w and str(task_id) in w for w in warnings), warnings
        finally:
            await db.close()

    run(_main())


def test_done_has_no_warning_when_todoist_close_succeeds():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_linked_task(db)
            cog = _cog_with_svc(db, _WorkingTodoist())
            interaction = _SyncInteraction()
            await Tasks.done.callback(cog, interaction, task_id=task_id)
            assert "⚠️" not in interaction.text()
        finally:
            await db.close()

    run(_main())


def test_delete_shows_a_warning_when_todoist_delete_fails(caplog):
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            task_id = await _seed_linked_task(db)
            cog = _cog_with_svc(db, _FailingTodoist())
            interaction = _SyncInteraction()
            with caplog.at_level(logging.WARNING, logger="tasks"):
                await Tasks.delete.callback(cog, interaction, task_id=task_id)

            # ローカルの削除（論理削除 = archived）は維持
            row = await TaskRepository(db).get_task(G1, task_id)
            assert row["status"] == "archived"
            text = interaction.text()
            assert "削除しました" in text
            assert "⚠️" in text and "Todoist 上で直接削除" in text, text
            warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
            assert any(str(G1) in w and str(task_id) in w for w in warnings), warnings
        finally:
            await db.close()

    run(_main())

