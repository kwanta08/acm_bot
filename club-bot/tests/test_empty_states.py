"""空状態に「次の1コマンド」を添えることのテスト（G2-5）。

「〜はありません。」だけの空状態は、初めて使う人に次の行き先を示さない。
特に `/report weekly` は新規サーバーで「未完了 0 / 超過 0 / 投票 0」と
表示され、**健全に運用できている状態と見分けが付かない**。

`utils/embeds.empty_state_embed(title, situation, next_command)` に集約し、
空状態には必ず次に打つコマンドを1つ添える。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())

from utils.db import Database
from utils.embeds import empty_state_embed

G1 = 111


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _text(embed) -> str:
    return f"{embed.title or ''} {embed.description or ''}"


class _Interaction:
    def __init__(self):
        self.guild = SimpleNamespace(id=G1, get_member=lambda uid: None)
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_embed(self):
        return self.sent[-1]["embed"]


# ---------------------------------------------------------------------
# empty_state_embed そのもの
# ---------------------------------------------------------------------
def test_empty_state_embed_contains_situation_and_next_command():
    embed = empty_state_embed("桁名一覧", "登録済みの桁名はありません。", "/layer keta-add")
    text = _text(embed)
    assert "登録済みの桁名はありません" in text
    assert "/layer keta-add" in text


def test_empty_state_embed_formats_the_command_as_code():
    """コマンド名はコードスパンで示す（既存の良い例と同じ見た目）。"""
    embed = empty_state_embed("一覧", "何もありません。", "/task add")
    assert "`/task add`" in (embed.description or "")


# ---------------------------------------------------------------------
# 各コマンドの空状態
# ---------------------------------------------------------------------
def test_task_list_empty_state_suggests_task_add():
    from cogs.tasks import Tasks

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Tasks(bot)
            embed = cog._build_task_list_embed("タスク一覧", [], None)
            assert "/task add" in _text(embed)
        finally:
            await db.close()

    run(_main())


def test_schedule_list_empty_state_suggests_schedule_create():
    from cogs.schedule import Schedule

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Schedule(bot)
            interaction = _Interaction()
            await Schedule.list_cmd.callback(cog, interaction)
            assert "/schedule create" in _text(interaction.last_embed)
        finally:
            await db.close()

    run(_main())


def test_keta_list_empty_state_suggests_keta_add():
    from cogs.layer_tracking import LayerTracking

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = LayerTracking(bot)
            interaction = _Interaction()
            await LayerTracking.keta_list.callback(cog, interaction)
            assert "/layer keta-add" in _text(interaction.last_embed)
        finally:
            await db.close()

    run(_main())


def test_report_audit_empty_state_names_a_command():
    from cogs.reports import Reports

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Reports(bot)
            interaction = _Interaction()
            await Reports.audit.callback(cog, interaction)
            text = _text(interaction.last_embed)
            assert "/" in text and "`" in text, f"次のコマンドが示されていない: {text}"
        finally:
            await db.close()

    run(_main())


def test_attendance_rate_empty_state_suggests_schedule_create():
    from cogs.reports import Reports

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Reports(bot)
            interaction = _Interaction()
            await Reports.attendance_rate.callback(cog, interaction)
            assert "/schedule create" in _text(interaction.last_embed)
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /report weekly — 全部0件は「健全」ではなく「未開始」
# ---------------------------------------------------------------------
def _patched_config():
    """reports.py の config.for_guild を最小のギルド設定へ差し替える。"""
    import cogs.reports as reports_mod

    class _Ctx:
        def __enter__(self):
            self._original = reports_mod.config.for_guild

            async def _fake(gid, db=None):
                return SimpleNamespace(
                    club_name_or_default="テスト航空研究会", competition_date=None
                )

            reports_mod.config.for_guild = _fake
            return self

        def __exit__(self, *exc):
            reports_mod.config.for_guild = self._original
            return False

    return _Ctx()


def test_weekly_report_with_no_data_says_not_started():
    from cogs.reports import Reports

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Reports(bot)
            interaction = _Interaction()
            with _patched_config():
                await Reports.weekly.callback(cog, interaction)
            text = _text(interaction.last_embed)
            assert "まだデータがありません" in text
            assert "/task add" in text and "/schedule create" in text
        finally:
            await db.close()

    run(_main())


def test_weekly_report_with_data_shows_the_numbers():
    """データがあれば従来どおり集計を出す（空状態表示に乗っ取られない）。"""
    from cogs.reports import Reports
    from repositories.task_repository import TaskRepository

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await TaskRepository(db).create_task(G1, "主桁の積層", created_by="tester")
            bot = SimpleNamespace(db=db, guilds=[], get_channel=lambda cid: None)
            cog = Reports(bot)
            interaction = _Interaction()
            with _patched_config():
                await Reports.weekly.callback(cog, interaction)
            embed = interaction.last_embed
            assert "まだデータがありません" not in _text(embed)
            names = [f.name for f in embed.fields]
            assert "未完了タスク" in names
        finally:
            await db.close()

    run(_main())
