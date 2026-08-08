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
    gconf = GuildConfig(guild_id=G1, schedule_emoji_ok_id=1001,
                        schedule_emoji_ng_id=1003)
    guild = FakeGuild([OK_EMOJI, NG_EMOJI])
    emojis = get_schedule_emojis(gconf, guild)
    assert emojis["ok"] is OK_EMOJI
    assert emojis["ng"] is NG_EMOJI          # アニメ絵文字もそのまま
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
