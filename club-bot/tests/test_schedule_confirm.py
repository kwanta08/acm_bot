"""確定日程（`/schedule confirm`）と前日・当日リマインドのテスト（G3-4）。

`finalize_schedule` は集計サマリーを投稿して終わりで、
**「結局いつに決まったのか」がどこにも残らない**。前日・当日のリマインドも無い。

このファイルが特に固定しているもの:

1. **対象外の候補を書けないのは SQL 側の担保**（Cog の if ではない）。
   リポジトリを直接叩いて他予定・他ギルドの候補が入らないことを見る
2. **`reminders_log` に書くのは送れたときだけ。** `exists()` は status を
   見ないので、失敗を同じキーで書くとその日の通知が二度と飛ばない
   （G2-3 が潰した「送っていないのに送信済み」と同じ形）
3. **ループが `cog_load` / `cog_unload` に登録されていること。**
   `hasattr` を見るだけのテストは、`start()` の行を消しても通ってしまう
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
from discord.ext import tasks

from cogs.reminders import CONFIRMED_REMINDER_TYPE, Reminders, phase_for_hour
from cogs.schedule import Schedule
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database
from utils.parser import TZ, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
DAY = datetime(2026, 10, 1, 18, 0, tzinfo=TZ)


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


async def _seed(
    db: Database, guild_id: int = G1, schedule_id: str = "sch_1", target_role_id: str | None = None
) -> ScheduleRepository:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        guild_id,
        schedule_id,
        "秋合宿",
        None,
        "部室",
        target_role_id,
        "2026-09-25T23:59:00+09:00",
        "tester",
        "555",
    )
    await repo.add_option(
        guild_id, f"{schedule_id}_o1", schedule_id, "10/1 18:00", to_iso(DAY), None, None
    )
    await repo.add_option(
        guild_id,
        f"{schedule_id}_o2",
        schedule_id,
        "10/2 18:00",
        to_iso(DAY + timedelta(days=1)),
        None,
        None,
    )
    return repo


# =====================================================================
# 1. リポジトリ（対象外の候補は SQL で弾く）
# =====================================================================
def test_confirming_stores_the_option():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            assert await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1") is True
            row = await repo.get_schedule(G1, "sch_1")
            assert row["confirmed_option_id"] == "sch_1_o1"
        finally:
            await db.close()

    run(_main())


def test_an_option_from_another_schedule_cannot_be_confirmed():
    """Cog を通さずリポジトリを直接叩いても書けないこと。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, schedule_id="sch_2")
            assert await repo.set_confirmed_option(G1, "sch_1", "sch_2_o1") is False
            assert (await repo.get_schedule(G1, "sch_1"))["confirmed_option_id"] is None
        finally:
            await db.close()

    run(_main())


def test_an_option_from_another_guild_cannot_be_confirmed():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, guild_id=G2, schedule_id="sch_g2")
            assert await repo.set_confirmed_option(G1, "sch_1", "sch_g2_o1") is False
            assert (await repo.get_schedule(G1, "sch_1"))["confirmed_option_id"] is None
        finally:
            await db.close()

    run(_main())


def test_a_deleted_schedule_cannot_be_confirmed():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            assert await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1") is False
        finally:
            await db.close()

    run(_main())


def test_unconfirming_clears_it_and_is_a_noop_when_not_confirmed():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            assert await repo.clear_confirmed_option(G1, "sch_1") is False
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            assert await repo.clear_confirmed_option(G1, "sch_1") is True
            assert (await repo.get_schedule(G1, "sch_1"))["confirmed_option_id"] is None
        finally:
            await db.close()

    run(_main())


