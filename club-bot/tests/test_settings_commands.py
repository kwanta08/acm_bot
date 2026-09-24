"""`/settings_*` コマンドの、設定キーのホワイトリスト（H1-3）。

`utils/settings_spec.py` の仕様表をコマンド層で効かせることを、コマンドの
callback を直接呼んで確かめる（権限の check は test_settings_role.py と同じく別途）。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import sys
import tempfile
import textwrap
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.settings import Settings
from config import config
from repositories.settings_repository import SettingsRepository
from utils.db import Database

G1 = 100000000000000001
SECRET = "s3cr3t-value-that-must-not-leak"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _Interaction:
    def __init__(self, guild_id: int = G1):
        self.guild = SimpleNamespace(id=guild_id)
        self.guild_id = guild_id
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, *args, **kwargs):
        self.sent.append({"args": args, **kwargs})

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")

    def everything_sent(self) -> str:
        """返信の**全呼び出し**の content と Embed を丸ごと文字列にする。"""
        parts = []
        for call in self.sent:
            parts.append(repr(call.get("args")))
            parts.append(str(call.get("content")))
            embed = call.get("embed")
            if embed is not None:
                parts.append(json.dumps(embed.to_dict(), ensure_ascii=False))
        return "\n".join(parts)


async def _make():
    db = Database(_tmp_db_path())
    await db.connect()
    return db, Settings(SimpleNamespace(db=db))


def _cleanup():
    config.clear_guild_cache()


# =====================================================================
# /settings_get: 設定キー以外の値を表示しない
# =====================================================================
@pytest.mark.parametrize(
    "name",
    [
        "ACM_H13_CANARY",  # どの一覧にも無い任意の名前（ホワイトリストであることの証明）
        "DISCORD_TOKEN",
        "ENCRYPTION_KEY",
        "DATABASE_URL",
        "discord_token",  # Windows の os.environ は大文字小文字を区別しない
        " DISCORD_TOKEN ",
    ],
)
def test_settings_get_never_reveals_environment_values(name):
    """仕様表に無いキーは**値を出さず、未知キーとして拒否する**。

    「値が出ていない」だけだと、例外で「取得に失敗しました」になっても緑になる。
    拒否の文面であることも見る。
    """

    async def _main():
        db, cog = await _make()
        try:
            with mock.patch.dict(os.environ, {name.strip().upper(): SECRET}):
                interaction = _Interaction()
                await Settings.settings_get.callback(cog, interaction, setting_key=name)
            assert SECRET not in interaction.everything_sent(), "環境変数の値を表示している"
            assert "という設定キーはありません" in interaction.text
            assert "取得に失敗" not in interaction.text
        finally:
            await db.close()
            _cleanup()

    run(_main())


def test_settings_get_still_shows_env_fallback_for_spec_keys():
    """仕様表のキーは、DB に無ければ効いている環境変数の値を従来どおり表示する（対照）。"""

    async def _main():
        db, cog = await _make()
        try:
            with mock.patch.dict(os.environ, {"DEFAULT_TASK_CHANNEL_ID": "424242"}):
                interaction = _Interaction()
                await Settings.settings_get.callback(
                    cog, interaction, setting_key="default_task_channel_id"
                )
            assert "424242" in interaction.text
            assert "環境変数" in interaction.text

            await SettingsRepository(db).set(G1, "DEFAULT_TASK_CHANNEL_ID", "777")
            interaction = _Interaction()
            await Settings.settings_get.callback(
                cog, interaction, setting_key="DEFAULT_TASK_CHANNEL_ID"
            )
            assert "777" in interaction.text
        finally:
            await db.close()
            _cleanup()

    run(_main())


@pytest.mark.parametrize("key", ["SETUP_VERSION", "TZ", "DB_PATH"])
def test_settings_get_shows_only_the_db_value_for_internal_and_legacy_keys(key):
    """内部キー・旧運用のキーは DB の値だけ。環境変数は読まない。"""

    async def _main():
        db, cog = await _make()
        try:
            with mock.patch.dict(os.environ, {key: SECRET}):
                interaction = _Interaction()
                await Settings.settings_get.callback(cog, interaction, setting_key=key)
                assert SECRET not in interaction.everything_sent()
                assert "設定されていません" in interaction.text

                await SettingsRepository(db).set(G1, key, "stored-in-db")
                interaction = _Interaction()
                await Settings.settings_get.callback(cog, interaction, setting_key=key)
                assert "stored-in-db" in interaction.text
                assert SECRET not in interaction.everything_sent()
        finally:
            await db.close()
            _cleanup()

    run(_main())


def test_settings_get_does_not_touch_the_environment_directly():
    """構造: `settings_get` は環境変数を自分で引かない（`env_fallback` だけが引く）。"""
    source = textwrap.dedent(inspect.getsource(Settings.settings_get.callback))
    names = {
        getattr(node, "attr", None) or getattr(node, "id", None)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute | ast.Name)
    }
    assert "getenv" not in names
    assert "environ" not in names
