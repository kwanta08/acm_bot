"""表グリッド API（P2-4: 読み取り専用）のテスト。

- 7つの対象テーブルが列定義つきで返ること
- **返る行が必ず自サーバーのものだけ**であること（他ギルドの行は1件も出ない）
- ホワイトリスト外のテーブル名は 404（SQL へ渡らない）
- 未ログイン 401 / 他サーバー 403
"""

from __future__ import annotations

import asyncio
import os
import re
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
from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.table_repository import (
    TABLES,
    TableRepository,
    UnknownColumnError,
    UnknownTableError,
    rows_to_csv,
)
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"
NOW = "2026-08-11 10:00"

# **件数ではなくキー集合で書く**
# （件数だけだと、別の表と入れ替わっても緑のまま通る）。
EXPECTED_TABLES = {
    "members",
    "teams",
    "schedules",
    "schedule_votes",
    "layer_records",
    "progress",
    # 読み取り専用（G4-3）
    "audit_log",
    "seasons",
    "progress_milestones",
    "layer_keta",
    "settings",
    # 進捗の日次履歴（G4-7）
    "progress_snapshots",
    # 資材・消耗品の在庫（G4-8）
    "stock_items",
    "stock_movements",
}


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


async def _seed(db_path: str) -> None:
    """両ギルドに、名前で見分けられるデータを入れる。"""
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        members = MemberRepository(db)
        progress = ProgressRepository(db)
        schedules = ScheduleRepository(db)
        sessions = LayerSessionRepository(db)

        for guild_id, mark in ((GUILD_A, "A大学"), (GUILD_B, "B大学")):
            await members.upsert_member(guild_id, USER_ID, f"{mark}の部員")
            await members.upsert_team(guild_id, "wing", f"{mark}の主翼班")
            await progress.upsert_node(guild_id, "m1", name=f"{mark}の機体", now_text=NOW)
            await schedules.create_schedule(
                guild_id,
                f"sch-{guild_id}",
                f"{mark}のミーティング",
                f"{mark}の説明",
                "部室",
                None,
                "2026-09-01 19:00",
                USER_ID,
                "1",
            )
            await schedules.add_option(
                guild_id,
                f"opt-{guild_id}",
                f"sch-{guild_id}",
                f"{mark}の候補日",
                "2026-09-01 19:00",
                None,
                None,
            )
            await schedules.set_vote(guild_id, f"opt-{guild_id}", USER_ID, "ok")
            await sessions.add_record(guild_id, USER_ID, f"{mark}の主桁", "1", NOW, NOW, 60)
    finally:
        await db.close()


