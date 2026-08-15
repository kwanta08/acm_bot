"""/data export（F2-2）のテスト。

公開配布する Bot として、書き出しが他サーバーのデータを巻き込まないことと、
サーバー ID・認証情報を含まないことが要件。

- B 大学のデータを件数を変えて置き、A 大学の ZIP に1件も混ざらないこと
- guild_id 列と Todoist トークンが ZIP 内のどこにも現れないこと
- 権限不足（L1〜L3・Manage Server なし）では実行できないこと
- CSV が BOM 付き UTF-8 で、数式インジェクションが無害化されること
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord.ext import commands

from cogs.data import MAX_ATTACHMENT_BYTES, Data, build_export_zip, export_filename
from config import GuildConfig
from repositories.table_repository import (
    CSV_BOM,
    TABLES,
    TableRepository,
    csv_safe,
    rows_to_csv,
)
from utils.db import Database
from utils.permissions import (
    Level,
    command_required_level,
    has_manage_guild_or_level,
)

GA = 100000000000000001  # A 大学
GB = 200000000000000002  # B 大学
SECRET = "tok_SHOULD_NEVER_APPEAR"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


async def _seed(
    db: Database, guild_id: int, *, tasks: int, members: int, title_prefix: str
) -> None:
    for i in range(tasks):
        await db.execute(
            "INSERT INTO tasks (guild_id, title, status, created_by, created_at)"
            " VALUES (?, ?, 'open', 'tester', '2026-01-01')",
            (guild_id, f"{title_prefix}タスク{i}"),
        )
    for i in range(members):
        # user_id は Discord のユーザー ID。guild_id とは無関係の値にする
        # （guild_id を含めると「サーバー ID が出ていない」検査が偽陽性になる）
        await db.execute(
            "INSERT INTO members (guild_id, user_id, display_name, joined_at,"
            " active_flag) VALUES (?, ?, ?, '2026-01-01', 1)",
            (guild_id, f"9{i:018d}", f"{title_prefix}メンバー{i}"),
        )


def _zip_texts(payload: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


# ---------------------------------------------------------------------
# ギルド分離
# ---------------------------------------------------------------------
def test_export_contains_only_own_guild_rows():
    async def _main():
        db = await _connected_db()
        try:
            await _seed(db, GA, tasks=3, members=2, title_prefix="A")
            await _seed(db, GB, tasks=7, members=5, title_prefix="B")

            payload, counts = await build_export_zip(db, GA)
            assert counts["tasks"] == 3
            assert counts["members"] == 2

            texts = _zip_texts(payload)
            joined = "\n".join(texts.values())
            assert "Aタスク0" in joined
            assert "Bタスク" not in joined, "他サーバーの行が混入している"
            assert "Bメンバー" not in joined
            assert str(GB) not in joined
        finally:
            await db.close()

    run(_main())


def test_export_of_empty_guild_still_has_all_tables():
    async def _main():
        db = await _connected_db()
        try:
            await _seed(db, GB, tasks=4, members=1, title_prefix="B")
            payload, counts = await build_export_zip(db, GA)

            assert set(counts) == set(TABLES)
            assert all(n == 0 for n in counts.values())
            names = set(_zip_texts(payload))
            assert {f"{k}.csv" for k in TABLES} <= names
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 機密情報を出さない
# ---------------------------------------------------------------------
def test_export_omits_guild_id_and_secrets():
    async def _main():
        db = await _connected_db()
        try:
            await _seed(db, GA, tasks=2, members=1, title_prefix="A")
            # Todoist トークン（暗号化済み文字列）を同じ DB に置いておく
            await db.execute(
                "INSERT INTO todoist_configs (guild_id, api_token_encrypted,"
                " created_by, created_at, updated_at)"
                " VALUES (?, ?, 'tester', '2026-01-01', '2026-01-01')",
                (GA, SECRET),
            )

            texts = _zip_texts((await build_export_zip(db, GA))[0])
            joined = "\n".join(texts.values())

            assert SECRET not in joined, "認証情報が書き出されている"
            assert str(GA) not in joined, "サーバー ID が書き出されている"
            # 列見出しにも guild_id を出さない
            for name, body in texts.items():
                if name.endswith(".csv"):
                    assert "guild_id" not in body.splitlines()[0]
        finally:
            await db.close()

    run(_main())


def test_whitelist_has_no_guild_id_column():
    """ホワイトリスト自体に guild_id が無いこと（構造的な保証）。"""
    for spec in TABLES.values():
        assert "guild_id" not in spec.column_names


def test_whitelist_excludes_credential_tables():
    for spec in TABLES.values():
        assert spec.table != "todoist_configs"
        assert not any("token" in c.name.lower() for c in spec.columns)


# ---------------------------------------------------------------------
# CSV の体裁
# ---------------------------------------------------------------------
def test_csv_has_bom_and_labels():
    async def _main():
        db = await _connected_db()
        try:
            await _seed(db, GA, tasks=1, members=0, title_prefix="A")
            texts = _zip_texts((await build_export_zip(db, GA))[0])
            body = texts["tasks.csv"]
            assert body.startswith(CSV_BOM), "BOM が無いと Excel で文字化けする"
            header = body[len(CSV_BOM) :].splitlines()[0]
            assert "タイトル" in header
        finally:
            await db.close()

    run(_main())


def test_csv_neutralizes_formula_injection():
    """利用者が入力した =cmd... がそのまま数式にならないこと。"""
    assert csv_safe("=cmd|'/c calc'!A1").startswith("'=")
    assert csv_safe("+1+1").startswith("'+")
    assert csv_safe("-2").startswith("'-")
    assert csv_safe("@SUM(A1)").startswith("'@")
    assert csv_safe("ふつうの文字列") == "ふつうの文字列"
    assert csv_safe(None) == ""


def test_injected_task_title_is_escaped_in_export():
    async def _main():
        db = await _connected_db()
        try:
            await db.execute(
                "INSERT INTO tasks (guild_id, title, status, created_by,"
                " created_at) VALUES (?, ?, 'open', 'tester', '2026-01-01')",
                (GA, '=HYPERLINK("http://evil")'),
            )
            body = _zip_texts((await build_export_zip(db, GA))[0])["tasks.csv"]
            assert "'=HYPERLINK" in body
            assert "\n=HYPERLINK" not in body
        finally:
            await db.close()

    run(_main())


def test_rows_to_csv_uses_whitelisted_columns_only():
    spec = TABLES["members"]
    csv_text = rows_to_csv(spec, [{"display_name": "山田", "guild_id": GA, "secret": SECRET}])
    assert "山田" in csv_text
    assert str(GA) not in csv_text
    assert SECRET not in csv_text


# ---------------------------------------------------------------------
# 全件取得（表示用の上限に引きずられない）
# ---------------------------------------------------------------------
def test_list_all_rows_reads_beyond_display_limit():
    async def _main():
        db = await _connected_db()
        try:
            await _seed(db, GA, tasks=520, members=0, title_prefix="A")
            rows = await TableRepository(db).list_all_rows(GA, "tasks")
            assert len(rows) == 520, "MAX_LIMIT=500 で打ち切られている"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 権限
# ---------------------------------------------------------------------
def _member(
    *, role_ids=(), manage_guild: bool = False, administrator: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        guild=SimpleNamespace(id=GA, owner_id=42),
        roles=[SimpleNamespace(id=r) for r in role_ids],
        guild_permissions=SimpleNamespace(administrator=administrator, manage_guild=manage_guild),
    )


def _export_command():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    for cmd in Data(bot).walk_app_commands():
        if cmd.name == "export":
            return cmd
    raise AssertionError("/data export が見つからない")


def test_export_declares_admin_level():
    """コマンドが L4 を要求すると宣言していること（/help のバッジ根拠でもある）。"""
    assert command_required_level(_export_command()) == Level.L4


def test_plain_member_and_leaders_cannot_export():
    """一般メンバー・班長・幹部では書き出せない。"""
    gconf = GuildConfig(guild_id=GA, leader_role_ids=[500], exec_role_id=600)
    assert not has_manage_guild_or_level(_member(), gconf, Level.L4)
    assert not has_manage_guild_or_level(_member(role_ids=(500,)), gconf, Level.L4)  # 班長 L2
    assert not has_manage_guild_or_level(_member(role_ids=(600,)), gconf, Level.L4)  # 幹部 L3


def test_manage_guild_can_export_without_any_role_config():
    """ロール未設定の新規サーバーでも Manage Server 保持者は書き出せる。"""
    assert has_manage_guild_or_level(_member(manage_guild=True), GuildConfig(guild_id=GA), Level.L4)


def test_admin_role_can_export():
    gconf = GuildConfig(guild_id=GA, admin_role_id=700)
    assert has_manage_guild_or_level(_member(role_ids=(700,)), gconf, Level.L4)


# ---------------------------------------------------------------------
# 添付上限
# ---------------------------------------------------------------------
def test_attachment_limit_is_8mb():
    assert MAX_ATTACHMENT_BYTES == 8 * 1024 * 1024


def test_export_filename_has_no_guild_name():
    assert export_filename(GA) == f"club-bot-export-{GA}.zip"
