"""D2-1: `GUILD_NAME` → `CLUB_NAME` の統一（スキーマ v22）。

ダッシュボードは「サークル名」を `GUILD_NAME` キーで保存していたが、
週次サマリー等が読むのは `CLUB_NAME`（config.py）。**保存しても反映されない**
不具合を、キーの統一とデータ移行で解消する。

移行の不変条件（ADR 0024「既定値で既存データを動かさない」に照らした扱い）:
- (a) `GUILD_NAME` だけのギルド: 値を `CLUB_NAME` へ**コピー**する
  （利用者が保存した値を初めて有効にする。意図の実現であって改変ではない）
- (b) 両方あるギルド: **`CLUB_NAME` を上書きしない**（現に効いている値を守る）
- (c) どちらも無いギルド: 何も起きない
- 旧キー `GUILD_NAME` の行は**消さない**（安全側。監査と巻き戻しの余地を残す）
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.db import SCHEMA_VERSION, TABLE_DDL, Database

G_A = 100000000000000001  # GUILD_NAME だけ
G_B = 200000000000000002  # 両方ある
G_C = 300000000000000003  # どちらも無い


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


def _make_v21_db() -> str:
    """v21 相当の DB を settings 入りで用意する。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    try:
        for ddl in TABLE_DDL.values():
            conn.executescript(ddl)
        rows = [
            (G_A, "GUILD_NAME", "鳥人研", "2026-01-01 00:00:00"),
            (G_B, "GUILD_NAME", "旧しらとり会", "2026-01-01 00:00:00"),
            (G_B, "CLUB_NAME", "しらとり会", "2026-02-02 00:00:00"),
            (G_C, "BOT_LOG_CHANNEL_ID", "123", "2026-01-01 00:00:00"),
        ]
        conn.executemany(
            "INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.execute("PRAGMA user_version = 21")
        conn.commit()
    finally:
        conn.close()
    return path


async def _settings(db: Database, guild_id: int) -> dict[str, str]:
    rows = await db.fetchall(
        "SELECT setting_key, setting_value FROM settings WHERE guild_id = ?",
        (guild_id,),
    )
    return {r["setting_key"]: r["setting_value"] for r in rows}


def test_v22_copies_guild_name_without_overwriting_club_name():
    async def _main():
        path = _make_v21_db()
        db = Database(path)
        await db.connect()
        try:
            assert await db._user_version() == SCHEMA_VERSION

            # (a) GUILD_NAME だけ → CLUB_NAME へコピー。旧行は残す
            a = await _settings(db, G_A)
            assert a["CLUB_NAME"] == "鳥人研"
            assert a["GUILD_NAME"] == "鳥人研"

            # (b) 両方 → CLUB_NAME を上書きしない
            b = await _settings(db, G_B)
            assert b["CLUB_NAME"] == "しらとり会"
            assert b["GUILD_NAME"] == "旧しらとり会"

            # (c) どちらも無い → CLUB_NAME は作られない
            c = await _settings(db, G_C)
            assert "CLUB_NAME" not in c
            assert "GUILD_NAME" not in c
            assert c["BOT_LOG_CHANNEL_ID"] == "123"
        finally:
            await db.close()

    run(_main())


def test_v22_migration_is_idempotent():
    """同じ移行を2回適用しても結果が変わらない（冪等）。"""

    async def _main():
        path = _make_v21_db()
        db = Database(path)
        await db.connect()
        try:
            before = await _settings(db, G_B)
            await db._migrate_v22_club_name_key()
            assert await _settings(db, G_B) == before
        finally:
            await db.close()

    run(_main())


def test_dashboard_settings_use_club_name_key():
    """ダッシュボードの編集キーが CLUB_NAME になっている（読む側と一致）。"""
    fastapi = pytest.importorskip(
        "fastapi", reason="dashboard/requirements.txt が未インストール"
    )
    del fastapi
    from dashboard.routers.settings import EDITABLE_SETTINGS

    keys = {spec.key for spec in EDITABLE_SETTINGS}
    assert "CLUB_NAME" in keys
    assert "GUILD_NAME" not in keys


# ---------------------------------------------------------------------
# PostgreSQL 実機（CLUB_TEST_PG_DSN があるときだけ）
# ---------------------------------------------------------------------
async def _pg_database_name(dsn: str) -> str:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchval("SELECT current_database()")
    finally:
        await con.close()


def _pg_dsn_or_skip() -> str:
    dsn = os.getenv("CLUB_TEST_PG_DSN")
    if not dsn:
        pytest.skip("CLUB_TEST_PG_DSN 未設定（テスト専用 DB の DSN を指定してください）")
    name = run(_pg_database_name(dsn))
    if "test" not in name.lower():
        pytest.skip(f"安全のためライブテストはテスト専用 DB でのみ実行します（接続先: {name}）")
    return dsn


def test_pg_live_club_name_copy_sql_runs_on_postgres():
    """コピーの SQL が PostgreSQL でも同じ結果になること（D2-1）。

    実 DB は既に v22 のため版ゲートで再実行されない。移行メソッドを直接
    呼び、(a)(b)(c) の3ケースが SQLite と同じ結果になることを確かめる。
    """
    dsn = _pg_dsn_or_skip()

    async def _main():
        db = Database("./unused.db", database_url=dsn)
        await db.connect()
        try:
            for guild_id in (G_A, G_B, G_C):
                await db.execute("DELETE FROM settings WHERE guild_id = ?", (guild_id,))
                await db.execute("DELETE FROM guilds WHERE guild_id = ?", (guild_id,))
            for row in (
                (G_A, "GUILD_NAME", "鳥人研", "2026-01-01 00:00:00"),
                (G_B, "GUILD_NAME", "旧しらとり会", "2026-01-01 00:00:00"),
                (G_B, "CLUB_NAME", "しらとり会", "2026-02-02 00:00:00"),
                (G_C, "BOT_LOG_CHANNEL_ID", "123", "2026-01-01 00:00:00"),
            ):
                await db.execute(
                    "INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    row,
                )

            await db._migrate_v22_club_name_key()

            a = await _settings(db, G_A)
            assert a["CLUB_NAME"] == "鳥人研" and a["GUILD_NAME"] == "鳥人研"
            b = await _settings(db, G_B)
            assert b["CLUB_NAME"] == "しらとり会" and b["GUILD_NAME"] == "旧しらとり会"
            assert "CLUB_NAME" not in await _settings(db, G_C)
        finally:
            for guild_id in (G_A, G_B, G_C):
                await db.execute("DELETE FROM settings WHERE guild_id = ?", (guild_id,))
            await db.close()

    run(_main())
