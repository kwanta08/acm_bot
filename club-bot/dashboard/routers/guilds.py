"""サーバー単位の概要 API（P2-3）。

すべてのハンドラは `GuildScope`（＝セッション照合済み）だけを受け取り、
生の guild_id を扱わない。データ取得は `scope.bind(repo)` 経由のみ。
"""
from __future__ import annotations

from fastapi import APIRouter

from dashboard.db import get_database
from dashboard.security import ScopedGuild
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.task_repository import TaskRepository

router = APIRouter(prefix="/api/guilds/{guild_id}")


@router.get("/summary")
async def guild_summary(scope: ScopedGuild):
    """サーバーの概要（件数と自分の権限）を返す。"""
    db = get_database()
    members = scope.bind(MemberRepository(db))
    progress = scope.bind(ProgressRepository(db))

    member_rows = await members.list_members()
    team_rows = await members.list_teams()
    return {
        "guild": {"id": str(scope.guild_id), "name": scope.guild_name},
        "viewer": {
            "id": scope.user_id,
            "name": scope.user_name,
            "level": int(scope.level),
            "manage_guild": scope.manage_guild,
            "can_edit": scope.level >= 2,
        },
        "counts": {
            "members": len(member_rows),
            "teams": len(team_rows),
            "progress_nodes": await progress.count_nodes(),
            "open_tasks": len(await scope.bind(
                TaskRepository(db)).list_tasks(status="open")),
        },
    }
