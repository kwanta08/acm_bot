"""
Embed 生成ユーティリティ（仕様 13）。

- 機能ごとに色を固定（13.2）
- フッターに更新時刻を表示（13.1）
- 一覧の打ち切りを利用者に見える形で伝える（add_truncation_note）
"""

from __future__ import annotations

import discord

from config import (
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_MEMBERS,
    COLOR_SCHEDULE,
    COLOR_SUCCESS,
    COLOR_TASKS,
)
from utils.parser import fmt_jp, now

# Discord の Embed が受け付ける field の最大数。
# これを超えて add_field すると送信時に HTTPException(400) になり、
# 利用者には「予期せぬエラー」としか出ない（時間をおいても直らない）。
MAX_EMBED_FIELDS = 25


def _base(title: str, description: str | None, color: int, executor: str | None) -> discord.Embed:
    title = title[:100]  # タイトルは最大100文字程度（13.1）
    embed = discord.Embed(title=title, description=description, color=color)
    footer = f"更新: {fmt_jp(now())}"
    if executor:
        footer += f" / 実行者: {executor}"
    embed.set_footer(text=footer)
    return embed


def schedule_embed(
    title: str, description: str | None = None, executor: str | None = None
) -> discord.Embed:
    return _base(title, description, COLOR_SCHEDULE, executor)


def task_embed(
    title: str, description: str | None = None, executor: str | None = None
) -> discord.Embed:
    return _base(title, description, COLOR_TASKS, executor)


def member_embed(
    title: str, description: str | None = None, executor: str | None = None
) -> discord.Embed:
    return _base(title, description, COLOR_MEMBERS, executor)


def info_embed(
    title: str, description: str | None = None, executor: str | None = None
) -> discord.Embed:
    return _base(title, description, COLOR_INFO, executor)


def success_embed(
    title: str, description: str | None = None, executor: str | None = None
) -> discord.Embed:
    return _base(title, description, COLOR_SUCCESS, executor)


def error_embed(message: str, code: str | None = None) -> discord.Embed:
    title = "エラー"
    if code:
        title += f"（{code}）"
    return _base(title, message, COLOR_ERROR, None)


def empty_state_embed(title: str, situation: str, next_command: str) -> discord.Embed:
    """空状態の Embed。「〜はありません」で終わらせず、次の1コマンドを必ず添える。

    初めて使う人にとって空の一覧は行き止まりに見える（G2-5）。
    next_command は `/task add` のようなコマンド1つ。複数の選択肢を
    並べたい場合は situation 側に書き、next_command には最初に打つ
    1つだけを渡す。
    """
    description = f"{situation}\n`{next_command}` から始めてください。"
    return _base(title, description, COLOR_INFO, None)


def add_truncation_note(
    embed: discord.Embed, total: int, shown: int, hint: str | None = None
) -> discord.Embed:
    """一覧を途中で打ち切ったことを本文に追記する。

    黙って切ると「該当は N 件」と誤読され、探し物が見つからない理由が
    利用者に分からない。件数と、絞り込む手段があるならそれも書く。
    """
    if total <= shown:
        return embed
    note = f"…ほか {total - shown} 件"
    if hint:
        note += f"（{hint}）"
    embed.description = f"{embed.description}\n{note}" if embed.description else note
    return embed
