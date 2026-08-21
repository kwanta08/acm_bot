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


def _config(db_path: str, database_url: str | None = None) -> DashboardConfig:
    return DashboardConfig(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://example.com/auth/callback",
        secret_key="unit-test-secret",
        db_path=db_path,
        database_url=database_url,
        secure_cookie=False,
    )


async def _seed(db_path: str, database_url: str | None = None) -> dict[str, int]:
    db = Database(db_path, database_url=database_url)
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


def _client(
    db_path: str, *, permissions: str = "32", database_url: str | None = None
) -> TestClient:
    app = create_app(_config(db_path, database_url))
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


# ---------------------------------------------------------------------
# 行 ID が主キーの型に変換できない場合（G1-0）
#
# PostgreSQL では asyncpg が DataError を投げて 500 になっていた。
# SQLite では型親和性で拾えてしまうため、ここで固定できるのは
# 「500 にならず 404 になる」ことと「書き込みへ進まない」ことだけ。
# 実際に PG で通ることは tests/test_db_postgres.py が担保する。
# ---------------------------------------------------------------------
@pytest.mark.parametrize("bad_id", ["abc", "5.5", "1%20OR%201", "٥"])
def test_unconvertible_row_id_is_404_not_500(bad_id):
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/tasks/{bad_id}", json={"title": "書き換え"}
        )
        assert res.status_code == 404, res.text
    finally:
        client.__exit__(None, None, None)


