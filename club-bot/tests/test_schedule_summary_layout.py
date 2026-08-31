"""締切後の集計サマリーの候補レイアウト（横並び）のテスト。

候補を inline=False で積むと1候補1行の縦積みになり、候補の多い予定で
「どの日が良いか」を見比べられなかった。このファイルが固定するもの:

1. **候補は inline field で出る**（Discord は inline を最大3列/行で
   横に並べる）。value は狭い列でも読めるよう状態ごとに1行
2. **場所・締切は field ではなく description に出る。** field にすると
   先頭の候補が同じ行に混ざり、候補の列がずれる
3. **候補が 25 件を超えても field は 25 で打ち切り、打ち切りを注記する。**
   26 件目を add_field すると送信時に HTTPException(400) になり、
   finalize_schedule が握り潰すため集計サマリーごと無言で消える
4. **最多参加候補は打ち切り後ではなく全候補から算出する**
   （人に見せる集計値が表示の都合で誤らない。/report と同じ方針）
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

from repositories.schedule_repository import ScheduleRepository
from services import schedule_service as svc
from utils.db import Database
from utils.embeds import MAX_EMBED_FIELDS
from utils.parser import TZ, to_iso

G1 = 100000000000000001
DAY = datetime(2026, 10, 1, 18, 0, tzinfo=TZ)
BOT = SimpleNamespace(get_user=lambda _uid: None)


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


async def _seed(db: Database, option_count: int = 2) -> ScheduleRepository:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1,
        "sch_1",
        "秋合宿",
        None,
        "部室",
        None,
        "2026-09-25T23:59:00+09:00",
        "tester",
        "555",
    )
    # list_options は start_at 順なので、日をずらして順序を固定する
    for i in range(option_count):
        await repo.add_option(
            G1, f"o{i + 1}", "sch_1", f"10/{i + 1} 18:00", to_iso(DAY + timedelta(days=i)), None, None
        )
    return repo


def test_candidates_are_inline_fields():
    """候補が inline field（横並び）で、value が状態ごとに1行であること。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_vote(G1, "o1", "1", "ok")
            await repo.set_vote(G1, "o1", "2", "ok")
            await repo.set_vote(G1, "o1", "3", "ng")
            await repo.set_vote(G1, "o2", "1", "maybe")

            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_summary_embed(repo, G1, BOT, schedule, None)

            assert [f.name for f in embed.fields] == ["10/1 18:00", "10/2 18:00"]
            assert all(f.inline for f in embed.fields), "候補が縦積み（inline=False）に戻っている"

            # 状態ごとに1行。人数 0 の状態も行ごと消さない（列間で比較するため）。
            # list_votes は並び順を保証しないので、名前は順不同で見る
            lines = (embed.fields[0].value or "").splitlines()
            assert lines[0].startswith("参加 2: ") and "<@1>" in lines[0] and "<@2>" in lines[0]
            assert lines[1] == "不参加 1: <@3>"
            assert lines[2] == "未定 0"
            assert embed.fields[1].value == "参加 0\n不参加 0\n未定 1: <@1>"
        finally:
            await db.close()

    run(_main())


def test_place_and_deadline_moved_to_description():
    """場所・締切が field に無いこと（candidate の列がずれるため）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_summary_embed(repo, G1, BOT, schedule, None)

            names = [f.name for f in embed.fields]
            assert "場所" not in names and "締切" not in names, (
                "場所・締切が field にあると、先頭の候補が同じ行に混ざって列がずれる"
            )
            body = embed.description or ""
            assert "場所: 部室" in body
            assert "締切: 2026/09/25 23:59" in body
        finally:
            await db.close()

    run(_main())


def test_summary_survives_more_than_25_options():
    """候補 26 件以上でも field を 25 で打ち切り、打ち切りを注記すること。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, option_count=30)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_summary_embed(repo, G1, BOT, schedule, None)

            assert len(embed.fields) == MAX_EMBED_FIELDS, "26 件目以降の field は送信時 400 になる"
            assert "ほか 5 件" in (embed.description or ""), "打ち切りが利用者に見えていない"
        finally:
            await db.close()

    run(_main())


def test_best_option_is_computed_from_all_options_not_just_shown_ones():
    """最多参加候補は表示から溢れた候補も含めて選ばれること。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, option_count=30)
            # 表示に載る候補（1件目）より、打ち切られる 28 件目の参加を多くする
            await repo.set_vote(G1, "o1", "1", "ok")
            await repo.set_vote(G1, "o28", "1", "ok")
            await repo.set_vote(G1, "o28", "2", "ok")

            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_summary_embed(repo, G1, BOT, schedule, None)

            body = embed.description or ""
            assert "最多参加候補: **10/28 18:00**（2名）" in body, (
                "表示した 25 件だけで最多参加を選んでいる"
            )
        finally:
            await db.close()

    run(_main())
