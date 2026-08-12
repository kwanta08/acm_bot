"""表グリッド API（P2-4: 読み取り専用）のテスト。

- 7つの対象テーブルが列定義つきで返ること
- **返る行が必ず自サーバーのものだけ**であること（他ギルドの行は1件も出ない）
- ホワイトリスト外のテーブル名は 404（SQL へ渡らない）
- 未ログイン 401 / 他サーバー 403
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")

import httpx
from fastapi.testclient import TestClient

from dashboard.config import DashboardConfig
from dashboard.main import create_app
from repositories.guild_repository import GuildRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.table_repository import (
    TABLES,
    TableRepository,
    UnknownColumnError,
    UnknownTableError,
    rows_to_csv,
)
from repositories.task_repository import TaskRepository
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"
NOW = "2026-08-11 10:00"

EXPECTED_TABLES = {"tasks", "members", "teams", "schedules",
                   "schedule_votes", "layer_records", "progress"}


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(db_path: str) -> DashboardConfig:
    return DashboardConfig(
        client_id="cid", client_secret="secret",
        redirect_uri="https://example.com/auth/callback",
        secret_key="unit-test-secret", db_path=db_path, secure_cookie=False)


async def _seed(db_path: str) -> None:
    """両ギルドに、名前で見分けられるデータを入れる。"""
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        members = MemberRepository(db)
        tasks = TaskRepository(db)
        progress = ProgressRepository(db)
        schedules = ScheduleRepository(db)
        sessions = LayerSessionRepository(db)

        for guild_id, mark in ((GUILD_A, "A大学"), (GUILD_B, "B大学")):
            await members.upsert_member(guild_id, USER_ID, f"{mark}の部員")
            await members.upsert_team(guild_id, "wing", f"{mark}の主翼班")
            await tasks.create_task(guild_id, f"{mark}のタスク", USER_ID)
            await progress.upsert_node(guild_id, "m1", name=f"{mark}の機体",
                                       now_text=NOW)
            await schedules.create_schedule(
                guild_id, f"sch-{guild_id}", f"{mark}のミーティング",
                f"{mark}の説明", "部室", None, "2026-09-01 19:00", USER_ID,
                "1")
            await schedules.add_option(
                guild_id, f"opt-{guild_id}", f"sch-{guild_id}",
                f"{mark}の候補日", "2026-09-01 19:00", None, None)
            await schedules.set_vote(guild_id, f"opt-{guild_id}",
                                     USER_ID, "ok")
            await sessions.add_record(guild_id, USER_ID, f"{mark}の主桁",
                                      "1", NOW, NOW, 60)
    finally:
        await db.close()


def _discord_transport(guilds: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": USER_ID,
                                             "username": "yamada"})
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _logged_in_client(db_path: str, *, permissions: str = "32") -> TestClient:
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(
        transport=_discord_transport([
            {"id": str(GUILD_A), "name": "A大学", "permissions": permissions}]))
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=c&state={state}")
    return client


# ---------------------------------------------------------------------
# リポジトリ（ホワイトリスト）
# ---------------------------------------------------------------------
def test_whitelist_covers_required_tables():
    assert set(TABLES) == EXPECTED_TABLES


def test_unknown_table_is_rejected():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            with pytest.raises(UnknownTableError):
                await repo.list_rows(GUILD_A, "settings")
            with pytest.raises(UnknownTableError):
                await repo.list_rows(GUILD_A, "todoist_configs")
        finally:
            await db.close()

    asyncio.run(_main())


def test_update_rejects_non_editable_column():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            with pytest.raises(UnknownColumnError):
                await repo.update_row(GUILD_A, "members", 1,
                                      {"user_id": "999"})
            with pytest.raises(UnknownColumnError):
                await repo.update_row(GUILD_A, "members", 1, {"guild_id": 1})
        finally:
            await db.close()

    asyncio.run(_main())


def test_repository_rows_are_guild_scoped():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            repo = TableRepository(db)
            for key in EXPECTED_TABLES:
                rows_a = await repo.list_rows(GUILD_A, key)
                rows_b = await repo.list_rows(GUILD_B, key)
                blob_a = str(rows_a)
                assert "B大学" not in blob_a, f"{key} に他ギルドの行が混入"
                assert "A大学" not in str(rows_b), f"{key} に他ギルドの行が混入"
                # 両ギルドとも同じ件数を入れてある
                assert len(rows_a) == len(rows_b)
        finally:
            await db.close()

    asyncio.run(_main())


def test_limit_is_capped():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            # 上限を超える指定でも例外にならず、SQL に巨大値が渡らない
            assert await repo.list_rows(GUILD_A, "tasks", limit=10**9) == []
            assert await repo.list_rows(GUILD_A, "tasks", offset=-5) == []
        finally:
            await db.close()

    asyncio.run(_main())


# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------
def test_table_list_endpoint():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables").json()
        assert {t["key"] for t in body["tables"]} == EXPECTED_TABLES
        assert body["can_edit"] is True
    finally:
        client.__exit__(None, None, None)


def test_read_table_returns_columns_and_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        assert body["table"]["key"] == "members"
        assert body["table"]["pk"] == "member_id"
        names = [c["name"] for c in body["columns"]]
        assert "display_name" in names
        assert "guild_id" not in names        # スコープ列は見せない
        assert body["total"] == 1
        assert body["rows"][0]["display_name"] == "A大学の部員"
    finally:
        client.__exit__(None, None, None)


def test_every_table_returns_only_own_guild_rows():
    """全テーブルで他ギルドの行が1件も返らないこと。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in sorted(EXPECTED_TABLES):
            res = client.get(f"/api/guilds/{GUILD_A}/tables/{key}")
            assert res.status_code == 200, key
            assert "B大学" not in res.text, f"{key} に他ギルドのデータが混入"
    finally:
        client.__exit__(None, None, None)


