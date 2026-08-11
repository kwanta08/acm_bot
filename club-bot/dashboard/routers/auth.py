"""ログイン・ログアウト・現在の利用者を返すルーター（P2-2）。

フロー:
    GET /auth/login    → state を発行してセッションへ保存し、Discord へリダイレクト
    GET /auth/callback → state を検証 → コードをトークンへ交換 →
                         利用者と所属サーバーを取得 → セッションを確立
    POST /auth/logout  → セッションを破棄
    GET  /api/me       → ログイン中の利用者とアクセスできるサーバー一覧
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from dashboard import auth
from dashboard.config import DashboardConfig
from dashboard.db import get_database
from repositories.guild_repository import GuildRepository
from utils.logger import get_logger

log = get_logger("dashboard.auth")

router = APIRouter()


def _config(request: Request) -> DashboardConfig:
    return request.app.state.config


def _http_client(request: Request) -> httpx.AsyncClient:
    """HTTP クライアント（テストではアプリ状態に差し替えたものが入る）。"""
    return request.app.state.http_client


async def _bot_guild_ids() -> set[int]:
    """bot が参加しているサーバーの ID 集合（guilds 台帳）。"""
    rows = await GuildRepository(get_database()).list_all()
    return {int(row["guild_id"]) for row in rows}


@router.get("/auth/login", include_in_schema=False)
async def login(request: Request):
    config = _config(request)
    if not config.oauth_ready:
        return JSONResponse(
            {"detail": "ダッシュボードのログイン設定が未完了です。"
                       "運営者にお問い合わせください。"},
            status_code=503)
    state = auth.new_state()
    request.session[auth.SESSION_STATE_KEY] = state
    return RedirectResponse(auth.build_authorize_url(config, state),
                            status_code=307)


@router.get("/auth/callback", include_in_schema=False)
async def callback(request: Request, code: str = "", state: str = ""):
    config = _config(request)
    expected = request.session.pop(auth.SESSION_STATE_KEY, None)
    # state はワンタイム。欠落・不一致は CSRF の可能性があるため拒否する
    if not expected or not state or not code or state != expected:
        return JSONResponse(
            {"detail": "ログインを完了できませんでした。"
                       "最初からやり直してください。"},
            status_code=400)

    client = _http_client(request)
    try:
        token = await auth.exchange_code(config, code, client)
        user = await auth.fetch_user(client, token)
        user_guilds = await auth.fetch_guilds(client, token)
    except auth.OAuthError as e:
        return JSONResponse({"detail": str(e)}, status_code=502)

    guilds = auth.select_accessible_guilds(user_guilds, await _bot_guild_ids())
    auth.store_session(request.session, user, guilds)
    log.info("ダッシュボードにログインしました（user=%s, guilds=%d）",
             user.id, len(guilds))
    return RedirectResponse("/", status_code=303)


@router.post("/auth/logout", include_in_schema=False)
async def logout(request: Request):
    request.session.clear()
    return JSONResponse({"status": "logged_out"})


@router.get("/api/me")
async def me(request: Request):
    """ログイン状態とアクセスできるサーバーを返す。

    ここで返すサーバーは「利用者が所属し、かつ bot も参加している」もののみ。
    """
    user = auth.read_user(request.session)
    if user is None:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {
        "authenticated": True,
        "user": {"id": user.id, "name": user.name, "avatar": user.avatar},
        "guilds": [
            {"id": g.id, "name": g.name, "manage_guild": g.manage_guild}
            for g in auth.read_guilds(request.session)
        ],
    }