def _discord_transport(guilds: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": USER_ID, "username": "yamada"})
        if request.url.path.endswith("/users/@me/guilds"):
            return httpx.Response(200, json=guilds)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _logged_in_client(db_path: str, *, permissions: str = "32") -> TestClient:
    app = create_app(_config(db_path))
    app.state.http_client = httpx.AsyncClient(
        transport=_discord_transport(
            [{"id": str(GUILD_A), "name": "A大学", "permissions": permissions}]
        )
    )
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    res = client.get("/auth/login")
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=c&state={state}")
    return client


# ---------------------------------------------------------------------
# リポジトリ（ホワイトリスト）
# ---------------------------------------------------------------------
def test_whitelist_covers_required_tables():
    assert set(TABLES) == EXPECTED_TABLES


def test_unknown_table_is_rejected():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            # settings は G4-3 でホワイトリスト入りしたが（閲覧は L4 限定）、
            # 認証情報のテーブルと bot 運用テーブルは今も対象外
            with pytest.raises(UnknownTableError):
                await repo.list_rows(GUILD_A, "todoist_configs")
            with pytest.raises(UnknownTableError):
                await repo.list_rows(GUILD_A, "guilds")
            with pytest.raises(UnknownTableError):
                await repo.list_rows(GUILD_A, "reminders_log")
        finally:
            await db.close()

    asyncio.run(_main())


def test_update_rejects_non_editable_column():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            with pytest.raises(UnknownColumnError):
                await repo.update_row(GUILD_A, "members", 1, {"user_id": "999"})
            with pytest.raises(UnknownColumnError):
                await repo.update_row(GUILD_A, "members", 1, {"guild_id": 1})
        finally:
            await db.close()

    asyncio.run(_main())


def test_repository_rows_are_guild_scoped():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _main():
        db = Database(db_path)
        await db.connect()
        try:
            repo = TableRepository(db)
            for key in EXPECTED_TABLES:
                rows_a = await repo.list_rows(GUILD_A, key)
                rows_b = await repo.list_rows(GUILD_B, key)
                blob_a = str(rows_a)
                assert "B大学" not in blob_a, f"{key} に他ギルドの行が混入"
                assert "A大学" not in str(rows_b), f"{key} に他ギルドの行が混入"
                # 両ギルドとも同じ件数を入れてある
                assert len(rows_a) == len(rows_b)
        finally:
            await db.close()

    asyncio.run(_main())


def test_limit_is_capped():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = TableRepository(db)
            # 上限を超える指定でも例外にならず、SQL に巨大値が渡らない
            assert await repo.list_rows(GUILD_A, "members", limit=10**9) == []
            assert await repo.list_rows(GUILD_A, "members", offset=-5) == []
        finally:
            await db.close()

    asyncio.run(_main())


# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------
def test_table_list_endpoint():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables").json()
        assert {t["key"] for t in body["tables"]} == EXPECTED_TABLES
        assert body["can_edit"] is True
    finally:
        client.__exit__(None, None, None)


def test_read_table_returns_columns_and_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        assert body["table"]["key"] == "members"
        assert body["table"]["pk"] == "member_id"
        names = [c["name"] for c in body["columns"]]
        assert "display_name" in names
        assert "guild_id" not in names  # スコープ列は見せない
        assert body["total"] == 1
        assert body["rows"][0]["display_name"] == "A大学の部員"
    finally:
        client.__exit__(None, None, None)


def test_offset_paging_returns_correct_rows():
    """`offset` 付きリクエストが正しい行を返す（D1-1）。

    フロントのページャは limit / offset を組み立てて送るだけなので、
    サーバー側の切り出しが正しいことをここで固定する。
    """
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _more_nodes() -> None:
        db = Database(db_path)
        await db.connect()
        try:
            progress = ProgressRepository(db)
            # 表示順に並ぶよう sort_order を振る（order_by は sort_order, node_id）
            for i in range(2, 6):
                await progress.upsert_node(
                    GUILD_A, f"m{i}", name=f"A大学のノード{i}",
                    sort_order=float(i), now_text=NOW,
                )
        finally:
            await db.close()

    asyncio.run(_more_nodes())
    client = _logged_in_client(db_path)
    try:
        first = client.get(f"/api/guilds/{GUILD_A}/tables/progress?limit=2&offset=0").json()
        second = client.get(f"/api/guilds/{GUILD_A}/tables/progress?limit=2&offset=2").json()
        assert first["total"] == 5
        assert second["total"] == 5
        assert len(first["rows"]) == 2
        assert len(second["rows"]) == 2
        # ページが重ならず、並び順どおりに続いている
        ids_first = [r["progress_node_id"] for r in first["rows"]]
        ids_second = [r["progress_node_id"] for r in second["rows"]]
        assert not set(ids_first) & set(ids_second)
        names = [r["name"] for r in first["rows"] + second["rows"]]
        assert names == ["A大学の機体", "A大学のノード2", "A大学のノード3", "A大学のノード4"]
        assert second["offset"] == 2
        assert second["limit"] == 2
    finally:
        client.__exit__(None, None, None)


def test_every_table_returns_only_own_guild_rows():
    """全テーブルで他ギルドの行が1件も返らないこと。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in sorted(EXPECTED_TABLES):
            res = client.get(f"/api/guilds/{GUILD_A}/tables/{key}")
            assert res.status_code == 200, key
            assert "B大学" not in res.text, f"{key} に他ギルドのデータが混入"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 検索（D1-2）。ホワイトリスト列（TableSpec.searchable）だけを OR 検索する
# ---------------------------------------------------------------------
def test_search_filters_rows_and_total():
    """部分一致でヒットし、total にも効く。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _more() -> None:
        db = Database(db_path)
        await db.connect()
        try:
            members = MemberRepository(db)
            await members.upsert_member(GUILD_A, "u-rib", "主翼のリブ切り出し係")
            await members.upsert_member(GUILD_A, "u-keta", "尾翼の桁巻き係")
        finally:
            await db.close()

    asyncio.run(_more())
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members?q=主翼").json()
        assert body["total"] == 1
        assert [r["display_name"] for r in body["rows"]] == ["主翼のリブ切り出し係"]
        # ヒットしない語は 0 件（エラーにしない）
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members?q=存在しない語").json()
        assert body["total"] == 0
        assert body["rows"] == []
    finally:
        client.__exit__(None, None, None)


def test_search_escapes_like_wildcards():
    """検索語の % / _ はワイルドカードとして扱わない。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _more() -> None:
        db = Database(db_path)
        await db.connect()
        try:
            members = MemberRepository(db)
            await members.upsert_member(GUILD_A, "u-1", "進捗50%の報告")
            await members.upsert_member(GUILD_A, "u-2", "進捗5割の報告")
            await members.upsert_member(GUILD_A, "u-3", "main_spar の検査")
            await members.upsert_member(GUILD_A, "u-4", "mainXspar の検査")
        finally:
            await db.close()

    asyncio.run(_more())
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members?q=50%25").json()
        assert [r["display_name"] for r in body["rows"]] == ["進捗50%の報告"]
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members?q=main_spar").json()
        assert [r["display_name"] for r in body["rows"]] == ["main_spar の検査"]
    finally:
        client.__exit__(None, None, None)


def test_search_does_not_match_unsearchable_columns():
    """searchable に無い列（作成者 ID 等）は検索対象外。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        # user_id は USER_ID（"42"）だが、searchable に無いのでヒットしない
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members?q=42").json()
        assert body["total"] == 0
    finally:
        client.__exit__(None, None, None)


def test_search_applies_to_csv_export():
    """画面と CSV の中身がずれない（?q= が export.csv にも効く）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _more() -> None:
        db = Database(db_path)
        await db.connect()
        try:
            await MemberRepository(db).upsert_member(GUILD_A, "u-rib", "主翼のリブ切り出し係")
        finally:
            await db.close()

    asyncio.run(_more())
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv?q=主翼")
        assert res.status_code == 200
        assert "主翼のリブ切り出し係" in res.text
        assert "A大学の部員" not in res.text
    finally:
        client.__exit__(None, None, None)


def test_search_on_unsearchable_table_is_rejected():
    """検索対象列の無い表への ?q= は 400（黙って全件を返さない）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes?q=x")
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_searchable_columns_are_whitelisted_text_columns():
    """searchable はその表の列に限られ、DDL 上 TEXT の列だけを指す。

    PostgreSQL では lower(整数列) が型エラーになる（SQLite は通る）ため、
    定義の時点で TEXT 以外を排除しておく（G1-0 と同型の事故の予防）。
    """
    from utils.db import TABLE_DDL

    for spec in TABLES.values():
        assert set(spec.searchable) <= set(spec.column_names), spec.key
        ddl = TABLE_DDL[spec.table]
        for name in spec.searchable:
            m = re.search(rf"^\s*{name}\s+(\w+)", ddl, flags=re.MULTILINE)
            assert m, f"{spec.key}.{name} が DDL に見つからない"
            assert m.group(1) == "TEXT", f"{spec.key}.{name} は TEXT 列ではない: {m.group(1)}"


# ---------------------------------------------------------------------
# ソート（D1-3）。ORDER BY はバインドできないため、列名は必ず
# TableSpec 側のホワイトリストから取る
# ---------------------------------------------------------------------
def _seed_sortable_nodes(db_path: str) -> None:
    async def _more() -> None:
        db = Database(db_path)
        await db.connect()
        try:
            progress = ProgressRepository(db)
            await progress.upsert_node(
                GUILD_A, "n-i", name="い", manual_progress=0.2, now_text=NOW)
            await progress.upsert_node(
                GUILD_A, "n-a", name="あ", manual_progress=0.3, now_text=NOW)
            # manual_progress NULL の行（NULL の並び順の検査に使う。
            # _seed の m1 も NULL なので NULL は計2行になる）
            await progress.upsert_node(GUILD_A, "n-u", name="う", now_text=NOW)
        finally:
            await db.close()

    asyncio.run(_more())


def test_sort_by_allowed_column():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    _seed_sortable_nodes(db_path)
    client = _logged_in_client(db_path)
    try:
        # SQLite の既定コレーションは UTF-8 バイト順（ASCII が仮名より先）
        body = client.get(f"/api/guilds/{GUILD_A}/tables/progress?sort=name&dir=asc").json()
        names = [r["name"] for r in body["rows"]]
        assert names == ["A大学の機体", "あ", "い", "う"]
        body = client.get(f"/api/guilds/{GUILD_A}/tables/progress?sort=name&dir=desc").json()
        assert [r["name"] for r in body["rows"]] == ["う", "い", "あ", "A大学の機体"]
    finally:
        client.__exit__(None, None, None)


def test_sort_nulls_are_always_last():
    """NULL は昇順・降順とも末尾（SQLite / PostgreSQL で揃える）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    _seed_sortable_nodes(db_path)
    client = _logged_in_client(db_path)
    try:
        for direction in ("asc", "desc"):
            body = client.get(
                f"/api/guilds/{GUILD_A}/tables/progress?sort=manual_progress&dir={direction}"
            ).json()
            values = [r["manual_progress"] for r in body["rows"]]
            assert values[-2:] == [None, None], (direction, values)
    finally:
        client.__exit__(None, None, None)


def test_sort_rejects_unknown_column_and_bad_dir():
    """許可外の列・不正な dir は 400（500 にしない）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        assert client.get(
            f"/api/guilds/{GUILD_A}/tables/members?sort=guild_id"
        ).status_code == 400
        assert client.get(
            f"/api/guilds/{GUILD_A}/tables/members?sort=display_name;DROP"
        ).status_code == 400
        assert client.get(
            f"/api/guilds/{GUILD_A}/tables/members?sort=display_name&dir=up"
        ).status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_sort_applies_to_csv_export():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    _seed_sortable_nodes(db_path)
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/progress/export.csv?sort=name&dir=asc")
        assert res.status_code == 200
        # 列順は TableSpec 準拠（name は5列目 = index 4）
        lines = [line.split(",")[4] for line in res.text.splitlines()[1:]]
        assert lines == ["A大学の機体", "あ", "い", "う"]
    finally:
        client.__exit__(None, None, None)


def test_unknown_table_returns_404():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in ("todoist_configs", "guilds", "reminders_log", "'; DROP TABLE"):
            assert client.get(f"/api/guilds/{GUILD_A}/tables/{key}").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_tables_require_scope():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        assert client.get(f"/api/guilds/{GUILD_B}/tables").status_code == 403
        assert client.get(f"/api/guilds/{GUILD_B}/tables/members").status_code == 403
    finally:
        client.__exit__(None, None, None)

    app = create_app(_config(db_path))
    with TestClient(app) as anon:
        assert anon.get(f"/api/guilds/{GUILD_A}/tables").status_code == 401


def test_read_only_viewer_sees_can_edit_false():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path, permissions="0")
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        assert body["can_edit"] is False
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 表ごとの閲覧レベル（G4-3）
#
# `GET /settings` はロール ID の実値を L4 にだけ返している（G1-6）。
# 同じ値が表グリッド経由で L1 に見えるのでは意味がないので、
# 表の定義（TableSpec.min_level）で必要レベルを持たせている。
# ---------------------------------------------------------------------
def test_a_plain_member_cannot_see_the_settings_table():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path, permissions="0")
    try:
        keys = {t["key"] for t in client.get(f"/api/guilds/{GUILD_A}/tables").json()["tables"]}
        assert "settings" not in keys, "一覧に L4 限定の表が出ている"
        assert "audit_log" not in keys, "一覧に L3 限定の表が出ている"
        assert "members" in keys, "通常の表まで隠している"

        assert client.get(f"/api/guilds/{GUILD_A}/tables/settings").status_code == 403
        assert client.get(f"/api/guilds/{GUILD_A}/tables/audit_log").status_code == 403
        res = client.get(f"/api/guilds/{GUILD_A}/tables/settings/export.csv")
        assert res.status_code == 403, "CSV 経由なら見えてしまう"
    finally:
        client.__exit__(None, None, None)


