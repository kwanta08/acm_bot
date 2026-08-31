"""Discord OAuth2 ログインと署名付きセッションのテスト（P2-2）。

Discord へは接続せず、httpx.MockTransport で API 応答を差し替える。
"""

from __future__ import annotations

import os
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")

import httpx
from fastapi.testclient import TestClient

from dashboard import auth
from dashboard.config import SESSION_COOKIE, DashboardConfig
from dashboard.main import create_app
from repositories.guild_repository import GuildRepository
from utils.db import Database

G1 = 100000000000000001
G2 = 200000000000000002


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(**overrides) -> DashboardConfig:
    base = {
        "client_id": "1234567890",
        "client_secret": "top-secret",
        "redirect_uri": "https://example.com/auth/callback",
        "secret_key": "unit-test-secret",
        "db_path": _tmp_db_path(),
        "secure_cookie": False,
    }
    base.update(overrides)
    return DashboardConfig(**base)


async def _seed_bot_guilds(db_path: str, guild_ids: list[int]) -> None:
    db = Database(db_path)
    await db.connect()
    try:
        for gid in guild_ids:
            await GuildRepository(db).ensure(gid, f"サークル{gid}")
    finally:
        await db.close()


def _discord_transport(*, user=None, guilds=None, token_status=200):
    """Discord API のフェイク。受け取ったリクエストも記録する。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/oauth2/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "bad"})
            return httpx.Response(200, json={"access_token": "at-123", "token_type": "Bearer"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(
                200,
                json=user
                or {"id": "42", "username": "yamada", "global_name": "山田", "avatar": "abc"},
            )
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds if guilds is not None else [])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler), seen


def _client(config: DashboardConfig, transport=None) -> TestClient:
    app = create_app(config)
    if transport is not None:
        app.state.http_client = httpx.AsyncClient(transport=transport)
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------
# 純粋関数
# ---------------------------------------------------------------------
def test_authorize_url_contains_scopes_and_state():
    url = auth.build_authorize_url(_config(), "st-1")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["1234567890"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["st-1"]
    # identify と guilds のみ（メッセージ・DM のスコープは要求しない）
    assert set(query["scope"][0].split()) == {"identify", "guilds"}


def test_has_manage_guild_bits():
    assert auth.has_manage_guild("32") is True  # MANAGE_GUILD
    assert auth.has_manage_guild("8") is True  # ADMINISTRATOR
    assert auth.has_manage_guild(str(0x20 | 0x400)) is True
    assert auth.has_manage_guild("1024") is False  # VIEW_CHANNEL のみ
    assert auth.has_manage_guild(None) is False
    assert auth.has_manage_guild("nonsense") is False


def test_select_accessible_guilds_requires_bot_presence():
    """bot が参加していないサーバーは候補に出さない。"""
    user_guilds = [
        {"id": str(G1), "name": "A大学", "permissions": "32"},
        {"id": str(G2), "name": "B大学", "permissions": "0"},
        {"id": "999", "name": "bot 未導入", "permissions": "8"},
    ]
    result = auth.select_accessible_guilds(user_guilds, {G1, G2})
    assert [g.id for g in result] == [str(G1), str(G2)]
    assert result[0].manage_guild is True
    assert result[1].manage_guild is False


def test_select_accessible_guilds_caps_session_size():
    many = [
        {"id": str(1000 + i), "name": f"g{i}", "permissions": "0"}
        for i in range(auth.MAX_SESSION_GUILDS + 10)
    ]
    bot_ids = {1000 + i for i in range(auth.MAX_SESSION_GUILDS + 10)}
    assert len(auth.select_accessible_guilds(many, bot_ids)) == auth.MAX_SESSION_GUILDS


def test_find_session_guild_only_returns_verified():
    session = {}
    auth.store_session(
        session, auth.SessionUser("42", "山田"), [auth.SessionGuild(str(G1), "A大学", True)]
    )
    assert auth.find_session_guild(session, G1) is not None
    assert auth.find_session_guild(session, G2) is None


def test_store_session_does_not_keep_access_token():
    session = {"oauth_state": "st"}
    auth.store_session(session, auth.SessionUser("42", "山田"), [])
    assert "oauth_state" not in session  # state は使い捨て
    assert "at-123" not in str(session)
    assert all("token" not in key for key in session)


# ---------------------------------------------------------------------
# ログインフロー
# ---------------------------------------------------------------------
def test_login_redirects_to_discord():
    with _client(_config()) as client:
        res = client.get("/auth/login")
        assert res.status_code == 307
        assert res.headers["location"].startswith("https://discord.com/oauth2/authorize")


def test_login_returns_503_when_unconfigured():
    with _client(_config(client_id="", client_secret="")) as client:
        assert client.get("/auth/login").status_code == 503


def test_callback_rejects_state_mismatch():
    with _client(_config()) as client:
        client.get("/auth/login")  # state をセッションへ
        res = client.get("/auth/callback?code=c&state=wrong")
        assert res.status_code == 400


def test_callback_rejects_missing_state():
    with _client(_config()) as client:
        assert client.get("/auth/callback?code=c&state=x").status_code == 400


def test_callback_establishes_session(anyio_backend=None):
    import asyncio

    config = _config()
    asyncio.run(_seed_bot_guilds(config.db_path, [G1]))
    transport, seen = _discord_transport(
        guilds=[
            {"id": str(G1), "name": "A大学 鳥人間", "permissions": "32"},
            {"id": "999", "name": "bot 未導入", "permissions": "8"},
        ]
    )
    with _client(config, transport) as client:
        login = client.get("/auth/login")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        res = client.get(f"/auth/callback?code=abc&state={state}")
        assert res.status_code == 303
        assert res.headers["location"] == "/"

        me = client.get("/api/me")
        assert me.status_code == 200
        body = me.json()
        assert body["authenticated"] is True
        assert body["user"]["name"] == "山田"
        # bot が参加しているサーバーだけが返る
        assert [g["id"] for g in body["guilds"]] == [str(G1)]
        assert body["guilds"][0]["manage_guild"] is True

    # トークン交換にはクライアントシークレットを送るが、以降の API は
    # Bearer トークンのみ（Cookie には残さない）
    assert any(r.url.path.endswith("/oauth2/token") for r in seen)
    assert any(r.url.path.endswith("/users/@me/guilds") for r in seen)


def test_callback_reports_token_failure():
    config = _config()
    transport, _ = _discord_transport(token_status=401)
    with _client(config, transport) as client:
        login = client.get("/auth/login")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        res = client.get(f"/auth/callback?code=abc&state={state}")
        assert res.status_code == 502


# ---------------------------------------------------------------------
# セッション
# ---------------------------------------------------------------------
def test_api_me_requires_login():
    with _client(_config()) as client:
        res = client.get("/api/me")
        assert res.status_code == 401
        assert res.json() == {"authenticated": False}


def test_tampered_cookie_is_rejected():
    """署名が合わない Cookie は未ログイン扱いになる。"""
    with _client(_config()) as client:
        client.cookies.set(SESSION_COOKIE, "not-a-valid-signed-session")
        assert client.get("/api/me").status_code == 401


def test_session_from_other_secret_is_rejected():
    """別の署名鍵で作られたセッションは受け付けない。"""
    import asyncio

    config = _config()
    asyncio.run(_seed_bot_guilds(config.db_path, [G1]))
    transport, _ = _discord_transport(
        guilds=[{"id": str(G1), "name": "A大学", "permissions": "32"}]
    )
    with _client(config, transport) as client:
        login = client.get("/auth/login")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        client.get(f"/auth/callback?code=abc&state={state}")
        stolen = client.cookies.get(SESSION_COOKIE)
    assert stolen

    other = _config(secret_key="another-secret", db_path=config.db_path)
    with _client(other) as client:
        client.cookies.set(SESSION_COOKIE, stolen)
        assert client.get("/api/me").status_code == 401


def test_logout_clears_session():
    import asyncio

    config = _config()
    asyncio.run(_seed_bot_guilds(config.db_path, [G1]))
    transport, _ = _discord_transport(
        guilds=[{"id": str(G1), "name": "A大学", "permissions": "32"}]
    )
    with _client(config, transport) as client:
        login = client.get("/auth/login")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        client.get(f"/auth/callback?code=abc&state={state}")
        assert client.get("/api/me").status_code == 200

        assert client.post("/auth/logout").status_code == 200
        assert client.get("/api/me").status_code == 401


def test_session_max_age_defaults_to_24_hours():
    """セッションの既定は 24 時間（D2-4）。

    Cookie には所属ギルド一覧と manage_guild が焼き込まれるため、
    退会・降格の反映がセッション寿命まで遅れる。7日は長すぎた。
    権限レベル（L1〜L4）は毎リクエスト DB から引くので古くならない。
    """
    from dashboard.config import DEFAULT_SESSION_MAX_AGE, load_config

    assert DEFAULT_SESSION_MAX_AGE == 24 * 60 * 60
    assert load_config({}).session_max_age == 24 * 60 * 60


def test_session_max_age_env_override_still_works():
    """`DASHBOARD_SESSION_MAX_AGE` での上書きは維持する（D2-4）。"""
    from dashboard.config import load_config

    config = load_config({"DASHBOARD_SESSION_MAX_AGE": "604800"})
    assert config.session_max_age == 604800


def test_unauthenticated_401_response_shape_is_stable():
    """401 の応答形が変わっていないこと（D1-6）。

    フロントは `err.status`（数値）と JSON の `detail` で分岐する。
    文言でのマッチはしないが、`detail` を持つ JSON であることは
    ログイン導線への切り替えとエラー表示の前提になっている。
    """
    app = create_app(_config())
    with TestClient(app) as client:
        # /api/me は authenticated フラグの形（フロントは status だけを見る）
        res = client.get("/api/me")
        assert res.status_code == 401
        assert res.json() == {"authenticated": False}
        # スコープ必須の API は detail 付き JSON（エラー表示に使う）
        res = client.get(f"/api/guilds/{10**17}/tables")
        assert res.status_code == 401
        body = res.json()
        assert isinstance(body.get("detail"), str) and body["detail"]
