"""表グリッドからの編集と監査ログのテスト（P2-5）。

- 班長以上のみ編集できること（一般メンバーは 403）
- 編集可能な列だけが更新でき、それ以外は 400
- **他サーバーの行 ID を指定しても更新されないこと**
- 変更が audit_log に必ず記録されること（変更前後の値つき）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("fastapi", reason="dashboard/requirements.txt が未インストール")

import httpx
from fastapi.testclient import TestClient

from dashboard.config import DashboardConfig
from dashboard.main import create_app
from repositories.audit_log_repository import AuditLogRepository
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.table_repository import TableRepository
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"
NOW = "2026-08-11 10:00"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _config(db_path: str) -> DashboardConfig:
    return DashboardConfig(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://example.com/auth/callback",
        secret_key="unit-test-secret",
        db_path=db_path,
        secure_cookie=False,
    )


async def _seed(db_path: str) -> dict[str, int]:
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        members = MemberRepository(db)
        progress = ProgressRepository(db)
        for guild_id, mark in ((GUILD_A, "A大学"), (GUILD_B, "B大学")):
            await members.upsert_member(guild_id, USER_ID, f"{mark}の部員")
            await progress.upsert_node(
                guild_id, "m1", name=f"{mark}の機体", manual_progress=0.1, now_text=NOW
            )
        rows = await db.fetchall("SELECT guild_id, member_id FROM members ORDER BY member_id")
        ids = {int(r["guild_id"]): int(r["member_id"]) for r in rows}
        node_rows = await db.fetchall("SELECT guild_id, progress_node_id FROM progress_nodes")
        return {
            "member": ids,
            "node": {int(r["guild_id"]): int(r["progress_node_id"]) for r in node_rows},
        }
    finally:
        await db.close()


def _transport(guilds: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": USER_ID, "username": "y"})
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _client(db_path: str, *, permissions: str = "32") -> TestClient:
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(
        transport=_transport([{"id": str(GUILD_A), "name": "A大学", "permissions": permissions}])
    )
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=c&state={state}")
    return client


async def _audit_entries(db_path: str, guild_id: int) -> list[dict]:
    db = Database(db_path)
    await db.connect()
    try:
        return await AuditLogRepository(db).list_recent(guild_id, limit=20)
    finally:
        await db.close()


# ---------------------------------------------------------------------
# 権限
# ---------------------------------------------------------------------
def test_plain_member_cannot_edit():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path, permissions="0")  # サーバー管理権限なし・班長でもない
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "書き換え"},
        )
        assert res.status_code == 403
    finally:
        client.__exit__(None, None, None)

    # DB は変わっていない
    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            row = await MemberRepository(db).get_member(GUILD_A, USER_ID)
            assert row["display_name"] == "A大学の部員"
        finally:
            await db.close()

    asyncio.run(_check())


def test_leader_can_edit():
    """班長（members.is_leader）は編集できる。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))

    async def _make_leader():
        db = Database(db_path)
        await db.connect()
        try:
            await MemberRepository(db).set_leader(GUILD_A, USER_ID, True)
        finally:
            await db.close()

    asyncio.run(_make_leader())
    client = _client(db_path, permissions="0")
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "班長が更新"},
        )
        assert res.status_code == 200
        assert res.json()["row"]["display_name"] == "班長が更新"
    finally:
        client.__exit__(None, None, None)


def test_unauthenticated_cannot_edit():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    app = create_app(_config(db_path))
    with TestClient(app) as client:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "x"},
        )
        assert res.status_code == 401


# ---------------------------------------------------------------------
# スコープ
# ---------------------------------------------------------------------
def test_cannot_edit_other_guild_row():
    """他サーバーの行 ID を指定しても更新されない。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    other_id = ids["member"][GUILD_B]
    client = _client(db_path)
    try:
        # 自分のサーバーの URL に他サーバーの行 ID を混ぜる
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{other_id}", json={"display_name": "乗っ取り"}
        )
        assert res.status_code == 404
        # 他サーバーの URL は 403
        assert (
            client.patch(
                f"/api/guilds/{GUILD_B}/tables/members/{other_id}",
                json={"display_name": "乗っ取り"},
            ).status_code
            == 403
        )
    finally:
        client.__exit__(None, None, None)

    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            row = await MemberRepository(db).get_member(GUILD_B, USER_ID)
            assert row["display_name"] == "B大学の部員"
        finally:
            await db.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------
# 列の検証
# ---------------------------------------------------------------------
def test_non_editable_column_is_rejected():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        for payload in (
            {"user_id": "999"},  # 編集不可
            {"guild_id": GUILD_B},  # スコープ列
            {"member_id": 1},  # 主キー
            {"unknown": "x"},
        ):  # 存在しない列
            res = client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}", json=payload
            )
            assert res.status_code == 400, payload
    finally:
        client.__exit__(None, None, None)


def test_empty_body_and_unknown_table():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}", json={}
            ).status_code
            == 400
        )
        assert (
            client.patch(f"/api/guilds/{GUILD_A}/tables/settings/1", json={"x": 1}).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/members/999999", json={"display_name": "x"}
            ).status_code
            == 404
        )
    finally:
        client.__exit__(None, None, None)


def test_progress_value_round_trip():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"manual_progress": 0.75, "status": "製作中"},
        )
        assert res.status_code == 200
        row = res.json()["row"]
        assert row["manual_progress"] == 0.75
        assert row["status"] == "製作中"
    finally:
        client.__exit__(None, None, None)


def test_progress_value_is_normalised_like_the_bot():
    """`50%` 等の入力は /progress edit と同じ 0.0〜1.0 に正規化される。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"manual_progress": "50%"},
        )
        assert res.status_code == 200
        assert res.json()["row"]["manual_progress"] == 0.5
    finally:
        client.__exit__(None, None, None)