def test_an_admin_can_see_the_settings_table():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)  # permissions=32（Manage Server）→ L4
    try:
        keys = {t["key"] for t in client.get(f"/api/guilds/{GUILD_A}/tables").json()["tables"]}
        assert {"settings", "audit_log"} <= keys
        assert client.get(f"/api/guilds/{GUILD_A}/tables/settings").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_the_new_tables_reject_edits():
    """読み取り専用の表は、行が実在しても PATCH を受け付けないこと。

    行が無いと 404 になり「編集できないから断った」のか
    「行が無いから断った」のか区別できないので、先に1行入れる。
    """
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    async def _add_row():
        db = Database(db_path)
        await db.connect()
        try:
            await AuditLogRepository(db).record(GUILD_A, "42", "setup.save", "CLUB_NAME", "A大学")
        finally:
            await db.close()

    asyncio.run(_add_row())
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/audit_log").json()
        assert body["total"] >= 1
        row_id = body["rows"][0]["audit_id"]
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/audit_log/{row_id}",
            json={"action": "書き換え"},
        )
        assert res.status_code == 400, "読み取り専用の表を編集できてしまう"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# CSV エクスポート（P3-2: Sheets 連携の置き換え）
# ---------------------------------------------------------------------
def test_csv_export_returns_labels_and_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "attachment" in res.headers["content-disposition"]
        assert f"members_{GUILD_A}.csv" in res.headers["content-disposition"]
        body = res.content.decode("utf-8-sig")
        lines = body.strip().splitlines()
        assert lines[0].startswith("ID,")  # 見出しは表示名
        assert "表示名" in lines[0]
        assert "A大学の部員" in body
        # Excel 対策の BOM が付く
        assert res.content.startswith(b"\xef\xbb\xbf")
    finally:
        client.__exit__(None, None, None)


