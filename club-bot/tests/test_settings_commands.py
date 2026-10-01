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
    # /settings_set は _after_change で config.load_from_db(db) を呼び、共有の
    # config が DB 接続を握る。他のテストへ持ち越さない
    config._db = None
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


# =====================================================================
# /settings_set: 未知のキー・種別に合わない値を保存しない
# =====================================================================
async def _set(cog, key: str, value: str) -> _Interaction:
    interaction = _Interaction()
    await Settings.settings_set.callback(cog, interaction, setting_key=key, value=value)
    return interaction


def test_settings_set_rejects_unknown_key():
    async def _main():
        db, cog = await _make()
        try:
            interaction = await _set(cog, "COMPETITON_DATE", "2026-07-25")
            assert await SettingsRepository(db).get_all(G1) == {}, "未知のキーを保存している"
            assert "という設定キーはありません" in interaction.text
            assert "COMPETITION_DATE" in interaction.text, "綴りの近い候補を出していない"
        finally:
            await db.close()
            _cleanup()

    run(_main())


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [
        ("COMPETITION_DATE", "2026-07-25", "2026/07/25"),
        ("DEFAULT_TASK_CHANNEL_ID", "123", "#general"),
        ("WEEKLY_DIGEST_WEEKDAY", "3", "7"),
        ("SCHEDULE_UI_STYLE", "reaction", "button"),
        ("DATA_RETENTION_DAYS", "30", "3.5"),
    ],
)
def test_settings_set_rejects_invalid_value(key, good, bad):
    """不正な値は保存しない。**既存の正しい値も上書きしない**。"""

    async def _main():
        db, cog = await _make()
        try:
            await _set(cog, key, good)
            interaction = await _set(cog, key, bad)
            assert await SettingsRepository(db).get(G1, key) == good, "正しい値を上書きした"
            assert key in interaction.text
            assert "保存" not in (interaction.sent[-1]["embed"].title or "")
        finally:
            await db.close()
            _cleanup()

    run(_main())


def test_settings_set_saves_the_normalized_value():
    async def _main():
        db, cog = await _make()
        try:
            interaction = await _set(cog, "SCHEDULE_UI_STYLE", " Reaction ")
            assert await SettingsRepository(db).get(G1, "SCHEDULE_UI_STYLE") == "reaction"
            assert "reaction" in interaction.text
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.schedule_ui_style == "reaction"
        finally:
            await db.close()
            _cleanup()

    run(_main())


@pytest.mark.parametrize(
    ("key", "raw", "stored"),
    [
        ("DEFAULT_TASK_CHANNEL_ID", "<#00123>", "123"),
        ("WELCOME_ENABLED", "true", "1"),
        ("COMPETITION_DATE", " 2026-07-25 ", "2026-07-25"),
    ],
)
def test_settings_set_stores_the_normalized_form(key, raw, stored):
    """生の入力ではなく保存形で書く（読み出し側が必ず読める形）。"""

    async def _main():
        db, cog = await _make()
        try:
            await _set(cog, key, raw)
            assert await SettingsRepository(db).get(G1, key) == stored
        finally:
            await db.close()
            _cleanup()

    run(_main())


def test_settings_set_uppercases_the_key():
    """小文字・空白付きのキーは仕様表のキーとして保存する（別の行を作らない）。"""

    async def _main():
        db, cog = await _make()
        try:
            await _set(cog, " competition_date ", "2026-07-25")
            stored = await SettingsRepository(db).get_all(G1)
            assert stored == {"COMPETITION_DATE": "2026-07-25"}
        finally:
            await db.close()
            _cleanup()

    run(_main())


@pytest.mark.parametrize(
    "marker",
    ["AUTO_SETUP_COMPLETED_AT", "SETUP_VERSION", "GUILD_NAME", "GUILD_COMMANDS_CLEARED_AT"],
)
def test_internal_marker_is_refused_by_the_command_but_not_by_the_repository(marker):
    """拒否は**コマンド層だけ**。起動時セットアップは SettingsRepository.set() で書ける。"""

    async def _main():
        db, cog = await _make()
        try:
            interaction = await _set(cog, marker, "tampered")
            assert await SettingsRepository(db).get(G1, marker) is None
            assert "内部" in interaction.text

            await SettingsRepository(db).set(G1, marker, "written-by-startup")
            assert await SettingsRepository(db).get(G1, marker) == "written-by-startup"
        finally:
            await db.close()
            _cleanup()

    run(_main())


