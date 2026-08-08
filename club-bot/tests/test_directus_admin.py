"""Directus セルフサービス・アクセス発行のテスト。

外部 HTTP は一切発生させず、FakeDirectusClient を注入して検証する。

- スキーマ v7/v9（guild_directus_access）・v8（members の代理主キー）
- Directus 未設定時に例外ではなく案内 Embed を返すこと
- 発行時に guild_directus_access へ記録され監査ログが残ること
- 失効時に Directus 側の停止が呼ばれ status が revoked になること
- ギルド分離（他ギルドの発行が見えないこと）
- メール重複・API 失敗時に DB を変更しないこと
- 秘密情報（管理トークン）を Embed に出さないこと

実行: venv/Scripts/python -m pytest tests/
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from cogs.directus_admin import DirectusAdmin
from repositories.directus_access_repository import (
    STATUS_INVITED,
    STATUS_REVOKED,
    DirectusAccessRepository,
)
from services.directus_service import (
    DirectusClient,
    DirectusEmailInUse,
    DirectusError,
    DirectusUnavailable,
)
from utils.db import SCHEMA_VERSION, Database

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2

EMAIL1 = "admin1@example.com"
EMAIL2 = "admin2@example.com"
ADMIN_TOKEN = "super-secret-directus-admin-token"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 側で新規作成させる
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ------------------------------------------------------------------
# Directus クライアントの Fake（外部 API のモック）
# ------------------------------------------------------------------

class FakeDirectusClient:
    """invite_user / revoke_user の呼び出しを記録するスタブ。"""

    def __init__(self, base_url: str = "http://localhost:8055",
                 raises: Exception | None = None):
        self.base_url = base_url
        self.invited: list[tuple[str, int]] = []
        self.revoked: list[str] = []
        self.patched: list[tuple[str, int]] = []
        self._raises = raises
        self._counter = 0

    async def invite_user(self, email: str, guild_id: int) -> str:
        if self._raises is not None:
            raise self._raises
        self.invited.append((email, guild_id))
        self._counter += 1
        user_id = f"directus-user-{self._counter}"
        self.patched.append((user_id, guild_id))
        return user_id

    async def revoke_user(self, user_id: str) -> None:
        if self._raises is not None:
            raise self._raises
        self.revoked.append(user_id)


def _make_cog(db: Database, client: FakeDirectusClient | None) -> DirectusAdmin:
    """commands.Cog は bot 参照を保持するだけなので SimpleNamespace で代用。"""
    def factory():
        if client is None:
            raise DirectusUnavailable("未設定")
        return client

    return DirectusAdmin(SimpleNamespace(db=db), client_factory=factory)


def _text(embed) -> str:
    """Embed のタイトル・本文・フィールドを結合した検証用テキスト。"""
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.append(str(field.name))
        parts.append(str(field.value))
    if embed.footer:
        parts.append(str(embed.footer.text or ""))
    return "\n".join(parts)


# ------------------------------------------------------------------
# スキーマ
# ------------------------------------------------------------------

def test_schema_has_guild_directus_access():
    async def _main():
        db = await _make_db()
        try:
            cols = {r["name"] for r in
                    await db.fetchall("PRAGMA table_info(guild_directus_access)")}
            assert {"guild_id", "directus_user_id", "email", "status",
                    "created_by", "created_at", "updated_at"} <= cols
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()
    run(_main())


def test_schema_has_no_directus_prefixed_table():
    """bot のテーブルが Directus の予約プレフィックスを使わないこと。

    Directus は業務 DB と同じデータベースへ `directus_*` という名前で
    自身のシステムテーブルを作る。bot 側が同じ名前空間を使うと、
    Directus 11 の `directus_access` のように衝突して Directus の初回
    セットアップが失敗する（回帰防止）。
    """
    async def _main():
        db = await _make_db()
        try:
            rows = await db.fetchall(
                "SELECT name FROM sqlite_master"
                " WHERE type IN ('table', 'view')")
            offenders = [r["name"] for r in rows
                         if r["name"].startswith("directus_")]
            assert offenders == [], (
                f"Directus の予約プレフィックスと衝突: {offenders}")
        finally:
            await db.close()
    run(_main())


def test_migrate_v9_renames_legacy_directus_access():
    """旧 directus_access が guild_directus_access へ改名され行が残ること。"""
    async def _main():
        path = _tmp_db_path()
        # v8 相当の DB を用意（テーブル名は旧名 directus_access）
        db = Database(path)
        await db.connect()
        await db.execute(
            "ALTER TABLE guild_directus_access RENAME TO directus_access")
        await db.execute(
            "INSERT INTO directus_access (guild_id, directus_user_id, email,"
            " status, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (G1, "legacy-user-1", EMAIL1, STATUS_INVITED, "admin1",
             "2026-01-01T00:00:00+09:00", "2026-01-01T00:00:00+09:00"))
        await db.conn.execute("PRAGMA user_version = 8")
        await db.conn.commit()
        await db.close()

        # 再接続で v9 マイグレーションが走る
        db2 = Database(path)
        await db2.connect()
        try:
            names = {r["name"] for r in await db2.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert "guild_directus_access" in names
            assert "directus_access" not in names

            row = await db2.fetchone(
                "SELECT * FROM guild_directus_access WHERE guild_id = ?",
                (G1,))
            assert row is not None
            assert row["email"] == EMAIL1
            assert row["directus_user_id"] == "legacy-user-1"

            ver = await db2.fetchone("PRAGMA user_version")
            assert ver[0] == SCHEMA_VERSION
        finally:
            await db2.close()
            os.unlink(path)
    run(_main())


def test_migrate_v9_keeps_directus_system_table():
    """Directus 自身の directus_access（guild_id 列なし）は改名しないこと。"""
    async def _main():
        path = _tmp_db_path()
        db = Database(path)
        await db.connect()
        # Directus 11 のシステムテーブルを模した定義（guild_id 列を持たない）
        await db.execute(
            "CREATE TABLE directus_access ("
            " id TEXT PRIMARY KEY, role TEXT, \"user\" TEXT, policy TEXT)")
        await db.execute(
            "INSERT INTO directus_access (id, role, \"user\", policy)"
            " VALUES ('a1', 'r1', NULL, 'p1')")
        await db.conn.execute("PRAGMA user_version = 8")
        await db.conn.commit()
        await db.close()

        db2 = Database(path)
        await db2.connect()
        try:
            # Directus のテーブルはそのまま残る
            cols = {r["name"] for r in await db2.fetchall(
                "PRAGMA table_info(directus_access)")}
            assert cols == {"id", "role", "user", "policy"}
            row = await db2.fetchone(
                "SELECT id FROM directus_access WHERE id = 'a1'")
            assert row is not None
            # bot 側のテーブルは新名で存在する
            assert await db2.fetchall(
                "PRAGMA table_info(guild_directus_access)")
        finally:
            await db2.close()
            os.unlink(path)
    run(_main())


def test_schema_v8_members_has_surrogate_pk():
    """members が単一列の主キー member_id を持ち、自然キーが UNIQUE であること。"""
    async def _main():
        db = await _make_db()
        try:
            info = await db.fetchall("PRAGMA table_info(members)")
            pk_cols = [r["name"] for r in info if r["pk"]]
            assert pk_cols == ["member_id"], f"単一列 PK であるべき: {pk_cols}"

            # 自然キーの一意性は維持されている
            await db.execute(
                "INSERT INTO members (guild_id, user_id, display_name, joined_at)"
                " VALUES (?, ?, ?, ?)", (G1, "u1", "Taro", "2026-01-01"))
            try:
                await db.execute(
                    "INSERT INTO members (guild_id, user_id, display_name, joined_at)"
                    " VALUES (?, ?, ?, ?)", (G1, "u1", "重複", "2026-01-02"))
                raise AssertionError("UNIQUE (guild_id, user_id) が効くべき")
            except aiosqlite.IntegrityError:
                pass

            # 同じ user_id でもギルドが違えば登録できる
            await db.execute(
                "INSERT INTO members (guild_id, user_id, display_name, joined_at)"
                " VALUES (?, ?, ?, ?)", (G2, "u1", "別ギルドの同名", "2026-01-03"))
            rows = await db.fetchall("SELECT member_id FROM members")
            assert len({r["member_id"] for r in rows}) == 2
        finally:
            await db.close()
    run(_main())


# v8 適用前（複合主キー）の members 定義
_LEGACY_MEMBERS_DDL = """
CREATE TABLE members (
    guild_id        INTEGER NOT NULL CHECK (guild_id >= 0),
    user_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);
