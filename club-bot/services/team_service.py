"""班・技能タグの参照ヘルパー（DB 駆動）。

config.py の固定配列（INITIAL_TEAMS / SKILL_TAGS）を廃止し、
teams / skill_tags テーブルからギルド単位で取得する。

Discord の autocomplete は最大25件までしか返せないため、
入力文字列で絞り込んだうえで上位25件を返す（タグ数が多くても
エラーにならない）。
"""
from __future__ import annotations

from discord import app_commands

from repositories.member_repository import MemberRepository
from repositories.skill_tag_repository import SkillTagRepository
from utils.db import Database

MAX_AUTOCOMPLETE = 25


async def team_name_map(db: Database, guild_id: int) -> dict[str, str]:
    """班キー → 表示名のマップ（無効化済みも含む。表示用途）。"""
    teams = await MemberRepository(db).list_teams(guild_id, active_only=False)
    return {t["team_key"]: t["team_name"] for t in teams}


def _matches(current: str, *values: str) -> bool:
    c = (current or "").lower()
    return any(c in v.lower() for v in values)


async def team_choices(db: Database, guild_id: int, current: str,
                       active_only: bool = True) -> list[app_commands.Choice[str]]:
    """班の autocomplete 候補。value は班キー、name は「表示名 (キー)」。"""
    teams = await MemberRepository(db).list_teams(guild_id, active_only=active_only)
    return [
        app_commands.Choice(name=f"{t['team_name']} ({t['team_key']})", value=t["team_key"])
        for t in teams
        if _matches(current, t["team_key"], t["team_name"])
    ][:MAX_AUTOCOMPLETE]


async def skill_choices(db: Database, guild_id: int, current: str,
                        active_only: bool = True) -> list[app_commands.Choice[str]]:
    """技能タグの autocomplete 候補。value/name はタグ名。"""
    rows = await SkillTagRepository(db).list_all(guild_id)
    return [
        app_commands.Choice(name=r["skill_name"], value=r["skill_name"])
        for r in rows
        if (not active_only or r["active_flag"]) and _matches(current, r["skill_name"])
    ][:MAX_AUTOCOMPLETE]
