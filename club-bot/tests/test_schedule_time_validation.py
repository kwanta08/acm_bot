"""過去の締切・過去の候補日・締切より前の候補を弾く（H1-5）。

以前は `/schedule create deadline:2025-07-02`（去年の日付。打ち間違いで最も多い形）が
通り、作成成功の緑 Embed が出て、5分以内に自動締切され 0 票で終わっていた。

- 判定は `services/schedule_service.py` の純粋関数（`now` はキーワード専用の引数）。
  問題は `TimeProblem` で構造化して返すので、テストは部分文字列ではなく等値で見る
- コマンドは判定を**投稿先・権限の検査より前、DB へ書く前**に行う
- 完全な日付（`YYYY-MM-DD`）を勝手に翌年へ送らない（エラーにして打ち直させる）

テストは実際の時計のまま、**2025 年（常に過去）と 2099 年（未来）の完全な日付**で組む
（`cogs.schedule.now` をモジュールへの差し替えで固定しない。フルセットでだけ赤くなる型を避ける）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

# /schedule create のフェイクは H1-2 のものを使う（Bot 自身の権限の問い合わせを記録する）
from test_schedule_create_failure import G1, NO_SEND, _Channel, _count, _env, _Interaction

from cogs.schedule import Schedule
from repositories.schedule_repository import ScheduleRepository
from services.schedule_service import (
    MAX_TIME_PROBLEMS_SHOWN,
    OPTION_BEFORE_DEADLINE,
    PAST_DEADLINE,
    PAST_OPTION,
    TimeProblem,
    deadline_problem,
    format_time_problems,
    schedule_time_problems,
)
from utils.parser import TZ, parse_datetime, parse_deadline, to_iso

NOW = datetime(2026, 9, 24, 16, 0, tzinfo=TZ)
CODE = "INVALID_SCHEDULE_TIME"


def run(coro):
    return asyncio.run(coro)


def _at(text: str) -> datetime:
    return parse_datetime(text)


# =====================================================================
# 1. 純粋関数
# =====================================================================
def test_no_problem_when_everything_is_in_the_future_and_after_the_deadline():
    deadline = _at("2026-10-01 12:00")
    options = [("2026-10-02", _at("2026-10-02")), ("2026-10-03 18:00", _at("2026-10-03 18:00"))]
    assert schedule_time_problems(deadline, "2026-10-01 12:00", options, now=NOW) == []


def test_past_deadline():
    deadline = _at("2025-07-02 23:59")
    assert deadline_problem(deadline, "2025-07-02", now=NOW) == TimeProblem(
        PAST_DEADLINE, "2025-07-02", deadline
    )
    problems = schedule_time_problems(deadline, "2025-07-02", [], now=NOW)
    assert problems == [TimeProblem(PAST_DEADLINE, "2025-07-02", deadline)]


def test_deadline_equal_to_now_is_past():
    assert deadline_problem(NOW, "now", now=NOW) == TimeProblem(PAST_DEADLINE, "now", NOW)


def test_future_deadline_has_no_problem():
    assert deadline_problem(_at("2026-09-24 16:01"), "x", now=NOW) is None


def test_past_option_is_reported_only_as_past():
    """締切が未来なら、過去の候補は「締切より前」にも当たる。**過去としてだけ**挙げる。"""
    deadline = _at("2026-10-01 12:00")
    past = _at("2025-07-03")
    options = [("2025-07-03", past), ("2026-10-02", _at("2026-10-02"))]
    assert schedule_time_problems(deadline, "d", options, now=NOW) == [
        TimeProblem(PAST_OPTION, "2025-07-03", past)
    ]


def test_option_equal_to_now_is_past():
    deadline = _at("2026-09-24 12:00")  # 締切も過去（締切の問題も同時に出る）
    problems = schedule_time_problems(deadline, "d", [("now", NOW)], now=NOW)
    assert TimeProblem(PAST_OPTION, "now", NOW) in problems


def test_option_before_deadline():
    deadline = _at("2026-10-10 12:00")
    early = _at("2026-10-05 18:00")
    options = [("2026-10-05 18:00", early), ("2026-10-11", _at("2026-10-11"))]
    assert schedule_time_problems(deadline, "d", options, now=NOW) == [
        TimeProblem(OPTION_BEFORE_DEADLINE, "2026-10-05 18:00", early)
    ]


def test_option_at_the_deadline_is_allowed():
    """「締切＝集合時刻」の運用があるので、締切と同時刻の候補は許可する。"""
    deadline = _at("2026-10-10 18:00")
    options = [("2026-10-10 18:00", _at("2026-10-10 18:00"))]
    assert schedule_time_problems(deadline, "d", options, now=NOW) == []


def test_date_only_on_the_same_day_is_before_the_deadline_and_explained():
    """日付だけの締切は 23:59、日付だけの候補は 00:00 なので、同日は締切より前。

    書式化された日時ではなく、**理由の説明文そのもの**が文面に入ることを見る。
    """
    deadline = parse_deadline("2026-10-10")
    option = _at("2026-10-10")
    problems = schedule_time_problems(deadline, "2026-10-10", [("2026-10-10", option)], now=NOW)
    assert problems == [TimeProblem(OPTION_BEFORE_DEADLINE, "2026-10-10", option)]
    text = format_time_problems(problems, deadline, now=NOW)
    assert "日付だけの締切はその日の 23:59" in text
    assert "日付だけの候補はその日の 00:00" in text


def test_format_limits_each_kind_and_notes_the_rest():
    """上限は**種別ごと**に MAX_TIME_PROBLEMS_SHOWN 件。超えた分は「…ほか N 件」。"""
    deadline = _at("2026-12-31 12:00")
    options = [(f"2026-11-{d:02d}", _at(f"2026-11-{d:02d}")) for d in range(1, 13)]
    problems = schedule_time_problems(deadline, "d", options, now=NOW)
    assert len(problems) == 12
    text = format_time_problems(problems, deadline, now=NOW)
    assert "…ほか 2 件" in text
    shown = [o[0] for o in options if f"「{o[0]}」" in text]
    assert len(shown) == MAX_TIME_PROBLEMS_SHOWN


def test_format_says_what_how_and_how_to_fix():
    deadline = _at("2025-07-02 23:59")
    text = format_time_problems(
        [TimeProblem(PAST_DEADLINE, "2025-07-02", deadline)], deadline, now=NOW
    )
    assert "締切" in text and "2025/07/02 23:59" in text and "過去" in text
    assert "YYYY-MM-DD HH:MM" in text
    assert "2099" not in text


@pytest.mark.parametrize(
    ("now", "deadline", "is_past"),
    [
        # now を実際の時計より**先**に置けば、2099 年の締切も過去
        (datetime(2100, 1, 1, tzinfo=TZ), "2099-06-01 12:00", True),
        # now を実際の時計より**前**に置けば、2025 年の締切も未来
        (datetime(2020, 1, 1, tzinfo=TZ), "2025-06-01 12:00", False),
    ],
)
def test_judgement_uses_only_the_given_now(now, deadline, is_past):
    """純粋性: 実際の時計を読まず、引数の now だけで判定する（両方向で固定）。"""
    problem = deadline_problem(_at(deadline), deadline, now=now)
    assert (problem is not None) is is_past


# =====================================================================
# 2. コマンドレベル
# =====================================================================
async def _create(env, channel, *, options: str, deadline: str) -> _Interaction:
    interaction = _Interaction(env.guild, channel)
    await Schedule.create.callback(
        env.cog, interaction, title="定例会", options=options, deadline=deadline
    )
    return interaction


def _title(interaction) -> str:
    return interaction.last_embed.title or ""


def test_create_rejects_past_deadline():
    async def _main():
        async with _env() as env:
            channel = _Channel()
            interaction = await _create(env, channel, options="2099-10-01", deadline="2025-07-02")
            assert await _count(env.db, "schedules") == 0
            assert await _count(env.db, "schedule_options") == 0
            assert channel.send_calls == 0
            assert CODE in _title(interaction)
            assert "2025/07/02" in interaction.last_text
            assert "過去" in interaction.last_text

    run(_main())


def test_full_date_is_not_moved_to_next_year():
    """完全な日付は翌年へ送らない。`2025-07-02` は 2026 年として作られない。"""

    async def _main():
        async with _env() as env:
            interaction = await _create(
                env, _Channel(), options="2099-10-01", deadline="2025-07-02 23:59"
            )
            assert await _count(env.db, "schedules") == 0
            assert "2025/07/02 23:59" in interaction.last_text

    run(_main())


def test_create_rejects_past_option():
    async def _main():
        async with _env() as env:
            channel = _Channel()
            interaction = await _create(
                env, channel, options="2025-07-03; 2099-10-02", deadline="2099-10-01 12:00"
            )
            assert await _count(env.db, "schedules") == 0
            assert channel.send_calls == 0
            assert CODE in _title(interaction)
            assert "「2025-07-03」" in interaction.last_text
            assert "過去" in interaction.last_text
            # 過去の候補を「締切より前」としては挙げない
            assert "締切（" not in interaction.last_text
            assert "「2099-10-02」" not in interaction.last_text

    run(_main())


@pytest.mark.parametrize(
    ("options", "rejected"),
    [
        ("2099-09-30 18:00; 2099-10-02", True),  # 締切より前の候補がある
        ("2099-10-01 12:00; 2099-10-02", False),  # 締切と同時刻は可（対照）
    ],
)
def test_create_option_before_deadline_vs_at_deadline(options, rejected):
    """同じフィクスチャで、締切より前は拒否・同時刻は作成されることを並べて見る。"""

    async def _main():
        async with _env() as env:
            channel = _Channel()
            interaction = await _create(env, channel, options=options, deadline="2099-10-01 12:00")
            if rejected:
                assert await _count(env.db, "schedules") == 0
                assert channel.send_calls == 0
                assert CODE in _title(interaction)
                assert "「2099-09-30 18:00」" in interaction.last_text
                assert "締切（2099/10/01 12:00）より前" in interaction.last_text
            else:
                assert await _count(env.db, "schedules") == 1
                assert "作成しました" in interaction.last_text
                schedule_id = (await ScheduleRepository(env.db).list_all(G1))[0]["schedule_id"]
                opts = await ScheduleRepository(env.db).list_options(G1, schedule_id)
                assert all(o["message_id"] for o in opts)

    run(_main())


def test_time_check_runs_before_the_permission_check():
    """過去の締切かつ Bot が投稿できないチャンネル → 日時のエラーで止まり、権限は問い合わせない。"""

    async def _main():
        async with _env() as env:
            channel = _Channel(perms=NO_SEND)
            interaction = await _create(env, channel, options="2099-10-01", deadline="2025-07-02")
            assert CODE in _title(interaction)
            assert channel.asked == [], "日時の検査より先に権限を検査している"
            assert await _count(env.db, "schedules") == 0

    run(_main())


async def _seed_open_schedule(db) -> None:
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        G1,
        schedule_id="sch_1",
        title="定例会",
        description=None,
        place=None,
        target_role_id=None,
        deadline_iso=to_iso(parse_deadline("2099-10-01 12:00")),
        created_by="tester",
        channel_id="555",
        ui_style="buttons",
    )
    await repo.mark_reminder_sent(G1, "sch_1")


def test_edit_deadline_rejects_past():
    """締切も、催促の送信済みフラグも変わらない（update_deadline を通っていない）。"""

    async def _main():
        async with _env() as env:
            await _seed_open_schedule(env.db)
            interaction = _Interaction(env.guild, None)
            await Schedule.edit_deadline.callback(
                env.cog, interaction, schedule_id="sch_1", deadline="2025-07-02"
            )
            row = await ScheduleRepository(env.db).get_schedule(G1, "sch_1")
            assert row["deadline"] == to_iso(parse_deadline("2099-10-01 12:00"))
            assert row["reminder_sent_flag"] == 1
            assert CODE in _title(interaction)
            assert "2025/07/02" in interaction.last_text

    run(_main())


def test_edit_deadline_accepts_future():
    """対照: 未来の締切なら更新され、催促の送信済みフラグが 0 に戻る。"""

    async def _main():
        async with _env() as env:
            await _seed_open_schedule(env.db)
            interaction = _Interaction(env.guild, None)
            await Schedule.edit_deadline.callback(
                env.cog, interaction, schedule_id="sch_1", deadline="2099-11-01 12:00"
            )
            row = await ScheduleRepository(env.db).get_schedule(G1, "sch_1")
            assert row["deadline"] == to_iso(parse_deadline("2099-11-01 12:00"))
            assert row["reminder_sent_flag"] == 0
            assert CODE not in _title(interaction)

    run(_main())
