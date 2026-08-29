"""積層記録の集計（`/layer stats`）のテスト（G4-1）。

`layer_records` には「誰が・どの桁の・何層目を・何分」が全部入っているのに、
人間が読める形で出すコマンドが無かった。`/progress` に出るのは率だけで、
時間情報（合計・1層あたり平均）はどこにも出ていなかった。

このファイルが特に固定しているもの:

1. **完了層数の数え方が `count_completed_layers` と同じであること**（層番号の
   種類数）。巻き直しを二重に数えると `/progress` の進捗率と食い違い、
   「同じ桁なのに画面ごとに層数が違う」という一番たちの悪い形になる
2. **目標層数が無い桁で「/0」や「0%」を作らないこと**（ADR 0021）。
   紐付けが無い桁は分母が「無い」のであって 0 ではない
3. **期間の境界**（今週＝月曜 0:00 / 今月＝1日 0:00）。集計対象の
   取りこぼしはユーザーからは「記録が消えた」に見える
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.layer_tracking import LayerTracking
from repositories.layer_keta_repository import LayerKetaRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.name_cache_repository import ENTITY_USER, NameCacheRepository
from repositories.progress_repository import ProgressRepository
from services.layer_stats_service import (
    PERIOD_ALL,
    PERIOD_MONTH,
    PERIOD_WEEK,
    aggregate_layer_stats,
    period_start,
)
from utils.db import Database
from utils.parser import TZ, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002


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


def _rec(keta: str, layer: str, user: str, minutes: int, ended: datetime) -> dict:
    return {
        "keta": keta,
        "layer_num": layer,
        "user_id": user,
        "minutes": minutes,
        "ended_at": to_iso(ended),
    }


def _at(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=TZ)


# =====================================================================
# 1. 期間の境界（純関数）
# =====================================================================
def test_week_starts_on_monday_at_midnight():
    # 2026-08-29 は土曜。その週の月曜は 08-24
    start = period_start(PERIOD_WEEK, _at(8, 29, hour=23))
    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=TZ)


def test_week_start_on_monday_itself_is_that_day():
    start = period_start(PERIOD_WEEK, datetime(2026, 8, 24, 9, 30, tzinfo=TZ))
    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=TZ)


def test_month_starts_on_the_first_at_midnight():
    assert period_start(PERIOD_MONTH, _at(8, 29)) == datetime(2026, 8, 1, 0, 0, tzinfo=TZ)


def test_all_period_has_no_start():
    assert period_start(PERIOD_ALL, _at(8, 29)) is None


def test_a_record_exactly_on_the_boundary_is_included():
    """月曜 0:00 ちょうどの記録を落とさないこと（`>` ではなく `>=`）。"""
    monday = datetime(2026, 8, 24, 0, 0, tzinfo=TZ)
    stats = aggregate_layer_stats([_rec("主桁1", "1", "u1", 60, monday)], {}, since=monday)
    assert stats.records == 1


def test_a_record_before_the_boundary_is_excluded():
    monday = datetime(2026, 8, 24, 0, 0, tzinfo=TZ)
    before = datetime(2026, 8, 23, 23, 59, tzinfo=TZ)
    stats = aggregate_layer_stats([_rec("主桁1", "1", "u1", 60, before)], {}, since=monday)
    assert stats.records == 0
    assert stats.ketas == []
    assert stats.members == []


# =====================================================================
# 2. 桁別の集計（純関数）
# =====================================================================
def test_layers_count_distinct_layer_numbers_not_records():
    """巻き直しても1層。`count_completed_layers` と同じ数え方であること。"""
    records = [
        _rec("主桁1", "1", "u1", 60, _at(8, 1)),
        _rec("主桁1", "1", "u2", 30, _at(8, 2)),  # 同じ層を巻き直し
        _rec("主桁1", "2", "u1", 90, _at(8, 3)),
    ]
    (keta,) = aggregate_layer_stats(records, {}).ketas
    assert keta.layers == 2, "巻き直しを二重に数えている"
    assert keta.minutes == 180, "合計時間は記録の全部を足す"


def test_average_minutes_per_layer_uses_completed_layers():
    records = [
        _rec("主桁1", "1", "u1", 60, _at(8, 1)),
        _rec("主桁1", "1", "u2", 30, _at(8, 2)),
        _rec("主桁1", "2", "u1", 90, _at(8, 3)),
    ]
    (keta,) = aggregate_layer_stats(records, {}).ketas
    assert keta.average_minutes == 90  # 180 分 ÷ 2 層


def test_target_layers_come_from_spar_links():
    records = [_rec("主桁1", "1", "u1", 60, _at(8, 1))]
    (keta,) = aggregate_layer_stats(records, {"主桁1": 20}).ketas
    assert keta.target == 20
    assert keta.remaining == 19


def test_a_keta_without_a_spar_link_has_no_target_not_zero():
    """ADR 0021: 分からないものを 0 にしない。"""
    records = [_rec("主桁1", "1", "u1", 60, _at(8, 1))]
    (keta,) = aggregate_layer_stats(records, {}).ketas
    assert keta.target is None
    assert keta.remaining is None
    assert keta.ratio is None


def test_ratio_is_clamped_at_one():
    records = [_rec("主桁1", str(n), "u1", 10, _at(8, 1)) for n in range(1, 6)]
    (keta,) = aggregate_layer_stats(records, {"主桁1": 3}).ketas
    assert keta.ratio == 1.0
    assert keta.remaining == 0


def test_last_worked_at_is_the_newest_record():
    records = [
        _rec("主桁1", "1", "u1", 60, _at(8, 3)),
        _rec("主桁1", "2", "u1", 60, _at(8, 20)),
        _rec("主桁1", "3", "u1", 60, _at(8, 10)),
    ]
    (keta,) = aggregate_layer_stats(records, {}).ketas
    assert keta.last_worked_at == to_iso(_at(8, 20))


def test_ketas_are_sorted_by_name():
    records = [
        _rec("主桁2", "1", "u1", 10, _at(8, 1)),
        _rec("主桁1", "1", "u1", 10, _at(8, 1)),
    ]
    assert [k.keta for k in aggregate_layer_stats(records, {}).ketas] == ["主桁1", "主桁2"]


# =====================================================================
# 3. 人別の集計（純関数）
# =====================================================================
def test_members_are_sorted_by_minutes_descending():
    records = [
        _rec("主桁1", "1", "u1", 30, _at(8, 1)),
        _rec("主桁1", "2", "u2", 120, _at(8, 2)),
        _rec("主桁2", "1", "u3", 60, _at(8, 3)),
    ]
    members = aggregate_layer_stats(records, {}).members
    assert [m.user_id for m in members] == ["u2", "u3", "u1"]
    assert [m.minutes for m in members] == [120, 60, 30]


def test_a_member_layer_count_is_distinct_per_keta_and_layer():
    """同じ桁の同じ層を2回巻いても、その人の層数は1。桁が違えば別の層。"""
    records = [
        _rec("主桁1", "1", "u1", 30, _at(8, 1)),
        _rec("主桁1", "1", "u1", 30, _at(8, 2)),
        _rec("主桁2", "1", "u1", 30, _at(8, 3)),
    ]
    (member,) = aggregate_layer_stats(records, {}).members
    assert member.layers == 2
    assert member.minutes == 90


def test_total_minutes_covers_every_record():
    records = [
        _rec("主桁1", "1", "u1", 30, _at(8, 1)),
        _rec("主桁2", "1", "u2", 45, _at(8, 2)),
    ]
    stats = aggregate_layer_stats(records, {})
    assert stats.total_minutes == 75
    assert stats.records == 2


def test_no_records_produces_empty_stats():
    stats = aggregate_layer_stats([], {"主桁1": 20})
    assert stats.ketas == []
    assert stats.members == []
    assert stats.total_minutes == 0


# =====================================================================
# 4. リポジトリ（ギルド境界と桁の絞り込み）
# =====================================================================
async def _seed_records(db: Database) -> LayerSessionRepository:
    repo = LayerSessionRepository(db)
    await repo.add_record(G1, "u1", "主桁1", "1", to_iso(_at(8, 1, 10)), to_iso(_at(8, 1)), 60)
    await repo.add_record(G1, "u2", "主桁2", "1", to_iso(_at(8, 2, 10)), to_iso(_at(8, 2)), 30)
    await repo.add_record(G2, "u9", "他大の桁", "1", to_iso(_at(8, 3, 10)), to_iso(_at(8, 3)), 99)
    return repo


def test_list_records_is_scoped_to_the_guild():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_records(db)
            rows = await repo.list_records(G1)
            assert {r["keta"] for r in rows} == {"主桁1", "主桁2"}
            assert all(r["minutes"] != 99 for r in rows), "他ギルドの記録が混ざっている"
        finally:
            await db.close()

    run(_main())


def test_list_records_can_filter_by_keta():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_records(db)
            rows = await repo.list_records(G1, keta="主桁1")
            assert [r["keta"] for r in rows] == ["主桁1"]
        finally:
            await db.close()

    run(_main())


def test_list_records_returns_the_columns_the_aggregation_needs():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_records(db)
            (row, _) = await repo.list_records(G1)
            for column in ("user_id", "keta", "layer_num", "minutes", "ended_at"):
                assert column in row, f"{column} が取得列に無い"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 5. コマンド
# =====================================================================
class _Interaction:
    def __init__(self, guild_id: int = G1):
        self.guild = SimpleNamespace(id=guild_id, get_member=lambda _i: None)
        self.user = SimpleNamespace(
            id=501,
            display_name="tester",
            guild=SimpleNamespace(owner_id=501),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        parts = [embed.title or "", embed.description or ""]
        parts += [f"{f.name}\n{f.value}" for f in embed.fields]
        return "\n".join(parts)


def _cog(db: Database) -> LayerTracking:
    return LayerTracking(SimpleNamespace(db=db, guilds=[], user=None))


def test_stats_is_level_1():
    assert command_required_level(LayerTracking.stats) == Level.L1


def test_stats_shows_ketas_members_and_totals():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_records(db)
            await repo.add_record(
                G1, "u1", "主桁1", "2", to_iso(_at(8, 4, 10)), to_iso(_at(8, 4)), 40
            )
            await ProgressRepository(db).upsert_spar_link(
                G1, "主桁1", "node_spar", 20, to_iso(_at(8, 1))
            )
            await NameCacheRepository(db).upsert(
                G1, ENTITY_USER, "u1", "たろう", to_iso(_at(8, 1))
            )

            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta=None, period=PERIOD_ALL)

            text = interaction.text
            assert "主桁1" in text and "主桁2" in text
            assert "2 / 20" in text, "目標層数付きの層数が出ていない"
            assert "たろう" in text, "discord_name_cache で名前を解決していない"
        finally:
            await db.close()

    run(_main())


def test_stats_without_a_spar_link_does_not_invent_a_denominator():
    async def _main():
        db = await _make_db()
        try:
            await _seed_records(db)
            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta="主桁2", period=PERIOD_ALL)
            text = interaction.text
            assert "/ 0" not in text and "0%" not in text, "目標が無い桁に分母を作っている"
        finally:
            await db.close()

    run(_main())


def test_stats_filters_by_keta():
    async def _main():
        db = await _make_db()
        try:
            await _seed_records(db)
            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta="主桁1", period=PERIOD_ALL)
            assert "主桁2" not in interaction.text
        finally:
            await db.close()

    run(_main())


def test_stats_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta=None, period=PERIOD_ALL)
            assert "/layer start" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_stats_does_not_leak_another_guilds_records():
    async def _main():
        db = await _make_db()
        try:
            await _seed_records(db)
            cog = _cog(db)
            interaction = _Interaction(guild_id=G2)
            await LayerTracking.stats.callback(cog, interaction, keta=None, period=PERIOD_ALL)
            text = interaction.text
            assert "他大の桁" in text
            assert "主桁1" not in text and "主桁2" not in text
        finally:
            await db.close()

    run(_main())


def test_stats_period_limits_the_records():
    """「今週」で先月の記録が混ざらないこと。"""

    async def _main():
        db = await _make_db()
        try:
            repo = LayerSessionRepository(db)
            old = _at(1, 5)
            await repo.add_record(G1, "u1", "旧桁", "1", to_iso(old), to_iso(old), 60)
            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta=None, period=PERIOD_WEEK)
            assert "旧桁" not in interaction.text
        finally:
            await db.close()

    run(_main())


def test_keta_autocomplete_is_registered_on_stats():
    """桁引数の補完が `/layer stats` に付いていること（G4-13 と同じ書き方）。"""
    param = LayerTracking.stats._params["keta"]
    assert param.autocomplete is not None
    assert param.autocomplete.__name__ == "_keta_autocomplete"


def test_keta_repo_is_unused_for_stats_when_the_guild_has_none():
    """桁マスタが空でも stats が落ちないこと（記録だけある移行途中のギルド）。"""

    async def _main():
        db = await _make_db()
        try:
            await LayerKetaRepository(db).add(G1, "主桁1", "u1", to_iso(_at(8, 1)))
            await _seed_records(db)
            cog = _cog(db)
            interaction = _Interaction()
            await LayerTracking.stats.callback(cog, interaction, keta=None, period=PERIOD_MONTH)
            assert interaction.sent
        finally:
            await db.close()

    run(_main())