"""


def test_v8_migration_preserves_existing_members():
    """複合主キーの既存 DB から移行しても members の行が保持されること。"""
    async def _main():
        path = _tmp_db_path()
        conn = await aiosqlite.connect(path)
        await conn.executescript(_LEGACY_MEMBERS_DDL)
        await conn.execute(
            "INSERT INTO members (guild_id, user_id, display_name, primary_team,"
            " joined_at) VALUES (?, ?, ?, ?, ?)",
            (G1, "u1", "Taro", "design", "2026-01-01"))
        await conn.execute(
            "INSERT INTO members (guild_id, user_id, display_name, joined_at)"
            " VALUES (?, ?, ?, ?)", (G2, "u2", "Hanako", "2026-01-02"))
        await conn.execute("PRAGMA user_version = 6")  # v7/v8 適用前
        await conn.commit()
        await conn.close()

        db = Database(path)
        await db.connect()
        try:
            rows = await db.fetchall(
                "SELECT member_id, guild_id, user_id, display_name, primary_team"
                " FROM members ORDER BY member_id")
            assert len(rows) == 2
            assert rows[0]["display_name"] == "Taro"
            assert rows[0]["primary_team"] == "design"   # 全列がコピーされる
            assert rows[1]["display_name"] == "Hanako"
            # 代理キーが採番されている
            assert all(r["member_id"] for r in rows)
            assert rows[0]["member_id"] != rows[1]["member_id"]

            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            # v7 のテーブルも作成されている
            cols = await db.fetchall("PRAGMA table_info(guild_directus_access)")
            assert cols
        finally:
            await db.close()

        # 再接続しても冪等（行が増減しない）
        db2 = Database(path)
        await db2.connect()
        try:
            row = await db2.fetchone("SELECT COUNT(*) AS c FROM members")
            assert row["c"] == 2
        finally:
            await db2.close()
    run(_main())


# ------------------------------------------------------------------
# 未設定時の扱い（例外ではなく案内）
# ------------------------------------------------------------------

def test_unconfigured_returns_guidance_not_error():
    async def _main():
        db = await _make_db()
        try:
            cog = _make_cog(db, None)  # DIRECTUS_URL 等が未設定の状態
            embed = await cog.issue_access(G1, EMAIL1, actor_id="admin")
            assert "未設定" in _text(embed)
            assert "DIRECTUS_URL" in _text(embed)
            # DB は変更されない
            assert await DirectusAccessRepository(db).get(G1) is None

            status = await cog.build_status_embed(G1)
            assert "未設定" in _text(status)

            revoke = await cog.revoke_access(G1, actor_id="admin")
            assert "発行されていません" in _text(revoke)
        finally:
            await db.close()
    run(_main())


# ------------------------------------------------------------------
# 発行
# ------------------------------------------------------------------

def test_issue_access_records_row_and_audit_log():
    async def _main():
        db = await _make_db()
        try:
            client = FakeDirectusClient()
            cog = _make_cog(db, client)
            embed = await cog.issue_access(G1, EMAIL1, actor_id="admin1",
                                           actor_name="管理者A")

            assert client.invited == [(EMAIL1, G1)]
            # guild_id がユーザーに設定される（未設定だと何も見えないため必須）
            assert client.patched == [("directus-user-1", G1)]

            record = await DirectusAccessRepository(db).get(G1)
            assert record["email"] == EMAIL1
            assert record["directus_user_id"] == "directus-user-1"
            assert record["status"] == STATUS_INVITED
            assert record["created_by"] == "admin1"

            logs = await db.fetchall(
                "SELECT action, actor_id FROM audit_log WHERE guild_id = ?", (G1,))
            assert [dict(r) for r in logs] == [
                {"action": "directus.setup", "actor_id": "admin1"}]

            text = _text(embed)
            assert "発行しました" in text
            assert client.base_url in text  # ログイン先を案内する
        finally:
            await db.close()
    run(_main())


def test_issue_access_rejects_invalid_email():
    async def _main():
        db = await _make_db()
        try:
            client = FakeDirectusClient()
            cog = _make_cog(db, client)
            embed = await cog.issue_access(G1, "not-an-email", actor_id="admin1")
            assert "形式" in _text(embed)
            assert client.invited == []  # 外部 API は呼ばれない
            assert await DirectusAccessRepository(db).get(G1) is None
        finally:
            await db.close()
    run(_main())


def test_issue_access_email_in_use_leaves_db_unchanged():
    async def _main():
        db = await _make_db()
        try:
            cog = _make_cog(db, FakeDirectusClient(raises=DirectusEmailInUse()))
            embed = await cog.issue_access(G1, EMAIL1, actor_id="admin1")
            assert "既に" in _text(embed)
            assert await DirectusAccessRepository(db).get(G1) is None
            assert await db.fetchall("SELECT * FROM audit_log") == []
        finally:
            await db.close()
    run(_main())


def test_issue_access_api_failure_leaves_db_unchanged():
    async def _main():
        db = await _make_db()
        try:
            cog = _make_cog(db, FakeDirectusClient(raises=DirectusError("HTTP 500")))
            embed = await cog.issue_access(G1, EMAIL1, actor_id="admin1")
            assert "DIRECTUS_API_FAILED" in _text(embed)
            assert await DirectusAccessRepository(db).get(G1) is None
        finally:
            await db.close()
    run(_main())


# ------------------------------------------------------------------
# 状態表示・失効
# ------------------------------------------------------------------

def test_status_embed_shows_record():
    async def _main():
        db = await _make_db()
        try:
            cog = _make_cog(db, FakeDirectusClient())
            await cog.issue_access(G1, EMAIL1, actor_id="admin1")
            embed = await cog.build_status_embed(G1)
            text = _text(embed)
            assert EMAIL1 in text
            assert "招待済み" in text
        finally:
            await db.close()
    run(_main())


def test_revoke_access_suspends_and_updates_status():
    async def _main():
        db = await _make_db()
        try:
            client = FakeDirectusClient()
            cog = _make_cog(db, client)
            await cog.issue_access(G1, EMAIL1, actor_id="admin1")

            embed = await cog.revoke_access(G1, actor_id="admin1",
                                            actor_name="管理者A")
            assert "失効" in _text(embed)
            assert client.revoked == ["directus-user-1"]

            record = await DirectusAccessRepository(db).get(G1)
            assert record["status"] == STATUS_REVOKED
            actions = [r["action"] for r in await db.fetchall(
                "SELECT action FROM audit_log WHERE guild_id = ? ORDER BY audit_id",
                (G1,))]
            assert actions == ["directus.setup", "directus.revoke"]
        finally:
            await db.close()
    run(_main())


def test_revoke_api_failure_keeps_status():
    async def _main():
        db = await _make_db()
        try:
            ok_client = FakeDirectusClient()
            cog = _make_cog(db, ok_client)
            await cog.issue_access(G1, EMAIL1, actor_id="admin1")

            # 失効時だけ API が失敗する状態に差し替える
            cog._client_factory = lambda: FakeDirectusClient(
                raises=DirectusError("HTTP 503"))
            embed = await cog.revoke_access(G1, actor_id="admin1")
            assert "DIRECTUS_API_FAILED" in _text(embed)
            record = await DirectusAccessRepository(db).get(G1)
            assert record["status"] == STATUS_INVITED  # 失効扱いにしない
        finally:
            await db.close()
    run(_main())


# ------------------------------------------------------------------
# ギルド分離
# ------------------------------------------------------------------

def test_guild_isolation():
    async def _main():
        db = await _make_db()
        try:
            client = FakeDirectusClient()
            cog = _make_cog(db, client)
            await cog.issue_access(G1, EMAIL1, actor_id="admin1")
            await cog.issue_access(G2, EMAIL2, actor_id="admin2")

            repo = DirectusAccessRepository(db)
            assert (await repo.get(G1))["email"] == EMAIL1
            assert (await repo.get(G2))["email"] == EMAIL2

            # G2 を失効しても G1 は影響を受けない
            await cog.revoke_access(G2, actor_id="admin2")
            assert (await repo.get(G1))["status"] == STATUS_INVITED
            assert (await repo.get(G2))["status"] == STATUS_REVOKED

            # 状態表示も混ざらない
            assert EMAIL2 not in _text(await cog.build_status_embed(G1))

            # 削除もギルド単位
            assert await repo.delete(G2) is True
            assert await repo.get(G2) is None
            assert await repo.get(G1) is not None
            assert await repo.delete(G2) is False
        finally:
            await db.close()
    run(_main())


def test_reissue_overwrites_same_guild_row():
    async def _main():
        db = await _make_db()
        try:
            cog = _make_cog(db, FakeDirectusClient())
            await cog.issue_access(G1, EMAIL1, actor_id="admin1")
            await cog.issue_access(G1, EMAIL2, actor_id="admin1")

            rows = await db.fetchall("SELECT * FROM guild_directus_access")
            assert len(rows) == 1  # 1ギルド1件
            assert rows[0]["email"] == EMAIL2
            assert rows[0]["directus_user_id"] == "directus-user-2"
        finally:
            await db.close()
    run(_main())


# ------------------------------------------------------------------
# DirectusClient（HTTP を発生させない範囲）
# ------------------------------------------------------------------

def test_client_requires_all_settings():
    for args in (("", ADMIN_TOKEN, "role"),
                 ("http://localhost:8055", "", "role"),
                 ("http://localhost:8055", ADMIN_TOKEN, "")):
        try:
            DirectusClient(*args)
            raise AssertionError(f"DirectusUnavailable が送出されるべき: {args}")
        except DirectusUnavailable:
            pass


def test_client_normalizes_base_url_and_hides_token():
    client = DirectusClient("http://localhost:8055/", ADMIN_TOKEN, "role-uuid")
    assert client.base_url == "http://localhost:8055"  # 末尾スラッシュを除去
    # 管理トークンは属性として露出しない（ヘッダー生成時のみ使う）
    assert ADMIN_TOKEN not in repr(client.__dict__.get("base_url", ""))
    assert ADMIN_TOKEN not in str(client.role_id)


def test_error_code_extraction():
    from services.directus_service import _error_code
    body = {"errors": [{"message": "x",
                        "extensions": {"code": "RECORD_NOT_UNIQUE"}}]}
    assert _error_code(body) == "RECORD_NOT_UNIQUE"
    assert _error_code({"errors": []}) is None
    assert _error_code({}) is None
    assert _error_code(None) is None
    assert _error_code({"errors": [{"message": "x"}]}) is None
