"""ギルドのライフサイクル（スキーマ v11 / migrations/010）のテスト。

Bot をサーバーから外してもデータが残り続けていた問題への対応。
**退出しただけでは消さない**（誤キックや一時的な離脱から復帰できるように）。
退出時に left_at と purge_after を記録し、猶予を過ぎたものだけを
日次ジョブ（F2-4）が削除する。

- 新規 DB に left_at / purge_after があり、SCHEMA_VERSION が 11 であること
- v10 相当の既存 DB から自動マイグレーションで列が増え、既存行が壊れないこと
- 退出 → 記録、再参加 → クリア。クリア後も既存データがそのまま残ること
- 猶予日数がギルド別設定 DATA_RETENTION_DAYS で上書きできること
- 1つのギルドの退出が他ギルドに影響しないこと
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import DEFAULT_DATA_RETENTION_DAYS, config
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from utils.db import SCHEMA_VERSION, Database
from utils.parser import TZ, from_iso

G1 = 100000000000000001
G2 = 200000000000000002


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


async def _columns(db: Database, table: str) -> set[str]:
    return {r["name"] for r in await db.fetchall(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_schema_version_is_at_least_11():
    assert SCHEMA_VERSION >= 11


def test_fresh_schema_has_lifecycle_columns():
    async def _main():
        db = await _connected_db()
        try:
            cols = await _columns(db, "guilds")
            assert {
                "guild_id",
                "guild_name",
                "joined_at",
                "setup_version",
                "left_at",
                "purge_after",
            } <= cols
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_purge_after_index_exists():
    async def _main():
        db = await _connected_db()
        try:
            rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")
            assert "idx_guilds_purge_after" in {r["name"] for r in rows}
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# v10 → v11 マイグレーション
# ---------------------------------------------------------------------
def _make_v10_db() -> str:
    """v11 以前（left_at / purge_after を持たない）の guilds を持つ DB。"""
    path = _tmp_db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE guilds (
            guild_id      INTEGER PRIMARY KEY CHECK (guild_id > 0),
            guild_name    TEXT NOT NULL,
            joined_at     TEXT NOT NULL,
            setup_version INTEGER NOT NULL DEFAULT 2
        );
        PRAGMA user_version = 10;
        """
    )
    conn.execute(
        "INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version) VALUES (?, ?, ?, 2)",
        (G1, "既存サークル", "2026-01-01T00:00:00+09:00"),
    )
    conn.commit()
    conn.close()
    return path


def test_v10_db_gains_columns_and_keeps_existing_rows():
    async def _main():
        db = Database(_make_v10_db())
        await db.connect()
        try:
            cols = await _columns(db, "guilds")
            assert {"left_at", "purge_after"} <= cols

            row = await db.fetchone("SELECT * FROM guilds WHERE guild_id = ?", (G1,))
            assert row is not None, "既存ギルドの行が失われた"
            assert row["guild_name"] == "既存サークル"
            assert row["joined_at"] == "2026-01-01T00:00:00+09:00"
            # 既存ギルドは「参加中」のまま。マイグレーションで削除対象にしない
            assert row["left_at"] is None
            assert row["purge_after"] is None
            assert await db._user_version() == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_migration_is_idempotent():
    """同じ DB を二度開いても列追加が失敗しない。"""

    async def _main():
        path = _make_v10_db()
        for _ in range(2):
            db = Database(path)
            await db.connect()
            cols = await _columns(db, "guilds")
            assert {"left_at", "purge_after"} <= cols
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 退出 → 記録 / 再参加 → クリア
# ---------------------------------------------------------------------
def test_mark_left_records_timestamps():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "テストサークル")
            left = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)

            left_iso, purge_iso = await repo.mark_left(
                G1, DEFAULT_DATA_RETENTION_DAYS, left_at=left
            )

            row = await repo.get(G1)
            assert row["left_at"] == left_iso
            assert row["purge_after"] == purge_iso
            # 既定は退出から30日後
            assert from_iso(purge_iso) - from_iso(left_iso) == timedelta(days=30)
        finally:
            await db.close()

    run(_main())


