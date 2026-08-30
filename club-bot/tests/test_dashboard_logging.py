"""ダッシュボードの INFO ログ（D2-5）。

- `setup_logging()` に出力ファイル名の引数がある（bot 側の既定は `bot.log` のまま）
- `create_app()` がダッシュボード用のファイル名（`dashboard.log`）でロガーを初期化する
- レベルは環境変数 `DASHBOARD_LOG_LEVEL` で上書きできる
- 出力ディレクトリは `LOG_DIR` で上書きできる（本番の systemd は
  ProtectHome=read-only ＋ ReadWritePaths のため、相対 `logs/` に書けない。
  deploy/club-bot-dashboard.service が LOG_DIR を実際の書き込み先へ向ける）

**同じログファイルを bot と2プロセスで掴むと RotatingFileHandler の
ローテーションで取り合いになる**ため、ファイル名の分離が本体。
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.logger import setup_logging


def _fresh_root():
    """root ロガーのハンドラを退避して空にする（試験後に戻す）。"""
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    return root, saved


def test_setup_logging_accepts_a_filename(tmp_path):
    root, saved = _fresh_root()
    try:
        with mock.patch.dict(os.environ, {"LOG_DIR": str(tmp_path)}):
            setup_logging(filename="dashboard.log")
        files = [
            h.baseFilename for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert files, "ファイルハンドラが登録されていない"
        assert os.path.basename(files[0]) == "dashboard.log"
        assert os.path.dirname(files[0]) == str(tmp_path)
    finally:
        for h in root.handlers:
            h.close()
        root.handlers = saved


def test_bot_default_filename_is_still_bot_log():
    """bot 側の既定は bot.log のまま（呼び出し側の変更が不要）。"""
    sig = inspect.signature(setup_logging)
    assert sig.parameters["filename"].default == "bot.log"


def test_create_app_initializes_dashboard_logging():
    fastapi = pytest.importorskip(
        "fastapi", reason="dashboard/requirements.txt が未インストール"
    )
    del fastapi
    from dashboard.config import DashboardConfig
    from dashboard.main import create_app

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    config = DashboardConfig(secret_key="k", db_path=db_path, secure_cookie=False)

    with mock.patch("dashboard.main.setup_logging") as m:
        create_app(config)
    m.assert_called_once()
    assert m.call_args.kwargs.get("filename") == "dashboard.log"

    # 既定レベルは INFO
    assert m.call_args.kwargs.get("level") == logging.INFO

    # 環境変数でレベルを上書きできる
    with (
        mock.patch.dict(os.environ, {"DASHBOARD_LOG_LEVEL": "WARNING"}),
        mock.patch("dashboard.main.setup_logging") as m2,
    ):
        create_app(config)
    assert m2.call_args.kwargs.get("level") == logging.WARNING


def test_dashboard_logger_can_emit_info_after_create_app():
    fastapi = pytest.importorskip(
        "fastapi", reason="dashboard/requirements.txt が未インストール"
    )
    del fastapi
    from dashboard.config import DashboardConfig
    from dashboard.main import create_app

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    create_app(DashboardConfig(secret_key="k", db_path=db_path, secure_cookie=False))
    assert logging.getLogger("dashboard").isEnabledFor(logging.INFO)
