"""members / teams テーブルの CRUD（仕様 10.1, 10.2）。

マルチテナント版: 全メソッドが guild_id を必須引数に取り、
他ギルドのデータが混ざらないことを保証する。
"""
from __future__ import annotations

import json
from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class MemberRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    # ---------- teams ----------
    async def upsert_team(self, guild_id: int, team_key: str, team_name: str,
                          leader_role_id: str | None = None,
                          channel_id: str | None = None) -> None:
        """班を登録・更新する。無効化済みの同名班は再有効化される。"""
        now_iso = to_iso(now())
        await self.db.execute(
            """
            INSERT INTO teams (guild_id, team_key, team_name, leader_role_id, channel_id,
                               active_flag, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, team_key) DO UPDATE SET
                team_name = excluded.team_name,
                leader_role_id = COALESCE(excluded.leader_role_id, teams.leader_role_id),
                channel_id = COALESCE(excluded.channel_id, teams.channel_id),
                active_flag = 1,
                updated_at = excluded.updated_at
            """,
            (guild_id, team_key, team_name, leader_role_id, channel_id, now_iso, now_iso),
        )

    async def deactivate_team(self, guild_id: int, team_key: str) -> bool:
        """班を無効化する（論理削除。メンバーの所属情報は保持）。対象が無ければ False。"""
        cur = await self.db.execute(
            "UPDATE teams SET active_flag = 0, updated_at = ?"
            " WHERE guild_id = ? AND team_key = ? AND active_flag = 1",
            (to_iso(now()), guild_id, team_key))
        return cur.rowcount > 0

    async def set_team_roles(self, guild_id: int, team_key: str, *,
                             member_role_id: str | None = None,
                             secondary_role_id: str | None = None) -> bool:
        """班のロール紐付けを更新する。指定した種別のみ更新。対象班が無ければ False。"""
        sets: list[str] = ["updated_at = ?"]
        params: list = [to_iso(now())]
        if member_role_id is not None:
            sets.append("member_role_id = ?")
            params.append(member_role_id)
        if secondary_role_id is not None:
            sets.append("secondary_role_id = ?")
            params.append(secondary_role_id)
        params.extend([guild_id, team_key])
        cur = await self.db.execute(
            f"UPDATE teams SET {', '.join(sets)} WHERE guild_id = ? AND team_key = ?",
            tuple(params))
        return cur.rowcount > 0

    async def count_primary_members(self, guild_id: int, team_key: str) -> int:
        """主所属が指定班のアクティブメンバー数（班の無効化前の確認用）。"""
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM members"
            " WHERE guild_id = ? AND primary_team = ? AND active_flag = 1",
            (guild_id, team_key))
        return int(row["c"]) if row else 0

    async def list_teams(self, guild_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM teams WHERE guild_id = ?"
        params: tuple = (guild_id,)
        if active_only:
            sql += " AND active_flag = 1"
        sql += " ORDER BY team_id"
        rows = await self.db.fetchall(sql, params)
        return [dict(r) for r in rows]

    async def get_team(self, guild_id: int, team_key: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM teams WHERE guild_id = ? AND team_key = ?",
            (guild_id, team_key))
        return dict(row) if row else None

    # ---------- members ----------
    async def upsert_member(self, guild_id: int, user_id: str, display_name: str,
                            primary_team: str | None = None) -> None:
        existing = await self.get_member(guild_id, user_id)
        if existing:
            await self.db.execute(
                "UPDATE members SET display_name = ?, primary_team = COALESCE(?, primary_team)"
                " WHERE guild_id = ? AND user_id = ?",
                (display_name, primary_team, guild_id, user_id),
            )
        else:
            await self.db.execute(
                """
                INSERT INTO members (guild_id, user_id, display_name, primary_team, secondary_teams,
                                     is_leader, skills, joined_at, active_flag)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, 1)
                """,
                (guild_id, user_id, display_name, primary_team, json.dumps([]),
                 json.dumps([]), to_iso(now())),
            )

    async def get_member(self, guild_id: int, user_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id))
        if not row:
            return None
        d = dict(row)
        d["secondary_teams"] = json.loads(d.get("secondary_teams") or "[]")
        d["skills"] = json.loads(d.get("skills") or "[]")
        return d

    async def list_members(self, guild_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM members WHERE guild_id = ?"
        if active_only:
            sql += " AND active_flag = 1"
        sql += " ORDER BY display_name"
        rows = await self.db.fetchall(sql, (guild_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["secondary_teams"] = json.loads(d.get("secondary_teams") or "[]")
            d["skills"] = json.loads(d.get("skills") or "[]")
            out.append(d)
        return out

    async def set_primary_team(self, guild_id: int, user_id: str, team_key: str) -> None:
        await self.db.execute(
            "UPDATE members SET primary_team = ? WHERE guild_id = ? AND user_id = ?",
            (team_key, guild_id, user_id))

    async def set_secondary_teams(self, guild_id: int, user_id: str, team_keys: list[str]) -> None:
        await self.db.execute(
            "UPDATE members SET secondary_teams = ? WHERE guild_id = ? AND user_id = ?",
            (json.dumps(team_keys, ensure_ascii=False), guild_id, user_id),)

    async def set_leader(self, guild_id: int, user_id: str, is_leader: bool) -> None:
        await self.db.execute(
            "UPDATE members SET is_leader = ? WHERE guild_id = ? AND user_id = ?",
            (1 if is_leader else 0, guild_id, user_id))

    async def add_skill(self, guild_id: int, user_id: str, skill: str) -> bool:
        m = await self.get_member(guild_id, user_id)
        if not m:
            return False
        skills = set(m["skills"])
        skills.add(skill)
        await self.db.execute(
            "UPDATE members SET skills = ? WHERE guild_id = ? AND user_id = ?",
            (json.dumps(sorted(skills), ensure_ascii=False), guild_id, user_id))
        return True

    async def remove_skill(self, guild_id: int, user_id: str, skill: str) -> bool:
        m = await self.get_member(guild_id, user_id)
        if not m:
            return False
        skills = [s for s in m["skills"] if s != skill]
        await self.db.execute(
            "UPDATE members SET skills = ? WHERE guild_id = ? AND user_id = ?",
            (json.dumps(skills, ensure_ascii=False), guild_id, user_id))
        return True

    async def search_support(self, guild_id: int, team_key: str | None,
                             skill: str | None) -> list[dict[str, Any]]:
        """班・技能タグで支援候補を検索する（仕様 11.4.4）。"""
        members = await self.list_members(guild_id)
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