# =====================================================================
# /settings_delete: 入力をそのまま照合する
# =====================================================================
@pytest.mark.parametrize("junk_key", ["competition_date", " COMPETITION_DATE"])
def test_settings_delete_matches_the_key_exactly(junk_key):
    """以前の /settings_set が作ったゴミ行を消すとき、有効な行を巻き込まない。"""

    async def _main():
        db, cog = await _make()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "COMPETITION_DATE", "2026-07-25")
            await repo.set(G1, junk_key, "2026/07/25")
            interaction = _Interaction()
            await Settings.settings_delete.callback(cog, interaction, setting_key=junk_key)
            assert await repo.get_all(G1) == {"COMPETITION_DATE": "2026-07-25"}
        finally:
            await db.close()
            _cleanup()

    run(_main())


# =====================================================================
# オートコンプリート
# =====================================================================
def test_setting_key_autocomplete_is_registered():
    from test_autocomplete import registered_autocomplete

    assert registered_autocomplete(Settings.settings_set, "setting_key") == "_setting_key_ac"
    assert registered_autocomplete(Settings.settings_get, "setting_key") == "_setting_key_ac"
    assert registered_autocomplete(Settings.settings_delete, "setting_key") == "_stored_key_ac"


def test_setting_key_autocomplete_shows_descriptions():
    async def _main():
        db, cog = await _make()
        try:
            choices = await cog._setting_key_ac(_Interaction(), "大会")
            assert [c.value for c in choices] == ["COMPETITION_DATE"]
            assert "大会日" in choices[0].name
        finally:
            await db.close()

    run(_main())


def test_delete_autocomplete_lists_stored_key_names_without_values():
    """候補は保存済みのキー名だけ。**値は出さない**。ギルド外（DM）では空。"""

    async def _main():
        db, cog = await _make()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "TODOIST_API_TOKEN", SECRET)
            await repo.set(G1, "competition_date", "2026/07/25")
            choices = await cog._stored_key_ac(_Interaction(), "")
            assert {c.value for c in choices} == {"TODOIST_API_TOKEN", "competition_date"}
            assert all(SECRET not in c.name and SECRET not in c.value for c in choices)

            dm = _Interaction()
            dm.guild_id = None
            assert await cog._stored_key_ac(dm, "") == []
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 既存の不正値は移行しない（既存データを動かさない）
# =====================================================================
#: H1 の基準スキーマ版。これより後に足されたマイグレーションは、開き直しで必ず走る
H1_BASELINE_SCHEMA_VERSION = 24


def test_existing_invalid_values_are_not_migrated_and_do_not_raise():
    """既に DB に入っている不正値は、マイグレーションでも読み出しでも書き換えない。

    DB の版を H1 の基準（24）へ戻してから開き直すので、24 より後に足された
    マイグレーションはすべてここで走る。**前提: それらのマイグレーションは
    何度走っても同じ結果になる（冪等）。** 冪等でないものを足すとここで落ちる。
    """

    async def _main():
        path = _tmp_db_path()
        db = Database(path)
        await db.connect()
        repo = SettingsRepository(db)
        await repo.set(G1, "COMPETITION_DATE", "2026/07/25")
        await repo.set(G1, "WEEKLY_DIGEST_WEEKDAY", "9")
        await db._set_user_version(H1_BASELINE_SCHEMA_VERSION)
        await db.close()

        db = Database(path)
        await db.connect()
        try:
            repo = SettingsRepository(db)
            assert await repo.get(G1, "COMPETITION_DATE") == "2026/07/25"
            assert await repo.get(G1, "WEEKLY_DIGEST_WEEKDAY") == "9"
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.competition_date == "2026/07/25"  # 読み出し側は例外を投げない
            assert gconf.weekly_digest_weekday == 0  # 範囲外は既定へ落ちる
        finally:
            await db.close()
            os.unlink(path)
            _cleanup()

    run(_main())


