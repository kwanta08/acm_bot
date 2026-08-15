"""【最重要】guild_id スコープ強制のテスト（P2-3）。

本プロジェクトが最も避けるべき事故は「他大学のデータが見える」こと。
ここでは **他ギルドのデータが取得できないこと** を多角的に検証する。

1. セッションに無いサーバーの URL を叩いても 403（データを返さない）
2. 未ログインなら 401
3. 返ってくる件数が、必ず自分のサーバーのものだけであること
4. GuildScope はセッション照合を通らないと生成されないこと
5. scope.bind() が検証済み guild_id を固定し、別 ID を渡せないこと
6. ダッシュボードのルートが生の guild_id をハンドラ引数に取っていないこと
   （＝ require_guild_scope を迂回する経路が無いこと）を静的に検査
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import tempfile
from typing import get_args, get_type_hints
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")

import httpx
from fastapi.testclient import TestClient

from dashboard import security
from dashboard.config import DashboardConfig
from dashboard.main import create_app
from dashboard.security import GuildScope
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.task_repository import TaskRepository
from utils.db import Database
from utils.permissions import Level

# A大学（利用者が所属）と B大学（他大学。絶対に見えてはならない）
GUILD_A = 100000000000000001
GUILD_B = 200000000000000002

USER_ID = "42"
NOW = "2026-08-11 10:00"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(db_path: str, **overrides) -> DashboardConfig:
    base = {
        "client_id": "cid",
        "client_secret": "secret",
        "redirect_uri": "https://example.com/auth/callback",
        "secret_key": "unit-test-secret",
        "db_path": db_path,
        "secure_cookie": False,
    }
    base.update(overrides)
    return DashboardConfig(**base)


async def _seed(db_path: str) -> None:
    """2つのサーバーに、見分けのつくデータを入れる。"""
    db = Database(db_path)
    await db.connect()
    try:
        guilds = GuildRepository(db)
        members = MemberRepository(db)
        tasks = TaskRepository(db)
        progress = ProgressRepository(db)

        await guilds.ensure(GUILD_A, "A大学 鳥人間")
        await guilds.ensure(GUILD_B, "B大学 鳥人間")

        # A大学: メンバー1・班1・タスク1・進捗ノード1
        await members.upsert_member(GUILD_A, USER_ID, "山田")
        await members.upsert_team(GUILD_A, "wing", "主翼班")
        await tasks.create_task(GUILD_A, "A大学のタスク", USER_ID)
        await progress.upsert_node(GUILD_A, "a1", name="A大学の機体", now_text=NOW)

        # B大学: 件数を変えておく（漏れたら数で分かる）
        for i in range(3):
            await members.upsert_member(GUILD_B, f"90{i}", f"B大学部員{i}")
        await members.upsert_team(GUILD_B, "tail", "尾翼班")
        await members.upsert_team(GUILD_B, "elec", "電装班")
        for i in range(5):
            await tasks.create_task(GUILD_B, f"B大学のタスク{i}", "900")
        for i in range(7):
            await progress.upsert_node(GUILD_B, f"b{i}", name=f"B大学の部品{i}", now_text=NOW)
    finally:
        await db.close()


def _discord_transport(guilds: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(
                200, json={"id": USER_ID, "username": "yamada", "global_name": "山田"}
            )
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _login(client: TestClient) -> None:
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    assert client.get(f"/auth/callback?code=c&state={state}").status_code == 303


def _client_logged_into_a(db_path: str, *, permissions: str = "0") -> TestClient:
    """A大学だけに所属している利用者としてログイン済みのクライアント。"""
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(
        transport=_discord_transport(
            [
                {"id": str(GUILD_A), "name": "A大学 鳥人間", "permissions": permissions},
            ]
        )
    )
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    _login(client)
    return client


# ---------------------------------------------------------------------
# 1〜3. 他ギルドのデータが取得できないこと
# ---------------------------------------------------------------------
def test_other_guild_is_forbidden():
    """セッションに無いサーバーの URL は 403（データを返さない）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_B}/summary")
        assert res.status_code == 403
        # 本文に他サーバーの情報を含めない
        body = res.text
        assert "B大学" not in body
        assert "尾翼" not in body
    finally:
        client.__exit__(None, None, None)


def test_own_guild_returns_only_own_data():
    """自分のサーバーの件数だけが返る（他サーバーの行が混ざらない）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/summary")
        assert res.status_code == 200
        counts = res.json()["counts"]
        # A大学の実データ（B大学は 3/2/5/7 件なので混入すれば必ず露見する）
        assert counts == {"members": 1, "teams": 1, "progress_nodes": 1, "open_tasks": 1}
    finally:
        client.__exit__(None, None, None)


def test_requires_login():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    app = create_app(_config(db_path))
    with TestClient(app) as client:
        assert client.get(f"/api/guilds/{GUILD_A}/summary").status_code == 401


def test_logout_revokes_access():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path)
    try:
        assert client.get(f"/api/guilds/{GUILD_A}/summary").status_code == 200
        client.post("/auth/logout")
        assert client.get(f"/api/guilds/{GUILD_A}/summary").status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_guild_id_variants_do_not_bypass_check():
    """パスの表記ゆれ・不正値でスコープ検証を迂回できない。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path)
    try:
        for path in (
            f"/api/guilds/{GUILD_B}/summary",
            f"/api/guilds/0{GUILD_B}/summary",  # 先頭ゼロ
            "/api/guilds/0/summary",  # ge=1 で弾く
            "/api/guilds/-1/summary",
            "/api/guilds/abc/summary",
        ):
            status = client.get(path).status_code
            assert status in (403, 422), f"{path} -> {status}"
    finally:
        client.__exit__(None, None, None)


