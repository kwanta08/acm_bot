"""
Embed 生成ユーティリティ（仕様 13）。

- 機能ごとに色を固定（13.2）
- フッターに更新時刻を表示（13.1）
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


def _base(title: str, description: str | None, color: int, executor: str | None) -> discord.Embed:
    title = title[:100]  # タイトルは最大100文字程度（13.1）
    embed = discord.Embed(title=title, description=description, color=color)
    footer = f"更新: {fmt_jp(now())}"
    if executor:
        footer += f" / 実行者: {executor}"
    embed.set_footer(text=footer)
    return embed


def schedule_embed(title: str, description: str | None = None, executor: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_SCHEDULE, executor)


def task_embed(title: str, description: str | None = None, executor: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_TASKS, executor)


def member_embed(title: str, description: str | None = None, executor: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_MEMBERS, executor)


def info_embed(title: str, description: str | None = None, executor: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_INFO, executor)


def success_embed(title: str, description: str | None = None, executor: str | None = None) -> discord.Embed:
    return _base(title, description, COLOR_SUCCESS, executor)


def error_embed(message: str, code: str | None = None) -> discord.Embed:
    title = "エラー"
    if code:
        title += f"（{code}）"
    return _base(title, message, COLOR_ERROR, None)
