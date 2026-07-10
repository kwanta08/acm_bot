"""
日程調整ロジック（仕様 11.2）。

リアクション集計、Embed 生成、締切処理を担う。
リアクション絵文字と投票状態の対応:
  ok = 参加 / ng = 不参加 / maybe = 未定
"""
from __future__ import annotations

import uuid
from typing import Any

import discord

from config import config
from repositories.schedule_repository import ScheduleRepository
from utils.embeds import schedule_embed
from utils.parser import fmt_jp, from_iso


DEFAULT_STATUS_TO_EMOJI = {
    "ok": "✅",
    "maybe": "❓",
    "ng": "❌",
}


def new_schedule_id() -> str:
    return uuid.uuid4().hex[:12]


def new_option_id() -> str:
    return uuid.uuid4().hex[:12]


def parse_options(options_str: str) -> list[str]:
    """`;` 区切りの候補日時文字列を分割する（仕様 11.2.2）。"""
    return [p.strip() for p in options_str.split(";") if p.strip()]


def get_schedule_emojis(bot, guild: discord.Guild | None = None) -> dict[str, str | discord.PartialEmoji]:
    """スケジュール用絵文字を返す。custom emoji が取れなければ既定絵文字へフォールバック。"""
    resolved = {}
    mapping = {
        "ok": config.schedule_emoji_ok_id,
        "maybe": config.schedule_emoji_maybe_id,
        "ng": config.schedule_emoji_ng_id,
    }
    for status, emoji_id in mapping.items():
        if emoji_id:
            resolved[status] = discord.PartialEmoji(name=status, id=emoji_id)
        else:
            resolved[status] = DEFAULT_STATUS_TO_EMOJI[status]
    return resolved


def build_emoji_maps(bot, guild: discord.Guild | None = None) -> dict:
    status_to_emoji = get_schedule_emojis(bot, guild)
    emoji_to_status = {}
    all_emojis = []

    for status, emoji in status_to_emoji.items():
        all_emojis.append(emoji)
        if isinstance(emoji, discord.PartialEmoji) and emoji.id:
            emoji_to_status[str(emoji.id)] = status
        else:
            emoji_to_status[str(emoji)] = status

    return {
        "status_to_emoji": status_to_emoji,
        "emoji_to_status": emoji_to_status,
        "all_emojis": all_emojis,
    }


async def build_option_embed(repo: ScheduleRepository, bot: discord.Client,
                             schedule: dict[str, Any], option: dict[str, Any],
                             guild: discord.Guild | None) -> discord.Embed:
    """候補日程1件分の投票状況 Embed を生成する（仕様 11.2.4）。"""
    votes = await repo.list_votes(option["option_id"])
    ok_users, ng_users, maybe_users = [], [], []
    for v in votes:
        name = await _resolve_name(bot, guild, v["user_id"])
        if v["status"] == "ok":
            ok_users.append(name)
        elif v["status"] == "ng":
            ng_users.append(name)
        elif v["status"] == "maybe":
            maybe_users.append(name)

    target_role_name = "全員"
    unanswered_count = "-"
    if schedule.get("target_role_id") and guild:
        role = guild.get_role(int(schedule["target_role_id"]))
        if role:
            target_role_name = role.name
            answered = {v["user_id"] for v in votes}
            targets = {str(m.id) for m in role.members if not m.bot}
            unanswered_count = str(len(targets - answered))

    embed = schedule_embed(f"【日程調整】{schedule['title']}")
    embed.add_field(name="候補日時", value=option["label"], inline=False)
    if schedule.get("place"):
        embed.add_field(name="場所", value=schedule["place"], inline=True)
    embed.add_field(name="締切", value=fmt_jp(from_iso(schedule["deadline"])), inline=True)
    embed.add_field(name="対象", value=target_role_name, inline=True)
    embed.add_field(name=f"参加 ({len(ok_users)})",
                    value="\n".join(ok_users) or "—", inline=True)
    embed.add_field(name=f"不参加 ({len(ng_users)})",
                    value="\n".join(ng_users) or "—", inline=True)
    embed.add_field(name=f"未定 ({len(maybe_users)})",
                    value="\n".join(maybe_users) or "—", inline=True)
    embed.add_field(name="未回答者数", value=unanswered_count, inline=True)
    if schedule.get("description"):
        embed.add_field(name="説明", value=schedule["description"], inline=False)
    return embed


async def _resolve_name(bot: discord.Client, guild: discord.Guild | None, user_id: str) -> str:
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            return member.display_name
    user = bot.get_user(int(user_id))
    if user:
        return user.display_name
    return f"<@{user_id}>"


async def build_summary_embed(repo: ScheduleRepository, bot: discord.Client,
                              schedule: dict[str, Any],
                              guild: discord.Guild | None) -> discord.Embed:
    """締切後の結果要約 Embed（仕様 11.2.5）。"""
    options = await repo.list_options(schedule["schedule_id"])
    embed = schedule_embed(f"【締切】{schedule['title']} 集計結果")
    if schedule.get("place"):
        embed.add_field(name="場所", value=schedule["place"], inline=True)
    embed.add_field(name="締切", value=fmt_jp(from_iso(schedule["deadline"])), inline=True)

    best_label = None
    best_ok = -1
    for opt in options:
        votes = await repo.list_votes(opt["option_id"])
        ok = sum(1 for v in votes if v["status"] == "ok")
        ng = sum(1 for v in votes if v["status"] == "ng")
        maybe = sum(1 for v in votes if v["status"] == "maybe")
        embed.add_field(
            name=opt["label"],
            value=f"ok {参加}　ng {不参加}　maybe {未定}",
            inline=False,
        )
        if ok > best_ok:
            best_ok = ok
            best_label = opt["label"]

    if best_label:
        embed.description = f"最多参加候補: **{best_label}**（{best_ok}名）"
    return embed

SCHEDULE_HEADER = ["候補日時", "参加", "未定", "不参加", "未回答"]

def _resolve_sheet_title(self, book, base_title: str) -> str:
    existing = {ws.title for ws in book.worksheets()}
    if base_title not in existing:
        return base_title
    i = 1
    while f"{base_title}({i})" in existing:
        i += 1
    return f"{base_title}({i})"

def _create_schedule_sheet_sync(self, title: str, options: list[dict], votes_map: dict):
    spreadsheet_id = config.schedule_spreadsheet_id
    if not spreadsheet_id:
        raise SheetsError("SCHEDULE_SPREADSHEET_ID が未設定です")

    book = self._open_book(spreadsheet_id)
    sheet_title = self._resolve_sheet_title(book, title)

    ws = book.add_worksheet(title=sheet_title, rows=500, cols=10)
    ws.append_row(SCHEDULE_HEADER, value_input_option="USER_ENTERED")

    for opt in options:
        label = opt["label"]
        v = votes_map.get(opt["option_id"], {"ok": [], "maybe": [], "ng": [], "unanswered": []})
        row = [
            label,
            "\n".join(v.get("ok", [])),
            "\n".join(v.get("maybe", [])),
            "\n".join(v.get("ng", [])),
            "\n".join(v.get("unanswered", [])),
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")

    return sheet_title

async def create_schedule_sheet(self, title: str, options: list[dict], votes_map: dict) -> str:
    if not config.schedule_sheets_enabled():
        return title
    return await self._run(self._create_schedule_sheet_sync, title, options, votes_map)