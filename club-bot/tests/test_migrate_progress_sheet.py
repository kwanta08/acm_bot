"""進捗シート → DB 移行スクリプトのユニットテスト。

Google Sheets へは接続せず、グリッド（2次元配列）を直接渡して検証する。

- dry-run が既定で DB を変更しないこと
- --apply でノード・紐付け・既定通知チャンネルが取り込まれること
- 再実行しても行が重複しないこと（冪等）
- 他サーバーが登録した Todoist 対応表の行を取り込まないこと
- 取り込み先が guild_id スコープであること
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import migrate_progress_sheet_to_db as mig

from repositories.progress_repository import ProgressRepository
from repositories.settings_repository import SettingsRepository
from services import progress_sheet_service as pss
from services import progress_sync_service
from utils.db import Database

G1 = 111
G2 = 222
NOW = "2026-08-11 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _db() -> tuple[Database, ProgressRepository]:
    db = Database(_tmp_db_path())
    await db.connect()
    return db, ProgressRepository(db)


def _progress_grid() -> list[list]:
    """進捗管理シート相当のグリッド（ヘッダー + 3行）。"""
    return [
        pss.PROGRESS_HEADER,
        # ID, 親ID, 表示順, 深さ, 名前, 担当, 状態, 手入力, 集計, バー,
        # ソース, TodoistID, 更新日時
        ["m1", "", "1", "0", "本機", "", "", "", "0.5", "", "manual", "", ""],
        ["wing", "m1", "1", "1", "主翼", "山田", "製作中", "50%", "0.5", "", "manual", "", ""],
        ["td_9", "wing", "2", "2", "リブ切り出し", "", "", "1", "1", "", "todoist", "9", ""],
    ]


# ---------------------------------------------------------------------
# 純粋関数
# ---------------------------------------------------------------------
def test_node_to_upsert_kwargs_drops_computed_columns():
    node = pss.grid_to_nodes(_progress_grid())[1]  # wing
    kwargs = mig.node_to_upsert_kwargs(node)
    assert kwargs["node_id"] == "wing"
    assert kwargs["parent_id"] == "m1"
    assert kwargs["name"] == "主翼"
    assert kwargs["assignee"] == "山田"
    assert kwargs["status"] == "製作中"
    assert kwargs["manual_progress"] == 0.5  # "50%" を正規化
    # 集計進捗率・深さはシート側の計算結果なので取り込まない
    assert "aggregated" not in kwargs
    assert "depth" not in kwargs


def test_spar_links_from_sheets_requires_target_layers():
    mappings = [
        {"spar_key": "主桁1", "node_id": "spar1"},
        {"spar_key": "主桁2", "node_id": "spar2"},
    ]
    rows, warnings = mig.spar_links_from_sheets(mappings, {"主桁1": 12})
    assert rows == [{"keta_name": "主桁1", "node_id": "spar1", "target_layers": 12}]
    assert any("主桁2" in w for w in warnings)


# ---------------------------------------------------------------------
# 取り込み
# ---------------------------------------------------------------------
def test_dry_run_does_not_write():
    async def _main():
        db, repo = await _db()
        try:
            stats = await mig.import_nodes(repo, G1, _progress_grid(), NOW, apply=False)
            assert stats.input_rows == 3
            assert stats.imported == 3
            assert await repo.count_nodes(G1) == 0  # DB は変わらない
        finally:
            await db.close()

    run(_main())


def test_apply_imports_nodes():
    async def _main():
        db, repo = await _db()
        try:
            await mig.import_nodes(repo, G1, _progress_grid(), NOW, apply=True)
            assert await repo.count_nodes(G1) == 3
            wing = await repo.get_node(G1, "wing")
            assert wing["parent_id"] == "m1"
            assert wing["manual_progress"] == 0.5
            assert wing["assignee"] == "山田"
            td = await repo.get_node(G1, "td_9")
            assert td["source"] == "todoist"
            assert td["todoist_task_id"] == "9"
            # 取り込み先は指定ギルドのみ
            assert await repo.count_nodes(G2) == 0
        finally:
            await db.close()

    run(_main())


def test_import_is_idempotent():
    async def _main():
        db, repo = await _db()
        try:
            await mig.import_nodes(repo, G1, _progress_grid(), NOW, apply=True)
            await mig.import_nodes(repo, G1, _progress_grid(), NOW, apply=True)
            assert await repo.count_nodes(G1) == 3
        finally:
            await db.close()

    run(_main())


def test_import_reports_broken_tree():
    """親 ID の書き間違い（孤児）は警告として報告される。"""

    async def _main():
        db, repo = await _db()
        try:
            grid = [
                pss.PROGRESS_HEADER,
                ["a", "missing", "1", "", "孤児", "", "", "", "", "", "manual", "", ""],
            ]
            stats = await mig.import_nodes(repo, G1, grid, NOW, apply=False)
            assert any("missing" in w for w in stats.warnings)
        finally:
            await db.close()

    run(_main())


def test_todoist_links_skip_other_guild_rows():
    """旧シートを複数サーバーで共有していた場合、他サーバーの行は取り込まない。"""

    async def _main():
        db, repo = await _db()
        try:
            grid = [
                pss.MAPPING_HEADER,
                ["主翼班", "wing", "100", str(G1)],
                ["尾翼班", "tail", "200", str(G2)],  # 別サーバーの登録
                ["電装班", "elec", "", ""],
            ]  # 登録元不明は取り込む
            stats = await mig.import_todoist_links(repo, G1, grid, NOW, apply=True)
            assert stats.imported == 2
            assert stats.skipped == 1
            names = {link["project_name"] for link in await repo.list_todoist_links(G1)}
            assert names == {"主翼班", "電装班"}
        finally:
            await db.close()

    run(_main())


def test_spar_links_imported():
    async def _main():
        db, repo = await _db()
        try:
            rows = [{"keta_name": "主桁1", "node_id": "spar1", "target_layers": 12}]
            await mig.import_spar_links(repo, G1, rows, NOW, apply=True)
            links = await repo.list_spar_links(G1)
            assert links[0]["keta_name"] == "主桁1"
            assert links[0]["target_layers"] == 12
            assert await repo.list_spar_links(G2) == []
        finally:
            await db.close()

    run(_main())


def test_default_channel_moved_to_settings():
    async def _main():
        db, _repo = await _db()
        try:
            sheet_settings = {pss.SHEET_KEY_DEFAULT_CHANNEL: "12345"}
            value = await mig.import_default_channel(db, G1, sheet_settings, apply=True)
            assert value == "12345"
            stored = await SettingsRepository(db).get(
                G1, progress_sync_service.SETTINGS_DEFAULT_CHANNEL_KEY
            )
            assert stored == "12345"
            # 別ギルドには入らない
            assert (
                await SettingsRepository(db).get(
                    G2, progress_sync_service.SETTINGS_DEFAULT_CHANNEL_KEY
                )
                is None
            )
        finally:
            await db.close()

    run(_main())


def test_default_channel_ignores_non_numeric():
    async def _main():
        db, _repo = await _db()
        try:
            assert (
                await mig.import_default_channel(
                    db, G1, {pss.SHEET_KEY_DEFAULT_CHANNEL: "（未設定）"}, apply=True
                )
                is None
            )
        finally:
            await db.close()

    run(_main())
