"""表グリッドからの編集と監査ログのテスト（P2-5）。

- 班長以上のみ編集できること（一般メンバーは 403）
- 編集可能な列だけが更新でき、それ以外は 400
- **他サーバーの行 ID を指定しても更新されないこと**
- 変更が audit_log に必ず記録されること（変更前後の値つき）
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
from repositories.audit_log_repository import AuditLogRepository
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.table_repository import TableRepository
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"
NOW = "2026-08-11 10:00"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(db_path: str) -> DashboardConfig:
    return DashboardConfig(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://example.com/auth/callback",
        secret_key="unit-test-secret",
        db_path=db_path,
        secure_cookie=False,
    )


async def _seed(db_path: str) -> dict[str, int]:
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        members = MemberRepository(db)
        progress = ProgressRepository(db)
        for guild_id, mark in ((GUILD_A, "A大学"), (GUILD_B, "B大学")):
            await members.upsert_member(guild_id, USER_ID, f"{mark}の部員")
            await progress.upsert_node(
                guild_id, "m1", name=f"{mark}の機体", manual_progress=0.1, now_text=NOW
            )
        rows = await db.fetchall("SELECT guild_id, member_id FROM members ORDER BY member_id")
        ids = {int(r["guild_id"]): int(r["member_id"]) for r in rows}
        node_rows = await db.fetchall("SELECT guild_id, progress_node_id FROM progress_nodes")
        return {
            "member": ids,
            "node": {int(r["guild_id"]): int(r["progress_node_id"]) for r in node_rows},
        }
    finally:
        await db.close()


def _transport(guilds: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": USER_ID, "username": "y"})
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _client(db_path: str, *, permissions: str = "32") -> TestClient:
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(
        transport=_transport([{"id": str(GUILD_A), "name": "A大学", "permissions": permissions}])
    )
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=c&state={state}")
    return client


async def _audit_entries(db_path: str, guild_id: int) -> list[dict]:
    db = Database(db_path)
    await db.connect()
    try:
        return await AuditLogRepository(db).list_recent(guild_id, limit=20)
    finally:
        await db.close()


# ---------------------------------------------------------------------
# 権限
# ---------------------------------------------------------------------
def test_plain_member_cannot_edit():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path, permissions="0")  # サーバー管理権限なし・班長でもない
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "書き換え"},
        )
        assert res.status_code == 403
    finally:
        client.__exit__(None, None, None)

    # DB は変わっていない
    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            row = await MemberRepository(db).get_member(GUILD_A, USER_ID)
            assert row["display_name"] == "A大学の部員"
        finally:
            await db.close()

    asyncio.run(_check())


def test_leader_can_edit():
    """班長（members.is_leader）は編集できる。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))

    async def _make_leader():
        db = Database(db_path)
        await db.connect()
        try:
            await MemberRepository(db).set_leader(GUILD_A, USER_ID, True)
        finally:
            await db.close()

    asyncio.run(_make_leader())
    client = _client(db_path, permissions="0")
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "班長が更新"},
        )
        assert res.status_code == 200
        assert res.json()["row"]["display_name"] == "班長が更新"
    finally:
        client.__exit__(None, None, None)


def test_unauthenticated_cannot_edit():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    app = create_app(_config(db_path))
    with TestClient(app) as client:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "x"},
        )
        assert res.status_code == 401


# ---------------------------------------------------------------------
# スコープ
# ---------------------------------------------------------------------
def test_cannot_edit_other_guild_row():
    """他サーバーの行 ID を指定しても更新されない。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    other_id = ids["member"][GUILD_B]
    client = _client(db_path)
    try:
        # 自分のサーバーの URL に他サーバーの行 ID を混ぜる
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{other_id}", json={"display_name": "乗っ取り"}
        )
        assert res.status_code == 404
        # 他サーバーの URL は 403
        assert (
            client.patch(
                f"/api/guilds/{GUILD_B}/tables/members/{other_id}",
                json={"display_name": "乗っ取り"},
            ).status_code
            == 403
        )
    finally:
        client.__exit__(None, None, None)

    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            row = await MemberRepository(db).get_member(GUILD_B, USER_ID)
            assert row["display_name"] == "B大学の部員"
        finally:
            await db.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------
# 列の検証
# ---------------------------------------------------------------------
def test_non_editable_column_is_rejected():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        for payload in (
            {"user_id": "999"},  # 編集不可
            {"guild_id": GUILD_B},  # スコープ列
            {"member_id": 1},  # 主キー
            {"unknown": "x"},
        ):  # 存在しない列
            res = client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}", json=payload
            )
            assert res.status_code == 400, payload
    finally:
        client.__exit__(None, None, None)


def test_empty_body_and_unknown_table():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}", json={}
            ).status_code
            == 400
        )
        assert (
            client.patch(f"/api/guilds/{GUILD_A}/tables/settings/1", json={"x": 1}).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/999999", json={"display_name": "x"}
            ).status_code
            == 404
        )
    finally:
        client.__exit__(None, None, None)


def test_progress_value_round_trip():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"manual_progress": 0.75, "status": "製作中"},
        )
        assert res.status_code == 200
        row = res.json()["row"]
        assert row["manual_progress"] == 0.75
        assert row["status"] == "製作中"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------
def test_edit_is_recorded_in_audit_log():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "新しい名前"},
        )
    finally:
        client.__exit__(None, None, None)

    entries = asyncio.run(_audit_entries(db_path, GUILD_A))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "dashboard.update"
    assert entry["actor_id"] == USER_ID
    assert "members#" in entry["target"]
    # 変更前後が残る
    assert "A大学の部員" in entry["detail"]
    assert "新しい名前" in entry["detail"]
    # 他サーバーのログには入らない
    assert asyncio.run(_audit_entries(db_path, GUILD_B)) == []


def test_rejected_edit_is_also_recorded():
    """編集不可の列への試みも監査ログに残る。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"user_id": "999"},
        )
    finally:
        client.__exit__(None, None, None)

    entries = asyncio.run(_audit_entries(db_path, GUILD_A))
    assert any(e["action"] == "dashboard.update.rejected" for e in entries)


def test_repository_update_is_guild_scoped():
    """リポジトリ単体でも他ギルドの行は更新できない。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            repo = TableRepository(db)
            # GUILD_A のスコープで GUILD_B の行 ID を指定しても更新されない
            changed = await repo.update_row(
                GUILD_A, "members", ids["member"][GUILD_B], {"display_name": "乗っ取り"}
            )
            assert changed is False
            row = await MemberRepository(db).get_member(GUILD_B, USER_ID)
            assert row["display_name"] == "B大学の部員"
        finally:
            await db.close()

    asyncio.run(_main())
