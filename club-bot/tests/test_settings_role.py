"""`/set_role` の追加・削除と重複除去のテスト（G3-1）。

`/set_role` は追記専用で重複チェックも無く、班長ロールを1つ外すには
全消しするしかなかった（その間、全班長が L1 に降格する）。
`action: add|remove` を足し、保存時に重複を除去する。

**このファイルは2種類のテストを分けて持つ。**

1. 純関数（`split_role_tokens` / `merge_role_ids`）の単体テスト
2. `Settings.set_role.callback` を直接呼ぶ経路のテスト。
   ヘルパが正しくてもコマンドがそれを通っていなければ意味がないため
   （gotcha `test-asserts-permission-but-decorator-missing` と同型の穴）

**注意**: 2 の callback 直呼びでは `@app_commands.check(is_admin)` は
**走らない**。権限は別に `Settings.set_role.checks` を実行して検査する。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.settings import Settings, merge_role_ids, split_role_tokens
from config import GuildConfig, config
from repositories.settings_repository import SettingsRepository
from utils.db import Database

G1 = 100000000000000001
KEY = "LEADER_ROLE_IDS"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def _cleanup_config() -> None:
    config._db = None
    config.leader_role_ids = []
    config.clear_guild_cache()


class _Interaction:
    """set_role.callback を呼ぶための最小限の interaction。"""

    def __init__(self, guild_id: int = G1):
        self.guild = SimpleNamespace(id=guild_id)
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")


def _cog(db: Database) -> Settings:
    return Settings(SimpleNamespace(db=db))


async def _call(cog, interaction, role_id: str, action: str = "add", role_type: str = KEY):
    await Settings.set_role.callback(
        cog, interaction, role_type=role_type, role_id=role_id, action=action
    )


# =====================================================================
# 1. 純関数
# =====================================================================
def test_split_role_tokens_accepts_mentions_and_commas():
    """GUIDE.md が案内しているカンマ区切り入力とロールメンションを受ける。"""
    assert split_role_tokens("111") == (["111"], [])
    assert split_role_tokens("<@&111>") == (["111"], [])
    # docs/GUIDE.md の「複数の班長ロールはカンマ区切り」を壊さない
    assert split_role_tokens(" 111 , 222,333 ") == (["111", "222", "333"], [])
    # 数字でないトークンは捨てずに「不正」として返す（黙って消さない）
    assert split_role_tokens("111,abc") == (["111"], ["abc"])
    assert split_role_tokens("") == ([], [])


def test_merge_role_ids_appends_and_dedupes():
    m = merge_role_ids("111", ["222"], remove=False)
    assert m.values == ["111", "222"]
    assert m.added == ["222"]
    assert m.changed

    # 既にある ID は重複として報告し、二重に積まない
    m2 = merge_role_ids("111,222", ["222"], remove=False)
    assert m2.values == ["111", "222"]
    assert m2.duplicates == ["222"]
    assert not m2.changed, "重複追加は変更なし扱い（保存しない）"


def test_merge_role_ids_removes_one_and_keeps_the_rest():
    """1つ外すために全消しする必要が無いこと（G3-1 の主目的）。"""
    m = merge_role_ids("111,222,333", ["222"], remove=True)
    assert m.values == ["111", "333"], "順序を保って1件だけ外す"
    assert m.removed == ["222"]
    assert m.changed


def test_merge_role_ids_remove_absent_is_a_noop():
    m = merge_role_ids("111", ["999"], remove=True)
    assert m.values == ["111"]
    assert m.not_found == ["999"]
    assert not m.changed


def test_merge_role_ids_reports_invalid_existing_tokens():
    """既存値の非数値トークンは黙って消さず、報告したうえで除く。"""
    m = merge_role_ids("111,<@&222>,abc", ["333"], remove=False)
    assert m.values == ["111", "222", "333"]
    assert m.dropped == ["abc"]


# =====================================================================
# 2. コマンド経路（callback 直呼び）
# =====================================================================
def test_set_role_add_persists_through_the_command():
    async def _main():
        db = await _make_db()
        try:
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "111")
            await _call(cog, interaction, "<@&222>")
            assert await SettingsRepository(db).get(G1, KEY) == "111,222"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_remove_takes_out_only_one():
    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, KEY, "111,222,333")
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "222", action="remove")
            assert await repo.get(G1, KEY) == "111,333"
            assert "222" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_noop_does_not_touch_the_stored_value():
    """変更が無いときは保存しない。

    保存まで走ると「何も変えていない操作で非数値トークンだけが黙って
    消える」ことになる（ADR 0024 の「明示的な操作でだけ変える」に反する）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            raw = "111,abc,222"
            await repo.set(G1, KEY, raw)
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "999", action="remove")  # 含まれていない
            assert await repo.get(G1, KEY) == raw, "1バイトも変わらないこと"
            assert "変更していません" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_warns_when_env_overrides_the_removal():
    """env が優先されていて remove が効かないときに、成功と言い切らない。

    config.for_guild() は DB 値が空のときグローバル（env 由来）へ
    フォールバックするため、最後の1件を外しても L2 は残る。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, KEY, "111")
            config.leader_role_ids = [111]  # env 由来のグローバル値
            cog, interaction = _cog(db), _Interaction()
            with mock.patch.dict(os.environ, {KEY: "111"}):
                await _call(cog, interaction, "111", action="remove")
            # 保存自体は行う（DB を巻き戻すと .env を直しても戻らなくなる）
            assert await repo.get(G1, KEY) == ""
            assert "環境変数" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_warns_when_env_holds_a_different_role():
    """DB を空にすると、保存していない**別の**ロールが L2 を得ることがある。

    「外した ID がまだ残っているか」だけを見ると、この経路は無言で通る
    （DB=111 を外した結果、env の 222 が有効になる）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, KEY, "111")
            config.leader_role_ids = [222]  # env 由来。DB とは別の ID
            cog, interaction = _cog(db), _Interaction()
            with mock.patch.dict(os.environ, {KEY: "222"}):
                await _call(cog, interaction, "111", action="remove")
            text = interaction.text
            assert "222" in text, "保存していないロールが有効なままなのに無言"
            assert "環境変数" in text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_warns_about_restart_when_env_is_not_set():
    """env が無いのに残っている場合は「.env を直せ」と言わない。

    GUILD_ID 指定のレガシーギルドでは、起動時に読んだ値がプロセス内に
    残る（config.load_from_db は一度入った値を減らさない）。存在しない
    .env の行を直せと案内するのは嘘の案内になる。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, KEY, "111")
            config.leader_role_ids = [111]
            cog, interaction = _cog(db), _Interaction()
            env = {k: v for k, v in os.environ.items() if k != KEY}
            with mock.patch.dict(os.environ, env, clear=True):
                await _call(cog, interaction, "111", action="remove")
            text = interaction.text
            assert "環境変数" not in text
            assert "再起動" in text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_rejects_a_non_numeric_role_id():
    async def _main():
        db = await _make_db()
        try:
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "リーダー")
            assert await SettingsRepository(db).get(G1, KEY) is None
            assert "リーダー" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_remove_is_rejected_for_single_value_keys():
    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "ADMIN_ROLE_ID", "777")
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "777", action="remove", role_type="ADMIN_ROLE_ID")
            assert await repo.get(G1, "ADMIN_ROLE_ID") == "777", "消えていないこと"
            assert "班長ロール" in interaction.text
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_rejects_multiple_ids_for_single_value_keys():
    async def _main():
        db = await _make_db()
        try:
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "777,888", role_type="ADMIN_ROLE_ID")
            assert await SettingsRepository(db).get(G1, "ADMIN_ROLE_ID") is None
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


def test_set_role_updates_the_resolved_config():
    """保存後に実効設定の再読込（_after_change）まで走っていること。

    これが抜けると、ギルド別キャッシュとレガシーギルドのグローバル値が
    古いまま残る。まさに「起動時に読んだ値が残る」警告文が扱っている経路。
    """

    async def _main():
        db = await _make_db()
        try:
            cog, interaction = _cog(db), _Interaction()
            await _call(cog, interaction, "111")
            # config.load_from_db(db) が走ると保持接続が入る
            assert config._db is db, "_after_change が呼ばれていない"
            gconf = await config.for_guild(G1, db=db)
            assert gconf.leader_role_ids == [111], "キャッシュが更新されていない"
        finally:
            await db.close()
            _cleanup_config()

    run(_main())


# =====================================================================
# 3. 権限（callback 直呼びでは走らないので別に検査する）
# =====================================================================
def test_set_role_still_requires_admin():
    """コマンドに is_admin チェックが付いていること（L2 の根拠を書き換えるため）。"""
    import discord

    from utils import permissions
    from utils.permissions import Level, PermissionDenied, command_required_level

    checks = Settings.set_role.checks
    assert len(checks) == 1, "想定外のチェック数"

    # チェックの**本数**と「ロール無しが拒否されること」だけでは、L4 を L2 へ
    # 格下げしても素通りする（ロール無しはどのレベルでも拒否されるため）。
    # /set_role は ADMIN_ROLE_ID と LEADER_ROLE_IDS = L4/L2 判定の根拠そのものを
    # 書き換えるので、班長が自分を管理者へ昇格できてしまう。必要レベルを直接固定する。
    assert command_required_level(Settings.set_role) == Level.L4

    member = mock.MagicMock(spec=discord.Member)
    member.id = 1
    member.guild = SimpleNamespace(owner_id=42)
    member.roles = []
    member.guild_permissions = SimpleNamespace(administrator=False, manage_guild=False)

    gconf = GuildConfig(guild_id=G1, admin_role_id=999)
    interaction = SimpleNamespace(user=member, guild=SimpleNamespace(id=G1))

    original = permissions._guild_config_for

    async def _fake(_interaction):
        return gconf

    permissions._guild_config_for = _fake
    try:
        with pytest.raises(PermissionDenied):
            run(checks[0](interaction))
    finally:
        permissions._guild_config_for = original