def test_lists_carry_the_confirmed_datetime():
    """一覧が確定日時を同じ行で返すこと（予定ごとに引かない）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")

            (open_row,) = await repo.list_open_schedules(G1)
            assert open_row["confirmed_start_at"] == to_iso(DAY)

            await repo.close_schedule(G1, "sch_1")
            (closed_row,) = await repo.list_closed_schedules(G1)
            assert closed_row["confirmed_start_at"] == to_iso(DAY), (
                "締切済み一覧に確定日が出ない（通常フローではこちらにしか出ない）"
            )
        finally:
            await db.close()

    run(_main())


def test_confirmed_between_ignores_closed_flag_but_not_deletion():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            start = datetime(2026, 10, 1, 0, 0, tzinfo=TZ)
            end = start + timedelta(days=1)

            # 開催中でも拾う（先に決まることはある）
            assert len(await repo.list_confirmed_between(G1, to_iso(start), to_iso(end))) == 1
            await repo.close_schedule(G1, "sch_1")
            assert len(await repo.list_confirmed_between(G1, to_iso(start), to_iso(end))) == 1
            # 削除済みは拾わない
            await repo.soft_delete_schedule(G1, "sch_1")
            assert await repo.list_confirmed_between(G1, to_iso(start), to_iso(end)) == []
        finally:
            await db.close()

    run(_main())


def test_confirmed_reminders_resume_after_restore():
    """復元すると確定リマインドが再開すること（G3-3 との関係）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            await repo.soft_delete_schedule(G1, "sch_1")
            await repo.restore_schedule(G1, "sch_1")
            start = datetime(2026, 10, 1, 0, 0, tzinfo=TZ)
            assert (
                len(
                    await repo.list_confirmed_between(
                        G1, to_iso(start), to_iso(start + timedelta(days=1))
                    )
                )
                == 1
            )
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. コマンド
# =====================================================================
class _Channel:
    def __init__(self, fail: bool = False):
        self.id = 555
        self.fail = fail
        self.sent: list[dict] = []

    async def send(self, content=None, *, embed=None, **kwargs):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")
        self.sent.append({"content": content, "embed": embed})
        return SimpleNamespace(id=1)


class _Guild:
    def __init__(self, channel=None, role=None):
        self.id = G1
        self._channel = channel
        self._role = role

    def get_channel_or_thread(self, channel_id: int):
        # 実装はスレッドも解決できる get_channel_or_thread を使う
        # （スレッド内で作られた予定は channel_id がスレッド ID になる）
        return self._channel

    def get_role(self, role_id: int):
        return self._role


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
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")


def _cog(db: Database, guild=None) -> Schedule:
    bot = SimpleNamespace(
        db=db,
        guilds=[],
        user=None,
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_channel=lambda _cid: None,
    )
    return Schedule(bot)


def test_confirm_command_announces_and_mentions_the_role():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db, target_role_id="900")
            channel = _Channel()
            role = SimpleNamespace(mention="<@&900>")
            cog = _cog(db, guild=_Guild(channel=channel, role=role))
            interaction = _Interaction()
            await Schedule.confirm.callback(
                cog, interaction, schedule_id="sch_1", option_id="sch_1_o1"
            )

            assert channel.sent, "告知していない"
            assert channel.sent[-1]["content"] == "<@&900>"
            embed = channel.sent[-1]["embed"]
            # 本文は description。title は 100 文字で切られるので日時を入れない
            assert "2026" in (embed.description or ""), "日時が本文に出ていない"
            assert "部室" in (embed.description or "")
            assert "決まりました" in (embed.title or "")
            assert "投票受付中" in interaction.text, "締切前であることを伝えていない"
        finally:
            await db.close()

    run(_main())


def test_confirm_command_rejects_an_option_from_another_schedule():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, schedule_id="sch_2")
            cog = _cog(db, guild=_Guild(channel=_Channel()))
            interaction = _Interaction()
            await Schedule.confirm.callback(
                cog, interaction, schedule_id="sch_1", option_id="sch_2_o1"
            )
            assert (await repo.get_schedule(G1, "sch_1"))["confirmed_option_id"] is None
            assert "この投票のもの" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_unconfirm_command_announces_the_cancellation():
    """既に日付を告知しているので、黙って消さない。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            channel = _Channel()
            cog = _cog(db, guild=_Guild(channel=channel))
            interaction = _Interaction()
            await Schedule.unconfirm.callback(cog, interaction, schedule_id="sch_1")

            assert (await repo.get_schedule(G1, "sch_1"))["confirmed_option_id"] is None
            assert channel.sent, "取り消しを告知していない"
            embed = channel.sent[-1]["embed"]
            assert "取り消し" in (embed.title or "")
            assert "未定に戻りました" in (embed.description or "")
        finally:
            await db.close()

    run(_main())


def test_confirm_does_not_close_the_vote():
    """別コマンドの副作用で公開サマリーを投稿しない（ADR 0024）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            cog = _cog(db, guild=_Guild(channel=_Channel()))
            await Schedule.confirm.callback(
                cog, _Interaction(), schedule_id="sch_1", option_id="sch_1_o1"
            )
            assert (await repo.get_schedule(G1, "sch_1"))["closed_flag"] == 0
        finally:
            await db.close()

    run(_main())