def test_csv_export_is_guild_scoped():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in sorted(EXPECTED_TABLES):
            res = client.get(f"/api/guilds/{GUILD_A}/tables/{key}/export.csv")
            assert res.status_code == 200, key
            assert "B大学" not in res.content.decode("utf-8-sig"), key
        # 他サーバーの CSV は取得できない
        assert client.get(f"/api/guilds/{GUILD_B}/tables/members/export.csv").status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_csv_export_requires_login():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    app = create_app(_config(db_path))
    with TestClient(app) as anon:
        assert anon.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv").status_code == 401


def test_csv_export_unknown_table_is_404():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/todoist_configs/export.csv")
        assert res.status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_csv_export_is_audited():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv")
    finally:
        client.__exit__(None, None, None)

    async def _entries():
        db = Database(db_path)
        await db.connect()
        try:
            from repositories.audit_log_repository import AuditLogRepository

            return await AuditLogRepository(db).list_recent(GUILD_A)
        finally:
            await db.close()

    assert any(e["action"] == "dashboard.export" for e in asyncio.run(_entries()))


# ---------------------------------------------------------------------
# メンバー表の班（slug ではなく班名で表示。編集は生値のまま）
# ---------------------------------------------------------------------
def test_member_team_columns_are_team_typed_and_editable():
    by_name = {c.name: c for c in TABLES["members"].columns}
    assert by_name["primary_team"].type == "team"
    assert by_name["secondary_teams"].type == "team_list"
    # 主所属班・副所属班は従来どおり編集できる
    assert by_name["primary_team"].editable is True
    assert by_name["secondary_teams"].editable is True