def test_non_numeric_progress_is_rejected_and_not_stored():
    """数値でない進捗率を保存させない。

    保存されると bot 側の float() 変換が落ち、そのサーバーの
    /progress view と定期同期がまとめて動かなくなる。
    """
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"manual_progress": "だいたい終わった"},
        )
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)

    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            # 元の値のまま。bot 側のツリー構築も通る
            node = await ProgressRepository(db).get_node(GUILD_A, "m1")
            assert float(node["manual_progress"]) == 0.1
        finally:
            await db.close()

    asyncio.run(_check())


# ---------------------------------------------------------------------
# 監査ログ
# ---------------------------------------------------------------------
def test_edit_is_recorded_in_audit_log():
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"display_name": "新しい名前"},
        )
    finally:
        client.__exit__(None, None, None)

    entries = asyncio.run(_audit_entries(db_path, GUILD_A))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "dashboard.update"
    assert entry["actor_id"] == USER_ID
    assert "members#" in entry["target"]
    # 変更前後が残る
    assert "A大学の部員" in entry["detail"]
    assert "新しい名前" in entry["detail"]
    # 他サーバーのログには入らない
    assert asyncio.run(_audit_entries(db_path, GUILD_B)) == []


def test_rejected_edit_is_also_recorded():
    """編集不可の列への試みも監査ログに残る。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"user_id": "999"},
        )
    finally:
        client.__exit__(None, None, None)

    entries = asyncio.run(_audit_entries(db_path, GUILD_A))
    assert any(e["action"] == "dashboard.update.rejected" for e in entries)


def test_repository_update_is_guild_scoped():
    """リポジトリ単体でも他ギルドの行は更新できない。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            repo = TableRepository(db)
            # GUILD_A のスコープで GUILD_B の行 ID を指定しても更新されない
            changed = await repo.update_row(
                GUILD_A, "members", ids["member"][GUILD_B], {"display_name": "乗っ取り"}
            )
            assert changed is False
            row = await MemberRepository(db).get_member(GUILD_B, USER_ID)
            assert row["display_name"] == "B大学の部員"
        finally:
            await db.close()

    asyncio.run(_main())


# ---------------------------------------------------------------------
# 権限まわりの列は Web から書けないこと
#
# cogs/members._sync_roles() は teams.member_role_id をそのまま add_roles() に
# 渡す。この列を Bot 管理者ロールの ID に書き換えてから /member assign-team（L2）を
# 実行すると、bot の権限で L4 相当のロールが付いてしまう。
# Discord 側の /team-role は元から管理者限定なので、Web だけが緩かった。
# ---------------------------------------------------------------------
async def _seed_team(db_path: str) -> int:
    db = Database(db_path)
    await db.connect()
    try:
        members = MemberRepository(db)
        await members.upsert_team(GUILD_A, "struct", "構造班")
        await db.execute(
            "UPDATE teams SET member_role_id = ? WHERE guild_id = ? AND team_key = ?",
            ("501", GUILD_A, "struct"),
        )
        row = await db.fetchone(
            "SELECT team_id FROM teams WHERE guild_id = ? AND team_key = ?", (GUILD_A, "struct")
        )
        return int(row["team_id"])
    finally:
        await db.close()


async def _team_role_id(db_path: str) -> str | None:
    db = Database(db_path)
    await db.connect()
    try:
        row = await db.fetchone(
            "SELECT member_role_id FROM teams WHERE guild_id = ? AND team_key = ?",
            (GUILD_A, "struct"),
        )
        return row["member_role_id"]
    finally:
        await db.close()


@pytest.mark.parametrize("column", ["member_role_id", "leader_role_id", "secondary_role_id"])
def test_team_role_ids_are_not_editable(column):
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    team_id = asyncio.run(_seed_team(db_path))
    client = _client(db_path)  # サーバー管理権限あり（L4）でも拒否される
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/teams/{team_id}",
            json={column: "999"},
        )
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)

    assert asyncio.run(_team_role_id(db_path)) == "501"


def test_is_leader_is_not_editable():
    """L2 が任意の相手を L2（＝ダッシュボードの編集権）へ昇格できないこと。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/members/{ids['member'][GUILD_A]}",
            json={"is_leader": 1},
        )
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_role_ids_are_masked_for_non_admin():
    """ロール ID の実値は L4 にだけ返す。設定済みかどうかは分かるようにする。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _set_role():
        db = Database(db_path)
        await db.connect()
        try:
            from repositories.settings_repository import SettingsRepository

            await SettingsRepository(db).set(GUILD_A, "ADMIN_ROLE_ID", "700")
        finally:
            await db.close()

    asyncio.run(_set_role())

    client = _client(db_path, permissions="0")  # 一般参加者
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/settings")
        assert res.status_code == 200
        by_key = {s["key"]: s for s in res.json()["settings"]}
        assert by_key["ADMIN_ROLE_ID"]["value"] == "（設定済み）"
        assert res.json()["can_edit"] is False
    finally:
        client.__exit__(None, None, None)

    client = _client(db_path)  # サーバー管理権限あり
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/settings")
        by_key = {s["key"]: s for s in res.json()["settings"]}
        assert by_key["ADMIN_ROLE_ID"]["value"] == "700"
    finally:
        client.__exit__(None, None, None)