def test_confirm_and_unconfirm_require_l2():
    assert command_required_level(Schedule.confirm) == Level.L2
    assert command_required_level(Schedule.unconfirm) == Level.L2


def test_confirm_autocompletes_options_of_the_selected_schedule():
    """`option_id` の候補が、選ばれた予定のものだけであること。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            await _seed(db, schedule_id="sch_2")
            cog = _cog(db)
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=G1),
                namespace=SimpleNamespace(schedule_id="sch_1"),
            )
            choices = await cog._option_ac(interaction, "")
            assert [c.value for c in choices] == ["sch_1_o1", "sch_1_o2"]
            # 候補名は正規化済みの日時（利用者の生入力ではない）
            assert choices[0].name.startswith("2026/10/01")
        finally:
            await db.close()

    run(_main())


def test_confirm_autocompletes_are_registered():
    """登録し忘れると候補が出ないまま誰も気づかない。

    公開 API の `param.autocomplete` は bool を返すプロパティなので
    `is not None` では常に真になる（G4-13）。名前まで見る。
    """
    for command, param, expected in (
        (Schedule.confirm, "schedule_id", "_schedule_ac_all"),
        (Schedule.confirm, "option_id", "_option_ac"),
        (Schedule.unconfirm, "schedule_id", "_schedule_ac_all"),
    ):
        autocomplete = command._params[param].autocomplete
        assert autocomplete is not None, f"{command.name}.{param} に補完が無い"
        assert autocomplete.__name__ == expected


# =====================================================================
# 3. リマインド
# =====================================================================
class _LogRepo:
    def __init__(self):
        self.rows: list[tuple] = []

    async def exists(self, guild_id, reminder_type, target_id) -> bool:
        return (guild_id, reminder_type, target_id) in {(r[0], r[1], r[2]) for r in self.rows}

    async def add(self, guild_id, reminder_type, target_id, *args) -> int:
        self.rows.append((guild_id, reminder_type, target_id))
        return len(self.rows)


def _reminders(db: Database, guild) -> tuple[Reminders, _LogRepo, list[str]]:
    logged: list[str] = []

    async def _log_to_channel(message, guild_id=None):
        logged.append(message)

    bot = SimpleNamespace(
        db=db,
        guilds=[SimpleNamespace(id=G1)],
        get_guild=lambda gid: guild if guild and guild.id == gid else None,
        get_channel=lambda _cid: None,
        log_to_channel=_log_to_channel,
    )
    cog = Reminders.__new__(Reminders)
    cog.bot = bot
    cog.schedule_repo = ScheduleRepository(db)
    cog.log_repo = _LogRepo()
    return cog, cog.log_repo, logged


def test_reminder_is_sent_the_day_before_and_on_the_day():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db, target_role_id="900")
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            channel = _Channel()
            role = SimpleNamespace(mention="<@&900>")
            cog, _log, _ = _reminders(db, _Guild(channel=channel, role=role))

            eve = datetime(2026, 9, 30, 20, 0, tzinfo=TZ)
            assert await cog.run_confirmed_reminders("eve", eve) == {G1: 1}
            assert "明日" in (channel.sent[-1]["embed"].description or "")
            assert channel.sent[-1]["content"] == "<@&900>"

            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)
            assert await cog.run_confirmed_reminders("day", day) == {G1: 1}
            body = channel.sent[-1]["embed"].description or ""
            assert "本日" in body
            assert "部室" in body, "場所が出ていない"
        finally:
            await db.close()

    run(_main())


def test_the_same_reminder_is_not_sent_twice():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            channel = _Channel()
            cog, _log, _ = _reminders(db, _Guild(channel=channel))
            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)
            await cog.run_confirmed_reminders("day", day)
            await cog.run_confirmed_reminders("day", day)
            assert len(channel.sent) == 1
        finally:
            await db.close()

    run(_main())


def test_a_failed_send_is_retried_next_time():
    """**送れなかった回を送信済みにしない。**

    `RemindersLogRepository.exists()` は status を見ないので、失敗を
    同じキーで書くとその日の通知が二度と飛ばない（G2-3 と同型）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            failing = _Channel(fail=True)
            cog, log_repo, logged = _reminders(db, _Guild(channel=failing))
            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)

            assert await cog.run_confirmed_reminders("day", day) == {}
            assert log_repo.rows == [], "送れていないのに送信済みにしている"
            assert logged, "運用者にも見えていない"

            # 次の実行では送られる
            ok = _Channel()
            cog.bot.get_guild = lambda gid: _Guild(channel=ok) if gid == G1 else None
            assert await cog.run_confirmed_reminders("day", day) == {G1: 1}
            assert len(ok.sent) == 1
        finally:
            await db.close()

    run(_main())


