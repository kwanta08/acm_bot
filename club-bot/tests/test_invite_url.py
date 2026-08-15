"""招待リンク生成（bot.build_invite_url）のユニットテスト。

Discord への接続は行わず、URL の組み立てと権限ビットのみ検証する。
"""

from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot import INVITE_PERMISSIONS, build_invite_url

CLIENT_ID = 123456789012345678


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_invite_url_contains_client_id_and_scopes():
    q = _query(build_invite_url(CLIENT_ID))
    assert q["client_id"] == [str(CLIENT_ID)]
    scopes = q["scope"][0].split()
    assert "bot" in scopes
    assert "applications.commands" in scopes


def test_invite_url_contains_minimal_permissions():
    q = _query(build_invite_url(CLIENT_ID))
    assert q["permissions"] == [str(INVITE_PERMISSIONS.value)]
    # 要件の最小権限が含まれること
    assert INVITE_PERMISSIONS.send_messages
    assert INVITE_PERMISSIONS.embed_links
    assert INVITE_PERMISSIONS.attach_files
    assert INVITE_PERMISSIONS.view_channel


def test_invite_url_does_not_request_admin_or_manage():
    # Administrator・Manage 系の強い権限は要求しない
    assert not INVITE_PERMISSIONS.administrator
    assert not INVITE_PERMISSIONS.manage_guild
    assert not INVITE_PERMISSIONS.manage_roles
    assert not INVITE_PERMISSIONS.manage_channels
