"""`/layer cancel` と積層セッションの押し忘れ検知のテスト（G4-2）。

`/layer start` したまま帰ると `/layer end` が「1200分」を記録し、
**完了層数が増えるので `/progress` の進捗率まで水増しされる**。
打ち間違えて start した場合の取り消し手段も無かった（`end` するしかなく、
ゴミ行が残る）。

このファイルが特に固定しているもの:

1. **cancel は記録を残さない**（`layer_records` に1行も増えない）。
   ここが `end` と違う唯一の点で、ここが壊れると水増しが直らない
2. **催促と自動取り消しは二重に飛ばない。** 自動取り消しの条件を満たす
   セッションを催促にも入れると、同じ tick で2通届く
3. **DM が拒否された（Forbidden）ときと落ちた（HTTPException）ときで
   `reminders_log` の扱いを変える。** 前者は同じセッションで再試行しない、
   後者は次の tick で再試行する。ここを取り違えると
   「5分ごとに永久に DM を試す」か「一度も届かない」のどちらかになる
4. **1ギルドの失敗が他ギルドを止めない**（gotcha `all-guilds-stop-getting-notifications`）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import discord

from cogs.layer_tracking import LayerTracking
from cogs.reminders import (
    LAYER_AUTO_CANCEL_TYPE,
    LAYER_STALE_ALERT_TYPE,
    Reminders,
)
from config import (
    DEFAULT_LAYER_SESSION_ALERT_MINUTES,
    DEFAULT_LAYER_SESSION_AUTO_CANCEL_MINUTES,
    GuildConfig,
    config,
)
from repositories.layer_keta_repository import LayerKetaRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.settings_repository import SettingsRepository
from services.layer_tracking_service import LayerTrackingService, classify_stale_sessions
from utils.db import Database
from utils.parser import now, to_iso
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


def _session(session_id: int, user_id: str, minutes_ago: int, base=None) -> dict:
    """`base` を渡すとその時刻からちょうど minutes_ago 分前の開始にする。

    渡さないと `now()` を2回呼ぶことになり、境界ちょうどのテストが
    数百ミリ秒の差で 239 分になって落ちる。
    """
    started = (base or now()) - timedelta(minutes=minutes_ago)
    return {
        "session_id": session_id,
        "user_id": user_id,
        "keta": "主桁1",
        "layer_num": "3",
        "started_at": to_iso(started),
    }


# =====================================================================
# 1. 振り分け（純関数）
# =====================================================================
def test_a_fresh_session_is_left_alone():
    current = now()
    alert, cancel = classify_stale_sessions([_session(1, "u1", 30)], current, 240, 720)
    assert alert == [] and cancel == []


def test_a_session_past_the_alert_threshold_is_alerted():
    current = now()
    alert, cancel = classify_stale_sessions([_session(1, "u1", 250)], current, 240, 720)
    assert [s.session_id for s in alert] == [1]
    assert cancel == []
    assert alert[0].elapsed_min >= 240


def test_a_session_past_the_auto_cancel_threshold_is_not_alerted_too():
    """同じ tick で催促と自動取り消しの2通を送らないこと。"""
    current = now()
    alert, cancel = classify_stale_sessions([_session(1, "u1", 800)], current, 240, 720)
    assert alert == [], "自動取り消しの対象を催促にも入れている"
    assert [s.session_id for s in cancel] == [1]


def test_the_boundary_minute_counts():
    """ちょうど閾値の分数で発火すること（`>` にすると5分ずれる）。"""
    current = now()
    alert, _ = classify_stale_sessions(
        [_session(1, "u1", 240, base=current)], current, 240, 720
    )
    assert [s.session_id for s in alert] == [1]


def test_zero_disables_the_alert_but_not_the_auto_cancel():
    current = now()
    alert, cancel = classify_stale_sessions([_session(1, "u1", 800)], current, 0, 720)
    assert alert == []
    assert len(cancel) == 1


def test_zero_disables_the_auto_cancel_but_not_the_alert():
    current = now()
    alert, cancel = classify_stale_sessions([_session(1, "u1", 800)], current, 240, 0)
    assert [s.session_id for s in alert] == [1]
    assert cancel == []


def test_a_broken_started_at_is_skipped_not_raised():
    """壊れた1行でそのギルドの点検全体を落とさない。"""
    current = now()
    broken = {"session_id": 9, "user_id": "u1", "keta": "x", "layer_num": "1", "started_at": "???"}
    alert, cancel = classify_stale_sessions([broken, _session(1, "u2", 800)], current, 240, 720)
    assert [s.session_id for s in cancel] == [1]
    assert alert == []


def test_the_defaults_are_240_and_720():
    assert DEFAULT_LAYER_SESSION_ALERT_MINUTES == 240
    assert DEFAULT_LAYER_SESSION_AUTO_CANCEL_MINUTES == 720
    gconf = GuildConfig(guild_id=G1)
    assert gconf.layer_session_alert_minutes == 240
    assert gconf.layer_session_auto_cancel_minutes == 720


def test_the_thresholds_are_guild_scoped_settings():
    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "LAYER_SESSION_ALERT_MINUTES", "90")
            await repo.set(G1, "LAYER_SESSION_AUTO_CANCEL_MINUTES", "180")
            config.clear_guild_cache()
            g1conf = await config.for_guild(G1, db=db)
            g2conf = await config.for_guild(G2, db=db)
            assert (g1conf.layer_session_alert_minutes, g1conf.layer_session_auto_cancel_minutes) == (
                90,
                180,
            )
            assert g2conf.layer_session_alert_minutes == DEFAULT_LAYER_SESSION_ALERT_MINUTES
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


# =====================================================================
# 2. cancel（記録を残さない）
# =====================================================================
async def _start_session(db: Database, guild_id: int = G1, user_id: str = "501") -> None:
    await LayerKetaRepository(db).add(guild_id, "主桁1", user_id, to_iso(now()))
    svc = LayerTrackingService(LayerSessionRepository(db).for_guild(guild_id))
    await svc.start(user_id, "主桁1", "3")


def test_cancel_removes_the_session_and_writes_no_record():
    async def _main():
        db = await _make_db()
        try:
            await _start_session(db)
            repo = LayerSessionRepository(db)
            svc = LayerTrackingService(repo.for_guild(G1))

            cancelled = await svc.cancel("501")
            assert cancelled is not None
            assert cancelled["keta"] == "主桁1"
            assert await repo.get_by_user(G1, "501") is None
            assert await repo.list_records(G1) == [], "cancel が記録を残している"
        finally:
            await db.close()

    run(_main())


def test_cancel_without_a_session_returns_none():
    async def _main():
        db = await _make_db()
        try:
            svc = LayerTrackingService(LayerSessionRepository(db).for_guild(G1))
            assert await svc.cancel("501") is None
        finally:
            await db.close()

    run(_main())


def test_cancel_does_not_touch_another_guilds_session():
    async def _main():
        db = await _make_db()
        try:
            await _start_session(db, guild_id=G1)
            await _start_session(db, guild_id=G2)
            repo = LayerSessionRepository(db)
            await LayerTrackingService(repo.for_guild(G1)).cancel("501")
            assert await repo.get_by_user(G2, "501") is not None
        finally:
            await db.close()

    run(_main())


class _Interaction:
    def __init__(self, guild_id: int = G1, user_id: int = 501):
        self.guild = SimpleNamespace(id=guild_id, get_member=lambda _i: None)
        self.user = SimpleNamespace(
            id=user_id,
            display_name="tester",
            guild=SimpleNamespace(owner_id=user_id),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
        self.sent: list[dict] = []
        self.response = SimpleNamespace(
            defer=self._noop, send_message=self._send, is_done=lambda: True
        )
        self.followup = SimpleNamespace(send=self._send)

    async def _noop(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")


def test_layer_cancel_is_level_1():
    assert command_required_level(LayerTracking.cancel) == Level.L1


def test_layer_cancel_command_discards_the_session():
    async def _main():
        db = await _make_db()
        try:
            await _start_session(db)
            cog = LayerTracking(SimpleNamespace(db=db, guilds=[], user=None))
            interaction = _Interaction()
            await LayerTracking.cancel.callback(cog, interaction)
            assert "取り消し" in interaction.text
            assert await LayerSessionRepository(db).get_by_user(G1, "501") is None
            assert await LayerSessionRepository(db).list_records(G1) == []
        finally:
            await db.close()

    run(_main())


def test_layer_cancel_command_says_so_when_there_is_nothing_to_cancel():
    async def _main():
        db = await _make_db()
        try:
            cog = LayerTracking(SimpleNamespace(db=db, guilds=[], user=None))
            interaction = _Interaction()
            await LayerTracking.cancel.callback(cog, interaction)
            assert "/layer start" in interaction.text
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. 定期点検（DM と reminders_log）
# =====================================================================
class _Member:
    def __init__(self, user_id: int, fail: type | None = None):
        self.id = user_id
        self.display_name = f"member{user_id}"
        self.fail = fail
        self.dms: list[str] = []

    async def send(self, content=None, **kwargs):
        if self.fail is discord.Forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="blocked"), "no dm")
        if self.fail is discord.HTTPException:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")
        self.dms.append(content)


class _Guild:
    def __init__(self, guild_id: int, members: dict[int, _Member]):
        self.id = guild_id
        self.name = str(guild_id)
        self._members = members

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class _Bot:
    def __init__(self, db, guilds):
        self.db = db
        self.guilds = guilds
        self.logged: list[tuple] = []

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_channel(self, _cid):
        return None

    def get_cog(self, _name):
        return None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


def _reminders(db, guilds) -> Reminders:
    return Reminders(_Bot(db, guilds))


async def _seed_stale(db: Database, minutes_ago: int, guild_id: int = G1, user_id: str = "501"):
    repo = LayerSessionRepository(db)
    await repo.start(
        guild_id, user_id, "主桁1", "3", to_iso(now() - timedelta(minutes=minutes_ago))
    )
    return repo


def test_a_stale_session_gets_one_dm_and_is_not_repeated():
    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 300)
            member = _Member(501)
            cog = _reminders(db, [_Guild(G1, {501: member})])

            await cog._process_layer_sessions(G1)
            assert len(member.dms) == 1
            assert "/layer end" in member.dms[0]

            await cog._process_layer_sessions(G1)
            assert len(member.dms) == 1, "同じセッションに二度催促している"
            # セッションは残る（催促であって取り消しではない）
            assert await LayerSessionRepository(db).get_by_user(G1, "501") is not None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_very_stale_session_is_cancelled_and_the_user_is_told():
    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 800)
            member = _Member(501)
            cog = _reminders(db, [_Guild(G1, {501: member})])

            await cog._process_layer_sessions(G1)
            repo = LayerSessionRepository(db)
            assert await repo.get_by_user(G1, "501") is None, "自動取り消しされていない"
            assert await repo.list_records(G1) == [], "自動取り消しが記録を残している"
            assert len(member.dms) == 1
            assert "取り消し" in member.dms[0]
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_forbidden_dm_is_not_retried_for_the_same_session():
    """DM 拒否は次の tick でも直らない。5分ごとに永久に試さないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 300)
            cog = _reminders(db, [_Guild(G1, {501: _Member(501, fail=discord.Forbidden)})])
            await cog._process_layer_sessions(G1)
            assert await RemindersLogRepository(db).exists(
                G1, LAYER_STALE_ALERT_TYPE, "layer_session:1"
            ), "拒否されたのに記録を残していない（毎tick再試行になる）"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_transient_dm_failure_is_retried():
    """一時障害（HTTPException）は送信済みにしない（G2-3 の作法）。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 300)
            flaky = _Member(501, fail=discord.HTTPException)
            cog = _reminders(db, [_Guild(G1, {501: flaky})])
            await cog._process_layer_sessions(G1)
            assert not await RemindersLogRepository(db).exists(
                G1, LAYER_STALE_ALERT_TYPE, "layer_session:1"
            ), "一時障害を送信済みとして記録している（二度と催促が飛ばない）"

            flaky.fail = None
            await cog._process_layer_sessions(G1)
            assert len(flaky.dms) == 1, "復旧後に再送していない"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_auto_cancel_happens_even_when_the_dm_fails():
    """通知できなくても水増しは止める（cancel が本体、DM は付随）。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 800)
            cog = _reminders(db, [_Guild(G1, {501: _Member(501, fail=discord.Forbidden)})])
            await cog._process_layer_sessions(G1)
            assert await LayerSessionRepository(db).get_by_user(G1, "501") is None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_missing_member_does_not_stop_the_auto_cancel():
    """退部済み・キャッシュ欠落でも取り消しは進む。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 800)
            cog = _reminders(db, [_Guild(G1, {})])
            await cog._process_layer_sessions(G1)
            assert await LayerSessionRepository(db).get_by_user(G1, "501") is None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_only_the_target_guild_is_touched():
    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 800, guild_id=G1)
            await _seed_stale(db, 800, guild_id=G2)
            cog = _reminders(db, [_Guild(G1, {501: _Member(501)}), _Guild(G2, {501: _Member(501)})])
            await cog._process_layer_sessions(G1)
            repo = LayerSessionRepository(db)
            assert await repo.get_by_user(G1, "501") is None
            assert await repo.get_by_user(G2, "501") is not None, "他ギルドを巻き込んでいる"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_thresholds_are_read_per_guild():
    """あるギルドの短い閾値が他ギルドへ漏れないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "LAYER_SESSION_AUTO_CANCEL_MINUTES", "60")
            await _seed_stale(db, 90, guild_id=G1)
            await _seed_stale(db, 90, guild_id=G2)
            config.clear_guild_cache()
            cog = _reminders(db, [_Guild(G1, {501: _Member(501)}), _Guild(G2, {501: _Member(501)})])
            await cog._process_layer_sessions(G1)
            await cog._process_layer_sessions(G2)
            repo = LayerSessionRepository(db)
            assert await repo.get_by_user(G1, "501") is None, "G1 の 60 分設定が効いていない"
            assert await repo.get_by_user(G2, "501") is not None, "G2 に G1 の設定が漏れている"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_tick_isolates_the_layer_job_from_the_schedule_jobs():
    """日程調整側が落ちても積層の点検が走ること（逆も同じ）。"""
    calls: list[str] = []

    async def boom(_guild_id):
        calls.append("schedule")
        raise RuntimeError("DB 接続断")

    async def layer(_guild_id):
        calls.append("layer")

    cog = Reminders.__new__(Reminders)
    cog.bot = SimpleNamespace(guilds=[SimpleNamespace(id=G1, name="g1")])
    cog._process_schedule_reminders = boom
    cog._process_schedule_close = boom
    cog._process_layer_sessions = layer

    run(cog.schedule_tick())
    assert "layer" in calls, "日程調整の失敗で積層の点検まで飛ばしている"


def test_the_reminder_types_are_distinct():
    assert LAYER_STALE_ALERT_TYPE != LAYER_AUTO_CANCEL_TYPE


def test_the_auto_cancel_is_logged_once_per_session():
    async def _main():
        db = await _make_db()
        try:
            await _seed_stale(db, 800)
            member = _Member(501)
            cog = _reminders(db, [_Guild(G1, {501: member})])
            await cog._process_layer_sessions(G1)
            assert await RemindersLogRepository(db).exists(
                G1, LAYER_AUTO_CANCEL_TYPE, "layer_session:1"
            )
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())