def test_nothing_is_sent_when_no_schedule_is_confirmed():
    """確定が無い日は沈黙する（「本日の予定はありません」を送らない）。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)  # 確定していない
            channel = _Channel()
            cog, _log_repo, logged = _reminders(db, _Guild(channel=channel))
            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)
            assert await cog.run_confirmed_reminders("day", day) == {}
            assert channel.sent == []
            assert logged == []
        finally:
            await db.close()

    run(_main())


def test_a_missing_channel_is_reported_to_the_operator_only():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            cog, log_repo, logged = _reminders(db, _Guild(channel=None))
            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)
            assert await cog.run_confirmed_reminders("day", day) == {}
            assert log_repo.rows == []
            assert logged, "運用者向けの記録が無い"
        finally:
            await db.close()

    run(_main())


def test_one_guild_failure_does_not_stop_the_others():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            channel = _Channel()
            cog, _log, _ = _reminders(db, _Guild(channel=channel))

            broken = SimpleNamespace(id=999)
            cog.bot.guilds = [broken, SimpleNamespace(id=G1)]
            original = cog.schedule_repo.list_confirmed_between

            async def _boom(guild_id, *args):
                if guild_id == 999:
                    raise RuntimeError("boom")
                return await original(guild_id, *args)

            cog.schedule_repo.list_confirmed_between = _boom
            day = datetime(2026, 10, 1, 8, 30, tzinfo=TZ)
            assert await cog.run_confirmed_reminders("day", day) == {G1: 1}
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. ループの登録（挙動で見る）
# =====================================================================
def _loop_names() -> list[str]:
    return [
        name for name in dir(Reminders) if isinstance(getattr(Reminders, name, None), tasks.Loop)
    ]


def test_every_loop_is_started_and_cancelled_by_the_cog():
    """`hasattr` を見るだけでは `start()` の行を消しても通ってしまう。

    実際に cog_load / cog_unload を呼び、全ループが動き出して止まることを見る。
    """

    async def _main():
        cog = Reminders.__new__(Reminders)
        cog.bot = SimpleNamespace(db=None, guilds=[], wait_until_ready=_ready)
        names = _loop_names()
        assert names, "ループを1つも収集できていない"
        try:
            await Reminders.cog_load(cog)
            not_running = [n for n in names if not getattr(cog, n).is_running()]
            assert not not_running, f"cog_load で開始されていないループ: {not_running}"
        finally:
            await Reminders.cog_unload(cog)
        # cancel() はタスクへ取り消しを投げるだけなので、実際に止まるまで1周譲る
        await asyncio.sleep(0)
        still = [n for n in names if getattr(cog, n).is_running()]
        assert not still, f"cog_unload で止まっていないループ: {still}"

    run(_main())


async def _ready():
    return None


def test_the_confirmed_reminder_loop_runs_in_jst():
    """tzinfo を忘れると UTC 起動になる。"""
    times = Reminders.confirmed_schedule_reminders.time
    assert times, "時刻が設定されていない"
    for t in times:
        assert t.tzinfo is TZ


def test_the_reminder_type_is_not_shared_with_other_jobs():
    """既存種別と衝突すると `exists()` が他の通知を殺す。"""
    assert CONFIRMED_REMINDER_TYPE not in {
        "schedule_unanswered",
        "task_due_7days",
        "task_overdue",
        "task_today_label",
        "milestone_alert",
        "todoist_section",
        "todoist_unlinked",
    }


def test_reminders_log_repository_ignores_status():
    """この不変条件が崩れたら、失敗を書かない設計の根拠が変わる。"""

    async def _main():
        db = await _make_db()
        try:
            repo = RemindersLogRepository(db)
            await repo.add(G1, CONFIRMED_REMINDER_TYPE, "confirmed:x", None, None, "failed")
            assert await repo.exists(G1, CONFIRMED_REMINDER_TYPE, "confirmed:x") is True, (
                "exists() が status を見るようになった。失敗を書かない設計を見直すこと"
            )
        finally:
            await db.close()

    run(_main())


def test_a_long_event_name_does_not_swallow_the_date():
    """告知の本文が Embed の title に入っていないこと。

    `utils/embeds._base` は title を100文字で無条件に切る。本文を title へ
    渡すと、イベント名が長いギルドで**日時や場所が黙って消える**
    （このコマンドの目的そのものが落ちる）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            long_title = "あ" * 90
            await repo.create_schedule(
                G1,
                "sch_l",
                long_title,
                None,
                "部室",
                None,
                "2026-09-25T23:59:00+09:00",
                "tester",
                "555",
            )
            await repo.add_option(G1, "sch_l_o1", "sch_l", "10/1", to_iso(DAY), None, None)
            channel = _Channel()
            cog = _cog(db, guild=_Guild(channel=channel))
            await Schedule.confirm.callback(
                cog, _Interaction(), schedule_id="sch_l", option_id="sch_l_o1"
            )
            body = channel.sent[-1]["embed"].description or ""
            assert "2026" in body, "イベント名が長いと日時が消えている"
            assert "部室" in body
        finally:
            await db.close()

    run(_main())


