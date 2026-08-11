"""settings 更新のプロセス間伝播（LISTEN/NOTIFY）のテスト（P2-6）。

PostgreSQL が無い環境でも動くよう、asyncpg のプール／接続をフェイクにして
「NOTIFY が正しいチャンネル・ペイロードで送られるか」「購読側の
コールバックが config キャッシュを無効化するか」を検証する。
SQLite 構成では通知を出さない（＝単一プロセス前提）ことも確認する。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.settings_repository import SettingsRepository
from utils import db as db_module
from utils.db import SETTINGS_CHANNEL, Database

G1 = 100000000000000001
G2 = 200000000000000002


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------
# SQLite（開発構成）: 通知しない
# ---------------------------------------------------------------------
def test_sqlite_does_not_notify():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            # 例外にならず、購読も開始できない（False）
            await db.set_setting(G1, "GUILD_NAME", "A大学")
            assert await db.start_settings_listener(lambda gid: None) is False
            assert await db.get_setting(G1, "GUILD_NAME") == "A大学"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# PostgreSQL 構成: NOTIFY を送る
# ---------------------------------------------------------------------
class _FakeConn:
    def __init__(self, sink: list):
        self.sink = sink

    async def execute(self, sql, *args):
        self.sink.append((sql, args))
        return "SELECT 1"

    async def fetchrow(self, *_args, **_kwargs):
        return None

    async def fetch(self, *_args, **_kwargs):
        return []


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, sink: list):
        self.conn = _FakeConn(sink)

    def acquire(self):
        return _FakeAcquire(self.conn)

    async def close(self):
        return None


def _pg_database(sink: list) -> Database:
    db = Database("./ignored.db", database_url="postgresql://x/y")
    db._pool = _FakePool(sink)
    return db


def test_notify_sends_channel_and_guild_id():
    async def _main():
        sink: list = []
        db = _pg_database(sink)
        await db.notify_settings_changed(G1)
        assert len(sink) == 1
        sql, args = sink[0]
        assert "pg_notify" in sql
        assert args == (SETTINGS_CHANNEL, str(G1))

    run(_main())


def test_notify_failure_does_not_raise():
    """通知に失敗しても設定更新自体は壊さない。"""
    class _BrokenPool(_FakePool):
        def acquire(self):
            raise RuntimeError("pool exhausted")

    async def _main():
        db = Database("./ignored.db", database_url="postgresql://x/y")
        db._pool = _BrokenPool([])
        await db.notify_settings_changed(G1)   # 例外を投げない

    run(_main())


# ---------------------------------------------------------------------
# 購読側: 通知でキャッシュが無効化される
# ---------------------------------------------------------------------
class _FakeListenerConn:
    def __init__(self):
        self.listeners: dict[str, object] = {}
        self.closed = False

    async def add_listener(self, channel, callback):
        self.listeners[channel] = callback

    async def close(self):
        self.closed = True


def test_listener_invokes_callback_with_guild_id(monkeypatch):
    async def _main():
        conn = _FakeListenerConn()

        class _FakeAsyncpg:
            @staticmethod
            async def connect(dsn=None):
                return conn

        monkeypatch.setattr(db_module, "asyncpg", _FakeAsyncpg)
        db = Database("./ignored.db", database_url="postgresql://x/y")

        invalidated: list[int] = []
        assert await db.start_settings_listener(invalidated.append) is True
        assert SETTINGS_CHANNEL in conn.listeners

        handler = conn.listeners[SETTINGS_CHANNEL]
        handler(None, 0, SETTINGS_CHANNEL, str(G2))
        assert invalidated == [G2]

        # 不正なペイロードは無視される
        handler(None, 0, SETTINGS_CHANNEL, "not-a-number")
        assert invalidated == [G2]

        # コールバックが例外を投げても購読は壊れない
        def _boom(_gid):
            raise RuntimeError("boom")

        db2 = Database("./ignored.db", database_url="postgresql://x/y")
        await db2.start_settings_listener(_boom)
        conn.listeners[SETTINGS_CHANNEL](None, 0, SETTINGS_CHANNEL, str(G1))

        await db.stop_settings_listener()
        assert conn.closed is True

    run(_main())


def test_listener_start_is_idempotent(monkeypatch):
    async def _main():
        conns: list[_FakeListenerConn] = []

        class _FakeAsyncpg:
            @staticmethod
            async def connect(dsn=None):
                conn = _FakeListenerConn()
                conns.append(conn)
                return conn

        monkeypatch.setattr(db_module, "asyncpg", _FakeAsyncpg)
        db = Database("./ignored.db", database_url="postgresql://x/y")
        assert await db.start_settings_listener(lambda _g: None) is True
        assert await db.start_settings_listener(lambda _g: None) is True
        assert len(conns) == 1   # 接続は1つだけ

    run(_main())


# ---------------------------------------------------------------------
# config キャッシュの無効化（bot 側の受け口）
# ---------------------------------------------------------------------
def test_config_invalidate_guild_clears_cache():
    from config import GuildConfig, config

    config._guild_cache[G1] = GuildConfig(guild_id=G1)
    config._guild_cache[G2] = GuildConfig(guild_id=G2)
    config.invalidate_guild(G1)
    assert G1 not in config._guild_cache
    assert G2 in config._guild_cache      # 他ギルドのキャッシュは残る
    config.clear_guild_cache()


def test_settings_repository_triggers_notify():
    """リポジトリ経由の更新・削除でも通知が飛ぶ（PostgreSQL 構成）。"""
    async def _main():
        sink: list = []
        db = _pg_database(sink)
        repo = SettingsRepository(db)
        await repo.set(G1, "GUILD_NAME", "A大学")
        notifies = [a for _s, a in sink if a and a[0] == SETTINGS_CHANNEL]
        assert notifies == [(SETTINGS_CHANNEL, str(G1))]

    run(_main())
