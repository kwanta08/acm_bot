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
# Todoist が正本（スキーマ v22）
#
# 以前はローカル DB にタスクの複製を持ち、Todoist 側の close/delete が
# 失敗しても「ローカルは完了、警告つき」で返していた（G2-7）。
# 複製をやめた今は**片側だけ成立する状態そのものが作れない**——
# Todoist が失敗したら操作は失敗し、TODOIST_API_FAILED を見せて終わる。
# ---------------------------------------------------------------------
from types import SimpleNamespace
from unittest import mock

import discord

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


_RAW_TASK = SimpleNamespace(
    id="td_1",
    content="主桁の積層",
    description="",
    due=None,
    priority=1,
    section_id=None,
)


class _FailingTodoist:
    """close/delete が常に失敗する有効サービス。"""

    enabled = True
    project_id = None

    async def get_task(self, task_id):
        return _RAW_TASK

    async def close_task(self, task_id):
        raise TodoistError("boom")

    async def delete_task(self, task_id):
        raise TodoistError("boom")


class _WorkingTodoist:
    enabled = True
    project_id = None

    def __init__(self):
        self.closed: list[str] = []
        self.deleted: list[str] = []

    async def get_task(self, task_id):
        return _RAW_TASK

    async def close_task(self, task_id):
        self.closed.append(task_id)

    async def delete_task(self, task_id):
        self.deleted.append(task_id)


class _DisabledTodoist:
    enabled = False
    project_id = None


def _cog_with_svc(db, svc):
    class _Manager:
        async def for_guild(self, guild_id):
            return svc

    return Tasks(
        SimpleNamespace(
            db=db, guilds=[], todoist_manager=_Manager(), get_channel=lambda cid: None
        )
    )


def test_done_fails_loudly_when_todoist_close_fails():
    """Todoist が閉じられなければ「完了にしました」とは言わないこと。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _cog_with_svc(db, _FailingTodoist())
            interaction = _SyncInteraction()
            await Tasks.done.callback(cog, interaction, task_id="td_1")

            text = interaction.text()
            assert "完了にしました" not in text, text
            assert "TODOIST_API_FAILED" in text, text
        finally:
            await db.close()

    run(_main())


def test_done_closes_the_task_in_todoist():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            svc = _WorkingTodoist()
            cog = _cog_with_svc(db, svc)
            interaction = _SyncInteraction()
            await Tasks.done.callback(cog, interaction, task_id="td_1")
            assert svc.closed == ["td_1"]
            assert "完了にしました" in interaction.text()
        finally:
            await db.close()

    run(_main())


def test_delete_fails_loudly_when_todoist_delete_fails():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = _cog_with_svc(db, _FailingTodoist())
            interaction = _SyncInteraction()
            await Tasks.delete.callback(cog, interaction, task_id="td_1")
            text = interaction.text()
            assert "削除しました" not in text, text
            assert "TODOIST_API_FAILED" in text, text
        finally:
            await db.close()

    run(_main())


def test_delete_removes_the_task_in_todoist():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            svc = _WorkingTodoist()
            cog = _cog_with_svc(db, svc)
            interaction = _SyncInteraction()
            await Tasks.delete.callback(cog, interaction, task_id="td_1")
            assert svc.deleted == ["td_1"]
            assert "削除しました" in interaction.text()
        finally:
            await db.close()

    run(_main())


def test_task_commands_refuse_to_run_without_todoist():
    """未設定ギルドでは、成功したふりをせず設定を促して終わること。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            for callback, kwargs in (
                (Tasks.add.callback, {"title": "主桁の積層"}),
                (Tasks.list_cmd.callback, {}),
                (Tasks.done.callback, {"task_id": "td_1"}),
                (Tasks.delete.callback, {"task_id": "td_1"}),
                (Tasks.overdue.callback, {}),
            ):
                cog = _cog_with_svc(db, _DisabledTodoist())
                interaction = _SyncInteraction()
                await callback(cog, interaction, **kwargs)
                text = interaction.text()
                assert "未設定" in text, text
                assert "/todoist-setup" in text, text
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# SDK の版差（close_task ↔ complete_task）
#
# todoist-api-python は v2 が close_task、v3 以降が complete_task。
# requirements は >=2.1.0 で両方を許すので、どちらでも完了できること。
# ローカルに複製が無い今、ここが落ちると /task done が丸ごと効かない。
# ---------------------------------------------------------------------
def _service_with_api(api):
    from services.todoist_service import TodoistService

    svc = TodoistService.__new__(TodoistService)
    svc.enabled = True
    svc._api = api
    svc.project_id = None
    svc.label_name = "今日やること"
    return svc


def test_close_task_uses_close_task_on_sdk_v2():
    closed: list[str] = []
    api = SimpleNamespace(close_task=lambda task_id: closed.append(task_id) or True)
    assert run(_service_with_api(api).close_task("td_1")) is True
    assert closed == ["td_1"]


def test_close_task_falls_back_to_complete_task_on_newer_sdks():
    completed: list[str] = []

    class _V4Api:
        def complete_task(self, task_id):
            completed.append(task_id)
            return True

    assert run(_service_with_api(_V4Api()).close_task("td_1")) is True
    assert completed == ["td_1"]


def test_close_task_raises_when_the_sdk_has_neither():
    import pytest

    with pytest.raises(TodoistError):
        run(_service_with_api(SimpleNamespace()).close_task("td_1"))