def test_the_executor_is_told_when_the_announcement_fails():
    """告知に失敗したのに「登録しました」だけを返さないこと。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db, guild=_Guild(channel=_Channel(fail=True)))
            interaction = _Interaction()
            await Schedule.confirm.callback(
                cog, interaction, schedule_id="sch_1", option_id="sch_1_o1"
            )
            assert "告知は送れませんでした" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_the_summary_does_not_grow_the_field_count():
    """集計サマリーの field を増やさないこと。

    候補数に上限が無いので、field を1つ増やすと上限25に当たる閾値が
    下がり、候補の多い予定で集計サマリーごと投稿されなくなる
    （finalize_schedule は HTTPException を握り潰すので無言で消える）。
    """

    async def _main():
        from services import schedule_service as svc

        db = await _make_db()
        try:
            repo = await _seed(db)
            scoped = repo.for_guild(G1)
            bot = SimpleNamespace(get_user=lambda _uid: None)

            schedule = await repo.get_schedule(G1, "sch_1")
            before = len((await svc.build_summary_embed(scoped, bot, schedule, None)).fields)

            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            schedule = await repo.get_schedule(G1, "sch_1")
            embed = await svc.build_summary_embed(scoped, bot, schedule, None)
            assert len(embed.fields) == before, "確定日で field が増えている"
            assert "確定した日程" in (embed.description or "")
        finally:
            await db.close()

    run(_main())


def test_confirmed_date_is_shown_in_both_lists():
    """受入基準の中核。**cog 側**が確定日を Embed に出すこと。

    リポジトリが `confirmed_start_at` を返すことは別のテストで見ているが、
    それを表示する側を見るテストが無いと、`schedule_list_value` から
    確定日の行を消しても緑のまま通る。
    締切済み一覧も見るのは、実運用では締切 → 確定の順になり
    `/schedule list-closed` にしか出ないため。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.set_confirmed_option(G1, "sch_1", "sch_1_o1")
            cog = _cog(db, guild=_Guild(channel=_Channel()))

            interaction = _Interaction()
            await Schedule.list_cmd.callback(cog, interaction)
            values = " ".join(f.value or "" for f in interaction.sent[-1]["embed"].fields)
            assert "2026/10/01 18:00" in values, "開催中一覧に確定日が出ていない"

            await repo.close_schedule(G1, "sch_1")
            interaction = _Interaction()
            await Schedule.list_closed_cmd.callback(cog, interaction)
            values = " ".join(f.value or "" for f in interaction.sent[-1]["embed"].fields)
            assert "2026/10/01 18:00" in values, "締切済み一覧に確定日が出ていない"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 7. ループ本体と範囲境界（test-adversary が見つけた穴）
