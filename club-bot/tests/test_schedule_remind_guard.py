"""`/schedule remind` の締切済みガードのテスト（G4-14）。

`remind` は `closed_flag` を見ていなかったため、L2 が ID を直打ちすれば
**締切済み・復元済みの予定でも未回答者へ DM が飛んでいた**
（オートコンプリートは開催中しか出さないので踏みにくいが、塞がっていなかった）。
`edit-deadline` は既に `closed_flag` を見て断っている。

このファイルが特に固定しているもの:

1. **締切済みでは DM が1通も飛ばないこと。** 「断りの Embed が出る」だけを
   見ると、断る前に送っている実装を素通りする
2. **文言を `edit-deadline` と揃えること**（受入基準）
3. **開催中は今までどおり動くこと**（ガードで塞ぎすぎていない）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.schedule import Schedule
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database
from utils.parser import TZ, to_iso

G1 = 100000000000000001
DEADLINE = datetime(2030, 1, 10, 23, 59, tzinfo=TZ)


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


async def _seed(db: Database) -> ScheduleRepository:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1, "sch_1", "秋合宿", None, "部室", None, to_iso(DEADLINE), "tester", "555"
    )
    await repo.add_option(
        G1, "sch_1_o1", "sch_1", "候補1", to_iso(DEADLINE + timedelta(days=1)), None, None
    )
    members = MemberRepository(db)
    for user_id, name in (("1", "たろう"), ("2", "はなこ")):
        await members.upsert_member(G1, user_id, name)
    return repo


class _Interaction:
    def __init__(self):
        self.guild = SimpleNamespace(id=G1)
        self.user = SimpleNamespace(
            id=501,
            display_name="tester",
            guild=SimpleNamespace(owner_id=501),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._noop, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _noop(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")


def _cog(db: Database) -> Schedule:
    """`notify_unanswered` を呼ばれたら記録するだけの Schedule。"""
    cog = Schedule(SimpleNamespace(db=db, guilds=[], user=None, get_guild=lambda _g: None))
    cog.notified = []

    async def _spy(schedule):
        cog.notified.append(schedule["schedule_id"])
        return 2

    cog.notify_unanswered = _spy
    return cog


def test_a_closed_schedule_is_refused_without_sending_anything():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.close_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")

            assert cog.notified == [], "締切済みなのに未回答者へ通知しようとしている"
            assert "締切済み" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_the_wording_matches_edit_deadline():
    """受入基準: 文言は `edit-deadline` と揃える。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.close_schedule(G1, "sch_1")
            cog = _cog(db)

            remind_interaction = _Interaction()
            await Schedule.remind.callback(cog, remind_interaction, schedule_id="sch_1")
            edit_interaction = _Interaction()
            await Schedule.edit_deadline.callback(
                cog, edit_interaction, schedule_id="sch_1", deadline="2030-02-01 12:00"
            )

            assert "この投票は既に締切済みです。" in remind_interaction.text
            assert "この投票は既に締切済みです。" in edit_interaction.text
        finally:
            await db.close()

    run(_main())


def test_a_restored_schedule_that_is_still_closed_is_refused():
    """G3-3 の復元は `closed_flag` を戻さない。復元直後も催促できないこと。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.close_schedule(G1, "sch_1")
            await repo.soft_delete_schedule(G1, "sch_1")
            await repo.restore_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")
            assert cog.notified == []
            assert "締切済み" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_an_open_schedule_still_reminds():
    """ガードで塞ぎすぎていないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")
            assert cog.notified == ["sch_1"]
            assert "再通知しました" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_a_deleted_schedule_is_still_not_found():
    """論理削除済みは従来どおり「見つからない」（G3-3 の挙動を壊さない）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.remind.callback(cog, interaction, schedule_id="sch_1")
            assert cog.notified == []
            assert interaction.sent, "何も返していない"
        finally:
            await db.close()

    run(_main())