def test_teams_table_key_column_stays_text():
    """班シートの「班キー」列は slug のままで正しい（表示解決の対象外）。"""
    by_name = {c.name: c for c in TABLES["teams"].columns}
    assert by_name["team_key"].type == "text"


# ---------------------------------------------------------------------
# 機体重量の列（F3-4）
# ---------------------------------------------------------------------
def test_progress_table_exposes_weight_columns():
    names = TABLES["progress"].column_names
    assert "target_weight_g" in names
    assert "actual_weight_g" in names


def test_weight_columns_are_editable():
    """班長以上（表グリッドの編集権限）で編集できる列にする。"""
    editable = TABLES["progress"].editable_columns
    assert "target_weight_g" in editable
    assert "actual_weight_g" in editable


def test_weight_columns_are_numeric():
    by_name = {c.name: c for c in TABLES["progress"].columns}
    assert by_name["target_weight_g"].type == "number"
    assert by_name["actual_weight_g"].type == "number"


def test_weight_columns_appear_in_csv_export():
    csv_text = rows_to_csv(TABLES["progress"], [])
    assert "目標重量(g)" in csv_text
    assert "実測重量(g)" in csv_text


def test_weight_can_be_updated_through_table_repository():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            await ProgressRepository(db).upsert_node(GUILD_A, "wing", name="主翼", now_text=NOW)
            row = await db.fetchone(
                "SELECT progress_node_id FROM progress_nodes WHERE guild_id = ?", (GUILD_A,)
            )
            pk = row["progress_node_id"]

            table = TableRepository(db)
            assert await table.update_row(
                GUILD_A, "progress", pk, {"actual_weight_g": 1240.0, "target_weight_g": 1100.0}
            )

            after = await table.get_row(GUILD_A, "progress", pk)
            assert after["actual_weight_g"] == 1240.0
            assert after["target_weight_g"] == 1100.0
        finally:
            await db.close()

    asyncio.run(_main())


