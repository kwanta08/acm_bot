"""進捗の日次スナップショット（スキーマ v18）のテスト（G4-7）。

`progress_nodes` は現在値しか持たないため、実績ペースが
「作成日 → 最終更新日の平均」でしか出せず（ADR 0022）、
**停滞期間を含まない**近似だった。「先週から何%進んだか」も分からない。

このファイルが特に固定しているもの:

1. **1日1行しか書かれないこと。** 20分ごとのループから呼ばれるので、
   ここが緩むと1日72行積まれてペースが壊れる。しかも
   **アプリの if ではなく UNIQUE 制約が保証していること**を見る
   （if を消しても DB が拒否する、が担保）
2. **履歴が足りないうちは予測を出さない**（ADR 0022 の核）。
   `snapshot_pace` が None を返し、従来の推定へフォールバックする
3. **未集計を 0.0 に丸めない**（ADR 0021）
4. **マイグレーションが既存データを動かさないこと**（ADR 0024）
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import pytest

from cogs.progress import Progress
from repositories.progress_repository import ProgressRepository
from services.milestone_service import (
    MIN_SNAPSHOT_SPAN_DAYS,
    MIN_SNAPSHOTS_FOR_PACE,
    SOURCE_NODE,
    SOURCE_SNAPSHOTS,
    recent_gain,
    snapshot_pace,
    sparkline,
)
from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
TODAY = date(2026, 8, 31)


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


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def _snap(offset: int, value: float | None) -> dict:
    return {"snapshot_date": _day(offset), "aggregated": value}


# =====================================================================
# 1. スキーマ
# =====================================================================
def test_schema_version_is_18():
    assert SCHEMA_VERSION >= 18


def test_the_table_is_declared_for_both_drivers():
    for ddl in (TABLE_DDL["progress_snapshots"], TABLE_DDL_PG["progress_snapshots"]):
        assert "progress_snapshots" in ddl
        assert re.search(r"UNIQUE\s*\(guild_id,\s*node_id,\s*snapshot_date\)", ddl), (
            "1日1行を保証する UNIQUE が無い"
        )


def test_the_value_columns_are_nullable():
    """未集計・未計測を 0.0 に丸めない（ADR 0021）。"""
    ddl = TABLE_DDL["progress_snapshots"]
    for column in ("aggregated", "actual_weight_g"):
        m = re.search(rf"{column}\s+REAL([^,\n]*)", ddl)
        assert m, column
        assert "NOT NULL" not in m.group(1), f"{column} が NOT NULL になっている"


def test_a_fresh_db_has_the_table_and_the_version():
    async def _main():
        db = await _make_db()
        try:
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            assert await db.fetchall("SELECT * FROM progress_snapshots") == []
        finally:
            await db.close()

    run(_main())


def test_migrating_an_old_db_adds_an_empty_table_without_touching_data():
    """ADR 0024: 既定値で既存データを動かさない。"""

    async def _main():
        path = _tmp_db_path()
        # v17 相当の DB を作る（progress_snapshots だけ無い状態）
        conn = sqlite3.connect(path)
        try:
            for name, ddl in TABLE_DDL.items():
                if name == "progress_snapshots":
                    continue
                conn.executescript(ddl)
            conn.execute(
                "INSERT INTO progress_nodes"
                " (guild_id, node_id, name, manual_progress, created_at, updated_at)"
                " VALUES (?, 'n1', '主桁', 0.4, '2026-08-01', '2026-08-20')",
                (G1,),
            )
            conn.execute("PRAGMA user_version = 17")
            conn.commit()
        finally:
            conn.close()

        db = Database(path)
        await db.connect()
        try:
            assert (await db.fetchone("PRAGMA user_version"))[0] == SCHEMA_VERSION
            assert await db.fetchall("SELECT * FROM progress_snapshots") == []
            node = await db.fetchone("SELECT * FROM progress_nodes WHERE node_id = 'n1'")
            assert node["manual_progress"] == 0.4, "既存行が書き換わっている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. 1日1行（リポジトリ）
# =====================================================================
def test_a_second_write_on_the_same_day_is_ignored():
    async def _main():
        db = await _make_db()
        try:
            repo = ProgressRepository(db)
            rows = [{"node_id": "n1", "aggregated": 0.2, "actual_weight_g": None}]
            assert await repo.save_snapshots(G1, _day(0), rows) == 1
            # **値を変えて**もう一度。UNIQUE が拒否するので増えない
            rows2 = [{"node_id": "n1", "aggregated": 0.9, "actual_weight_g": 100.0}]
            assert await repo.save_snapshots(G1, _day(0), rows2) == 0

            stored = await repo.list_snapshots(G1, "n1")
            assert len(stored) == 1, "同じ日に2行入っている"
            assert stored[0]["aggregated"] == 0.2, "その日の最初の値が上書きされている"
        finally:
            await db.close()

    run(_main())


def test_the_unique_constraint_is_what_enforces_one_row_a_day():
    """**アプリの if ではなく DB が拒否すること。**

    生の INSERT を直接叩いて、制約側で弾かれることを見る。
    """

    async def _main():
        db = await _make_db()
        try:
            sql = (
                "INSERT INTO progress_snapshots"
                " (guild_id, node_id, snapshot_date, aggregated, actual_weight_g)"
                " VALUES (?, ?, ?, ?, ?)"
            )
            await db.execute(sql, (G1, "n1", _day(0), 0.2, None))
            with pytest.raises(sqlite3.IntegrityError):
                await db.execute(sql, (G1, "n1", _day(0), 0.9, None))
        finally:
            await db.close()

    run(_main())


def test_different_days_and_nodes_and_guilds_coexist():
    async def _main():
        db = await _make_db()
        try:
            repo = ProgressRepository(db)
            await repo.save_snapshots(G1, _day(0), [{"node_id": "n1", "aggregated": 0.2}])
            await repo.save_snapshots(G1, _day(-1), [{"node_id": "n1", "aggregated": 0.1}])
            await repo.save_snapshots(G1, _day(0), [{"node_id": "n2", "aggregated": 0.5}])
            await repo.save_snapshots(G2, _day(0), [{"node_id": "n1", "aggregated": 0.9}])

            assert len(await repo.list_snapshots(G1, "n1")) == 2
            assert [r["aggregated"] for r in await repo.list_snapshots(G2, "n1")] == [0.9]
        finally:
            await db.close()

    run(_main())


def test_has_snapshot_is_scoped_to_the_guild_and_day():
    async def _main():
        db = await _make_db()
        try:
            repo = ProgressRepository(db)
            await repo.save_snapshots(G1, _day(0), [{"node_id": "n1", "aggregated": 0.2}])
            assert await repo.has_snapshot(G1, _day(0)) is True
            assert await repo.has_snapshot(G1, _day(-1)) is False
            assert await repo.has_snapshot(G2, _day(0)) is False
        finally:
            await db.close()

    run(_main())


def test_unmeasured_values_stay_null():
    async def _main():
        db = await _make_db()
        try:
            repo = ProgressRepository(db)
            await repo.save_snapshots(
                G1, _day(0), [{"node_id": "n1", "aggregated": None, "actual_weight_g": None}]
            )
            (row,) = await repo.list_snapshots(G1, "n1")
            assert row["aggregated"] is None, "未集計を 0.0 に丸めている"
            assert row["actual_weight_g"] is None
        finally:
            await db.close()

    run(_main())


def test_list_snapshots_can_limit_the_window():
    async def _main():
        db = await _make_db()
        try:
            repo = ProgressRepository(db)
            for offset in (-30, -10, -1):
                await repo.save_snapshots(
                    G1, _day(offset), [{"node_id": "n1", "aggregated": 0.1}]
                )
            rows = await repo.list_snapshots(G1, "n1", since_date=_day(-15))
            assert [r["snapshot_date"] for r in rows] == [_day(-10), _day(-1)]
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. ペース（純関数・ADR 0022 の核）
# =====================================================================
def test_pace_is_unknown_until_enough_history_has_piled_up():
    """**溜まるまでは予測を出さない。** 呼び出し側が従来の推定へ落ちる。"""
    few = [_snap(-i, 0.1 * i) for i in range(MIN_SNAPSHOTS_FOR_PACE - 1)]
    pace = snapshot_pace(few)
    assert pace.per_day is None
    assert "足りない" in pace.reason


def test_pace_is_unknown_with_too_few_points_even_when_they_are_far_apart():
    """**件数の条件だけを切り出して見る。**

    2点が離れていれば期間の条件は満たすので、ここが緑なのは
    件数の下限が効いているからだけ。2点は「たまたま動いた1回」と
    区別できない（前のテストは期間の条件でも通ってしまい、
    件数の下限を 2 に下げる改変を素通りさせていた）。
    """
    two_far_apart = [_snap(-30, 0.1), _snap(0, 0.6)]
    assert len(two_far_apart) < MIN_SNAPSHOTS_FOR_PACE
    assert snapshot_pace(two_far_apart).per_day is None


def test_pace_is_unknown_when_the_span_is_too_short():
    """**期間の条件だけを切り出して見る**（件数は足りている）。"""
    same_week = [_snap(-i, 0.1 * i) for i in range(MIN_SNAPSHOT_SPAN_DAYS)]
    assert len(same_week) >= MIN_SNAPSHOTS_FOR_PACE
    assert snapshot_pace(same_week).per_day is None


def test_pace_is_the_gain_over_the_measured_span():
    rows = [_snap(-10, 0.2), _snap(-5, 0.4), _snap(0, 0.7)]
    pace = snapshot_pace(rows)
    assert pace.source == SOURCE_SNAPSHOTS
    assert pace.per_day == pytest.approx(0.5 / 10)


def test_a_flat_period_is_a_measured_zero_not_unknown():
    """まったく進んでいないのは実測値。判定不能にしない。"""
    rows = [_snap(-10, 0.3), _snap(-5, 0.3), _snap(0, 0.3)]
    pace = snapshot_pace(rows)
    assert pace.per_day == 0.0
    assert pace.source == SOURCE_SNAPSHOTS


def test_unmeasured_rows_are_ignored_not_counted_as_zero():
    rows = [_snap(-10, None), _snap(-8, 0.2), _snap(-4, 0.3), _snap(0, 0.5)]
    pace = snapshot_pace(rows)
    assert pace.per_day == pytest.approx(0.3 / 8), "None を 0.0 として数えている"


def test_recent_gain_needs_two_points():
    assert recent_gain([_snap(0, 0.5)], 7, TODAY) is None
    assert recent_gain([], 7, TODAY) is None


def test_recent_gain_compares_against_the_newest_point_outside_the_window():
    rows = [_snap(-30, 0.1), _snap(-8, 0.4), _snap(-3, 0.5), _snap(0, 0.6)]
    assert recent_gain(rows, 7, TODAY) == pytest.approx(0.2)


def test_recent_gain_falls_back_to_the_oldest_point_when_the_window_covers_everything():
    rows = [_snap(-3, 0.4), _snap(0, 0.6)]
    assert recent_gain(rows, 7, TODAY) == pytest.approx(0.2)


def test_recent_gain_is_none_when_every_point_is_on_the_same_day():
    rows = [{"snapshot_date": _day(0), "aggregated": 0.4}, {"snapshot_date": _day(0), "aggregated": 0.6}]
    assert recent_gain(rows, 7, TODAY) is None


# =====================================================================
# 4. スパークライン
# =====================================================================
def test_sparkline_maps_the_full_range():
    assert sparkline([0.0]) == "▁"
    assert sparkline([1.0]) == "█"
    assert len(sparkline([0.0, 0.5, 1.0])) == 3


def test_sparkline_shows_a_blank_for_unmeasured_values():
    """0% と「測っていない」を同じ字にしない。"""
    assert sparkline([None]) == " "
    assert sparkline([0.0]) != sparkline([None])


def test_sparkline_is_not_normalised_per_series():
    """5%→6% が満杯のグラフに見えないこと。"""
    assert sparkline([0.05, 0.06]) == "▁▁"


# =====================================================================
# 5. 書き込み（Cog）
# =====================================================================
class _Bot:
    def __init__(self, db):
        self.db = db
        self.guilds = []
        self.todoist_manager = None
        self.logged: list = []

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


def _cog(db: Database) -> Progress:
    return Progress(_Bot(db))


async def _seed_tree(db: Database, guild_id: int = G1) -> None:
    repo = ProgressRepository(db)
    await repo.upsert_node(
        guild_id, "root", name="1号機", now_text="2026-08-01", manual_progress=None
    )
    await repo.upsert_node(
        guild_id,
        "n1",
        parent_id="root",
        name="主桁",
        manual_progress=0.4,
        now_text="2026-08-01",
    )


def test_the_daily_snapshot_is_written_once_per_day():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            cog = _cog(db)
            first = await cog.save_daily_snapshot(G1, today=TODAY)
            assert first >= 1
            assert await cog.save_daily_snapshot(G1, today=TODAY) == 0, "同じ日に2回書いている"
            assert await cog.save_daily_snapshot(G1, today=TODAY + timedelta(days=1)) >= 1
        finally:
            await db.close()

    run(_main())


def test_the_second_call_of_the_day_does_not_even_read_the_tree():
    """`has_snapshot` の早期 return が効いていること。

    UNIQUE 制約があるので**消しても行は増えない**（正しさは守られる）が、
    20分ごとに全ノードぶんのツリー読み込みと INSERT を空振りさせる。
    「制約が守るから if は不要」で消される種類のコードなので、
    効率の側面をテストで固定しておく。
    """

    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            cog = _cog(db)
            await cog.save_daily_snapshot(G1, today=TODAY)

            loads = []
            original = cog.load_tree

            async def _spy(guild_id):
                loads.append(guild_id)
                return await original(guild_id)

            cog.load_tree = _spy
            assert await cog.save_daily_snapshot(G1, today=TODAY) == 0
            assert loads == [], "その日の分があるのにツリーを読み直している"
        finally:
            await db.close()

    run(_main())


def test_the_snapshot_records_every_node_of_the_tree():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            await _cog(db).save_daily_snapshot(G1, today=TODAY)
            repo = ProgressRepository(db)
            assert len(await repo.list_snapshots(G1, "n1")) == 1
            assert len(await repo.list_snapshots(G1, "root")) == 1
        finally:
            await db.close()

    run(_main())


def test_the_snapshot_is_scoped_to_the_guild():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db, guild_id=G1)
            await _seed_tree(db, guild_id=G2)
            await _cog(db).save_daily_snapshot(G1, today=TODAY)
            repo = ProgressRepository(db)
            assert await repo.list_snapshots(G1, "n1") != []
            assert await repo.list_snapshots(G2, "n1") == [], "他ギルドまで書いている"
        finally:
            await db.close()

    run(_main())


def test_an_empty_tree_writes_nothing():
    async def _main():
        db = await _make_db()
        try:
            assert await _cog(db).save_daily_snapshot(G1, today=TODAY) == 0
        finally:
            await db.close()

    run(_main())


def test_the_sync_loop_saves_snapshots():
    """20分ループから呼ばれていること（`hasattr` では担保にならない）。"""
    import inspect

    source = inspect.getsource(Progress.periodic_sync.coro)
    assert "save_daily_snapshot" in source


def test_the_snapshot_is_attempted_even_when_the_sync_reported_errors():
    """同期が失敗したギルドで履歴だけ抜けると、あとからペースが狂う。"""
    import inspect

    source = inspect.getsource(Progress.periodic_sync.coro)
    save_at = source.index("save_daily_snapshot")
    skip_at = source.index("if not result.errors:")
    assert save_at < skip_at, "errors の continue より後で保存している"


# =====================================================================
# 6. ペースの優先順位（Cog）
# =====================================================================
def test_snapshot_pace_overrides_the_node_estimate_once_it_is_available():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            repo = ProgressRepository(db)
            for offset, value in ((-10, 0.1), (-5, 0.25), (0, 0.4)):
                await repo.save_snapshots(
                    G1, _day(offset), [{"node_id": "n1", "aggregated": value}]
                )
            overrides = await _cog(db).pace_overrides(G1)
            assert "n1" in overrides
            assert overrides["n1"].source == SOURCE_SNAPSHOTS
            assert overrides["n1"].per_day == pytest.approx(0.3 / 10)
        finally:
            await db.close()

    run(_main())


def test_a_node_without_enough_history_is_left_to_the_old_estimate():
    """**ADR 0022 の核。** 溜まっていないノードには何も主張しない。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            await ProgressRepository(db).save_snapshots(
                G1, _day(0), [{"node_id": "n1", "aggregated": 0.4}]
            )
            overrides = await _cog(db).pace_overrides(G1)
            assert "n1" not in overrides, "1点しかないのにペースを主張している"
        finally:
            await db.close()

    run(_main())


