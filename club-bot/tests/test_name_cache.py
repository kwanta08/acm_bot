"""Discord 表示名キャッシュ（スキーマ v15）のテスト。

- discord_name_cache テーブルが作成され、v14 相当の既存 DB からの
  マイグレーションでも既存データが保持されること
- NameCacheRepository の upsert / replace_all / delete / names が
  guild_id スコープを守ること
- 表示名の解決チェーン（ニックネーム → グローバル表示名 → ユーザー名）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.member_repository import MemberRepository
from repositories.name_cache_repository import (
    ENTITY_CHANNEL,
    ENTITY_USER,
    NameCacheRepository,
)
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
NOW = "2026-08-15T21:03:47+09:00"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# スキーマ / マイグレーション
# ---------------------------------------------------------------------
def test_fresh_db_has_name_cache_table():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cols = await db._table_columns("discord_name_cache")
            assert {"guild_id", "entity_type", "entity_id", "name", "updated_at"} <= set(cols)
        finally:
            await db.close()

    _run(_main())


def test_migration_from_v14_keeps_existing_rows():
    """v14 相当の DB へ v15 を適用してもデータが壊れない。"""
    db_path = _tmp_db_path()

    async def _seed_v14():
        db = Database(db_path)
        await db.connect()
        try:
            # 既存ギルドのデータ（members）を入れて、バージョンを v14 に戻す
            await MemberRepository(db).upsert_member(GUILD_A, "42", "山田太郎")
            await db._set_user_version(14)
        finally:
            await db.close()

    async def _reopen():
        db = Database(db_path)
        await db.connect()  # ここで v15 マイグレーションが走る
        try:
            assert await db._user_version() == 15
            member = await MemberRepository(db).get_member(GUILD_A, "42")
            assert member is not None
            assert member["display_name"] == "山田太郎"
            # 新テーブルは空で使える
            assert await NameCacheRepository(db).names(GUILD_A, ENTITY_USER) == {}
        finally:
            await db.close()

    _run(_seed_v14())
    _run(_reopen())


# ---------------------------------------------------------------------
# リポジトリ
# ---------------------------------------------------------------------
def test_upsert_and_names_roundtrip():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = NameCacheRepository(db)
            await repo.upsert(GUILD_A, ENTITY_USER, "42", "山田太郎", NOW)
            await repo.upsert(GUILD_A, ENTITY_CHANNEL, "555", "general", NOW)
            # 上書き
            await repo.upsert(GUILD_A, ENTITY_USER, "42", "山田(新)", NOW)

            assert await repo.names(GUILD_A, ENTITY_USER) == {"42": "山田(新)"}
            assert await repo.names(GUILD_A, ENTITY_CHANNEL) == {"555": "general"}
        finally:
            await db.close()

    _run(_main())


def test_replace_all_removes_stale_entries():
    """チャンネル同期は全入れ替え（bot 停止中の削除分を残さない）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = NameCacheRepository(db)
            await repo.upsert(GUILD_A, ENTITY_CHANNEL, "1", "old-channel", NOW)
            await repo.replace_all(GUILD_A, ENTITY_CHANNEL, [("2", "general"), ("3", "工房")], NOW)
            assert await repo.names(GUILD_A, ENTITY_CHANNEL) == {"2": "general", "3": "工房"}
        finally:
            await db.close()

    _run(_main())


def test_upsert_many_keeps_missing_entries():
    """ユーザー同期は追記・上書きのみ（退会者の最後の名前を残す）。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = NameCacheRepository(db)
            await repo.upsert(GUILD_A, ENTITY_USER, "9", "退会済みの人", NOW)
            await repo.upsert_many(GUILD_A, ENTITY_USER, [("42", "山田太郎")], NOW)
            names = await repo.names(GUILD_A, ENTITY_USER)
            assert names == {"9": "退会済みの人", "42": "山田太郎"}
        finally:
            await db.close()

    _run(_main())


def test_delete_single_entry():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = NameCacheRepository(db)
            await repo.upsert(GUILD_A, ENTITY_CHANNEL, "555", "general", NOW)
            assert await repo.delete(GUILD_A, ENTITY_CHANNEL, "555") is True
            assert await repo.delete(GUILD_A, ENTITY_CHANNEL, "555") is False
            assert await repo.names(GUILD_A, ENTITY_CHANNEL) == {}
        finally:
            await db.close()

    _run(_main())


def test_names_are_guild_scoped():
    """別ギルドの名前が混ざらない・別ギルドの行を消せない。"""

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = NameCacheRepository(db)
            await repo.upsert(GUILD_A, ENTITY_USER, "42", "A大学での名前", NOW)
            await repo.upsert(GUILD_B, ENTITY_USER, "42", "B大学での名前", NOW)

            assert await repo.names(GUILD_A, ENTITY_USER) == {"42": "A大学での名前"}
            assert await repo.names(GUILD_B, ENTITY_USER) == {"42": "B大学での名前"}

            # A のスコープで delete しても B の行は消えない
            await repo.delete(GUILD_A, ENTITY_USER, "42")
            assert await repo.names(GUILD_B, ENTITY_USER) == {"42": "B大学での名前"}

            # replace_all も自ギルドの行しか触らない
            await repo.replace_all(GUILD_B, ENTITY_USER, [], NOW)
            await repo.upsert(GUILD_A, ENTITY_USER, "42", "A大学での名前", NOW)
            await repo.replace_all(GUILD_B, ENTITY_USER, [("7", "B大学の部員")], NOW)
            assert await repo.names(GUILD_A, ENTITY_USER) == {"42": "A大学での名前"}
        finally:
            await db.close()

    _run(_main())


# ---------------------------------------------------------------------
# 表示名の解決チェーン（bot がキャッシュへ書く名前の決め方）
# ---------------------------------------------------------------------
def _fake_member(nick=None, global_name=None, name="username"):
    return SimpleNamespace(nick=nick, global_name=global_name, name=name)


def test_member_cache_name_priority_chain():
    """ニックネーム → グローバル表示名 → ユーザー名 の順で解決する。"""
    from cogs.name_cache import member_cache_name

    assert member_cache_name(_fake_member(nick="ニック", global_name="グローバル")) == "ニック"
    assert member_cache_name(_fake_member(nick=None, global_name="グローバル")) == "グローバル"
    assert (
        member_cache_name(_fake_member(nick=None, global_name=None, name="username")) == "username"
    )


def test_sync_guild_populates_cache():
    """Cog の全同期がチャンネル（スレッド含む）とメンバーを書き込む。"""
    from cogs.name_cache import NameCache

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            cog = NameCache(SimpleNamespace(db=db, guilds=[]))
            guild = SimpleNamespace(
                id=GUILD_A,
                chunked=True,
                channels=[SimpleNamespace(id=555, name="general")],
                threads=[SimpleNamespace(id=556, name="桁巻きスレッド")],
                members=[
                    SimpleNamespace(
                        id=42, nick="ニック", global_name="グローバル", name="username"
                    ),
                    SimpleNamespace(id=43, nick=None, global_name=None, name="suzuki"),
                ],
            )
            await cog._sync_guild(guild)

            repo = NameCacheRepository(db)
            assert await repo.names(GUILD_A, ENTITY_CHANNEL) == {
                "555": "general",
                "556": "桁巻きスレッド",
            }
            assert await repo.names(GUILD_A, ENTITY_USER) == {"42": "ニック", "43": "suzuki"}
        finally:
            await db.close()

    _run(_main())
