"""タイムアウトした View が画面に反映されることのテスト（G2-4）。

これまで6箇所の `on_timeout` は `item.disabled = True` するだけで
`message.edit(view=self)` を呼んでいなかった。discord.py の View は
サーバー側の状態を編集しない限り画面が変わらないため、利用者には
「ボタンはあるのに押しても無反応」に見えていた。
`RolloverView`（現 `ConfirmView`）には `on_timeout` すら無く、
選択途中で5分経つと確定ボタンが静かに死んでいた。

`utils/views.TimeoutAwareView` に集約し、タイムアウト時は
「時間切れです。もう一度実行してください」へ差し替える。
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

from utils.db import Database
from utils.views import ConfirmView, TimeoutAwareView

G1 = 111
OWNER = 501


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _View(TimeoutAwareView):
    """テスト用の最小 View。"""

    def __init__(self):
        super().__init__(timeout=1.0)
        self.add_item(discord.ui.Button(label="押す"))


def _fake_message():
    message = mock.MagicMock()
    message.edit = mock.AsyncMock(return_value=None)
    return message


# ---------------------------------------------------------------------
# TimeoutAwareView そのもの
# ---------------------------------------------------------------------
def test_on_timeout_edits_the_message():
    """タイムアウトで message.edit が呼ばれ、時間切れの表示に差し替わること。"""

    async def _main():
        view = _View()
        view.message = _fake_message()
        await view.on_timeout()

        view.message.edit.assert_awaited_once()
        kwargs = view.message.edit.await_args.kwargs
        embed = kwargs["embed"]
        assert "時間切れ" in f"{embed.title or ''} {embed.description or ''}"
        assert "もう一度" in f"{embed.title or ''} {embed.description or ''}"
        assert kwargs["view"] is None, "押せないボタンを画面に残している"

    run(_main())


def test_on_timeout_disables_everything():
    async def _main():
        view = _View()
        view.message = _fake_message()
        await view.on_timeout()
        assert all(item.disabled for item in view.children if hasattr(item, "disabled"))

    run(_main())


def test_on_timeout_without_a_message_does_not_crash():
    """message を覚えさせ損ねた View でも例外にしない（従来と同じ挙動に落ちるだけ）。"""

    async def _main():
        view = _View()
        assert view.message is None
        await view.on_timeout()  # 例外にならないこと

    run(_main())


def test_on_timeout_survives_a_failed_edit():
    """メッセージが消されていた等で edit が失敗しても例外にしない。"""

    async def _main():
        view = _View()
        view.message = _fake_message()
        view.message.edit.side_effect = discord.NotFound(
            mock.MagicMock(status=404), "gone"
        )
        await view.on_timeout()  # 例外にならないこと

    run(_main())


# ---------------------------------------------------------------------
# 6箇所が継承していること
# ---------------------------------------------------------------------
def test_confirm_view_is_timeout_aware():
    """ConfirmView（/progress remove・/schedule delete・/season new・rollover）。"""
    assert issubclass(ConfirmView, TimeoutAwareView)


def test_all_interactive_views_are_timeout_aware():
    from cogs.progress import ProgressView, ProjectSetupWizard
    from cogs.setup_wizard import SetupWizardView
    from cogs.tasks import SectionSelectView
    from cogs.todoist_admin import TodoistSetupView

    for cls in (
        ProgressView,
        ProjectSetupWizard,
        SetupWizardView,
        SectionSelectView,
        TodoistSetupView,
    ):
        assert issubclass(cls, TimeoutAwareView), f"{cls.__name__} が TimeoutAwareView ではない"


def test_no_view_overrides_on_timeout_with_the_broken_pattern():
    """「disabled にするだけ」の on_timeout 再発を防ぐ。

    TimeoutAwareView を継承していても on_timeout を上書きして
    message.edit を呼ばなければ元の木阿弥なので、上書き自体を検出する。
    """
    from cogs.progress import ProgressView, ProjectSetupWizard
    from cogs.setup_wizard import SetupWizardView
    from cogs.tasks import SectionSelectView
    from cogs.todoist_admin import TodoistSetupView

    for cls in (
        ConfirmView,
        ProgressView,
        ProjectSetupWizard,
        SetupWizardView,
        SectionSelectView,
        TodoistSetupView,
    ):
        assert cls.on_timeout is TimeoutAwareView.on_timeout, (
            f"{cls.__name__} が on_timeout を独自実装している"
        )


def test_rollover_view_timeout_is_extended_to_900():
    """卒業者を25名まで選ぶ操作は5分では足りない。"""
    from cogs.season import RolloverView

    view = RolloverView(SimpleNamespace(), G1, "2027年度", OWNER)
    assert view.timeout == 900


# ---------------------------------------------------------------------
# 送信側が message を覚えさせていること（代表: /progress remove）
# ---------------------------------------------------------------------
class _Interaction:
    def __init__(self):
        self.guild = SimpleNamespace(id=G1)
        self.user = SimpleNamespace(id=OWNER, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)
        return _fake_message()  # followup.send は WebhookMessage を返す


def test_progress_remove_remembers_the_sent_message():
    from cogs.progress import Progress
    from repositories.progress_repository import ProgressRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(G1, "main", name="主翼", parent_id=None)
            cog = Progress(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction()
            await Progress.progress_remove.callback(cog, interaction, node="main")

            view = interaction.sent[-1]["view"]
            assert view.message is not None, "送った message を View が覚えていない"
        finally:
            await db.close()

    run(_main())
