"""マイルストーンと大会逆算（スキーマ v13 / migrations/012）のテスト。

進捗率は見えても「大会に間に合うのか」が見えなかった。ノードごとに期限を
置き、大会日から逆算した必要ペースと実績ペースを比べて遅延を知らせる。

- progress_milestones が作られ、(guild_id, node_id, name) が一意であること
- v12 相当の既存 DB からマイグレーションで追加され、既存データが壊れないこと
- 大会日はギルド別設定 COMPETITION_DATE で、既定値を持たないこと
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord

from cogs.progress import (
    COMPETITION_DATE_HELP,
    Progress,
    build_countdown_embed,
)
from cogs.reminders import MILESTONE_ALERT_WEEKDAY, Reminders
from config import config
from repositories.progress_repository import ProgressRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.settings_repository import SettingsRepository
from services.milestone_service import (
    SOURCE_LAYER_RECORDS,
    VERDICT_BEHIND,
    VERDICT_DONE,
    VERDICT_ON_TRACK,
    VERDICT_OVERDUE,
    VERDICT_UNKNOWN,
    days_until_competition,
    evaluate_all,
    evaluate_milestone,
    node_pace,
    spar_pace,
)
from services.progress_tree import ProgressNode, build_and_aggregate
from utils.db import SCHEMA_VERSION, Database
from utils.parser import TZ
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
NOW = "2026-08-12 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_schema_version_is_at_least_13():
    assert SCHEMA_VERSION >= 13


def test_fresh_schema_has_milestones_table():
    async def _main():
        db = await _connected_db()
        try:
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(progress_milestones)")}
            assert {
                "milestone_id",
                "guild_id",
                "node_id",
                "name",
                "due_date",
                "created_at",
                "updated_at",
            } <= cols
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_milestone_index_exists():
    async def _main():
        db = await _connected_db()
        try:
            rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")
            assert "idx_progress_milestones_guild_due" in {r["name"] for r in rows}
        finally:
            await db.close()

    run(_main())


def _make_v12_db() -> str:
    """progress_milestones を持たない DB（v12 相当）。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE progress_nodes (
            progress_node_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        INTEGER NOT NULL,
            node_id         TEXT NOT NULL,
            parent_id       TEXT,
            sort_order      REAL NOT NULL DEFAULT 0,
            name            TEXT NOT NULL DEFAULT '',
            assignee        TEXT,
            status          TEXT,
            manual_progress REAL,
            source          TEXT NOT NULL DEFAULT 'manual',
            todoist_task_id TEXT,
            weight          REAL NOT NULL DEFAULT 1,
            target_weight_g REAL,
            actual_weight_g REAL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE (guild_id, node_id)
        );
        PRAGMA user_version = 12;
        """
    )
    conn.execute(
        "INSERT INTO progress_nodes (guild_id, node_id, name, manual_progress,"
        " created_at, updated_at) VALUES (?, 'wing', '主翼', 0.5, ?, ?)",
        (G1, NOW, NOW),
    )
    conn.commit()
    conn.close()
    return path


def test_v12_db_gains_milestones_and_keeps_nodes():
    async def _main():
        db = Database(_make_v12_db())
        await db.connect()
        try:
            rows = await db.fetchall("PRAGMA table_info(progress_milestones)")
            assert rows, "progress_milestones が作られていない"

            node = await db.fetchone("SELECT * FROM progress_nodes WHERE guild_id = ?", (G1,))
            assert node["name"] == "主翼"
            assert node["manual_progress"] == 0.5
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_milestone_migration_is_idempotent():
    async def _main():
        path = _make_v12_db()
        for _ in range(2):
            db = Database(path)
            await db.connect()
            assert await db.fetchall("PRAGMA table_info(progress_milestones)")
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------
def test_add_and_list_milestones_sorted_by_due_date():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-01", NOW)
            await repo.add_milestone(G1, "wing", "設計完了", "2026-08-20", NOW)

            rows = await repo.list_milestones(G1)
            assert [r["name"] for r in rows] == ["設計完了", "接着完了"]
            assert rows[0]["due_date"] == "2026-08-20"
        finally:
            await db.close()

    run(_main())


def test_same_name_on_same_node_updates_due_date():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-01", NOW)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-15", NOW)

            rows = await repo.list_milestones(G1)
            assert len(rows) == 1
            assert rows[0]["due_date"] == "2026-09-15"
        finally:
            await db.close()

    run(_main())


def test_same_name_on_different_nodes_coexists():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-01", NOW)
            await repo.add_milestone(G1, "tail", "接着完了", "2026-09-10", NOW)
            assert len(await repo.list_milestones(G1)) == 2
        finally:
            await db.close()

    run(_main())


def test_remove_milestone():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-01", NOW)

            assert await repo.remove_milestone(G1, "wing", "接着完了") is True
            assert await repo.list_milestones(G1) == []
            # 二度目は False
            assert await repo.remove_milestone(G1, "wing", "接着完了") is False
        finally:
            await db.close()

    run(_main())


def test_milestones_are_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = ProgressRepository(db)
            await repo.add_milestone(G1, "wing", "接着完了", "2026-09-01", NOW)
            await repo.add_milestone(G2, "wing", "接着完了", "2026-10-01", NOW)

            a = await repo.list_milestones(G1)
            b = await repo.list_milestones(G2)
            assert [r["due_date"] for r in a] == ["2026-09-01"]
            assert [r["due_date"] for r in b] == ["2026-10-01"]

            # 片方を消しても他方は残る
            await repo.remove_milestone(G1, "wing", "接着完了")
            assert await repo.list_milestones(G2)
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 大会日（ギルド別設定）
# ---------------------------------------------------------------------
def test_competition_date_has_no_default():
    """大会も日程もサークルごとに違うので既定値を持たない。"""

    async def _main():
        db = await _connected_db()
        try:
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.competition_date is None
        finally:
            config.invalidate_guild(G1)
            await db.close()

    run(_main())


def test_competition_date_is_resolved_per_guild():
    async def _main():
        db = await _connected_db()
        try:
            await SettingsRepository(db).set(G1, "COMPETITION_DATE", "2026-07-25")

            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.competition_date == "2026-07-25"
            other = await config.for_guild(G2, db=db, force_reload=True)
            assert other.competition_date is None
        finally:
            config.invalidate_guild(G1)
            config.invalidate_guild(G2)
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 遅延判定（F4-2）
# ---------------------------------------------------------------------
TODAY = date(2026, 8, 12)


def _dt(*args) -> datetime:
    """タイムゾーン付きの datetime（既存コードと同じ TZ を使う）。"""
    return datetime(*args, tzinfo=TZ)


def _pnode(node_id, progress, created, updated, parent=None, name=None):
    return ProgressNode(
        node_id=node_id,
        parent_id=parent,
        name=name or node_id,
        manual_progress=progress,
        created_at=created,
        updated_at=updated,
    )


def _status(progress, created, updated, due, *, today=TODAY):
    tree = build_and_aggregate([_pnode("wing", progress, created, updated)])
    node = tree.by_id["wing"]
    return evaluate_milestone(node, "接着完了", due, today=today, pace=node_pace(node, today=today))


def test_on_track_when_pace_is_enough():
    """10日で50%進んだノードに、残り30日で残り50% → 間に合う。"""
    st = _status(0.5, "2026-08-02", "2026-08-12", date(2026, 9, 11))
    assert st.verdict == VERDICT_ON_TRACK
    assert st.actual_per_day is not None
    assert abs(st.actual_per_day - 0.05) < 1e-9
    assert st.is_behind is False


def test_behind_when_pace_is_too_slow():
    """30日で10%しか進んでいないノードに、残り10日で残り90% → 遅延。"""
    st = _status(0.1, "2026-07-13", "2026-08-12", date(2026, 8, 22))
    assert st.verdict == VERDICT_BEHIND
    assert st.is_behind is True
    assert st.required_per_day is not None
    assert st.actual_per_day < st.required_per_day


def test_unknown_when_history_is_too_short():
    """作成と最終更新が同日ならペースを出せない（嘘の予測を出さない）。"""
    st = _status(0.3, "2026-08-12", "2026-08-12", date(2026, 9, 1))
    assert st.verdict == VERDICT_UNKNOWN
    assert st.actual_per_day is None
    assert st.reason
    # 必要ペースは分かるので示す
    assert st.required_per_day is not None


def test_unknown_when_timestamps_are_missing():
    st = _status(0.3, "", "", date(2026, 9, 1))
    assert st.verdict == VERDICT_UNKNOWN
    assert "記録されていない" in st.reason


def test_done_regardless_of_due_date():
    st = _status(1.0, "2026-07-01", "2026-08-01", date(2026, 7, 1))
    assert st.verdict == VERDICT_DONE
    assert st.is_behind is False


def test_overdue_when_due_date_has_passed():
    st = _status(0.4, "2026-07-01", "2026-08-01", date(2026, 8, 11))
    assert st.verdict == VERDICT_OVERDUE
    assert st.days_left == -1
    assert st.is_behind is True


def test_due_today_and_incomplete_is_behind():
    """当日で未完なら、ペースに関わらず間に合わない。"""
    st = _status(0.9, "2026-07-01", "2026-08-01", TODAY)
    assert st.verdict == VERDICT_BEHIND
    assert st.days_left == 0


def test_due_today_and_complete_is_done():
    st = _status(1.0, "2026-07-01", "2026-08-01", TODAY)
    assert st.verdict == VERDICT_DONE


def test_zero_progress_with_history_is_behind_not_unknown():
    """一度も進んでいないが期間はある → ペース 0 として遅延判定できる。"""
    st = _status(0.0, "2026-07-01", "2026-08-01", date(2026, 8, 20))
    assert st.verdict == VERDICT_BEHIND
    assert st.actual_per_day == 0.0


def test_aggregated_progress_is_used_for_parent_nodes():
    """親ノードは子から積み上げた集計進捗で判定される。"""
    tree = build_and_aggregate(
        [
            _pnode("airframe", None, "2026-07-01", "2026-08-11"),
            _pnode("wing", 0.6, "2026-07-01", "2026-08-11", parent="airframe"),
            _pnode("tail", 0.4, "2026-07-01", "2026-08-11", parent="airframe"),
        ]
    )
    node = tree.by_id["airframe"]
    st = evaluate_milestone(
        node, "機体完成", date(2026, 9, 1), today=TODAY, pace=node_pace(node, today=TODAY)
    )
    assert abs(st.progress - 0.5) < 1e-9


# ---- 桁巻きの作業記録からのペース ----------------------------------
def test_spar_pace_from_layer_records():
    """10日で5層 → 0.5層/日。目標10層なら 0.05（進捗率/日）。"""
    dates = [
        date(2026, 8, 1),
        date(2026, 8, 3),
        date(2026, 8, 5),
        date(2026, 8, 8),
        date(2026, 8, 11),
    ]
    pace = spar_pace(dates, target_layers=10)
    assert pace.source == SOURCE_LAYER_RECORDS
    assert abs(pace.per_day - 0.05) < 1e-9


def test_spar_pace_needs_two_or_more_days():
    assert spar_pace([date(2026, 8, 1)], 10).per_day is None
    assert spar_pace([], 10).per_day is None
    # 同じ日に2件だけでは速度を出せない
    assert spar_pace([date(2026, 8, 1), date(2026, 8, 1)], 10).per_day is None


def test_spar_pace_requires_target_layers():
    pace = spar_pace([date(2026, 8, 1), date(2026, 8, 5)], 0)
    assert pace.per_day is None
    assert "目標層数" in pace.reason


def test_spar_pace_overrides_node_pace():
    tree = build_and_aggregate([_pnode("spar", 0.5, "2026-08-02", "2026-08-12")])
    milestones = [{"node_id": "spar", "name": "積層完了", "due_date": "2026-09-01"}]
    override = spar_pace([date(2026, 8, 1), date(2026, 8, 11)], 10)

    result = evaluate_all(tree, milestones, today=TODAY, pace_by_node={"spar": override})
    assert result[0].pace_source == SOURCE_LAYER_RECORDS


# ---- 一覧の判定 ----------------------------------------------------
def test_evaluate_all_sorts_by_due_date():
    tree = build_and_aggregate(
        [
            _pnode("wing", 0.5, "2026-08-02", "2026-08-12"),
            _pnode("tail", 0.5, "2026-08-02", "2026-08-12"),
        ]
    )
    result = evaluate_all(
        tree,
        [
            {"node_id": "wing", "name": "後", "due_date": "2026-09-10"},
            {"node_id": "tail", "name": "先", "due_date": "2026-08-20"},
        ],
        today=TODAY,
    )
    assert [s.name for s in result] == ["先", "後"]


def test_evaluate_all_skips_milestones_of_missing_nodes():
    """ノードが消えても行は残る（FK を張っていない）ので表示から外す。"""
    tree = build_and_aggregate([_pnode("wing", 0.5, "2026-08-02", "2026-08-12")])
    result = evaluate_all(
        tree,
        [
            {"node_id": "wing", "name": "生きている", "due_date": "2026-09-01"},
            {"node_id": "deleted", "name": "消えたノード", "due_date": "2026-09-01"},
        ],
        today=TODAY,
    )
    assert [s.name for s in result] == ["生きている"]


def test_evaluate_all_skips_broken_due_dates():
    tree = build_and_aggregate([_pnode("wing", 0.5, "2026-08-02", "2026-08-12")])
    result = evaluate_all(
        tree,
        [
            {"node_id": "wing", "name": "壊れた期限", "due_date": "not-a-date"},
        ],
        today=TODAY,
    )
    assert result == []


def test_most_nodes_are_judgeable_in_a_realistic_tree():
    """現実的なツリーで「判定不能」ばかりにならないこと。

    進捗履歴のテーブルが無いため created_at / updated_at からしか
    ペースを出せない。実運用に近い形（作成から日が経ち、更新もされている）で
    ほとんどのノードが判定できることを確かめる。
    """
    nodes = [_pnode("airframe", None, "2026-06-01", "2026-08-11")]
    milestones = []
    for i in range(6):
        nodes.append(
            _pnode(f"part{i}", 0.1 * (i + 1), "2026-06-01", "2026-08-11", parent="airframe")
        )
        milestones.append({"node_id": f"part{i}", "name": "完了", "due_date": "2026-09-01"})
    result = evaluate_all(build_and_aggregate(nodes), milestones, today=TODAY)

    unknown = [s for s in result if s.verdict == VERDICT_UNKNOWN]
    assert len(result) == 6
    assert not unknown, f"判定不能が出た: {[s.reason for s in unknown]}"


# ---- 大会までの日数 ------------------------------------------------
def test_days_until_competition():
    assert days_until_competition("2026-08-22", TODAY) == 10
    assert days_until_competition("2026-08-12", TODAY) == 0
    assert days_until_competition("2026-08-02", TODAY) == -10


def test_days_until_competition_without_setting():
    assert days_until_competition(None, TODAY) is None
    assert days_until_competition("", TODAY) is None
    assert days_until_competition("いつか", TODAY) is None


# ---------------------------------------------------------------------
# コマンドと表示
# ---------------------------------------------------------------------
class _FakeProgressBot:
    db = None
    todoist_manager = None
    guilds = ()


def _command(qualified: str):
    for cmd in Progress(_FakeProgressBot()).walk_app_commands():
        if cmd.qualified_name == qualified:
            return cmd
    raise AssertionError(f"/{qualified} が見つからない")


def test_milestone_commands_require_expected_levels():
    """設定は班長以上、閲覧は誰でも。"""
    assert command_required_level(_command("milestone add")) == Level.L2
    assert command_required_level(_command("milestone remove")) == Level.L2
    assert command_required_level(_command("milestone list")) == Level.L1
    assert command_required_level(_command("countdown")) == Level.L1


def test_countdown_is_a_top_level_command():
    names = {c.qualified_name for c in Progress(_FakeProgressBot()).walk_app_commands()}
    assert "countdown" in names
    assert {"milestone add", "milestone remove", "milestone list"} <= names


def test_competition_date_help_points_at_the_setting_key():
    """未設定時は設定方法を案内して終わる。"""
    assert "COMPETITION_DATE" in COMPETITION_DATE_HELP
    assert "YYYY-MM-DD" in COMPETITION_DATE_HELP


# ---- Embed --------------------------------------------------------
def _statuses_for_embed():
    tree = build_and_aggregate(
        [
            _pnode("late", 0.1, "2026-07-13", "2026-08-12"),
            _pnode("fine", 0.5, "2026-08-02", "2026-08-12"),
            _pnode("fresh", 0.3, "2026-08-12", "2026-08-12"),
        ]
    )
    return evaluate_all(
        tree,
        [
            {"node_id": "late", "name": "遅れ", "due_date": "2026-08-22"},
            {"node_id": "fine", "name": "余裕", "due_date": "2026-09-11"},
            {"node_id": "fresh", "name": "不明", "due_date": "2026-09-01"},
        ],
        today=TODAY,
    )


def test_countdown_embed_summarises_delays():
    embed = build_countdown_embed("2026-09-30", _statuses_for_embed(), TODAY)
    desc = embed.description or ""
    assert "残り 49 日" in desc
    assert "遅延 **1 件**" in desc
    assert "判定不能 1 件" in desc
    assert len(embed.fields) == 3


def test_countdown_embed_without_milestones_guides_the_user():
    embed = build_countdown_embed("2026-09-30", [], TODAY)
    assert "/milestone add" in (embed.description or "")
    assert embed.fields == []


def test_countdown_embed_handles_competition_day_and_past():
    today_embed = build_countdown_embed("2026-08-12", [], TODAY)
    assert "本日が大会日" in (today_embed.description or "")

    past_embed = build_countdown_embed("2026-08-02", [], TODAY)
    assert "10 日が経過" in (past_embed.description or "")


def test_countdown_embed_fits_discord_limits():
    tree_nodes = []
    milestones = []
    for i in range(40):
        tree_nodes.append(_pnode(f"n{i}", 0.2, "2026-06-01", "2026-08-11"))
        milestones.append({"node_id": f"n{i}", "name": f"節目{i}", "due_date": "2026-09-01"})
    statuses = evaluate_all(build_and_aggregate(tree_nodes), milestones, today=TODAY)
    embed = build_countdown_embed("2026-09-30", statuses, TODAY)
    assert len(embed.fields) <= 25
    assert len(embed) <= 6000
    assert "ほか 15 件" in (embed.description or "")


def test_countdown_embed_explains_unknown_reason():
    embed = build_countdown_embed("2026-09-30", _statuses_for_embed(), TODAY)
    unknown_field = next(f for f in embed.fields if "判定不能" in f.name)
    assert "判定できません" in unknown_field.value


# ---------------------------------------------------------------------
# 週次アラート（F4-3）
# ---------------------------------------------------------------------
class _Channel:
    def __init__(self, channel_id: int, *, fail: bool = False):
        self.id = channel_id
        self.fail = fail
        self.sent: list = []

    async def send(self, **kwargs):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=500), "boom")
        self.sent.append(kwargs)


class _AlertBot:
    def __init__(self, db, channels: dict[int, _Channel]):
        self.db = db
        self._channels = channels
        self.guilds = [SimpleNamespace(id=gid) for gid in channels]
        self.logged: list[str] = []

    def get_channel(self, channel_id):
        for channel in self._channels.values():
            if channel.id == channel_id:
                return channel
        return None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append(message)


def _alert_cog(db, channels):
    cog = Reminders.__new__(Reminders)  # ループを起動せずに組み立てる
    cog.bot = _AlertBot(db, channels)
    cog.log_repo = RemindersLogRepository(db)
    return cog


async def _seed_behind_guild(db, guild_id: int, channel_id: int) -> None:
    """遅延しているマイルストーンを1件持つサーバーを作る。"""
    await SettingsRepository(db).set(guild_id, "PROGRESS_DEFAULT_CHANNEL_ID", str(channel_id))
    repo = ProgressRepository(db)
    await repo.upsert_node(
        guild_id, "wing", name="主翼", manual_progress=0.1, now_text="2026-07-13 10:00"
    )
    await db.execute(
        "UPDATE progress_nodes SET created_at = '2026-07-13 10:00',"
        " updated_at = '2026-08-12 10:00' WHERE guild_id = ?",
        (guild_id,),
    )
    await repo.add_milestone(guild_id, "wing", "接着完了", "2026-08-22", NOW)


def test_weekly_alert_sends_only_to_guilds_with_delays():
    async def _main():
        db = await _connected_db()
        try:
            ch_a, ch_b = _Channel(9001), _Channel(9002)
            await _seed_behind_guild(db, G1, 9001)
            # G2 はマイルストーンを持たない → 沈黙
            await SettingsRepository(db).set(G2, "PROGRESS_DEFAULT_CHANNEL_ID", "9002")

            cog = _alert_cog(db, {G1: ch_a, G2: ch_b})
            sent = await cog.run_milestone_alerts(_dt(2026, 8, 12, 8, 30))

            assert set(sent) == {G1}
            assert len(ch_a.sent) == 1
            assert ch_b.sent == [], "遅延が無いサーバーへは送らない"
        finally:
            config.invalidate_guild(G1)
            config.invalidate_guild(G2)
            await db.close()

    run(_main())


def test_weekly_alert_is_not_sent_twice_in_the_same_week():
    async def _main():
        db = await _connected_db()
        try:
            ch = _Channel(9001)
            await _seed_behind_guild(db, G1, 9001)
            cog = _alert_cog(db, {G1: ch})

            first = await cog.run_milestone_alerts(_dt(2026, 8, 12, 8, 30))
            second = await cog.run_milestone_alerts(_dt(2026, 8, 14, 8, 30))

            assert set(first) == {G1}
            assert second == {}, "同じ週に二度送ってはいけない"
            assert len(ch.sent) == 1
        finally:
            config.invalidate_guild(G1)
            await db.close()

    run(_main())


def test_weekly_alert_sends_again_next_week():
    async def _main():
        db = await _connected_db()
        try:
            ch = _Channel(9001)
            await _seed_behind_guild(db, G1, 9001)
            cog = _alert_cog(db, {G1: ch})

            await cog.run_milestone_alerts(_dt(2026, 8, 12, 8, 30))
            await cog.run_milestone_alerts(_dt(2026, 8, 19, 8, 30))

            assert len(ch.sent) == 2
        finally:
            config.invalidate_guild(G1)
            await db.close()

    run(_main())


def test_send_failure_in_one_guild_does_not_stop_others():
    """送信に失敗したサーバーがあっても、他サーバーへは送られる。"""

    async def _main():
        db = await _connected_db()
        try:
            broken, healthy = _Channel(9001, fail=True), _Channel(9002)
            await _seed_behind_guild(db, G1, 9001)
            await _seed_behind_guild(db, G2, 9002)

            cog = _alert_cog(db, {G1: broken, G2: healthy})
            await cog.run_milestone_alerts(_dt(2026, 8, 12, 8, 30))

            assert len(healthy.sent) == 1
            assert broken.sent == []
        finally:
            config.invalidate_guild(G1)
            config.invalidate_guild(G2)
            await db.close()

    run(_main())


def test_week_key_is_iso_week():
    assert Reminders.week_key(_dt(2026, 8, 12)) == "2026-W33"
    assert Reminders.week_key(_dt(2026, 8, 19)) == "2026-W34"


def test_weekly_alert_loop_is_registered():
    assert hasattr(Reminders, "weekly_milestone_alert")
    assert hasattr(Reminders.weekly_milestone_alert, "cancel")


def test_alert_runs_on_monday_only():
    """月曜以外は何もしない（週次のため）。"""
    assert MILESTONE_ALERT_WEEKDAY == 0
    assert _dt(2026, 8, 12).weekday() == 2  # このテストの基準日は水曜
