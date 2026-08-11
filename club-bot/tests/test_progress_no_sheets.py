"""GOOGLE_CREDENTIALS_PATH なしで /progress が完全動作することの回帰テスト（P1-6）。

公開配布の要件「導入サークルが .env を編集しなくてよい」を崩していた唯一の
箇所が /progress の Google Sheets 依存だった。DB 移行後は次を保証する。

1. /progress の実行経路が services/progress_sheet_service（gspread 依存）を
   import していないこと — 再混入するとここで落ちる
2. GOOGLE_CREDENTIALS_PATH が未設定でも、機体の追加 → 表示 → Todoist 同期 →
   桁巻き反映 → 集計まで一通り動くこと
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.progress import Progress, build_level_embed, chart_items
from repositories.progress_repository import ProgressRepository
from services import progress_sync_service, spar_winding_service
from utils.db import Database

BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# /progress の実行に関わるモジュール（ここから Sheets へ触れてはいけない）
RUNTIME_MODULES = [
    "cogs/progress.py",
    "services/progress_sync_service.py",
    "services/spar_winding_service.py",
    "services/progress_tree.py",
    "repositories/progress_repository.py",
]

FORBIDDEN_IMPORTS = ("gspread", "google", "google.oauth2",
                     "services.progress_sheet_service")

G1 = 111
NOW = "2026-08-11 10:00"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _imported_modules(rel_path: str) -> set[str]:
    """モジュールが import している名前を AST から集める（関数内 import も含む）。"""
    with open(os.path.join(BOT_ROOT, rel_path), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


# ---------------------------------------------------------------------
# 1. Sheets 依存が実行経路に無いこと
# ---------------------------------------------------------------------
def test_runtime_modules_do_not_import_sheets():
    violations: list[str] = []
    for rel_path in RUNTIME_MODULES:
        for name in _imported_modules(rel_path):
            if any(name == f or name.startswith(f + ".")
                   for f in FORBIDDEN_IMPORTS):
                violations.append(f"{rel_path}: {name}")
    assert not violations, (
        "/progress の実行経路に Google Sheets 依存が混入しています:\n"
        + "\n".join(violations))


def test_progress_sheet_service_is_import_only_for_migration():
    """シートアダプタは移行スクリプトからのみ参照される（書き込み機能を持たない）。"""
    from services import progress_sheet_service as pss

    # 読み取り専用: 書き戻し・シート初期化の API を持たない
    for removed in ("build_writeback_ranges", "sparkline_formula",
                    "dashboard_cells", "conditional_format_request"):
        assert not hasattr(pss, removed), f"{removed} が残っている"
    client = pss.ProgressSheetClient(client=object())
    for removed in ("apply_value_ranges", "append_progress_rows",
                    "append_mapping_row", "setup_book"):
        assert not hasattr(client, removed), f"client.{removed} が残っている"


# ---------------------------------------------------------------------
# 2. 認証情報なしで一通り動くこと
# ---------------------------------------------------------------------
@dataclass
class FakeTask:
    id: str
    content: str
    parent_id: str | None = None


@dataclass
class FakeProject:
    id: str
    name: str


class FakeTodoist:
    enabled = True

    def __init__(self, projects, tasks_by_project):
        self._projects = projects
        self._tasks = tasks_by_project

    async def get_projects(self):
        return self._projects

    async def get_tasks(self, project_id=None):
        return self._tasks.get(project_id, [])


def test_full_progress_flow_without_google_credentials(monkeypatch):
    """機体追加 → 表示 → Todoist 同期 → 桁巻き反映 → 集計。"""
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        svc = FakeTodoist([FakeProject("P1", "主翼班")],
                          {"P1": [FakeTask("1", "リブ切り出し")]})

        class _Manager:
            async def for_guild(self, guild_id):
                return svc

        bot = SimpleNamespace(db=db, guilds=[], todoist_manager=_Manager())
        cog = Progress(bot)
        repo = ProgressRepository(db)
        try:
            # 機体・パーツを登録（/progress add 相当）
            await repo.upsert_node(G1, "m1", name="1号機", now_text=NOW)
            await repo.upsert_node(G1, "wing", parent_id="m1", name="主翼",
                                   now_text=NOW)
            await repo.upsert_node(G1, "spar", parent_id="m1", name="主桁",
                                   now_text=NOW)

            # 桁巻きの紐付けと積層記録（/progress spar-link + /layer end 相当）
            await repo.upsert_spar_link(G1, "主桁1", "spar", 2, NOW)
            for layer in ("1",):
                await db.execute(
                    "INSERT INTO layer_records (guild_id, user_id, keta,"
                    " layer_num, started_at, ended_at, minutes)"
                    " VALUES (?, '1', '主桁1', ?, '2026-08-01 10:00',"
                    " '2026-08-01 11:00', 60)",
                    (G1, layer))

            # Todoist の紐付け（/progress setup 相当）
            await repo.upsert_todoist_link(G1, "主翼班", "wing", NOW)

            # 同期（/progress sync 相当）
            result = await progress_sync_service.sync_guild_db(db, G1, svc)
            assert result.added == 1          # td_1 を取り込む
            assert result.spar_updated == 1   # 主桁 = 1/2

            # 表示（/progress view 相当）
            tree = await cog.load_tree(G1)
            assert [n.node_id for n in tree.roots] == ["m1"]
            assert tree.by_id["spar"].aggregated == 0.5
            assert tree.by_id["wing"].aggregated == 0.0   # td_1 は未完了
            assert tree.by_id["m1"].aggregated == 0.25    # (0.0 + 0.5) / 2

            embed = build_level_embed(tree, "m1")
            assert "1号機" in embed.title
            assert chart_items(tree, "m1")
        finally:
            await db.close()

    run(_main())


def test_spar_sync_needs_no_external_book(monkeypatch):
    """桁巻きの進捗が layer_records だけで計算できる（別ブック不要）。"""
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)

    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        repo = ProgressRepository(db)
        try:
            await repo.upsert_node(G1, "spar", name="主桁", now_text=NOW)
            await repo.upsert_spar_link(G1, "主桁1", "spar", 4, NOW)
            for layer in ("1", "2", "3"):
                await db.execute(
                    "INSERT INTO layer_records (guild_id, user_id, keta,"
                    " layer_num, started_at, ended_at, minutes)"
                    " VALUES (?, '1', '主桁1', ?, '2026-08-01 10:00',"
                    " '2026-08-01 11:00', 60)",
                    (G1, layer))

            plan = await spar_winding_service.sync_spar_winding_db(
                repo, G1, NOW)
            assert plan.updated == 1
            assert (await repo.get_node(G1, "spar"))["manual_progress"] == 0.75
        finally:
            await db.close()

    run(_main())
