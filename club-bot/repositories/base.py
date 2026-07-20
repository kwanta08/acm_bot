"""
リポジトリ共通基盤（マルチテナント版）

全リポジトリの公開メソッドは第1引数に guild_id: int を必須で受け取り、
全 SQL に guild_id 条件を付ける。

`for_guild(guild_id)` は guild_id を固定した呼び出しプロキシを返す。
services/ 配下のコード（変更禁止のため guild_id を渡せない）に
リポジトリを渡す際に利用する。
"""
from __future__ import annotations

import functools
from typing import Any

from utils.db import Database


class GuildBoundRepository:
    """
    リポジトリを guild_id 固定で呼び出すための読み取り専用プロキシ。

    取得したメソッド呼び出しの先頭に自動で guild_id を差し込む。
    非コーラブル属性（db など）はそのまま委譲する。
    """

    def __init__(self, repo: "BaseRepository", guild_id: int):
        self._repo = repo
        self._guild_id = guild_id

    @property
    def guild_id(self) -> int:
        return self._guild_id

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._repo, name)
        if name.startswith("_") or not callable(attr):
            return attr

        @functools.wraps(attr)
        async def bound(*args, **kwargs):
            return await attr(self._guild_id, *args, **kwargs)

        return bound


class BaseRepository:
    """guild_id スコープのリポジトリ基底クラス。"""

    def __init__(self, db: Database):
        self.db = db

    def for_guild(self, guild_id: int) -> GuildBoundRepository:
        """guild_id 固定のプロキシを返す（services 互換用）。"""
        return GuildBoundRepository(self, guild_id)