def test_weight_update_does_not_cross_guilds():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = ProgressRepository(db)
            await repo.upsert_node(GUILD_A, "wing", name="主翼", now_text=NOW)
            await repo.upsert_node(GUILD_B, "wing", name="別大学", now_text=NOW)
            row_b = await db.fetchone(
                "SELECT progress_node_id FROM progress_nodes WHERE guild_id = ?", (GUILD_B,)
            )

            table = TableRepository(db)
            # B の行 ID を A のスコープで更新しようとしても通らない
            assert (
                await table.update_row(
                    GUILD_A, "progress", row_b["progress_node_id"], {"actual_weight_g": 999.0}
                )
                is False
            )

            after = await table.get_row(GUILD_B, "progress", row_b["progress_node_id"])
            assert after["actual_weight_g"] is None
        finally:
            await db.close()

    asyncio.run(_main())


# ---------------------------------------------------------------------
# CSV は全件出す / シート絞り込みに従う
#
# 画面表示の上限（MAX_LIMIT=500）で切ると、数年運用したサーバーが
# 引き継ぎ用に落とした CSV から古い行が黙って欠ける。監査ログには
# 「500 行を CSV 出力」と正常終了で残るため、欠落に気づく手段が無い。
# ---------------------------------------------------------------------
def test_csv_export_is_not_capped_at_display_limit():
    from repositories.table_repository import MAX_LIMIT

    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))

    over = MAX_LIMIT + 20

    async def _seed_many():
        db = Database(db_path)
        await db.connect()
        try:
            repo = MemberRepository(db)
            for i in range(over):
                await repo.upsert_member(GUILD_A, f"u{i:04d}", f"部員{i:04d}")
        finally:
            await db.close()

    asyncio.run(_seed_many())

    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv")
        assert res.status_code == 200
        body = res.content.decode("utf-8-sig")
        assert "部員0000" in body
        assert f"部員{over - 1:04d}" in body, "MAX_LIMIT で打ち切られている"
        # 見出し1行 + データ行（末尾の改行で空要素が出ないよう strip）
        assert len(body.strip().splitlines()) >= over + 1
    finally:
        client.__exit__(None, None, None)


