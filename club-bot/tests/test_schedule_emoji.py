"""日程調整のリアクション絵文字解決（ギルド別設定）のユニットテスト。

Discord へは接続せず、フェイクの guild / gconf で
「設定あり → カスタム絵文字」「未設定 → 既定」「削除済み → 既定へ
フォールバック」を検証する。
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.schedule import (
    EMOJI_SETTING_KEYS,
    filter_emoji_choices,
    resolve_emoji_input,
)
from config import GuildConfig
from services.schedule_service import (
    DEFAULT_STATUS_TO_EMOJI,
    build_emoji_maps,
    emoji_key,
    get_schedule_emojis,
)

G1 = 111


class FakeEmoji(SimpleNamespace):
    """discord.Emoji 互換（id / name / animated / __str__）。"""

    def __str__(self):
        prefix = "a" if self.animated else ""
        return f"<{prefix}:{self.name}:{self.id}>"


class FakeGuild:
    def __init__(self, emojis=()):
        self.id = G1
        self.emojis = list(emojis)

    def get_emoji(self, emoji_id):
        return next((e for e in self.emojis if e.id == emoji_id), None)


OK_EMOJI = FakeEmoji(id=1001, name="sanka", animated=False)
NG_EMOJI = FakeEmoji(id=1003, name="fusanka", animated=True)  # アニメーション


def test_unconfigured_falls_back_to_defaults():
    gconf = GuildConfig(guild_id=G1)
    emojis = get_schedule_emojis(gconf, FakeGuild())
    assert emojis == DEFAULT_STATUS_TO_EMOJI


def test_configured_emojis_resolved_from_guild():
    gconf = GuildConfig(guild_id=G1, schedule_emoji_ok_id=1001, schedule_emoji_ng_id=1003)
    guild = FakeGuild([OK_EMOJI, NG_EMOJI])
    emojis = get_schedule_emojis(gconf, guild)
    assert emojis["ok"] is OK_EMOJI
    assert emojis["ng"] is NG_EMOJI  # アニメ絵文字もそのまま
    assert emojis["maybe"] == DEFAULT_STATUS_TO_EMOJI["maybe"]  # 未設定は既定


def test_deleted_emoji_falls_back_to_default():
    """設定後にサーバーから削除された絵文字は既定へフォールバックする。"""
    gconf = GuildConfig(guild_id=G1, schedule_emoji_ok_id=9999)
    emojis = get_schedule_emojis(gconf, FakeGuild([OK_EMOJI]))
    assert emojis["ok"] == DEFAULT_STATUS_TO_EMOJI["ok"]


def test_emoji_key_for_custom_and_unicode():
    assert emoji_key(OK_EMOJI) == "1001"
    assert emoji_key("✅") == "✅"


def test_build_emoji_maps_keys_match_reaction_payload():
    """emoji_to_status のキーがリアクション payload の解決形式と一致する。"""
    gconf = GuildConfig(guild_id=G1, schedule_emoji_ok_id=1001)
    guild = FakeGuild([OK_EMOJI])
    maps = build_emoji_maps(gconf, guild)
    # カスタム絵文字は str(id)、Unicode はそのもの
    assert maps["emoji_to_status"]["1001"] == "ok"
    assert maps["emoji_to_status"]["❓"] == "maybe"
    assert maps["emoji_to_status"]["❌"] == "ng"
    assert maps["all_emojis"] == [OK_EMOJI, "❓", "❌"]
    assert maps["status_to_emoji"]["ok"] is OK_EMOJI


# ---------------------------------------------------------------------
# /schedule emoji set（オートコンプリート・入力解決）
# ---------------------------------------------------------------------
def _emojis(n=3, prefix="tori"):
    return [FakeEmoji(id=2000 + i, name=f"{prefix}{i}", animated=False) for i in range(n)]


def test_filter_emoji_choices_filters_by_name():
    emojis = [*_emojis(3), FakeEmoji(id=3000, name="hikouki", animated=False)]
    choices = filter_emoji_choices(emojis, "tori")
    assert [c.name for c in choices] == [":tori0:", ":tori1:", ":tori2:"]
    # value は絵文字 ID（実行時に guild.get_emoji(int(value)) で解決）
    assert [c.value for c in choices] == ["2000", "2001", "2002"]


def test_filter_emoji_choices_strips_colons_and_ignores_case():
    emojis = [FakeEmoji(id=1, name="ToriSan", animated=False)]
    assert len(filter_emoji_choices(emojis, ":tori")) == 1
    assert len(filter_emoji_choices(emojis, "SAN")) == 1


def test_filter_emoji_choices_caps_at_25():
    choices = filter_emoji_choices(_emojis(40), "")
    assert len(choices) == 25


def test_resolve_emoji_input_by_id_and_name():
    guild = FakeGuild([OK_EMOJI])
    assert resolve_emoji_input(guild, "1001") is OK_EMOJI  # ID（候補選択）
    assert resolve_emoji_input(guild, "sanka") is OK_EMOJI  # 名前手入力
    assert resolve_emoji_input(guild, ":sanka:") is OK_EMOJI
    assert resolve_emoji_input(guild, "9999") is None  # 不在 ID
    assert resolve_emoji_input(guild, "ghost") is None  # 不在名


def test_emoji_setting_keys_match_guild_config_resolution():
    """設定キーが config.for_guild の解決キーと一致している。"""
    assert EMOJI_SETTING_KEYS == {
        "ok": "SCHEDULE_EMOJI_OK_ID",
        "maybe": "SCHEDULE_EMOJI_MAYBE_ID",
        "ng": "SCHEDULE_EMOJI_NG_ID",
    }


# ---------------------------------------------------------------------
# 設定 → for_guild 解決 → リセット（実 sqlite）
# ---------------------------------------------------------------------
def test_set_and_reset_roundtrip_via_for_guild(monkeypatch, tmp_path):
    """settings への保存が for_guild で解決され、削除で既定に戻る。"""
    import asyncio

    from config import config
    from repositories.settings_repository import SettingsRepository
    from utils.db import Database

    # 環境変数フォールバックの影響を除去
    for key in EMOJI_SETTING_KEYS.values():
        monkeypatch.delenv(key, raising=False)

    async def _main():
        db = Database(str(tmp_path / "t.db"))
        await db.connect()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "SCHEDULE_EMOJI_OK_ID", "1234")
            gconf = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf.schedule_emoji_ok_id == 1234
            assert gconf.schedule_emoji_maybe_id is None

            # リセット（/schedule emoji reset 相当）
            await repo.delete(G1, "SCHEDULE_EMOJI_OK_ID")
            config.invalidate_guild(G1)
            gconf2 = await config.for_guild(G1, db=db, force_reload=True)
            assert gconf2.schedule_emoji_ok_id is None
            assert get_schedule_emojis(gconf2, FakeGuild()) == DEFAULT_STATUS_TO_EMOJI
        finally:
            await db.close()
            config.invalidate_guild(G1)

    asyncio.run(_main())
