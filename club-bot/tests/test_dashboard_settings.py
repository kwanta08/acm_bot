"""ダッシュボードの設定 API（P2-6）のテスト。

- 管理者のみ更新できること
- ホワイトリスト外のキー（Todoist トークン等）は変更できないこと
- 更新が他サーバーへ影響しないこと・監査ログに残ること
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
from repositories.settings_repository import SettingsRepository
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"


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
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        settings = SettingsRepository(db)
        await settings.set(GUILD_A, "GUILD_NAME", "A大学 鳥人間")
        await settings.set(GUILD_B, "GUILD_NAME", "B大学 鳥人間")
        # ダッシュボードから触れてはいけない値
        await settings.set(GUILD_A, "TODOIST_API_TOKEN_LEGACY", "secret-token")
    finally:
        await db.close()


def _transport(permissions: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": USER_ID, "username": "y"})
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=[
                {"id": str(GUILD_A), "name": "A大学",
                 "permissions": permissions}])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _client(db_path: str, *, permissions: str = "32") -> TestClient:
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(transport=_transport(permissions))
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=c&state={state}")
    return client


async def _setting(db_path: str, guild_id: int, key: str) -> str | None:
    db = Database(db_path)
    await db.connect()
    try:
        return await SettingsRepository(db).get(guild_id, key)
    finally:
        await db.close()


def test_read_settings_lists_whitelist_only():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/settings").json()
        keys = {s["key"] for s in body["settings"]}
        assert "GUILD_NAME" in keys
        assert "PROGRESS_DEFAULT_CHANNEL_ID" in keys
        # 機密値になりうるキーは一覧にも値にも出ない
        assert "TODOIST_API_TOKEN_LEGACY" not in keys
        assert "secret-token" not in client.get(
            f"/api/guilds/{GUILD_A}/settings").text
        assert body["can_edit"] is True
        current = {s["key"]: s["value"] for s in body["settings"]}
        assert current["GUILD_NAME"] == "A大学 鳥人間"
    finally:
        client.__exit__(None, None, None)


def test_admin_can_update_settings():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(f"/api/guilds/{GUILD_A}/settings",
                           json={"GUILD_NAME": "A大学 人力飛行機部",
                                 "PROGRESS_DEFAULT_CHANNEL_ID": "12345"})
        assert res.status_code == 200
    finally:
        client.__exit__(None, None, None)

    assert asyncio.run(_setting(db_path, GUILD_A, "GUILD_NAME")) == \
        "A大学 人力飛行機部"
    assert asyncio.run(
        _setting(db_path, GUILD_A, "PROGRESS_DEFAULT_CHANNEL_ID")) == "12345"
    # 他サーバーには影響しない
    assert asyncio.run(_setting(db_path, GUILD_B, "GUILD_NAME")) == "B大学 鳥人間"


def test_non_admin_cannot_update():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path, permissions="0")   # サーバー管理権限なし
    try:
        assert client.get(
            f"/api/guilds/{GUILD_A}/settings").json()["can_edit"] is False
        res = client.patch(f"/api/guilds/{GUILD_A}/settings",
                           json={"GUILD_NAME": "書き換え"})
        assert res.status_code == 403
    finally:
        client.__exit__(None, None, None)
    assert asyncio.run(_setting(db_path, GUILD_A, "GUILD_NAME")) == "A大学 鳥人間"


def test_unknown_keys_are_rejected():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        for payload in ({"TODOIST_API_TOKEN_LEGACY": "x"},
                        {"ENCRYPTION_KEY": "x"},
                        {"AUTO_SETUP_DONE": "x"},
                        {}):
            assert client.patch(f"/api/guilds/{GUILD_A}/settings",
                                json=payload).status_code == 400
    finally:
        client.__exit__(None, None, None)
    assert asyncio.run(
        _setting(db_path, GUILD_A, "TODOIST_API_TOKEN_LEGACY")) == "secret-token"


def test_channel_ids_must_be_numeric():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(f"/api/guilds/{GUILD_A}/settings",
                           json={"BOT_LOG_CHANNEL_ID": "#bot-log"})
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_empty_value_deletes_setting():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        assert client.patch(f"/api/guilds/{GUILD_A}/settings",
                            json={"GUILD_NAME": ""}).status_code == 200
    finally:
        client.__exit__(None, None, None)
    assert asyncio.run(_setting(db_path, GUILD_A, "GUILD_NAME")) is None


def test_settings_change_is_audited():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        client.patch(f"/api/guilds/{GUILD_A}/settings",
                     json={"GUILD_NAME": "新しい名前"})
    finally:
        client.__exit__(None, None, None)

    async def _entries():
        db = Database(db_path)
        await db.connect()
        try:
            return await AuditLogRepository(db).list_recent(GUILD_A)
        finally:
            await db.close()

    entries = asyncio.run(_entries())
    assert any(e["action"] == "dashboard.settings" for e in entries)
    detail = next(e["detail"] for e in entries
                  if e["action"] == "dashboard.settings")
    assert "A大学 鳥人間" in detail and "新しい名前" in detail


def test_other_guild_settings_are_forbidden():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        assert client.get(
            f"/api/guilds/{GUILD_B}/settings").status_code == 403
        assert client.patch(f"/api/guilds/{GUILD_B}/settings",
                            json={"GUILD_NAME": "乗っ取り"}).status_code == 403
    finally:
        client.__exit__(None, None, None)
    assert asyncio.run(_setting(db_path, GUILD_B, "GUILD_NAME")) == "B大学 鳥人間"
