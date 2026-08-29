"""`/report weekly public:` と週次ダイジェストのテスト（G4-5）。

`/report weekly` は L2 以上・ephemeral 固定で、部員には
「今週サークル全体で何が進んだか」が見えなかった。

**ADR 0023 は覆していない。** 0023 が禁じたのは「遅延が無い週にも
『問題ありません』を送る」こと。こちらは実績の報告で、しかも既定 OFF。
このファイルは、その線引きが実装で保たれていることを固定する:

1. **OFF のギルドには何も送らない**（既存ギルドの通知量が変わらない）
2. **ダイジェストに「遅延はありません」の類を書かない**
   （書いた瞬間に 0023 が却下した「毎週届く定型文」になる）
3. **マイルストーン警告とは別のジョブ・別の reminder_type**（共存する）
4. `/report weekly` と自動投稿が**同じ Embed** を使う
   （別々に組むと同じ「今週」の数字が画面ごとに食い違う）
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

import discord

from cogs.reminders import MILESTONE_ALERT_TYPE, WEEKLY_DIGEST_TYPE, Reminders
from cogs.reports import Reports
from config import DEFAULT_WEEKLY_DIGEST_WEEKDAY, GuildConfig, config
from repositories.layer_session_repository import LayerSessionRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.settings_repository import SettingsRepository
from repositories.task_repository import TaskRepository
from services.weekly_digest_service import (
    count_completed_between,
    last_week_range,
    week_label,
)
from utils.db import Database
from utils.parser import TZ, to_iso

G1 = 100000000000000001
G2 = 200000000000000002

#: 2026-08-31 は月曜。先週は 08-24（月）〜08-30（日）
MONDAY = datetime(2026, 8, 31, 8, 30, tzinfo=TZ)


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


# =====================================================================
# 1. 期間と数え方（純関数）
# =====================================================================
def test_last_week_is_the_previous_monday_to_sunday():
    start, end = last_week_range(MONDAY)
    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 8, 31, 0, 0, tzinfo=TZ)


def test_last_week_does_not_include_today():
    """当日を含めると翌週の集計と二重になる。"""
    _, end = last_week_range(MONDAY)
    assert end <= MONDAY.replace(hour=0, minute=0)


def test_last_week_from_the_middle_of_a_week():
    start, end = last_week_range(datetime(2026, 9, 3, 20, 0, tzinfo=TZ))  # 木曜
    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 8, 31, 0, 0, tzinfo=TZ)


def test_week_label_shows_the_last_day_not_the_exclusive_end():
    start, end = last_week_range(MONDAY)
    assert week_label(start, end) == "8/24〜8/30"


def test_completed_counts_only_the_half_open_range():
    start, end = last_week_range(MONDAY)
    rows = [
        {"completed_at": to_iso(start)},  # 境界は含む
        {"completed_at": to_iso(end)},  # 上端は含まない（今週の分）
        {"completed_at": to_iso(start - timedelta(seconds=1))},  # 先々週
        {"completed_at": to_iso(start + timedelta(days=3))},
    ]
    assert count_completed_between(rows, start, end) == 2


def test_completed_ignores_rows_without_a_timestamp():
    start, end = last_week_range(MONDAY)
    rows = [{"completed_at": None}, {"completed_at": ""}, {"completed_at": "こわれた"}]
    assert count_completed_between(rows, start, end) == 0


# =====================================================================
# 2. 設定（既定 OFF）
# =====================================================================
def test_the_digest_is_off_by_default():
    gconf = GuildConfig(guild_id=G1)
    assert gconf.weekly_digest_enabled is False
    assert gconf.weekly_digest_weekday == DEFAULT_WEEKLY_DIGEST_WEEKDAY == 0


def test_the_digest_settings_are_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "WEEKLY_DIGEST_ENABLED", "1")
            await repo.set(G1, "WEEKLY_DIGEST_WEEKDAY", "4")
            config.clear_guild_cache()
            g1 = await config.for_guild(G1, db=db)
            g2 = await config.for_guild(G2, db=db)
            assert g1.weekly_digest_enabled is True
            assert g1.weekly_digest_weekday == 4
            assert g2.weekly_digest_enabled is False, "他ギルドへ設定が漏れている"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_an_out_of_range_weekday_falls_back_to_the_default():
    """不正値でそのギルドの全コマンドを死なせない（例外を投げない）。"""

    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "WEEKLY_DIGEST_WEEKDAY", "9")
            config.clear_guild_cache()
            gconf = await config.for_guild(G1, db=db)
            assert gconf.weekly_digest_weekday == DEFAULT_WEEKLY_DIGEST_WEEKDAY
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


# =====================================================================
# 3. Embed（/report weekly と共通）
# =====================================================================
async def _seed(db: Database, guild_id: int = G1) -> None:
    tasks = TaskRepository(db)
    task_id = await tasks.create_task(guild_id, "先週やったこと", "501")
    await db.execute(
        "UPDATE tasks SET status = 'done', completed_at = ?"
        " WHERE guild_id = ? AND local_task_id = ?",
        (to_iso(datetime(2026, 8, 26, 15, 0, tzinfo=TZ)), guild_id, task_id),
    )
    await tasks.create_task(guild_id, "まだのタスク", "501", due_date="2026-12-01")

    layers = LayerSessionRepository(db)
    for user, day in (("501", 25), ("502", 26)):
        at = datetime(2026, 8, day, 18, 0, tzinfo=TZ)
        await layers.add_record(guild_id, user, "主桁1", str(day), to_iso(at), to_iso(at), 90)
    # 先週の外（今週）の記録。混ざったら分かる
    outside = datetime(2026, 8, 31, 9, 0, tzinfo=TZ)
    await layers.add_record(guild_id, "503", "主桁1", "99", to_iso(outside), to_iso(outside), 999)


def _reports(db: Database) -> Reports:
    return Reports(SimpleNamespace(db=db, guilds=[], user=None))


def _embed_text(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{f.name}\n{f.value}" for f in embed.fields]
    return "\n".join(parts)


def test_the_weekly_embed_reports_last_week_numbers():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            embed = await _reports(db).build_weekly_embed(G1, now_dt=MONDAY)
            assert embed is not None
            text = _embed_text(embed)
            assert "8/24〜8/30" in text
            assert "完了タスク 1 件" in text
            assert "積層 2 件" in text, "先週以外の記録が混ざっている"
            assert "180 分" in text
            assert "参加 2 人" in text
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_weekly_embed_never_says_nothing_is_wrong():
    """ADR 0023 が却下した「毎週届く定型文」を作らないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            embed = await _reports(db).build_weekly_embed(G1, now_dt=MONDAY)
            text = _embed_text(embed)
            for phrase in ("問題ありません", "遅延はありません", "遅れはありません", "異常なし"):
                assert phrase not in text, f"ダイジェストに定型文「{phrase}」が入っている"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_weekly_embed_is_none_when_nothing_has_started():
    async def _main():
        db = await _make_db()
        try:
            assert await _reports(db).build_weekly_embed(G1, now_dt=MONDAY) is None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_weekly_embed_is_built_when_only_last_week_has_data():
    """未完了0・投票0でも、先週の実績があれば報告する価値がある。"""

    async def _main():
        db = await _make_db()
        try:
            at = datetime(2026, 8, 26, 18, 0, tzinfo=TZ)
            await LayerSessionRepository(db).add_record(
                G1, "501", "主桁1", "1", to_iso(at), to_iso(at), 60
            )
            embed = await _reports(db).build_weekly_embed(G1, now_dt=MONDAY)
            assert embed is not None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_weekly_embed_is_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db, guild_id=G2)
            assert await _reports(db).build_weekly_embed(G1, now_dt=MONDAY) is None
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