def test_the_old_node_estimate_still_works_when_there_is_no_history():
    """フォールバック先が生きていること（従来の挙動を壊していない）。"""
    from services.milestone_service import node_pace
    from services.progress_tree import ProgressNode

    node = ProgressNode(
        node_id="n1", name="主桁", created_at="2026-08-01", updated_at="2026-08-21"
    )
    node.aggregated = 0.4
    pace = node_pace(node, today=TODAY)
    assert pace.per_day == pytest.approx(0.4 / 20)
    assert pace.source == SOURCE_NODE


# =====================================================================
# 7. コマンド
# =====================================================================
class _Interaction:
    def __init__(self, guild_id: int = G1):
        self.guild = SimpleNamespace(id=guild_id)
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
        parts = [embed.title or "", embed.description or ""]
        parts += [f"{f.name}\n{f.value}" for f in embed.fields]
        return "\n".join(parts)


def test_history_is_level_1():
    assert command_required_level(Progress.progress_history) == Level.L1


def test_history_shows_a_sparkline_and_the_recent_gain():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            repo = ProgressRepository(db)
            for offset, value in ((-20, 0.1), (-10, 0.3), (-3, 0.5), (0, 0.6)):
                await repo.save_snapshots(
                    G1, _day(offset), [{"node_id": "n1", "aggregated": value}]
                )
            interaction = _Interaction()
            await Progress.progress_history.callback(_cog(db), interaction, node="n1", days=60)
            text = interaction.text
            assert "主桁" in text
            assert any(ch in text for ch in "▁▂▃▄▅▆▇█"), "スパークラインが出ていない"
            assert "10% → 60%" in text
            assert "直近7日の伸び" in text
        finally:
            await db.close()

    run(_main())


