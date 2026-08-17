"""ダッシュボードのシート切替とID表示廃止（表示名・チャンネル名・JST）のテスト。

- 出欠回答: タブ1つ = 予定1件。指定した予定の回答だけが返り、開催日時の
  降順に並ぶ。予定 0 件でも落ちない
- 桁巻き記録: タブ1つ = 桁1つ。マスタに無い桁（記録にだけ残る）も漏れない
- 表示名: 名前キャッシュ → members 台帳 → ID フォールバックの優先順位
- チャンネル名: #付き表示と削除済みフォールバック
- 日時: JST 秒単位の `_display`（時刻未指定の候補日時だけは日付のみ）
- すべて guild_id スコープ（他ギルドの予定・桁・名前が混ざらない）
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
from repositories.guild_repository import GuildRepository
from repositories.layer_keta_repository import LayerKetaRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.name_cache_repository import (
    ENTITY_CHANNEL,
    ENTITY_USER,
    NameCacheRepository,
)
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database

GUILD_A = 100000000000000001
GUILD_B = 200000000000000002
USER_ID = "42"
NOW = "2026-08-11 10:00"

# JST 秒単位の表示形式（分で丸めない）
JST_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


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
    """A大学に予定2件・桁3種、B大学に予定1件・桁1種を入れる。"""
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await GuildRepository(db).ensure(GUILD_B, "B大学")
        members = MemberRepository(db)
        schedules = ScheduleRepository(db)
        ketas = LayerKetaRepository(db)
        records = LayerSessionRepository(db)
        cache = NameCacheRepository(db)

        # --- A大学 ---
        # 名簿（台帳のみの部員 44 を含む）と Discord 表示名キャッシュ
        await members.upsert_member(GUILD_A, USER_ID, "A大学の部員")
        await members.upsert_member(GUILD_A, "44", "台帳のみ部員")
        await cache.upsert(
            GUILD_A, ENTITY_USER, USER_ID, "山田(ニック)", "2026-08-15T21:00:00+09:00"
        )
        await cache.upsert(GUILD_A, ENTITY_CHANNEL, "555", "general", "2026-08-15T21:00:00+09:00")
        # 班（通知チャンネルは未キャッシュの 777 = 削除済み扱い）
        await members.upsert_team(GUILD_A, "wing", "主翼班", channel_id="777")

        # 予定1: 合宿（開催候補は 9/10 と 9/11。9/10 が代表日時になる）
        await schedules.create_schedule(
            GUILD_A,
            "sch-a1",
            "合宿",
            None,
            "琵琶湖",
            None,
            "2026-09-01T19:00:00+09:00",
            USER_ID,
            "555",
        )
        await schedules.add_option(
            GUILD_A, "opt-a1a", "sch-a1", "9/10(木) 9:00", "2026-09-10T09:00:00+09:00", None, None
        )
        await schedules.add_option(
            GUILD_A, "opt-a1b", "sch-a1", "9/11(金) 9:00", "2026-09-11T09:00:00+09:00", None, None
        )
        await schedules.set_vote(GUILD_A, "opt-a1a", USER_ID, "ok")
        await schedules.set_vote(GUILD_A, "opt-a1b", USER_ID, "maybe")

        # 予定2: ミーティング（8/20。退会済みユーザー 43 が回答）
        await schedules.create_schedule(
            GUILD_A,
            "sch-a2",
            "ミーティング",
            None,
            "部室",
            None,
            "2026-08-18T19:00:00+09:00",
            USER_ID,
            "555",
        )
        await schedules.add_option(
            GUILD_A, "opt-a2a", "sch-a2", "8/20(木) 19:00", "2026-08-20T19:00:00+09:00", None, None
        )
        await schedules.set_vote(GUILD_A, "opt-a2a", "43", "ng")

        # 桁: マスタ2種 + マスタに無い「旧桁」の記録
        await ketas.add(GUILD_A, "主桁", USER_ID, NOW)
        await ketas.add(GUILD_A, "翼桁", USER_ID, NOW)
        await records.add_record(GUILD_A, USER_ID, "主桁", "1", NOW, NOW, 60)
        await records.add_record(GUILD_A, USER_ID, "主桁", "2", NOW, NOW, 45)
        await records.add_record(GUILD_A, USER_ID, "翼桁", "1", NOW, NOW, 30)
        await records.add_record(GUILD_A, USER_ID, "旧桁", "1", NOW, NOW, 15)

        # --- B大学（混入すれば名前で分かる） ---
        await members.upsert_member(GUILD_B, "900", "B大学の部員")
        await schedules.create_schedule(
            GUILD_B,
            "sch-b1",
            "B大学のミーティング",
            None,
            None,
            None,
            "2026-09-01T19:00:00+09:00",
            "900",
            "9555",
        )
        await schedules.add_option(
            GUILD_B, "opt-b1a", "sch-b1", "B大学の候補日", "2026-09-02T19:00:00+09:00", None, None
        )
        await schedules.set_vote(GUILD_B, "opt-b1a", "900", "ok")
        await ketas.add(GUILD_B, "B桁", "900", NOW)
        await records.add_record(GUILD_B, "900", "B桁", "1", NOW, NOW, 60)
    finally:
        await db.close()


async def _seed_empty(db_path: str) -> None:
    """予定も桁も無いギルド（空状態の検証用）。"""
    db = Database(db_path)
    await db.connect()
    try:
        await GuildRepository(db).ensure(GUILD_A, "A大学")
        await MemberRepository(db).upsert_member(GUILD_A, USER_ID, "部員")
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
# 出欠回答: 予定ごとのシート切替
# ---------------------------------------------------------------------
def test_attendance_sheets_are_sorted_desc_and_default_to_first():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        sheets = body["sheets"]
        assert sheets["noun"] == "予定"
        # 開催日時（最初の候補日）の降順: 合宿 9/10 → ミーティング 8/20
        assert [s["id"] for s in sheets["items"]] == ["sch-a1", "sch-a2"]
        assert sheets["items"][0]["label"] == "合宿"
        assert sheets["items"][0]["at"] == "2026-09-10 09:00:00"
        # 未指定なら先頭（最新）のシートに絞られる
        assert sheets["active"] == "sch-a1"
        assert body["total"] == 2
        assert {r["option_id"] for r in body["rows"]} == {"opt-a1a", "opt-a1b"}
    finally:
        client.__exit__(None, None, None)


def test_attendance_sheet_param_filters_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(
            f"/api/guilds/{GUILD_A}/tables/schedule_votes", params={"sheet": "sch-a2"}
        ).json()
        assert body["sheets"]["active"] == "sch-a2"
        assert body["total"] == 1
        assert [r["option_id"] for r in body["rows"]] == ["opt-a2a"]
    finally:
        client.__exit__(None, None, None)


def test_attendance_sheets_are_guild_scoped():
    """他ギルドの予定はタブに出ず、ID を直指定しても行は返らない。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        assert all(s["id"] != "sch-b1" for s in body["sheets"]["items"])

        stolen = client.get(
            f"/api/guilds/{GUILD_A}/tables/schedule_votes", params={"sheet": "sch-b1"}
        ).json()
        assert stolen["total"] == 0
        assert stolen["rows"] == []
        # ピボットにも他ギルドの候補・回答者は現れない
        assert stolen["pivot"]["rows"] == []
        assert "B大学" not in str(stolen)
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 桁巻き記録: 桁ごとのシート切替
# ---------------------------------------------------------------------
def test_layer_sheets_include_master_and_orphan_keta():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/layer_records").json()
        sheets = body["sheets"]
        assert sheets["noun"] == "桁"
        # マスタ（主桁・翼桁）に加え、記録にだけ残る旧桁も漏れない
        assert [s["id"] for s in sheets["items"]] == ["主桁", "旧桁", "翼桁"]
        # 既定は先頭の桁。その桁の記録だけが返る
        assert sheets["active"] == "主桁"
        assert body["total"] == 2
        assert all(r["keta"] == "主桁" for r in body["rows"])
    finally:
        client.__exit__(None, None, None)


