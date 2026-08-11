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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dashboard.config import DashboardConfig, get_config
from dashboard.db import close_database, get_database, open_database
from utils.logger import get_logger

log = get_logger("dashboard")

# 静的ファイルはパッケージからの相対で解決する（起動時のカレントに依存しない）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """DB 接続をアプリの生存期間に紐付ける。"""
    config: DashboardConfig = app.state.config
    await open_database(config)
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