def test_csv_export_rejects_sheet_for_plain_table():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members/export.csv?sheet=x")
        assert res.status_code == 400
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# PostgreSQL 実機での検索（D1-2）
#
# SQLite の LIKE は既定で大文字小文字を区別しない（ASCII のみ）が、
# PostgreSQL は区別する。`lower(col) LIKE lower(?)` の揃え方が実機で
# 効いていることを、SQLite では検出できない大文字検索で確かめる。
# CLUB_TEST_PG_DSN があるときだけ走る（gotcha `dashboard-tests-silently-skipped`）。
# ---------------------------------------------------------------------
async def _pg_database_name(dsn: str) -> str:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchval("SELECT current_database()")
    finally:
        await con.close()


def _pg_dsn_or_skip() -> str:
    dsn = os.getenv("CLUB_TEST_PG_DSN")
    if not dsn:
        pytest.skip("CLUB_TEST_PG_DSN 未設定（テスト専用 DB の DSN を指定してください）")
    name = asyncio.run(_pg_database_name(dsn))
    if "test" not in name.lower():
        pytest.skip(f"安全のためテスト専用 DB でのみ実行します（接続先: {name}）")
    return dsn


async def _pg_reset_rows(dsn: str) -> None:
    """このテストが使うギルドの行だけ消す（他の行には触らない）。"""
    db = Database("./unused.db", database_url=dsn)
    await db.connect()
    try:
        for table in ("progress_nodes", "members", "guilds"):
            for guild_id in (GUILD_A, GUILD_B):
                await db.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
    finally:
        await db.close()


def test_pg_live_search_is_case_insensitive_and_escaped():
    dsn = _pg_dsn_or_skip()
    asyncio.run(_pg_reset_rows(dsn))

    async def _main() -> None:
        db = Database("./unused.db", database_url=dsn)
        await db.connect()
        try:
            await GuildRepository(db).ensure(GUILD_A, "A大学")
            members = MemberRepository(db)
            await members.upsert_member(GUILD_A, "u-1", "Main_Spar の検査")
            await members.upsert_member(GUILD_A, "u-2", "MainXSpar の検査")
            await members.upsert_member(GUILD_A, "u-3", "進捗50%の報告")

            repo = TableRepository(db)
            # 大文字小文字を区別しない（PG の LIKE は素だと区別する）
            rows = await repo.list_rows(GUILD_A, "members", q="main_spar")
            assert [r["display_name"] for r in rows] == ["Main_Spar の検査"]
            rows = await repo.list_rows(GUILD_A, "members", q="MAIN_SPAR")
            assert [r["display_name"] for r in rows] == ["Main_Spar の検査"]
            # % はワイルドカードとして扱わない
            rows = await repo.list_rows(GUILD_A, "members", q="50%")
            assert [r["display_name"] for r in rows] == ["進捗50%の報告"]
            # total にも効く
            assert await repo.count_rows(GUILD_A, "members", q="main_spar") == 1
        finally:
            await db.close()

    asyncio.run(_main())


def test_pg_live_sort_nulls_are_always_last():
    """PG でも NULL が昇順・降順とも末尾に来ること（D1-3）。

    PostgreSQL の既定は DESC で NULL が先頭（NULLS FIRST）。
    `(col IS NULL), col` の書き方が実機で効いていることを確かめる
    （SQLite は元々 DESC でも NULL が末尾なので、SQLite だけでは検出できない）。
    """
    dsn = _pg_dsn_or_skip()
    asyncio.run(_pg_reset_rows(dsn))

    async def _main() -> None:
        db = Database("./unused.db", database_url=dsn)
        await db.connect()
        try:
            await GuildRepository(db).ensure(GUILD_A, "A大学")
            progress = ProgressRepository(db)
            await progress.upsert_node(
                GUILD_A, "n-low", name="低", manual_progress=0.25, now_text=NOW)
            await progress.upsert_node(
                GUILD_A, "n-high", name="高", manual_progress=0.75, now_text=NOW)
            await progress.upsert_node(GUILD_A, "n-none", name="なし", now_text=NOW)

            repo = TableRepository(db)
            for direction, expected in (
                ("asc", [0.25, 0.75, None]),
                ("desc", [0.75, 0.25, None]),
            ):
                rows = await repo.list_rows(
                    GUILD_A, "progress", sort="manual_progress", dir=direction)
                assert [r["manual_progress"] for r in rows] == expected, direction
        finally:
            await db.close()

    asyncio.run(_main())
