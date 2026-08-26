"""破壊的操作に共通の確認ステップを入れることのテスト（G2-1）。

確認があったのは `/data delete`・`/season rollover`・`/team-remove` の3つだけで、
同じくらい取り返しのつかない操作に確認が無かった:

- `/progress remove` は**配下ごと**削除し、実行後に件数を報告するだけ
- `/schedule delete` は投票メッセージを全削除してから DB を CASCADE 削除（票が完全消失）
- `/season new` は現年度を即終了する

`utils/views.ConfirmView` に確認ステップを1箇所へ集約し、上記へ適用する。

**このタスクでは削除の方式を変えない。** `/schedule delete` の論理削除化は
破壊的なので G3-3 で扱う（ADR 0018 / 0024 の「既存データを動かさない」軸）。
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
import pytest

from repositories.progress_repository import ProgressRepository
from utils.db import Database
from utils.views import ConfirmView

G1 = 111
G2 = 222
OWNER = 501
OTHER = 502


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _member(user_id: int):
    stub = mock.MagicMock(spec=discord.Member)
    stub.id = user_id
    stub.display_name = f"user{user_id}"
    stub.roles = []
    stub.guild = SimpleNamespace(id=G1, owner_id=999)
    stub.guild_permissions = SimpleNamespace(administrator=True, manage_guild=True)
    return stub


class _Interaction:
    """ボタン押下の interaction を模したスタブ。"""

    def __init__(self, user_id: int = OWNER):
        self.user = _member(user_id)
        self.guild = SimpleNamespace(id=G1)
        self.sent: list = []
        self.edited: list = []
        self.deferred = 0
        self.response = SimpleNamespace(
            defer=self._defer,
            send_message=self._send,
            edit_message=self._edit,
        )
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        self.deferred += 1

    async def _send(self, *args, **kwargs):
        self.sent.append(kwargs)

    async def _edit(self, *args, **kwargs):
        self.edited.append(kwargs)

    @property
    def last_embed(self):
        return self.sent[-1].get("embed") if self.sent else None

    def text(self) -> str:
        """送信・編集で出た文言をまとめて返す。"""
        out = []
        for kwargs in (*self.sent, *self.edited):
            embed = kwargs.get("embed")
            if embed is not None:
                out.append(f"{embed.title or ''} {embed.description or ''}")
        return "\n".join(out)


def _preview() -> discord.Embed:
    return discord.Embed(title="確認", description="消しますか")


def _view(on_confirm, owner_id: int = OWNER) -> ConfirmView:
    return ConfirmView(owner_id, _preview(), on_confirm)


# ---------------------------------------------------------------------
# ConfirmView そのもの
# ---------------------------------------------------------------------
def test_nothing_runs_until_the_confirm_button_is_pressed():
    """View を作っただけでは破壊的処理が走らないこと。"""
    calls: list[str] = []

    async def _on_confirm(interaction):
        calls.append("ran")

    _view(_on_confirm)
    assert calls == []


def test_confirm_button_runs_the_callback():
    calls: list[str] = []

    async def _on_confirm(interaction):
        calls.append("ran")

    view = _view(_on_confirm)
    interaction = _Interaction(OWNER)
    run(view.confirm.callback(interaction))
    assert calls == ["ran"]
    assert view.confirmed is True


def test_cancel_button_does_not_run_the_callback():
    calls: list[str] = []

    async def _on_confirm(interaction):
        calls.append("ran")

    view = _view(_on_confirm)
    interaction = _Interaction(OWNER)
    run(view.cancel.callback(interaction))
    assert calls == []
    assert view.confirmed is False
    assert "中止" in interaction.text()


def test_someone_else_cannot_confirm():
    """他人の interaction は拒否され、コールバックが走らないこと。"""
    calls: list[str] = []

    async def _on_confirm(interaction):
        calls.append("ran")

    view = _view(_on_confirm, owner_id=OWNER)
    interaction = _Interaction(OTHER)
    run(view.confirm.callback(interaction))
    assert calls == [], "他人が押したのに処理が走っている"
    assert view.confirmed is False
    assert "実行者" in interaction.text()


def test_someone_else_cannot_cancel():
    """他人が「やめる」を押して確認を流せないこと。"""

    async def _on_confirm(interaction):
        raise AssertionError("呼ばれてはいけない")

    view = _view(_on_confirm, owner_id=OWNER)
    interaction = _Interaction(OTHER)
    run(view.cancel.callback(interaction))
    assert view.cancelled is False
    assert "実行者" in interaction.text()


def test_double_press_runs_the_callback_only_once():
    """連打で二重に削除されないこと。"""
    calls: list[str] = []

    async def _on_confirm(interaction):
        calls.append("ran")

    view = _view(_on_confirm)
    run(view.confirm.callback(_Interaction(OWNER)))
    run(view.confirm.callback(_Interaction(OWNER)))
    assert calls == ["ran"], f"確定が {len(calls)} 回走っている"


def test_buttons_are_disabled_after_confirming():
    async def _on_confirm(interaction):
        return None

    view = _view(_on_confirm)
    run(view.confirm.callback(_Interaction(OWNER)))
    assert all(item.disabled for item in view.children if hasattr(item, "disabled"))


def test_callback_failure_does_not_leave_the_view_running():
    """コールバックが例外を投げても View を止めること（押しっぱなしにしない）。

    `is_finished()` を見るので View の生成もループ内で行う。discord.py 2.7 は
    停止用の Future を実行中ループがある時にだけ作るため、ループ外で生成した
    View は `stop()` しても `is_finished()` が False のままになる。
    """

    async def _main():
        async def _on_confirm(interaction):
            raise RuntimeError("削除に失敗")

        view = _view(_on_confirm)
        with pytest.raises(RuntimeError):
            await view.confirm.callback(_Interaction(OWNER))
        assert view.is_finished()

    run(_main())


# ---------------------------------------------------------------------
# ProgressRepository.count_subtree（削除前のプレビュー用）
# ---------------------------------------------------------------------
async def _seed_tree(db: Database, guild_id: int) -> ProgressRepository:
    repo = ProgressRepository(db)
    await repo.upsert_node(guild_id, "main", name="主翼", parent_id=None)
    await repo.upsert_node(guild_id, "spar", name="主桁", parent_id="main")
    await repo.upsert_node(guild_id, "rib", name="リブ", parent_id="main")
    await repo.upsert_node(guild_id, "layer", name="積層", parent_id="spar")
    await repo.upsert_node(guild_id, "tail", name="尾翼", parent_id=None)
    return repo


def test_count_subtree_counts_the_node_and_its_descendants():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            # main / spar / rib / layer
            assert await repo.count_subtree(G1, "main") == 4
            # spar / layer
            assert await repo.count_subtree(G1, "spar") == 2
            # 葉は自分だけ
            assert await repo.count_subtree(G1, "layer") == 1
            assert await repo.count_subtree(G1, "tail") == 1
        finally:
            await db.close()

    run(_main())


def test_count_subtree_matches_what_delete_subtree_removes():
    """プレビューの件数と実際に消える件数がずれないこと。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            previewed = await repo.count_subtree(G1, "main")
            deleted = await repo.delete_subtree(G1, "main")
            assert previewed == deleted
        finally:
            await db.close()

    run(_main())


