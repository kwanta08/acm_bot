"""ダッシュボード（FastAPI）の雛形テスト（P2-1）。

dashboard/requirements.txt を入れていない環境ではスキップする
（bot 本体のテストは依存を増やさずに動き続ける）。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")
from fastapi.testclient import TestClient

from dashboard import db as dashboard_db
from dashboard.config import DashboardConfig, load_config
from dashboard.main import create_app


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(**overrides) -> DashboardConfig:
    base = {
        "client_id": "cid",
        "client_secret": "secret",
        "redirect_uri": "https://example.com/auth/callback",
        "secret_key": "unit-test-secret",
        "db_path": _tmp_db_path(),
        "secure_cookie": False,
    }
    base.update(overrides)
    return DashboardConfig(**base)


# ---------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------
def test_load_config_reads_env():
    config = load_config(
        {
            "DISCORD_CLIENT_ID": " 123 ",
            "DISCORD_CLIENT_SECRET": '"sec"',
            "DASHBOARD_REDIRECT_URI": "https://example.com/auth/callback",
            "DASHBOARD_SECRET_KEY": "key",
            "DB_PATH": "./data/club.db",
            "DASHBOARD_SESSION_MAX_AGE": "3600",
        }
    )
    # 前後の空白と囲い引用符は除去される
    assert config.client_id == "123"
    assert config.client_secret == "sec"
    assert config.session_max_age == 3600
    assert config.oauth_ready is True
    assert config.missing() == []
    assert config.database_url is None


def test_config_reports_missing_values():
    config = load_config({})
    assert config.oauth_ready is False
    assert "DASHBOARD_SECRET_KEY" in config.missing()
    assert "DISCORD_CLIENT_ID" in config.missing()


def test_secure_cookie_defaults_to_true():
    assert load_config({}).secure_cookie is True
    assert load_config({"DASHBOARD_SECURE_COOKIE": "0"}).secure_cookie is False


def test_dashboard_needs_no_bot_token():
    """ダッシュボードは Discord Bot トークンを一切参照しない。"""
    config = load_config({"DISCORD_TOKEN": "should-be-ignored"})
    assert not hasattr(config, "discord_token")


# ---------------------------------------------------------------------
# アプリ
# ---------------------------------------------------------------------
def test_healthz_reports_ok_with_database():
    app = create_app(_config())
    with TestClient(app) as client:
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
    # ライフスパン終了後は接続が閉じられている
    assert dashboard_db.get_database(required=False) is None


def test_index_is_served():
    app = create_app(_config())
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "ダッシュボード" in res.text


def test_static_files_are_served():
    app = create_app(_config())
    with TestClient(app) as client:
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200


def test_openapi_docs_are_disabled():
    """公開 HTTP の攻撃面を増やさないため API ドキュメントは配信しない。"""
    app = create_app(_config())
    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


def test_app_starts_even_when_oauth_unconfigured():
    """設定不足でも起動は止めない（/healthz と静的配信は動く）。"""
    app = create_app(_config(client_id="", client_secret="", redirect_uri=""))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_preview_page_is_served_but_not_linked():
    """コンポーネントのプレビュー（D0-4）。

    開発用の静的ページとして配信される一方、本番の導線（index.html）からは
    リンクされない。認証の後ろに置かない代わりに、データを一切含めない
    （ダミーはハードコードの文字列のみ）。
    """
    app = create_app(_config())
    with TestClient(app) as client:
        res = client.get("/static/preview.html")
        assert res.status_code == 200
        # style.css 以外の CSS を持たない（プレビュー専用スタイルの禁止）
        assert "<style" not in res.text
        assert 'style="' not in res.text

        index = client.get("/").text
        assert "preview.html" not in index
