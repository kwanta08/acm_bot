"""**guild_id スコープの強制**（P2-3）。

本プロジェクトが最も避けるべき事故は「他大学のデータが見える」ことである。
リポジトリ層はすでに guild_id を必須引数にしているため、Web 層は
**「セッションで検証済みの guild_id 以外を絶対にリポジトリへ渡さない」**
という一点を守る設計にする。

そのための仕組み:

1. ルートハンドラは `guild_id: int` を直接受け取らない。
   FastAPI の依存性 `require_guild_scope` が返す `GuildScope` だけを受け取る
2. `GuildScope` はセッション照合を通った場合のみ生成される
   （URL・ボディ由来の値からは作れない）
3. リポジトリへは `scope.bind(repo)` 経由でしか触れない。
   これは `repo.for_guild(検証済み guild_id)` を返すプロキシで、
   以降の呼び出しに guild_id が自動で差し込まれる

つまり「間違った guild_id を渡す」ためには、意図的に本モジュールを
迂回するコードを書く必要がある。迂回していないことは
tests/test_dashboard_scope.py が検査する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request

from dashboard import auth
from dashboard.db import get_database
from repositories.base import BaseRepository, GuildBoundRepository
from repositories.member_repository import MemberRepository
from utils.db import Database
from utils.permissions import Level


@dataclass(frozen=True)
class GuildScope:
    """セッションで検証済みのサーバーと、その中での利用者の権限。

    このオブジェクトを持っていること自体が「この guild_id へのアクセスは
    検証済み」という証明になる。生成は require_guild_scope() のみ。
    """

    guild_id: int
    guild_name: str
    user_id: str
    user_name: str
    level: Level
    manage_guild: bool

    def bind(self, repo: BaseRepository) -> GuildBoundRepository:
        """リポジトリを検証済み guild_id に固定したプロキシを返す。

        以降の呼び出しには guild_id が自動で差し込まれるため、
        呼び出し側が別のサーバーの ID を渡す余地が無い。
        """
        return repo.for_guild(self.guild_id)

    def require(self, level: Level) -> None:
        """権限レベルが足りなければ 403 を投げる。"""
        if self.level < level:
            raise HTTPException(
                status_code=403,
                detail="この操作を行う権限がありません。")


async def resolve_level(db: Database, guild_id: int, user_id: str,
                        manage_guild: bool) -> Level:
    """ダッシュボード上の権限レベルを決める。

    bot 側の utils/permissions.py のレベル体系に合わせる:
      L4: Discord の「サーバー管理」権限を持つ（＝ bot 管理者相当）
      L2: members.is_leader が立っている（班長）
      L1: それ以外のサーバー参加者（既定）

    ロール ID による L3（幹部）判定は Bot トークンなしでは行えないため、
    ダッシュボードでは L4 / L2 / L1 の3段階で扱う。
    """
    if manage_guild:
        return Level.L4
    member = await MemberRepository(db).get_member(guild_id, user_id)
    if member and member.get("is_leader"):
        return Level.L2
    return Level.L1


async def require_guild_scope(
    request: Request,
    guild_id: Annotated[int, Path(ge=1,
                                  description="対象サーバー（Discord ギルド）ID")],
) -> GuildScope:
    """URL の guild_id をセッションと照合し、検証済みスコープを返す。

    - 未ログイン: 401
    - セッションに無いサーバー（＝所属していない / bot が居ない）: 403

    403 のメッセージにサーバー名などの情報を含めない
    （存在の有無を推測させない）。
    """
    user = auth.read_user(request.session)
    if user is None:
        raise HTTPException(status_code=401, detail="ログインが必要です。")

    session_guild = auth.find_session_guild(request.session, guild_id)
    if session_guild is None:
        raise HTTPException(
            status_code=403,
            detail="このサーバーのデータにはアクセスできません。")

    level = await resolve_level(get_database(), guild_id, user.id,
                                session_guild.manage_guild)
    return GuildScope(
        guild_id=guild_id,
        guild_name=session_guild.name,
        user_id=user.id,
        user_name=user.name,
        level=level,
        manage_guild=session_guild.manage_guild,
    )


def require_level(level: Level):
    """指定レベル以上を要求する依存性を作る（編集系エンドポイント用）。"""

    async def _dependency(
        scope: Annotated[GuildScope, Depends(require_guild_scope)],
    ) -> GuildScope:
        scope.require(level)
        return scope

    return _dependency


# ルートハンドラはこの型注釈だけを受け取る（生の guild_id を受け取らない）。
# ScopedGuild : 閲覧（サーバー参加者なら誰でも）
# EditorGuild : 編集（班長以上）
ScopedGuild = Annotated[GuildScope, Depends(require_guild_scope)]
EditorGuild = Annotated[GuildScope, Depends(require_level(Level.L2))]