# =====================================================================
# 4. コマンドの public 引数
# =====================================================================
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
        self.deferred: list[dict] = []
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, **kwargs):
        self.deferred.append(kwargs)

    async def _send(self, **kwargs):
        self.sent.append(kwargs)


def test_weekly_is_private_by_default():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            interaction = _Interaction()
            await Reports.weekly.callback(_reports(db), interaction)
            assert interaction.deferred[-1]["ephemeral"] is True
            assert interaction.sent[-1]["ephemeral"] is True
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_weekly_can_be_posted_publicly():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            interaction = _Interaction()
            await Reports.weekly.callback(_reports(db), interaction, public=True)
            assert interaction.deferred[-1]["ephemeral"] is False, "defer が ephemeral のまま"
            assert interaction.sent[-1]["ephemeral"] is False
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_empty_state_stays_private_even_with_public():
    """「まだデータがありません」をチャンネルへ流さない。"""

    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Reports.weekly.callback(_reports(db), interaction, public=True)
            assert interaction.sent[-1]["ephemeral"] is True
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


# =====================================================================
# 5. 自動投稿
# =====================================================================
class _Channel:
    def __init__(self, fail: type | None = None):
        self.id = 777
        self.fail = fail
        self.sent: list[dict] = []

    async def send(self, content=None, *, embed=None, **kwargs):
        if self.fail is discord.Forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="no"), "denied")
        if self.fail is discord.HTTPException:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")
        self.sent.append({"content": content, "embed": embed})
        return SimpleNamespace(id=1)


class _Guild:
    def __init__(self, guild_id: int, channel=None):
        self.id = guild_id
        self.name = str(guild_id)
        self._channel = channel

    def get_channel(self, _cid):
        return self._channel

    def get_channel_or_thread(self, _cid):
        return self._channel


class _Bot:
    def __init__(self, db, guilds, reports):
        self.db = db
        self.guilds = guilds
        self._reports = reports
        self.logged: list[tuple] = []

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_channel(self, _cid):
        return None

    def get_cog(self, name: str):
        return self._reports if name == "Reports" else None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