# =====================================================================
def test_the_hour_maps_to_the_right_phase():
    """時刻 → phase の対応づけ。

    テストが `run_confirmed_reminders(phase, ...)` を phase 直指定で
    しか呼んでいないと、ここを反転させても全部緑のまま通る
    （朝に「明日◯◯」、夜に「もう終わった予定」を流す状態）。
    """
    assert phase_for_hour(8) == "day"
    assert phase_for_hour(20) == "eve"
    # ループが発火する2つの時刻が、別々の phase になること
    times = Reminders.confirmed_schedule_reminders.time
    phases = {phase_for_hour(t.hour) for t in times}
    assert phases == {"day", "eve"}, f"2回の発火が同じ phase になっている: {phases}"


def test_the_range_includes_midnight_and_excludes_the_next_day():
    """`start_at` の範囲境界（`>=` / `<`）を SQLite で固定する。

    00:00 ちょうど開始の予定を取りこぼす変更（`>` へ）も、翌日 00:00 を
    巻き込む変更（`<=` へ）も、いまは PG ライブテストでしか見ておらず
    DSN 未設定で skip される。**skip は緑ではない**（G1-0 / G1-9 と同型）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = ScheduleRepository(db)
            start = datetime(2026, 10, 1, 0, 0, tzinfo=TZ)
            await repo.create_schedule(
                G1, "sch_mid", "深夜", None, None, None, to_iso(start), "u1", "555"
            )
            # ちょうど 00:00（含む）と、翌日 00:00（含まない）
            await repo.add_option(G1, "opt_mid", "sch_mid", "10/1", to_iso(start), None, None)
            await repo.create_schedule(
                G1, "sch_next", "翌日", None, None, None, to_iso(start), "u1", "555"
            )
            await repo.add_option(
                G1,
                "opt_next",
                "sch_next",
                "10/2",
                to_iso(start + timedelta(days=1)),
                None,
                None,
            )
            await repo.set_confirmed_option(G1, "sch_mid", "opt_mid")
            await repo.set_confirmed_option(G1, "sch_next", "opt_next")

            rows = await repo.list_confirmed_between(
                G1, to_iso(start), to_iso(start + timedelta(days=1))
            )
            ids = [r["schedule_id"] for r in rows]
            assert ids == ["sch_mid"], f"境界の扱いが違う: {ids}"
        finally:
            await db.close()

    run(_main())


def test_a_successful_announcement_does_not_warn():
    """告知が成功したときに「送れませんでした」を出さないこと。

    失敗を伝える側だけを見ていると、常に警告を出す実装（`_announce_confirmation`
    が常に None を返す形）が緑のまま通る。
    """

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db, guild=_Guild(channel=_Channel()))
            interaction = _Interaction()
            await Schedule.confirm.callback(
                cog, interaction, schedule_id="sch_1", option_id="sch_1_o1"
            )
            assert "告知は送れませんでした" not in interaction.text
        finally:
            await db.close()

    run(_main())
