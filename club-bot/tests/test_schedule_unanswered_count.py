"""投票メッセージの「未回答者数」を催促と揃えることのテスト（G4-12）。

G3-2 で催促（`notify_unanswered`）の母集団は「ロール保持者 − 台帳の退部者」
または「台帳の現役」になったが、`build_option_embed` が出す**未回答者数は
ロール基準のまま**で、しかも**候補単位**で数えていた。
部員が最初に見る数字はこちらなので、実際に DM が飛ぶ相手と食い違う
（`target_role` 未設定なら表示は `-` のまま DM は飛ぶ）。

このファイルが特に固定しているもの:

1. **予定単位で数える。** 候補単位だと「3候補のうち1つに答えた人」が
   未回答として出る
2. **母集団は `select_unanswered_targets`。** ロール未設定でも台帳の現役から
   数える（従来は `-` のまま）。退部者は引く
3. **特定できないときは `-`。0 と混ぜない**（ADR 0021 / 0022）
4. **`build_option_embed` / `build_summary_embed` が `guild_id` を
   明示引数で受け取ること**（ADR 0009 の完了条件2）
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from services import schedule_service as svc
from utils.db import Database
from utils.parser import TZ, to_iso

G1 = 100000000000000001
G2 = 200000000000000002
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


class _Role:
    def __init__(self, role_id: int, member_ids):
        self.id = role_id
        self.name = "主翼班"
        self.members = [SimpleNamespace(id=i, bot=False) for i in member_ids]


class _Guild:
    def __init__(self, roles: dict[int, _Role] | None = None):
        self.id = G1
        self._roles = roles or {}

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_member(self, _user_id: int):
        return None


class _Bot:
    def get_user(self, _user_id):
        return None


async def _seed(
    db: Database, *, guild_id: int = G1, target_role_id: str | None = None, options: int = 2
) -> ScheduleRepository:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        guild_id,
        "sch_1",
        "秋合宿",
        None,
        "部室",
        target_role_id,
        to_iso(DEADLINE),
        "tester",
        "555",
    )
    for n in range(1, options + 1):
        await repo.add_option(
            guild_id,
            f"sch_1_o{n}",
            "sch_1",
            f"候補{n}",
            to_iso(DEADLINE + timedelta(days=n)),
            None,
            None,
        )
    return repo


async def _roster(db: Database, guild_id: int = G1) -> tuple[set[str], set[str]]:
    """名簿の (現役, 退部・休止) を作って返す（cogs/schedule.py と同じ作り方）。"""
    repo = MemberRepository(db)
    for user_id, name in (("1", "たろう"), ("2", "はなこ"), ("3", "やめた人")):
        await repo.upsert_member(guild_id, user_id, name)
    await repo.set_status(guild_id, "3", "alumni")
    active = {str(m["user_id"]) for m in await repo.list_members(guild_id)}
    everyone = {
        str(m["user_id"])
        for m in await repo.list_members(guild_id, active_only=False, include_alumni=True)
    }
    return active, everyone - active


def _field(embed, name: str) -> str:
    return next(f.value for f in embed.fields if f.name.startswith(name))


# =====================================================================
# 1. ADR 0009 の完了条件2
# =====================================================================
def test_the_embed_builders_take_guild_id_explicitly():
    """プロキシ（`repo.for_guild`）を渡す形をやめたこと。"""
    for func in (svc.build_option_embed, svc.build_summary_embed):
        params = list(inspect.signature(func).parameters)
        assert params[0] == "repo"
        assert params[1] == "guild_id", f"{func.__name__} が guild_id を受け取っていない"


def test_the_schedule_cog_no_longer_passes_the_proxy():
    """`cogs/schedule.py` の5箇所からプロキシが消えたこと。"""
    path = os.path.join(os.path.dirname(__file__), "..", "cogs", "schedule.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    assert "self.repo.for_guild(" not in source, "まだ for_guild プロキシを渡している"


# =====================================================================
# 2. 予定単位で数える
# =====================================================================
def test_the_count_is_per_schedule_not_per_option():
    """候補1つだけに答えた人を未回答に数えないこと。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            active, retired = await _roster(db)
            # たろうは候補1にだけ回答。はなこは未回答
            await repo.set_vote(G1, "sch_1_o1", "1", "ok")
            schedule = await repo.get_schedule(G1, "sch_1")

            for option_id in ("sch_1_o1", "sch_1_o2"):
                embed = await svc.build_option_embed(
                    repo,
                    G1,
                    _Bot(),
                    schedule,
                    {"option_id": option_id, "label": "候補"},
                    _Guild(),
                    roster_active_ids=active,
                    roster_retired_ids=retired,
                )
                assert _field(embed, "未回答者数") == "1", (
                    "候補ごとに数えている（候補2でも たろう は回答済み扱いのはず）"
                )
        finally:
            await db.close()

    run(_main())