def test_unknown_table_returns_404():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in ("settings", "todoist_configs", "guilds", "'; DROP TABLE"):
            assert client.get(
                f"/api/guilds/{GUILD_A}/tables/{key}").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_tables_require_scope():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        assert client.get(
            f"/api/guilds/{GUILD_B}/tables").status_code == 403
        assert client.get(
            f"/api/guilds/{GUILD_B}/tables/members").status_code == 403
    finally:
        client.__exit__(None, None, None)

    app = create_app(_config(db_path))
    with TestClient(app) as anon:
        assert anon.get(f"/api/guilds/{GUILD_A}/tables").status_code == 401


def test_read_only_viewer_sees_can_edit_false():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path, permissions="0")
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        assert body["can_edit"] is False
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# CSV エクスポート（P3-2: Sheets 連携の置き換え）
# ---------------------------------------------------------------------
def test_csv_export_returns_labels_and_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "attachment" in res.headers["content-disposition"]
        assert f"members_{GUILD_A}.csv" in res.headers["content-disposition"]
        body = res.content.decode("utf-8-sig")
        lines = body.strip().splitlines()
        assert lines[0].startswith("ID,")        # 見出しは表示名
        assert "表示名" in lines[0]
        assert "A大学の部員" in body
        # Excel 対策の BOM が付く
        assert res.content.startswith(b"\xef\xbb\xbf")
    finally:
        client.__exit__(None, None, None)


def test_csv_export_is_guild_scoped():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in sorted(EXPECTED_TABLES):
            res = client.get(f"/api/guilds/{GUILD_A}/tables/{key}/export.csv")
            assert res.status_code == 200, key
            assert "B大学" not in res.content.decode("utf-8-sig"), key
        # 他サーバーの CSV は取得できない
        assert client.get(
            f"/api/guilds/{GUILD_B}/tables/members/export.csv").status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_csv_export_requires_login():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    app = create_app(_config(db_path))
    with TestClient(app) as anon:
        assert anon.get(
            f"/api/guilds/{GUILD_A}/tables/members/export.csv").status_code == 401


def test_csv_export_unknown_table_is_404():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        assert client.get(
            f"/api/guilds/{GUILD_A}/tables/settings/export.csv").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_csv_export_is_audited():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv")
    finally:
        client.__exit__(None, None, None)

    async def _entries():
        db = Database(db_path)
        await db.connect()
        try:
            from repositories.audit_log_repository import AuditLogRepository
            return await AuditLogRepository(db).list_recent(GUILD_A)
        finally:
            await db.close()

    assert any(e["action"] == "dashboard.export" for e in asyncio.run(_entries()))


# ---------------------------------------------------------------------
# 機体重量の列（F3-4）
# ---------------------------------------------------------------------
def test_progress_table_exposes_weight_columns():
    names = TABLES["progress"].column_names
    assert "target_weight_g" in names
    assert "actual_weight_g" in names


def test_weight_columns_are_editable():
    """班長以上（表グリッドの編集権限）で編集できる列にする。"""
    editable = TABLES["progress"].editable_columns
    assert "target_weight_g" in editable
    assert "actual_weight_g" in editable


def test_weight_columns_are_numeric():
    by_name = {c.name: c for c in TABLES["progress"].columns}
    assert by_name["target_weight_g"].type == "number"
    assert by_name["actual_weight_g"].type == "number"


def test_weight_columns_appear_in_csv_export():
    csv_text = rows_to_csv(TABLES["progress"], [])
    assert "目標重量(g)" in csv_text
    assert "実測重量(g)" in csv_text


def test_weight_can_be_updated_through_table_repository():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await ProgressRepository(db).upsert_node(
                GUILD_A, "wing", name="主翼", now_text=NOW)
            row = await db.fetchone(
                "SELECT progress_node_id FROM progress_nodes"
                " WHERE guild_id = ?", (GUILD_A,))
            pk = row["progress_node_id"]

            table = TableRepository(db)
            assert await table.update_row(
                GUILD_A, "progress", pk,
                {"actual_weight_g": 1240.0, "target_weight_g": 1100.0})

            after = await table.get_row(GUILD_A, "progress", pk)
            assert after["actual_weight_g"] == 1240.0
            assert after["target_weight_g"] == 1100.0
        finally:
            await db.close()

    asyncio.run(_main())


def test_weight_update_does_not_cross_guilds():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(GUILD_A, "wing", name="主翼", now_text=NOW)
            await repo.upsert_node(GUILD_B, "wing", name="別大学", now_text=NOW)
            row_b = await db.fetchone(
                "SELECT progress_node_id FROM progress_nodes"
                " WHERE guild_id = ?", (GUILD_B,))

            table = TableRepository(db)
            # B の行 ID を A のスコープで更新しようとしても通らない
            assert await table.update_row(
                GUILD_A, "progress", row_b["progress_node_id"],
                {"actual_weight_g": 999.0}) is False

            after = await table.get_row(GUILD_B, "progress",
                                        row_b["progress_node_id"])
            assert after["actual_weight_g"] is None
        finally:
            await db.close()

    asyncio.run(_main())