def test_unconvertible_row_id_does_not_write():
    """変換の失敗は get_row で起きるので、UPDATE には到達しない。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/tasks/abc", json={"title": "書き換わってはいけない"}
            ).status_code
            == 404
        )
        # 既存行はそのまま
        res = client.get(f"/api/guilds/{GUILD_A}/tables/tasks")
        titles = [r["title"] for r in res.json()["rows"]]
        assert "書き換わってはいけない" not in titles
        assert ids  # シードが効いていること
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# PostgreSQL 実機でのダッシュボード編集（G1-0）
#
# 上の SQLite テストは、型親和性のせいで**修正前でも緑になる**。
# 「直しても再発を検出できない」状態を作らないため、HTTP 経路そのものを
# PostgreSQL で1往復させる。CLUB_TEST_PG_DSN があるときだけ走る
# （skip を緑と数えない: gotcha `dashboard-tests-silently-skipped`）。
# ---------------------------------------------------------------------
async def _pg_database_name(dsn: str) -> str:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchval("SELECT current_database()")
    finally:
        await con.close()


def _pg_dsn_or_skip() -> str:
    """CLUB_TEST_PG_DSN がテスト専用 DB を指す場合だけ返す。

    本番 DB を誤って書き換えないよう、接続先のデータベース名に
    "test" を含む場合に限る（tests/test_db_postgres.py と同じ規約）。
    """
    dsn = os.getenv("CLUB_TEST_PG_DSN")
    if not dsn:
        pytest.skip("CLUB_TEST_PG_DSN 未設定（テスト専用 DB の DSN を指定してください）")
    name = asyncio.run(_pg_database_name(dsn))
    if "test" not in name.lower():
        pytest.skip(f"安全のためテスト専用 DB でのみ実行します（接続先: {name}）")
    return dsn


async def _pg_reset(dsn: str) -> None:
    """このテストが使うギルドの行だけ消す（他の行には触らない）。"""
    db = Database("./unused.db", database_url=dsn)
    await db.connect()
    try:
        for table in ("tasks", "progress_nodes", "members", "guilds"):
            for guild_id in (GUILD_A, GUILD_B):
                await db.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
    finally:
        await db.close()


def test_pg_live_dashboard_edit_accepts_string_row_id():
    """URL 由来の str な row_id で PATCH が通ること（500 にならない）。

    修正前は asyncpg が Bind の時点で DataError を投げ、
    `before = await repo.get_row(...)` で 500 になっていた。
    """
    dsn = _pg_dsn_or_skip()
    asyncio.run(_pg_reset(dsn))
    ids = asyncio.run(_seed("./unused.db", dsn))
    client = _client("./unused.db", database_url=dsn)
    try:
        node_id = ids["node"][GUILD_A]
        assert isinstance(node_id, int)

        # 画面が組み立てる URL と同じく str で渡る
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{node_id}",
            json={"manual_progress": "0.75"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["row"]["manual_progress"] == 0.75

        # 変換できない ID は 500 ではなく 404
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/progress/abc", json={"manual_progress": "0.5"}
            ).status_code
            == 404
        )

        # 他ギルドの行は PG でも見えない（guild_id スコープの回帰）
        other = ids["node"][GUILD_B]
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/progress/{other}", json={"manual_progress": "0.5"}
            ).status_code
            == 404
        )
    finally:
        client.__exit__(None, None, None)
        asyncio.run(_pg_reset(dsn))


# ---------------------------------------------------------------------
# number 列を DDL の型どおりに受けること（G1-9）
#
# INTEGER 列（tasks.priority / layer_records.minutes）に小数が来たら 400。
# REAL 列（progress.sort_order / 重量2列）は小数を受ける。
# SQLite はどちらの列にも何でも保存できるので、**HTTP で 400 が返ること**と
# **PG 実機で通ること**の両方を見る。
# ---------------------------------------------------------------------
async def _insert_task(db_path: str, database_url: str | None = None) -> int:
    db = Database(db_path, database_url=database_url)
    await db.connect()
    try:
        cur = await db.execute(
            "INSERT INTO tasks (guild_id, title, status, created_by, created_at, priority)"
            " VALUES (?, '主桁の積層', 'open', 'tester', '2026-01-01', 2)",
            (GUILD_A,),
        )
        return cur.lastrowid
    finally:
        await db.close()


def test_fraction_for_an_integer_column_is_400():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    task_id = asyncio.run(_insert_task(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/tasks/{task_id}", json={"priority": "2.7"}
        )
        assert res.status_code == 400, res.text
        assert "整数" in res.json()["detail"]

        # JSON 由来の float も同じ扱い（文字列を経ない経路）
        assert (
            client.patch(
                f"/api/guilds/{GUILD_A}/tables/tasks/{task_id}", json={"priority": 2.7}
            ).status_code
            == 400
        )
    finally:
        client.__exit__(None, None, None)

    async def _check():
        db = Database(db_path)
        await db.connect()
        try:
            row = await TableRepository(db).get_row(GUILD_A, "tasks", task_id)
            assert row["priority"] == 2  # 丸めも書き込みもされていない
        finally:
            await db.close()

    asyncio.run(_check())


def test_real_column_still_accepts_fractions():
    """INTEGER を締めたついでに重量・表示順まで拒否していないこと。"""
    db_path = _tmp_db_path()
    ids = asyncio.run(_seed(db_path))
    client = _client(db_path)
    try:
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"sort_order": "2.5", "target_weight_g": "1234.5"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["row"]["sort_order"] == 2.5
        assert res.json()["row"]["target_weight_g"] == 1234.5
    finally:
        client.__exit__(None, None, None)


def test_pg_live_number_columns_match_the_ddl_types():
    """PG 実機: INTEGER 列は小数を弾き、REAL 列は受ける。

    修正前は `{"priority": 2.7}` がそのまま asyncpg へ渡り、
    int8 引数に float を渡した時点で DataError（＝500）になっていた。
    """
    dsn = _pg_dsn_or_skip()
    asyncio.run(_pg_reset(dsn))
    ids = asyncio.run(_seed("./unused.db", dsn))
    task_id = asyncio.run(_insert_task("./unused.db", dsn))
    client = _client("./unused.db", database_url=dsn)
    try:
        # INTEGER 列: 小数は 400（500 にしない）
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/tasks/{task_id}", json={"priority": "2.7"}
        )
        assert res.status_code == 400, res.text

        # INTEGER 列: 整数は通る（int でバインドされている）
        res = client.patch(f"/api/guilds/{GUILD_A}/tables/tasks/{task_id}", json={"priority": "3"})
        assert res.status_code == 200, res.text
        assert res.json()["row"]["priority"] == 3

        # REAL 列: 小数を受ける
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"sort_order": "2.5"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["row"]["sort_order"] == 2.5

        # REAL 列: 整数を入れても float として通る
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/progress/{ids['node'][GUILD_A]}",
            json={"target_weight_g": 1200},
        )
        assert res.status_code == 200, res.text
    finally:
        client.__exit__(None, None, None)
        asyncio.run(_pg_reset(dsn))