def test_the_count_uses_the_roster_when_no_role_is_set():
    """従来は `-` のままだった（DM は飛ぶのに画面には出ない）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            active, retired = await _roster(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                _Guild(),
                roster_active_ids=active,
                roster_retired_ids=retired,
            )
            # 現役2名（退部者は除く）が全員未回答
            assert _field(embed, "未回答者数") == "2"
            assert _field(embed, "対象") == "名簿の現役"
        finally:
            await db.close()

    run(_main())


def test_the_count_subtracts_retired_members_from_the_role():
    """G3-2 と同じ規則（積集合にしない）。名簿未登録のロール保持者は残す。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, target_role_id="900")
            active, retired = await _roster(db)
            guild = _Guild({900: _Role(900, [1, 2, 3, 99])})
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                guild,
                roster_active_ids=active,
                roster_retired_ids=retired,
            )
            # ロール保持者4名 − 退部者1名 = 3名（名簿未登録の 99 は残る）
            assert _field(embed, "未回答者数") == "3"
            assert _field(embed, "対象") == "主翼班"
        finally:
            await db.close()

    run(_main())


def test_answers_reduce_the_count():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            active, retired = await _roster(db)
            await repo.set_vote(G1, "sch_1_o1", "1", "ng")
            await repo.set_vote(G1, "sch_1_o2", "2", "maybe")
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                _Guild(),
                roster_active_ids=active,
                roster_retired_ids=retired,
            )
            assert _field(embed, "未回答者数") == "0", "ng・maybe を未回答に数えている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. 特定できないときは `-`（0 と混ぜない）
# =====================================================================
def test_an_unresolvable_role_shows_a_dash_not_zero():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, target_role_id="900")
            active, retired = await _roster(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                _Guild(),  # ロール 900 は存在しない
                roster_active_ids=active,
                roster_retired_ids=retired,
            )
            assert _field(embed, "未回答者数") == "-", "特定できないのに 0 と出している"
        finally:
            await db.close()

    run(_main())


def test_a_role_with_no_members_shows_a_dash():
    """ロールは生きているが保持者が見えない。0 とは主張しない。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, target_role_id="900")
            active, retired = await _roster(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                _Guild({900: _Role(900, [])}),
                roster_active_ids=active,
                roster_retired_ids=retired,
            )
            assert _field(embed, "未回答者数") == "-"
        finally:
            await db.close()

    run(_main())


def test_an_empty_roster_without_a_role_shows_a_dash():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo,
                G1,
                _Bot(),
                schedule,
                {"option_id": "sch_1_o1", "label": "候補"},
                _Guild(),
                roster_active_ids=set(),
                roster_retired_ids=set(),
            )
            assert _field(embed, "未回答者数") == "-"
        finally:
            await db.close()

    run(_main())


def test_without_a_roster_argument_the_count_is_a_dash():
    """名簿を渡さなければ母集団を決められない。**推測で数字を出さない。**"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _roster(db)
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_option_embed(
                repo, G1, _Bot(), schedule, {"option_id": "sch_1_o1", "label": "候補"}, _Guild()
            )
            assert _field(embed, "未回答者数") == "-"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. 催促との一致
# =====================================================================
def test_the_count_matches_what_notify_unanswered_would_target():
    """画面の数字と、実際に DM が飛ぶ人数が一致すること（G4-12 の要点）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, target_role_id="900")
            active, retired = await _roster(db)
            await repo.set_vote(G1, "sch_1_o1", "1", "ok")
            schedule = await repo.get_schedule(G1, "sch_1")
            guild = _Guild({900: _Role(900, [1, 2, 3])})

            shown = await svc.count_unanswered(repo, G1, schedule, guild, active, retired)
            # notify_unanswered が使うのと同じ計算を、ここでも直接行う
            answered = await repo.list_voters_for_schedule(G1, "sch_1")
            targets = svc.select_unanswered_targets(
                role_member_ids={"1", "2", "3"},
                roster_active_ids=active,
                roster_retired_ids=retired,
                answered_ids=answered,
            )
            assert shown == len(targets)
            assert shown == 1, "はなこ（未回答・現役）だけが対象のはず"
        finally:
            await db.close()

    run(_main())


def test_the_count_is_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, guild_id=G1)
            active, retired = await _roster(db, G1)
            await _roster(db, G2)
            # 他ギルドで同じ人が回答しても、こちらの未回答は減らない
            await ScheduleRepository(db).create_schedule(
                G2, "sch_g2", "他大の予定", None, "部室", None, to_iso(DEADLINE), "t", "1"
            )
            await ScheduleRepository(db).add_option(
                G2, "sch_g2_o1", "sch_g2", "候補", to_iso(DEADLINE), None, None
            )
            await ScheduleRepository(db).set_vote(G2, "sch_g2_o1", "1", "ok")

            schedule = await repo.get_schedule(G1, "sch_1")
            assert await svc.count_unanswered(repo, G1, schedule, _Guild(), active, retired) == 2
        finally:
            await db.close()

    run(_main())