def _reminders(db, guilds, reports=None) -> Reminders:
    return Reminders(_Bot(db, guilds, reports if reports is not None else _reports(db)))


async def _enable(db: Database, guild_id: int = G1, weekday: int | None = None) -> None:
    repo = SettingsRepository(db)
    await repo.set(guild_id, "WEEKLY_DIGEST_ENABLED", "1")
    await repo.set(guild_id, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
    if weekday is not None:
        await repo.set(guild_id, "WEEKLY_DIGEST_WEEKDAY", str(weekday))
    config.clear_guild_cache()


def test_nothing_is_sent_when_the_digest_is_off():
    """**既定 OFF。** 何も設定していないギルドの通知量は変わらない。

    **投稿先チャンネルは設定しておく。** 未設定のままだと「チャンネルが
    無いから送れなかった」でも同じ結果になり、OFF の検査が空振りする
    （実際、この形にする前は `weekly_digest_enabled` の判定を丸ごと
    消しても緑のままだった）。曜日も一致させ、**OFF だけが送信を
    止めている**状態にする。
    """

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await SettingsRepository(db).set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            config.clear_guild_cache()
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {}
            assert channel.sent == [], "OFF のギルドへ送っている"
            assert cog.bot.logged == [], "OFF なのに運用者ログを出している"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_turning_it_on_is_the_only_difference():
    """直前のテストと同じ状態から、ON にするだけで届くこと。

    「OFF だから送られなかった」ことを、対になる ON のケースで裏付ける。
    """

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await SettingsRepository(db).set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            await SettingsRepository(db).set(G1, "WEEKLY_DIGEST_ENABLED", "1")
            config.clear_guild_cache()
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {G1: 1}
            assert len(channel.sent) == 1
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_digest_is_sent_when_enabled():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await _enable(db)
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {G1: 1}
            assert len(channel.sent) == 1
            assert "先週の実績" in _embed_text(channel.sent[0]["embed"])
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_digest_only_fires_on_the_configured_weekday():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await _enable(db, weekday=4)  # 金曜
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {}
            friday = MONDAY + timedelta(days=4)
            assert await cog.run_weekly_digest(friday) == {G1: 1}
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_digest_is_not_repeated_in_the_same_week():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await _enable(db)
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            await cog.run_weekly_digest(MONDAY)
            await cog.run_weekly_digest(MONDAY)
            assert len(channel.sent) == 1, "同じ週に二度送っている"
            assert await RemindersLogRepository(db).exists(
                G1, WEEKLY_DIGEST_TYPE, f"digest:{Reminders.week_key(MONDAY)}"
            )
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_send_failure_is_not_recorded_as_sent():
    """失敗を送信済みにすると、その週は二度と届かない（G2-3 の作法）。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await _enable(db)
            channel = _Channel(fail=discord.HTTPException)
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {}
            assert not await RemindersLogRepository(db).exists(
                G1, WEEKLY_DIGEST_TYPE, f"digest:{Reminders.week_key(MONDAY)}"
            )
            assert cog.bot.logged, "運用者にも見えないまま失敗している"

            channel.fail = None
            assert await cog.run_weekly_digest(MONDAY) == {G1: 1}
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_nothing_is_sent_when_there_is_no_channel():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await SettingsRepository(db).set(G1, "WEEKLY_DIGEST_ENABLED", "1")
            config.clear_guild_cache()
            cog = _reminders(db, [_Guild(G1, None)])
            assert await cog.run_weekly_digest(MONDAY) == {}
            assert cog.bot.logged, "ON なのに届いていないことが運用者に見えない"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_one_guild_failure_does_not_stop_the_others():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db, guild_id=G1)
            await _seed(db, guild_id=G2)
            await _enable(db, G1)
            await _enable(db, G2)
            good = _Channel()
            cog = _reminders(db, [_Guild(G1, _Channel(fail=discord.Forbidden)), _Guild(G2, good)])
            sent = await cog.run_weekly_digest(MONDAY)
            assert sent == {G2: 1}
            assert len(good.sent) == 1
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_digest_does_not_send_for_a_guild_with_no_data():
    async def _main():
        db = await _make_db()
        try:
            await _enable(db)
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel)])
            assert await cog.run_weekly_digest(MONDAY) == {}
            assert channel.sent == []
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


# =====================================================================
# 6. ADR 0023 との共存
# =====================================================================
def test_the_digest_and_the_milestone_alert_are_separate_jobs():
    assert WEEKLY_DIGEST_TYPE != MILESTONE_ALERT_TYPE
    assert hasattr(Reminders, "weekly_digest")
    assert hasattr(Reminders, "weekly_milestone_alert")


def test_the_digest_loop_is_started_and_stopped_with_the_cog():
    """`hasattr` を見るだけでは `start()` の行を消しても通ってしまう。"""
    import inspect

    load = inspect.getsource(Reminders.cog_load)
    unload = inspect.getsource(Reminders.cog_unload)
    assert "self.weekly_digest.start()" in load
    assert "self.weekly_digest.cancel()" in unload
