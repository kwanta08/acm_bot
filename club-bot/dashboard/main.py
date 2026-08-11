"""ダッシュボード（FastAPI）のアプリケーションファクトリ。

**bot とは別プロセス**で動かす。Bot の常時接続が最重要資産であり、
Web 層のバグやメモリリークで Discord から切断される事態を避けるため
（設計方針 2.2 / docs/DESIGN_PUBLIC_DISTRIBUTION.md）。

起動:
    cd club-bot
    venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8000

DB は bot と同じものを共有する（読み書きはすべて guild_id スコープの
リポジトリ層を経由する）。
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from dashboard.config import SESSION_COOKIE, DashboardConfig, get_config
from dashboard.db import close_database, get_database, open_database
from dashboard.routers import auth as auth_router
from dashboard.routers import guilds as guilds_router
from utils.logger import get_logger

log = get_logger("dashboard")

# 静的ファイルはパッケージからの相対で解決する（起動時のカレントに依存しない）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """DB 接続と HTTP クライアントをアプリの生存期間に紐付ける。"""
    config: DashboardConfig = app.state.config
    await open_database(config)
    # Discord API 用。テストでは create_app 後に差し替える
    if getattr(app.state, "http_client", None) is None:
        app.state.http_client = httpx.AsyncClient(timeout=10.0)
        app.state._owns_http_client = True
    missing = config.missing()
    if missing:
        # 起動自体は止めない（/healthz と静的配信は動かせる）。
        # ログイン導線だけが使えない状態になる。
        log.warning("ダッシュボードの設定が不足しています: %s",
                    ", ".join(missing))
    log.info("ダッシュボードを起動しました（DB: %s）",
             get_database().driver_name)
    try:
        yield
    finally:
        if getattr(app.state, "_owns_http_client", False):
            await app.state.http_client.aclose()
            app.state.http_client = None
            app.state._owns_http_client = False
        await close_database()
        log.info("ダッシュボードを停止しました")


def create_app(config: DashboardConfig | None = None) -> FastAPI:
    """アプリを組み立てる（テストでは config を差し替えられる）。"""
    config = config or get_config()
    app = FastAPI(
        title="鳥人間サークル運営 Bot ダッシュボード",
        description="Discord でログインし、自分のサーバーのデータだけを"
                    "表形式で閲覧・編集します。",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,      # 公開 HTTP の攻撃面を増やさない
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = config
    app.state.http_client = None

    # 署名付きセッション（サーバー側セッションストアを持たない）。
    # 署名鍵が無い場合は推測不能な一時鍵を使う（プロセス再起動でログアウト）。
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key or secrets.token_urlsafe(48),
        session_cookie=SESSION_COOKIE,
        max_age=config.session_max_age,
        same_site="lax",       # OAuth のリダイレクトで Cookie を保持できる最小設定
        https_only=config.secure_cookie,
    )

    app.include_router(auth_router.router)
    app.include_router(guilds_router.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        """死活監視用（認証不要・情報を漏らさない）。"""
        db = get_database(required=False)
        healthy = bool(db and await db.is_healthy())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded"},
            status_code=200 if healthy else 503)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(INDEX_HTML)

    try:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    except RuntimeError:  # 静的ディレクトリが無い環境（テスト等）
        log.warning("静的ファイルディレクトリが見つかりません: %s", STATIC_DIR)

    return app


app = create_app()