def test_layer_sheet_param_filters_rows():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        for keta, count in (("翼桁", 1), ("旧桁", 1), ("B桁", 0)):
            body = client.get(
                f"/api/guilds/{GUILD_A}/tables/layer_records", params={"sheet": keta}
            ).json()
            assert body["total"] == count, keta
            assert all(r["keta"] == keta for r in body["rows"])
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 空状態（予定 0 件・桁 0 件）
# ---------------------------------------------------------------------
def test_empty_sheets_do_not_crash():
    db_path = _tmp_db_path()
    asyncio.run(_seed_empty(db_path))
    client = _logged_in_client(db_path)
    try:
        for key in ("schedule_votes", "layer_records"):
            res = client.get(f"/api/guilds/{GUILD_A}/tables/{key}")
            assert res.status_code == 200, key
            body = res.json()
            assert body["sheets"]["items"] == []
            assert body["sheets"]["active"] is None
            assert body["rows"] == []
            assert body["total"] == 0
    finally:
        client.__exit__(None, None, None)


def test_sheet_param_is_rejected_for_plain_tables():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/members", params={"sheet": "x"})
        assert res.status_code == 400
        # シート非対応の表は sheets もピボットも返さない
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        assert body["sheets"] is None
        assert body["pivot"] is None
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 表示名・チャンネル名・JST の解決（_display）
# ---------------------------------------------------------------------
def test_user_display_prefers_cache_then_register_then_id():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        # members 表: キャッシュ優先（42 = 山田(ニック)）と台帳のみ（44）
        body = client.get(f"/api/guilds/{GUILD_A}/tables/members").json()
        display_by_uid = {r["user_id"]: r["_display"]["user_id"] for r in body["rows"]}
        assert display_by_uid[USER_ID] == "山田(ニック)"
        assert display_by_uid["44"] == "台帳のみ部員"

        # 出欠回答: 退会済み（キャッシュにも台帳にも無い 43）は ID 付き
        votes = client.get(
            f"/api/guilds/{GUILD_A}/tables/schedule_votes", params={"sheet": "sch-a2"}
        ).json()
        assert votes["rows"][0]["_display"]["user_id"] == "不明なユーザー (43)"
        # 生の値（ID）は残っている（DB は ID 保持）
        assert votes["rows"][0]["user_id"] == "43"
    finally:
        client.__exit__(None, None, None)


