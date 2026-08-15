"""手書き .env の書式ゆれで設定解決が壊れないことの回帰テスト。

config.for_guild() は権限チェック（utils/permissions.require）を含む
ほぼ全てのコマンド・定期通知の入口で呼ばれる。ここで例外が出ると
そのサーバーの機能が丸ごと停止するため、環境変数の値は必ず
_clean 経由で読み、不正値は「未設定」に倒す。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from config import Config

EMOJI_ENV_KEYS = (
    "SCHEDULE_EMOJI_OK_ID",
    "SCHEDULE_EMOJI_MAYBE_ID",
    "SCHEDULE_EMOJI_NG_ID",
)


@pytest.fixture
def clean_emoji_env(monkeypatch):
    for key in EMOJI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _emoji_ids(config: Config) -> list[int | None]:
    return [
        config.schedule_emoji_ok_id,
        config.schedule_emoji_maybe_id,
        config.schedule_emoji_ng_id,
    ]


def test_unset_emoji_ids_are_none(clean_emoji_env):
    assert _emoji_ids(Config()) == [None, None, None]


def test_quoted_emoji_ids_are_parsed(clean_emoji_env):
    """ "123" のように引用符付きで書かれていても読めること。"""
    for key in EMOJI_ENV_KEYS:
        clean_emoji_env.setenv(key, '"123456789012345678"')
    assert _emoji_ids(Config()) == [123456789012345678] * 3


def test_padded_emoji_ids_are_parsed(clean_emoji_env):
    """前後の空白・全角スペースが混ざっていても読めること。"""
    for key in EMOJI_ENV_KEYS:
        clean_emoji_env.setenv(key, "　 123456789012345678 ")
    assert _emoji_ids(Config()) == [123456789012345678] * 3


@pytest.mark.parametrize("raw", ["abc", "<:ok:123>", "12 34", "#コメント"])
def test_invalid_emoji_ids_fall_back_to_none(clean_emoji_env, raw):
    """不正値は例外ではなく未設定扱い（既定の絵文字にフォールバックする）。"""
    for key in EMOJI_ENV_KEYS:
        clean_emoji_env.setenv(key, raw)
    assert _emoji_ids(Config()) == [None, None, None]