def test_mark_left_does_not_delete_data():
    """退出を記録した時点ではデータを消さない。"""

    async def _main():
        db = await _connected_db()
        try:
            await GuildRepository(db).ensure(G1, "テストサークル")
            await MemberRepository(db).upsert_team(G1, "struct", "構造")

            await GuildRepository(db).mark_left(G1, DEFAULT_DATA_RETENTION_DAYS)

            teams = await MemberRepository(db).list_teams(G1)
            assert len(teams) == 1, "退出記録でデータが消えてはいけない"
        finally:
            await db.close()

    run(_main())


def test_rejoin_clears_lifecycle_and_keeps_data():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "テストサークル")
            await MemberRepository(db).upsert_team(G1, "struct", "構造")
            await repo.mark_left(G1, DEFAULT_DATA_RETENTION_DAYS)

            # 再招待
            await repo.ensure(G1, "テストサークル")
            await repo.clear_left(G1)

            row = await repo.get(G1)
            assert row["left_at"] is None
            assert row["purge_after"] is None
            teams = await MemberRepository(db).list_teams(G1)
            assert [t["team_key"] for t in teams] == ["struct"], (
                "再参加で既存データがそのまま復活すること"
            )
        finally:
            await db.close()

    run(_main())


def test_retention_days_zero_marks_immediately():
    """DATA_RETENTION_DAYS=0 なら退出時点で削除対象になる。"""

    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "テストサークル")
            left = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
            left_iso, purge_iso = await repo.mark_left(G1, 0, left_at=left)
            assert purge_iso == left_iso
        finally:
            await db.close()

    run(_main())


def test_negative_retention_days_is_clamped():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "テストサークル")
            left = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
            left_iso, purge_iso = await repo.mark_left(G1, -5, left_at=left)
            assert purge_iso == left_iso, "負の保持日数は 0 に丸める"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# guild_id スコープ
# ---------------------------------------------------------------------
def test_mark_left_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "A大学")
            await repo.ensure(G2, "B大学")

            await repo.mark_left(G1, DEFAULT_DATA_RETENTION_DAYS)

            assert (await repo.get(G1))["left_at"] is not None
            other = await repo.get(G2)
            assert other["left_at"] is None, "他サーバーを巻き込んで退出扱いにしない"
            assert other["purge_after"] is None
        finally:
            await db.close()

    run(_main())


def test_clear_left_is_guild_scoped():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "A大学")
            await repo.ensure(G2, "B大学")
            await repo.mark_left(G1, DEFAULT_DATA_RETENTION_DAYS)
            await repo.mark_left(G2, DEFAULT_DATA_RETENTION_DAYS)

            await repo.clear_left(G1)

            assert (await repo.get(G1))["purge_after"] is None
            assert (await repo.get(G2))["purge_after"] is not None
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# ギルド別設定
# ---------------------------------------------------------------------
def test_retention_days_defaults_to_30():
    async def _main():
        db = await _connected_db()
        try:
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.data_retention_days == DEFAULT_DATA_RETENTION_DAYS == 30
        finally:
            config.invalidate_guild(G1)
            await db.close()

    run(_main())


def test_retention_days_can_be_overridden_per_guild():
    async def _main():
        db = await _connected_db()
        try:
            await SettingsRepository(db).set(G1, "DATA_RETENTION_DAYS", "7")

            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.data_retention_days == 7
            # 設定していない別サーバーは既定のまま
            other = await config.for_guild(G2, db=db, force_reload=True)
            assert other.data_retention_days == DEFAULT_DATA_RETENTION_DAYS
        finally:
            config.invalidate_guild(G1)
            config.invalidate_guild(G2)
            await db.close()

    run(_main())