def test_channel_display_resolves_and_falls_back():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        schedules = client.get(f"/api/guilds/{GUILD_A}/tables/schedules").json()
        by_id = {r["schedule_id"]: r for r in schedules["rows"]}
        assert by_id["sch-a1"]["_display"]["channel_id"] == "#general"

        teams = client.get(f"/api/guilds/{GUILD_A}/tables/teams").json()
        assert teams["rows"][0]["_display"]["channel_id"] == ("不明なチャンネル (777)")
    finally:
        client.__exit__(None, None, None)


def test_datetimes_are_displayed_in_jst_seconds():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        # 出欠回答の updated_at（set_vote が aware ISO で書く）
        votes = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        for row in votes["rows"]:
            assert JST_SECONDS.match(row["_display"]["updated_at"]), row

        # schedules.deadline は datetime 型として整形される
        schedules = client.get(f"/api/guilds/{GUILD_A}/tables/schedules").json()
        by_id = {r["schedule_id"]: r for r in schedules["rows"]}
        assert by_id["sch-a1"]["_display"]["deadline"] == "2026-09-01 19:00:00"
        # 候補ラベルも解決される
        assert votes["rows"][0]["_display"]["option_id"] in ("9/10(木) 9:00", "9/11(金) 9:00")
    finally:
        client.__exit__(None, None, None)


def test_csv_export_uses_display_values():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes/export.csv")
        assert res.status_code == 200
        assert res.content.startswith(b"\xef\xbb\xbf")  # Excel 用 BOM
        body = res.content.decode("utf-8-sig")
        lines = body.strip().splitlines()
        assert lines[0] == "ID,候補,回答者,回答,更新日時"
        # 回答者列は表示名。生のユーザー ID がフィールドとして現れない
        fields = {f for line in lines[1:] for f in line.split(",")}
        assert "山田(ニック)" in fields
        assert USER_ID not in fields
        assert "不明なユーザー (43)" in fields
    finally:
        client.__exit__(None, None, None)


def test_patch_response_includes_display():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)  # permissions=32 → L4（編集可）
    try:
        votes = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        row = votes["rows"][0]
        res = client.patch(
            f"/api/guilds/{GUILD_A}/tables/schedule_votes/{row['vote_id']}", json={"status": "ng"}
        )
        assert res.status_code == 200
        updated = res.json()["row"]
        assert updated["status"] == "ng"
        assert updated["_display"]["user_id"] == "山田(ニック)"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------
# 出欠回答のピボット表（1行 = 候補日時、セル = 表示名の列挙）
# ---------------------------------------------------------------------
def test_attendance_pivot_columns_and_rows_ascending():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        pivot = body["pivot"]
        # 列構成は固定 5 列
        assert [c["label"] for c in pivot["columns"]] == [
            "候補日時",
            "参加",
            "不参加",
            "未定",
            "未回答",
        ]
        # 1行 = 候補日時1つ。昇順・JST 秒単位
        assert [r["at"] for r in pivot["rows"]] == [
            "2026-09-10 09:00:00",
            "2026-09-11 09:00:00",
        ]
    finally:
        client.__exit__(None, None, None)


def test_attendance_pivot_groups_votes_by_status():
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        rows = body["pivot"]["rows"]
        # 9/10 は参加、9/11 は未定（seed の投票どおり）。表示名で列挙される
        assert rows[0]["groups"]["ok"] == ["山田(ニック)"]
        assert rows[0]["groups"]["maybe"] == []
        assert rows[1]["groups"]["ok"] == []
        assert rows[1]["groups"]["maybe"] == ["山田(ニック)"]
        # 未回答 = 現役の登録メンバーのうち、どの候補にも投票していない人。
        # 全行で同じ顔ぶれ（bot の催促と同じ「予定単位」の定義）
        for row in rows:
            assert row["groups"]["none"] == ["台帳のみ部員"]
    finally:
        client.__exit__(None, None, None)


