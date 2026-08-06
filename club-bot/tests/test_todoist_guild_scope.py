"""Todoist 連携のギルド別運用に関するガードテスト。

既存実装（/todoist-setup によるギルド別暗号化登録）が
将来の変更で後退しないことを確認する。

- 環境変数の Todoist トークンなしで起動できる（validate は DISCORD_TOKEN のみ必須）
- 環境変数フォールバックが復活していない（設計: フォールバックを残さない）
- 未設定ギルドでは無効サービス + 「未設定です」案内で終了する

実行: venv/bin/python -m pytest tests/
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.tasks import Tasks  # noqa: E402
from config import Config  # noqa: E402
from services.todoist_service import TodoistServiceManager  # noqa: E402
from utils.db import Database  # noqa: E402

G1 = 100000000000000001  # ギルド1


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 側で新規作成させる
    return path


def test_startup_without_todoist_env():
    """DISCORD_TOKEN だけあれば起動バリデーションを通過すること。"""
    c = Config()
    c.discord_token = "dummy-token"
    assert c.validate() == []

    # Todoist トークンの環境変数フォールバックが復活していないこと
    # （設計: 平文トークンは .env / settings に置かず /todoist-setup で登録）
    for name in ("TODOIST_API_TOKEN", "TODOIST_TOKEN"):
        assert not hasattr(c, name.lower()), f"環境変数フォールバック痕跡: {name}"


def test_unconfigured_guild_disabled_and_guided():
    """未設定ギルドでは例外なく無効サービスが返り、案内 Embed が用意されていること。"""
    db = Database(_tmp_db_path())
    run(db.connect())
    try:
        manager = TodoistServiceManager(db)
        svc = run(manager.for_guild(G1))
        assert svc.enabled is False
        assert run(manager.is_configured(G1)) is False

        embed = Tasks._todoist_unconfigured_embed()
        assert "未設定" in (embed.title or "")
        assert "/todoist-setup" in (embed.description or "")
    finally:
        run(db.close())
