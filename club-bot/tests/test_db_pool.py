"""asyncpg コネクションプールの設定テスト（P2-7）。

bot とダッシュボードは別プロセスで独立したプールを持つ。旧既定の
max_size=5 では、20分ごとの同期ジョブとダッシュボードの同時読み取りが
重なると枯渇しうるため既定値を引き上げ、環境変数で調整できるようにした。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.db import (
    DEFAULT_POOL_MAX_SIZE,
    DEFAULT_POOL_MIN_SIZE,
    POOL_COMMAND_TIMEOUT,
    Database,
    resolve_pool_size,
)


def test_defaults_are_larger_than_legacy_five():
    """旧既定（5）より大きいこと。"""
    assert DEFAULT_POOL_MAX_SIZE > 5
    assert resolve_pool_size(None, None, env={}) == (DEFAULT_POOL_MIN_SIZE, DEFAULT_POOL_MAX_SIZE)


def test_env_overrides_defaults():
    assert resolve_pool_size(
        None, None, env={"DB_POOL_MIN_SIZE": "2", "DB_POOL_MAX_SIZE": "20"}
    ) == (2, 20)


def test_arguments_take_precedence_over_env():
    assert resolve_pool_size(3, 7, env={"DB_POOL_MIN_SIZE": "2", "DB_POOL_MAX_SIZE": "20"}) == (
        3,
        7,
    )


def test_invalid_env_falls_back_to_defaults():
    assert resolve_pool_size(
        None, None, env={"DB_POOL_MIN_SIZE": "たくさん", "DB_POOL_MAX_SIZE": ""}
    ) == (DEFAULT_POOL_MIN_SIZE, DEFAULT_POOL_MAX_SIZE)


def test_sizes_are_normalized():
    # 0 や負値は 1 以上へ、min > max は max に合わせる
    assert resolve_pool_size(0, 0, env={}) == (1, 1)
    assert resolve_pool_size(-5, 3, env={}) == (1, 3)
    assert resolve_pool_size(9, 4, env={}) == (4, 4)


def test_database_exposes_resolved_sizes():
    db = Database(
        "./ignored.db", database_url="postgresql://x/y", pool_min_size=2, pool_max_size=12
    )
    assert (db.pool_min_size, db.pool_max_size) == (2, 12)


def test_pool_stats_is_none_for_sqlite():
    assert Database("./ignored.db").pool_stats() is None


def test_pool_stats_reports_usage():
    class _FakePool:
        def get_size(self):
            return 4

        def get_idle_size(self):
            return 1

    db = Database("./ignored.db", database_url="postgresql://x/y", pool_max_size=10)
    db._pool = _FakePool()
    stats = db.pool_stats()
    assert stats["max_size"] == 10
    assert stats["size"] == 4
    assert stats["idle"] == 1
    assert stats["in_use"] == 3
    # 接続文字列や認証情報は含めない
    assert "postgresql" not in str(stats)


def test_command_timeout_is_bounded():
    """異常時に接続を握り続けないよう上限時間を設けている。"""
    assert 0 < POOL_COMMAND_TIMEOUT <= 60


@pytest.mark.parametrize("dashboard_max", ["8", "16"])
def test_dashboard_pool_is_independent(dashboard_max):
    """ダッシュボードは bot と別の値を設定できる。"""
    fastapi = pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")
    assert fastapi is not None
    from dashboard.config import load_config

    config = load_config({"DASHBOARD_DB_POOL_MAX_SIZE": dashboard_max, "DB_POOL_MAX_SIZE": "10"})
    assert config.db_pool_max_size == int(dashboard_max)
    # 未指定なら None（utils/db.py 側の既定・環境変数にフォールバック）
    assert load_config({}).db_pool_max_size is None