def test_history_does_not_claim_a_zero_gain_without_history():
    """記録が1点しか無いときに「+0.0 ポイント」と書かないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            await ProgressRepository(db).save_snapshots(
                G1, _day(0), [{"node_id": "n1", "aggregated": 0.4}]
            )
            interaction = _Interaction()
            await Progress.progress_history.callback(_cog(db), interaction, node="n1", days=60)
            text = interaction.text
            assert "比較できる記録がまだありません" in text
            assert "+0.0" not in text
        finally:
            await db.close()

    run(_main())


def test_history_says_so_when_nothing_has_been_recorded_yet():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            interaction = _Interaction()
            await Progress.progress_history.callback(_cog(db), interaction, node=None, days=60)
            assert "まだ履歴がありません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_history_rejects_an_unknown_node():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db)
            interaction = _Interaction()
            await Progress.progress_history.callback(
                _cog(db), interaction, node="nope", days=60
            )
            assert "見つかりません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_history_does_not_leak_another_guilds_snapshots():
    async def _main():
        db = await _make_db()
        try:
            await _seed_tree(db, guild_id=G1)
            await _seed_tree(db, guild_id=G2)
            repo = ProgressRepository(db)
            for offset, value in ((-10, 0.1), (-5, 0.5), (0, 0.9)):
                await repo.save_snapshots(
                    G2, _day(offset), [{"node_id": "n1", "aggregated": value}]
                )
            interaction = _Interaction(guild_id=G1)
            await Progress.progress_history.callback(_cog(db), interaction, node="n1", days=60)
            assert "この期間の記録はまだありません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_node_autocomplete_is_registered_on_history():
    param = Progress.progress_history._params["node"]
    assert param.autocomplete is not None
    assert param.autocomplete.__name__ == "_node_autocomplete"
