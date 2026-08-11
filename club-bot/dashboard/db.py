"""ダッシュボードの DB 接続ライフサイクル。

bot と同じ utils.db.Database を使う（SQLite / PostgreSQL 両対応）。
**別プロセス**のためコネクションプールは独立している。

リポジトリ層はすべて guild_id を必須引数に取るため、ここでは
「接続を1つ持つ」ことだけを担当し、スコープの強制は
dashboard/security.py が行う。
"""
from __future__ import annotations

from dashboard.config import DashboardConfig
from utils.db import Database

_db: Database | None = None


async def open_database(config: DashboardConfig) -> Database:
    """接続を開く（アプリ起動時に1回）。"""
    global _db
    if _db is not None:
        return _db
    db = Database(config.db_path, database_url=config.database_url)
    await db.connect()
    _db = db
    return db


async def close_database() -> None:
    """接続を閉じる（アプリ停止時）。"""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def get_database(*, required: bool = True) -> Database:
    """開いている接続を返す。

    required=False のときは未接続でも None を返す（/healthz 用）。
    """
    if _db is None and required:
        raise RuntimeError("データベースが未接続です（lifespan 外での呼び出し）")
    return _db  # type: ignore[return-value]


def set_database(db: Database | None) -> None:
    """テスト用に接続を差し替える。"""
    global _db
    _db = db