def test_attendance_pivot_includes_unregistered_voter_with_fallback():
    """台帳に無い回答者（退会済み等）も ID 付きフォールバック名でセルに出る。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(
            f"/api/guilds/{GUILD_A}/tables/schedule_votes", params={"sheet": "sch-a2"}
        ).json()
        rows = body["pivot"]["rows"]
        assert rows[0]["groups"]["ng"] == ["不明なユーザー (43)"]
        # 現役メンバーは誰も回答していないので全員が未回答
        # （名前順: 台帳のみ部員 → 山田(ニック)）
        assert rows[0]["groups"]["none"] == ["台帳のみ部員", "山田(ニック)"]
    finally:
        client.__exit__(None, None, None)


def test_attendance_pivot_with_no_options_is_empty():
    """候補日時が 0 件の予定でもクラッシュせず空のピボットを返す。"""
    db_path = _tmp_db_path()

    async def _seed_no_options():
        db = Database(db_path)
        await db.connect()
        try:
            await GuildRepository(db).ensure(GUILD_A, "A大学")
            await MemberRepository(db).upsert_member(GUILD_A, USER_ID, "部員")
            await ScheduleRepository(db).create_schedule(
                GUILD_A,
                "sch-x",
                "候補未定の予定",
                None,
                None,
                None,
                "2026-09-01T19:00:00+09:00",
                USER_ID,
                "555",
            )
        finally:
            await db.close()

    asyncio.run(_seed_no_options())
    client = _logged_in_client(db_path)
    try:
        res = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes")
        assert res.status_code == 200
        body = res.json()
        assert body["sheets"]["active"] == "sch-x"
        assert body["pivot"]["rows"] == []
        assert body["rows"] == []
    finally:
        client.__exit__(None, None, None)


def test_attendance_pivot_date_only_option_omits_time():
    """時刻未指定の候補（label が日付だけ）は 00:00:00 を出さず日付だけにする。

    start_at は bot が to_iso(parse_datetime(label)) で書くため、日付だけの
    入力でも 00:00:00 付きになる。判定は label で行う。時刻付きの候補と
    他の datetime 列（締切・更新日時）は従来どおり秒まで出す。
    """
    db_path = _tmp_db_path()

    async def _seed_date_only():
        db = Database(db_path)
        await db.connect()
        try:
            await GuildRepository(db).ensure(GUILD_A, "A大学")
            await MemberRepository(db).upsert_member(GUILD_A, USER_ID, "部員")
            schedules = ScheduleRepository(db)
            await schedules.create_schedule(
                GUILD_A,
                "sch-d",
                "終日作業",
                None,
                None,
                None,
                "2026-08-31T23:59:00+09:00",
                USER_ID,
                "555",
            )
            # 日付だけで登録した候補（bot と同じく 00:00:00 付きで保存される）
            await schedules.add_option(
                GUILD_A, "opt-d1", "sch-d", "2026-09-01", "2026-09-01T00:00:00+09:00", None, None
            )
            # 時刻付きの候補
            await schedules.add_option(
                GUILD_A, "opt-d2", "sch-d", "9/2 19:00", "2026-09-02T19:00:00+09:00", None, None
            )
            await schedules.set_vote(GUILD_A, "opt-d1", USER_ID, "ok")
        finally:
            await db.close()

    asyncio.run(_seed_date_only())
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes").json()
        assert [r["at"] for r in body["pivot"]["rows"]] == ["2026-09-01", "2026-09-02 19:00:00"]
        assert "00:00:00" not in str(body["pivot"])
        # 他の datetime 列は秒まで（更新日時・締切）
        for row in body["rows"]:
            assert JST_SECONDS.match(row["_display"]["updated_at"]), row
        schedules = client.get(f"/api/guilds/{GUILD_A}/tables/schedules").json()
        assert schedules["rows"][0]["_display"]["deadline"] == "2026-08-31 23:59:00"

        # CSV の候補列も画面と同じ（ユーザーが打った日付だけの表記。00:00:00 は出ない）
        res = client.get(f"/api/guilds/{GUILD_A}/tables/schedule_votes/export.csv")
        lines = res.content.decode("utf-8-sig").strip().splitlines()
        assert lines[0] == "ID,候補,回答者,回答,更新日時"
        assert lines[1].split(",")[1] == "2026-09-01"
        assert "2026-09-01 00:00:00" not in res.text
    finally:
        client.__exit__(None, None, None)


def test_layer_records_have_no_pivot():
    """ピボットは出欠回答だけ（桁巻き記録は従来のグリッド表示）。"""
    db_path = _tmp_db_path()
    asyncio.run(_seed(db_path))
    client = _logged_in_client(db_path)
    try:
        body = client.get(f"/api/guilds/{GUILD_A}/tables/layer_records").json()
        assert body["pivot"] is None
    finally:
        client.__exit__(None, None, None)