def test_count_subtree_is_guild_scoped():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            await repo.upsert_node(G2, "main", name="主翼", parent_id=None)
            assert await repo.count_subtree(G2, "main") == 1
            assert await repo.count_subtree(G1, "main") == 4
        finally:
            await db.close()

    run(_main())


def test_count_subtree_returns_zero_for_an_unknown_node():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = ProgressRepository(db)
            assert await repo.count_subtree(G1, "nope") == 0
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /progress remove — 確認するまで消えないこと
# ---------------------------------------------------------------------
def test_progress_remove_does_not_delete_before_confirmation():
    """コマンドを実行しただけでは1件も消えないこと。"""
    from cogs.progress import Progress

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            cog = Progress(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(OWNER)
            await Progress.progress_remove.callback(cog, interaction, node="main")

            assert len(await repo.list_nodes(G1)) == 5, "確認前に削除されている"
            # 配下の件数がプレビューに出ていること
            assert "4" in interaction.text(), interaction.text()
        finally:
            await db.close()

    run(_main())


def test_progress_remove_deletes_after_confirmation():
    from cogs.progress import Progress

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            cog = Progress(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(OWNER)
            await Progress.progress_remove.callback(cog, interaction, node="main")

            view = interaction.sent[-1]["view"]
            await view.confirm.callback(_Interaction(OWNER))

            remaining = {r["node_id"] for r in await repo.list_nodes(G1)}
            assert remaining == {"tail"}
        finally:
            await db.close()

    run(_main())


def test_progress_remove_confirmation_is_owner_only():
    """他人が確定ボタンを押しても消えないこと。"""
    from cogs.progress import Progress

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = await _seed_tree(db, G1)
            cog = Progress(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(OWNER)
            await Progress.progress_remove.callback(cog, interaction, node="main")

            view = interaction.sent[-1]["view"]
            await view.confirm.callback(_Interaction(OTHER))

            assert len(await repo.list_nodes(G1)) == 5, "他人の確定で削除されている"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /schedule delete — 票が消える前に確認すること
#
# 削除の**方式**はこのタスクでは変えない（論理削除化は G3-3）。
# ここで足すのは確認ステップだけ。
# ---------------------------------------------------------------------
async def _seed_schedule(db: Database) -> str:
    from repositories.schedule_repository import ScheduleRepository

    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1,
        "sch_1",
        "第1回 全体ミーティング",
        None,
        None,
        None,
        "2026-09-01T23:59:00",
        "tester",
        "12345",
    )
    await repo.add_option(G1, "opt_1", "sch_1", "9/1 10:00", "2026-09-01T10:00:00", None, None)
    return "sch_1"


def _schedule_cog(db: Database):
    from cogs.schedule import Schedule

    return Schedule(SimpleNamespace(db=db, guilds=[], get_channel=lambda _cid: None))


def test_schedule_delete_does_not_delete_before_confirmation():
    from cogs.schedule import Schedule
    from repositories.schedule_repository import ScheduleRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedule(db)
            cog = _schedule_cog(db)
            interaction = _Interaction(OWNER)
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")

            assert await ScheduleRepository(db).get_schedule(G1, "sch_1") is not None, (
                "確認前に削除されている"
            )
        finally:
            await db.close()

    run(_main())


def test_schedule_delete_deletes_after_confirmation():
    from cogs.schedule import Schedule
    from repositories.schedule_repository import ScheduleRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedule(db)
            cog = _schedule_cog(db)
            interaction = _Interaction(OWNER)
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")

            view = interaction.sent[-1]["view"]
            await view.confirm.callback(_Interaction(OWNER))

            assert await ScheduleRepository(db).get_schedule(G1, "sch_1") is None
        finally:
            await db.close()

    run(_main())


def test_schedule_delete_confirmation_is_owner_only():
    from cogs.schedule import Schedule
    from repositories.schedule_repository import ScheduleRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await _seed_schedule(db)
            cog = _schedule_cog(db)
            interaction = _Interaction(OWNER)
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")

            view = interaction.sent[-1]["view"]
            await view.confirm.callback(_Interaction(OTHER))

            assert await ScheduleRepository(db).get_schedule(G1, "sch_1") is not None
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /season new — 現年度を即終了する前に確認すること
# ---------------------------------------------------------------------
def test_season_new_does_not_end_the_current_season_before_confirmation():
    from cogs.season import Season
    from repositories.season_repository import SeasonRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = SeasonRepository(db)
            await repo.start_new(G1, "2026年度")
            cog = Season(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(OWNER)
            await Season.season_new.callback(cog, interaction, name="2027年度")

            current = await repo.current(G1)
            assert current and current["name"] == "2026年度", "確認前に年度が切り替わっている"
        finally:
            await db.close()

    run(_main())


def test_season_new_starts_the_season_after_confirmation():
    from cogs.season import Season
    from repositories.season_repository import SeasonRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = SeasonRepository(db)
            await repo.start_new(G1, "2026年度")
            cog = Season(SimpleNamespace(db=db, guilds=[]))
            interaction = _Interaction(OWNER)
            await Season.season_new.callback(cog, interaction, name="2027年度")

            view = interaction.sent[-1]["view"]
            await view.confirm.callback(_Interaction(OWNER))

            current = await repo.current(G1)
            assert current and current["name"] == "2027年度"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /season rollover — ConfirmView へ切り出しても挙動が変わらないこと
# ---------------------------------------------------------------------
def test_rollover_view_is_a_confirm_view():
    """確認の作法が1箇所に集約されていること。"""
    from cogs.season import RolloverView

    assert issubclass(RolloverView, ConfirmView)


def test_rollover_view_still_has_the_user_picker():
    """卒業者を選ぶ UI を落としていないこと。"""
    from cogs.season import RolloverView

    view = RolloverView(SimpleNamespace(), G1, "2027年度", OWNER)
    assert any(isinstance(item, discord.ui.UserSelect) for item in view.children)


def test_rollover_view_rejects_someone_else():
    from cogs.season import RolloverView

    view = RolloverView(SimpleNamespace(), G1, "2027年度", OWNER)
    interaction = _Interaction(OTHER)
    run(view.confirm.callback(interaction))
    assert "実行者" in interaction.text()