def test_query_and_body_guild_id_are_ignored():
    """クエリやボディで別サーバーを指定しても効かない。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/summary", params={"guild_id": str(GUILD_B)})
        assert res.status_code == 200
        assert res.json()["guild"]["id"] == str(GUILD_A)
        assert res.json()["counts"]["members"] == 1
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 4〜5. スコープオブジェクトの性質
# ---------------------------------------------------------------------
def test_scope_bind_fixes_guild_id():
    """bind したリポジトリは検証済み guild_id しか見ない。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            scope = GuildScope(
                guild_id=GUILD_A,
                guild_name="A大学",
                user_id=USER_ID,
                user_name="山田",
                level=Level.L1,
                manage_guild=False,
            )
            progress = scope.bind(ProgressRepository(db))
            nodes = await progress.list_nodes()
            assert [n["node_id"] for n in nodes] == ["a1"]
            # 別ギルドの ID を渡す余地が無い（第1引数は自動で差し込まれる）
            assert await progress.count_nodes() == 1
        finally:
            await db.close()

    asyncio.run(_main())


def test_scope_require_level():
    scope = GuildScope(
        guild_id=GUILD_A,
        guild_name="A大学",
        user_id=USER_ID,
        user_name="山田",
        level=Level.L1,
        manage_guild=False,
    )
    scope.require(Level.L1)
    with pytest.raises(Exception) as exc:
        scope.require(Level.L2)
    assert getattr(exc.value, "status_code", None) == 403


def test_resolve_level_from_db():
    """L4=サーバー管理権限 / L2=班長 / L1=一般。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            assert await security.resolve_level(db, GUILD_A, USER_ID, True) == Level.L4
            assert await security.resolve_level(db, GUILD_A, USER_ID, False) == Level.L1
            await MemberRepository(db).set_leader(GUILD_A, USER_ID, True)
            assert await security.resolve_level(db, GUILD_A, USER_ID, False) == Level.L2
            # 未登録の利用者は既定の L1
            assert await security.resolve_level(db, GUILD_A, "999", False) == Level.L1
        finally:
            await db.close()

    asyncio.run(_main())


def test_manage_guild_user_gets_level4_via_api():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path, permissions="32")
    try:
        viewer = client.get(f"/api/guilds/{GUILD_A}/summary").json()["viewer"]
        assert viewer["level"] == 4
        assert viewer["manage_guild"] is True
        assert viewer["can_edit"] is True
    finally:
        client.__exit__(None, None, None)


def test_plain_member_cannot_edit():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client_logged_into_a(db_path, permissions="0")
    try:
        viewer = client.get(f"/api/guilds/{GUILD_A}/summary").json()["viewer"]
        assert viewer["level"] == 1
        assert viewer["can_edit"] is False
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 6. 迂回経路が存在しないことの静的検査
# ---------------------------------------------------------------------
ROUTER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "routers"
)


def _router_functions(path: str) -> list[ast.AsyncFunctionDef]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and any(_is_route_decorator(d) for d in n.decorator_list)
    ]


def _is_route_decorator(node: ast.expr) -> bool:
    func = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"get", "post", "patch", "put", "delete"}
        and isinstance(func.value, ast.Name)
        and func.value.id == "router"
    )


def test_no_route_takes_raw_guild_id():
    """guild_id を直接受け取るハンドラが無いこと。

    受け取れてしまうと、セッション照合を通さずリポジトリへ渡す事故が
    起こりうる。スコープ付き API は必ず GuildScope を受け取る。
    """
    violations: list[str] = []
    for name in sorted(os.listdir(ROUTER_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(ROUTER_DIR, name)
        for func in _router_functions(path):
            args = [a.arg for a in func.args.args + func.args.kwonlyargs]
            if "guild_id" in args:
                violations.append(f"{name}:{func.name}")
    assert not violations, (
        "ルートハンドラが生の guild_id を受け取っています"
        "（require_guild_scope 経由にしてください）: " + ", ".join(violations)
    )


def _iter_routes(router, seen=None):
    """include_router で入れ子になったルートも含めて列挙する。"""
    seen = seen if seen is not None else set()
    for route in getattr(router, "routes", []):
        if id(route) in seen:
            continue
        seen.add(id(route))
        if getattr(route, "path", None) and hasattr(route, "endpoint"):
            yield route
        # starlette の新しい版は include_router を _IncludedRouter で包む
        nested = getattr(route, "original_router", None) or route
        if nested is not route or hasattr(nested, "routes"):
            yield from _iter_routes(nested, seen)


def test_scoped_routes_depend_on_guild_scope():
    """/api/guilds/{guild_id}/... の全ルートが GuildScope を要求する。"""
    app = create_app(_config(_tmp_db_path()))
    checked = 0
    for route in _iter_routes(app.router):
        path = route.path
        if not path.startswith("/api/guilds/"):
            continue
        checked += 1
        hints = get_type_hints(route.endpoint, include_extras=True)
        scoped = any(hint is GuildScope or GuildScope in get_args(hint) for hint in hints.values())
        assert scoped, f"{path} が GuildScope を要求していない"
    assert checked >= 1, "スコープ付きルートが見つからない（検査が空振り）"