# =====================================================================
# 内部マーカーは /settings_delete でも消させない（diff-auditor 1周目）
# =====================================================================
@pytest.mark.parametrize("marker", ["AUTO_SETUP_COMPLETED_AT", "GUILD_NAME", "SETUP_VERSION"])
def test_settings_delete_refuses_internal_marker(marker):
    """AUTO_SETUP_COMPLETED_AT を消すと、次の起動で自動セットアップがやり直される。"""

    async def _main():
        db, cog = await _make()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, marker, "set-by-startup")
            interaction = _Interaction()
            await Settings.settings_delete.callback(cog, interaction, setting_key=marker)
            assert await repo.get(G1, marker) == "set-by-startup", "内部マーカーを消した"
            assert "内部" in interaction.text
        finally:
            await db.close()
            _cleanup()

    run(_main())


def test_delete_autocomplete_hides_internal_markers():
    async def _main():
        db, cog = await _make()
        try:
            repo = SettingsRepository(db)
            for key in ("AUTO_SETUP_COMPLETED_AT", "GUILD_NAME", "SETUP_VERSION"):
                await repo.set(G1, key, "x")
            await repo.set(G1, "competition_date", "2026/07/25")
            choices = await cog._stored_key_ac(_Interaction(), "")
            assert [c.value for c in choices] == ["competition_date"]
        finally:
            await db.close()

    run(_main())


def test_delete_autocomplete_requires_admin():
    """オートコンプリートはコマンドの check を通らない。補完関数自身の check で弾く。

    cog に束ねたコマンドで discord.py の実際の補完の呼び出し（`_invoke_autocomplete`）を
    通す。保存済みのキーがある状態で、管理者でない（Member ですらない）人には
    **空の候補**が返ることを見る（check を外すと、保存済みのキー名がそのまま返る）。
    """
    from utils.permissions import is_admin

    assert is_admin in getattr(Settings._stored_key_ac, "__discord_app_commands_checks__", [])

    async def _main():
        db, cog = await _make()
        try:
            await SettingsRepository(db).set(G1, "competition_date", "2026/07/25")
            # 対照: 補完関数そのものは保存済みのキーを返す
            direct = await cog._stored_key_ac(_Interaction(), "")
            assert [c.value for c in direct] == ["competition_date"]

            responded: list = []

            async def _autocomplete(choices):
                responded.append(choices)

            interaction = _Interaction()
            interaction.response = SimpleNamespace(
                is_done=lambda: False, autocomplete=_autocomplete
            )
            namespace = SimpleNamespace(setting_key="")
            await cog.settings_delete._invoke_autocomplete(interaction, "setting_key", namespace)
            assert responded == [[]], "管理者でない人に保存済みのキー名を見せている"
        finally:
            await db.close()

    run(_main())


def test_settings_delete_still_removes_lowercase_junk_of_an_internal_key():
    """内部キーの拒否は**完全一致**。以前の /settings_set が作った小文字のゴミ行は消せる。

    大文字化して判定すると、補完の候補に出るのに消せない行ができる。
    キーは大文字小文字を区別して保存されるので、本物のマーカーは巻き込まない。
    """

    async def _main():
        db, cog = await _make()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "AUTO_SETUP_COMPLETED_AT", "real")
            await repo.set(G1, "auto_setup_completed_at", "junk")
            await Settings.settings_delete.callback(
                cog, _Interaction(), setting_key="auto_setup_completed_at"
            )
            assert await repo.get_all(G1) == {"AUTO_SETUP_COMPLETED_AT": "real"}
        finally:
            await db.close()
            _cleanup()

    run(_main())


# =====================================================================
# 環境変数は Bot が実際に読んでいるキーだけ表示する（diff-auditor 1周目）
# =====================================================================
@pytest.mark.parametrize("key", ["COMPETITION_DATE", "DATA_RETENTION_DAYS", "CLUB_NAME"])
def test_settings_get_does_not_show_env_for_keys_the_bot_does_not_read(key):
    """config が環境変数を読まないキーは、環境変数にあっても「設定されていません」。

    表示すると「環境変数から取得」と見せながら、Bot は未設定として動く。
    """

    async def _main():
        db, cog = await _make()
        try:
            with mock.patch.dict(os.environ, {key: "from-env"}):
                interaction = _Interaction()
                await Settings.settings_get.callback(cog, interaction, setting_key=key)
            assert "from-env" not in interaction.everything_sent()
            assert "設定されていません" in interaction.text
        finally:
            await db.close()
            _cleanup()

    run(_main())
