"""members / teams テーブルの CRUD（仕様 10.1, 10.2）。"""
from __future__ import annotations

import json
from typing import Any

from utils.db import Database
from utils.parser import now, to_iso


class MemberRepository:
    def __init__(self, db: Database):
        self.db = db

    # ---------- teams ----------
    async def upsert_team(self, team_key: str, team_name: str,
                          leader_role_id: str | None = None,
                          channel_id: str | None = None) -> None:
        await self.db.execute(
            """
            INSERT INTO teams (team_key, team_name, leader_role_id, channel_id, active_flag)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(team_key) DO UPDATE SET
                team_name = excluded.team_name,
                leader_role_id = COALESCE(excluded.leader_role_id, teams.leader_role_id),
                channel_id = COALESCE(excluded.channel_id, teams.channel_id)
            """,
            (team_key, team_name, leader_role_id, channel_id),
        )

    async def list_teams(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM teams"
        if active_only:
            sql += " WHERE active_flag = 1"
        sql += " ORDER BY team_id"
        rows = await self.db.fetchall(sql)
        return [dict(r) for r in rows]

    async def get_team(self, team_key: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM teams WHERE team_key = ?", (team_key,))
        return dict(row) if row else None

    # ---------- members ----------
    async def upsert_member(self, user_id: str, display_name: str,
                            primary_team: str | None = None) -> None:
        existing = await self.get_member(user_id)
        if existing:
            await self.db.execute(
                "UPDATE members SET display_name = ?, primary_team = COALESCE(?, primary_team) WHERE user_id = ?",
                (display_name, primary_team, user_id),
            )
        else:
            await self.db.execute(
                """
                INSERT INTO members (user_id, display_name, primary_team, secondary_teams,
                                     is_leader, skills, joined_at, active_flag)
                VALUES (?, ?, ?, ?, 0, ?, ?, 1)
                """,
                (user_id, display_name, primary_team, json.dumps([]),
                 json.dumps([]), to_iso(now())),
            )

    async def get_member(self, user_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM members WHERE user_id = ?", (user_id,))
        if not row:
            return None
        d = dict(row)
        d["secondary_teams"] = json.loads(d.get("secondary_teams") or "[]")
        d["skills"] = json.loads(d.get("skills") or "[]")
        return d

    async def list_members(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM members"
        if active_only:
            sql += " WHERE active_flag = 1"
        sql += " ORDER BY display_name"
        rows = await self.db.fetchall(sql)
        out = []
        for r in rows:
            d = dict(r)
            d["secondary_teams"] = json.loads(d.get("secondary_teams") or "[]")
            d["skills"] = json.loads(d.get("skills") or "[]")
            out.append(d)
        return out

    async def set_primary_team(self, user_id: str, team_key: str) -> None:
        await self.db.execute(
            "UPDATE members SET primary_team = ? WHERE user_id = ?", (team_key, user_id))

    async def set_leader(self, user_id: str, is_leader: bool) -> None:
        await self.db.execute(
            "UPDATE members SET is_leader = ? WHERE user_id = ?",
            (1 if is_leader else 0, user_id))

    async def add_skill(self, user_id: str, skill: str) -> bool:
        m = await self.get_member(user_id)
        if not m:
            return False
        skills = set(m["skills"])
        skills.add(skill)
        await self.db.execute(
            "UPDATE members SET skills = ? WHERE user_id = ?",
            (json.dumps(sorted(skills), ensure_ascii=False), user_id))
        return True

    async def remove_skill(self, user_id: str, skill: str) -> bool:
        m = await self.get_member(user_id)
        if not m:
            return False
        skills = [s for s in m["skills"] if s != skill]
        await self.db.execute(
            "UPDATE members SET skills = ? WHERE user_id = ?",
            (json.dumps(skills, ensure_ascii=False), user_id))
        return True

    async def search_support(self, team_key: str | None, skill: str | None) -> list[dict[str, Any]]:
        """班・技能タグで支援候補を検索する（仕様 11.4.4）。"""
        members = await self.list_members()
        out = []
        for m in members:
            if team_key:
                if m.get("primary_team") != team_key and team_key not in m["secondary_teams"]:
                    continue
            if skill:
                if skill not in m["skills"]:
                    continue
            out.append(m)
        return out
