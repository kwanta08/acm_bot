"""一覧系コマンドが Discord の Embed field 上限（25）を超えないことのテスト。

Embed は field を 25 個までしか持てない。超えて add_field すると送信時に
HTTPException(400) になり、利用者には「予期せぬエラーが発生しました。
時間をおいて再試行してください」としか出ない。時間をおいても直らないため、
データが増えた時点でそのコマンドは恒久的に壊れる。

打ち切ったこと自体も本文に出す。黙って切ると「該当は 25 件」と誤読され、
探し物が見つからない理由が利用者に分からない。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.layer_tracking import LayerTracking
from cogs.schedule import Schedule
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database
from utils.embeds import MAX_EMBED_FIELDS, add_truncation_note, info_embed

G1 = 111
OVER = MAX_EMBED_FIELDS + 5  # 上限を確実に超える件数


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _Interaction:
    """defer / followup.send だけを受けるフェイク interaction。"""

    def __init__(self, guild_id: int = G1):
        self.user = SimpleNamespace(id=42, display_name="山田")
        self.guild = SimpleNamespace(id=guild_id, get_member=lambda _id: None)
        self.sent: list = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_embed(self):
        return self.sent[-1]["embed"]


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ---------------------------------------------------------------------
# add_truncation_note（純粋関数）
# ---------------------------------------------------------------------
def test_note_absent_when_nothing_was_truncated():
    embed = info_embed("一覧", "本文")
    add_truncation_note(embed, total=10, shown=25)
    assert embed.description == "本文"


def test_note_reports_remaining_count():
    embed = info_embed("一覧", "本文")
    add_truncation_note(embed, total=30, shown=25)
    assert "ほか 5 件" in embed.description
    assert embed.description.startswith("本文")


def test_note_becomes_description_when_empty():
    embed = info_embed("一覧")
    add_truncation_note(embed, total=30, shown=25, hint="新しい順")
    assert embed.description == "…ほか 5 件（新しい順）"


# ---------------------------------------------------------------------
# /schedule list / list-closed
# ---------------------------------------------------------------------
async def _seed_schedules(repo: ScheduleRepository, count: int, *, closed: bool) -> None:
    for i in range(count):
        sid = f"sch_{i:03d}"
        await repo.create_schedule(
            G1,
            sid,
            f"練習 {i}",
            None,
            None,
            None,
            f"2026-09-{(i % 28) + 1:02d}T19:00:00+09:00",
            "42",
            "999",
        )
        if closed:
            await repo.close_schedule(G1, sid)


def test_schedule_list_stays_within_field_limit():
    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            await _seed_schedules(repo, OVER, closed=False)
            cog = Schedule(SimpleNamespace(db=db))
            interaction = _Interaction()
            await Schedule.list_cmd.callback(cog, interaction)

            embed = interaction.last_embed
            assert len(embed.fields) == MAX_EMBED_FIELDS
            assert f"ほか {OVER - MAX_EMBED_FIELDS} 件" in (embed.description or "")
        finally:
            await db.close()

    run(_main())


def test_schedule_list_closed_stays_within_field_limit():
    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            await _seed_schedules(repo, OVER, closed=True)
            cog = Schedule(SimpleNamespace(db=db))
            interaction = _Interaction()
            await Schedule.list_closed_cmd.callback(cog, interaction)

            embed = interaction.last_embed
            assert len(embed.fields) == MAX_EMBED_FIELDS
            assert f"ほか {OVER - MAX_EMBED_FIELDS} 件" in (embed.description or "")
        finally:
            await db.close()

    run(_main())


def test_schedule_list_under_limit_has_no_note():
    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            await _seed_schedules(repo, 3, closed=False)
            cog = Schedule(SimpleNamespace(db=db))
            interaction = _Interaction()
            await Schedule.list_cmd.callback(cog, interaction)

            embed = interaction.last_embed
            assert len(embed.fields) == 3
            assert "ほか" not in (embed.description or "")
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /layer status
# ---------------------------------------------------------------------
def test_layer_status_stays_within_field_limit():
    async def _main():
        db = await _make_db()
        try:
            cog = LayerTracking(SimpleNamespace(db=db))
            for i in range(OVER):
                await cog.session_repo.start(
                    G1, str(1000 + i), "主桁1", str(i + 1), "2026-08-20T09:00:00+09:00"
                )
            interaction = _Interaction()
            await LayerTracking.status.callback(cog, interaction)

            embed = interaction.last_embed
            assert len(embed.fields) == MAX_EMBED_FIELDS
            assert f"ほか {OVER - MAX_EMBED_FIELDS} 件" in (embed.description or "")
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# /report attendance-rate は「表示は絞るが集計は全件」
#
# 26 件目以降を集計から落とすと、表示されないだけでなく参加率の数字
# そのものが誤る。人に見せる集計値が黙って間違うのが一番まずい。
# ---------------------------------------------------------------------
def test_attendance_rate_aggregates_all_schedules():
    from cogs.reports import Reports
    from repositories.schedule_repository import ScheduleRepository

    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            # 25 件は全員 ok、26 件目以降は全員 ng にして、
            # 打ち切ると通算参加率が 100% に化けるようにする
            for i in range(OVER):
                sid = f"sch_{i:03d}"
                await repo.create_schedule(
                    G1, sid, f"練習 {i}", None, None, None, "2026-09-01T19:00:00+09:00", "42", "9"
                )
                oid = f"opt_{i:03d}"
                await repo.add_option(
                    G1, oid, sid, "9/1 19:00", "2026-09-01T19:00:00+09:00", None, None
                )
                status = "ok" if i < MAX_EMBED_FIELDS else "ng"
                await repo.set_vote(G1, oid, str(1000 + i), status)

            cog = Reports(SimpleNamespace(db=db))
            interaction = _Interaction()
            await Reports.attendance_rate.callback(cog, interaction)

            embed = interaction.last_embed
            assert len(embed.fields) == MAX_EMBED_FIELDS
            desc = embed.description or ""
            assert "100%" not in desc, "打ち切った25件だけで集計している"
            assert f"全 {OVER} 件" in desc
            assert f"票 {OVER}" in desc
            assert f"ほか {OVER - MAX_EMBED_FIELDS} 件" in desc
        finally:
            await db.close()

    run(_main())
