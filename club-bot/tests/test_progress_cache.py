"""進捗ツリーのメモリキャッシュ（Progress.load_tree）のユニットテスト。

/progress の表示が定期同期の構築したキャッシュを参照し、
クリック・コマンドの都度シートを読みに行かないことを検証する。
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs import progress as progress_cog
from cogs.progress import Progress
from services import progress_sheet_service as pss

G1 = 111
HEADER = pss.PROGRESS_HEADER


def run(coro):
    return asyncio.run(coro)


class CountingSheetClient:
    """read_progress_grid の呼び出し回数を数えるフェイク。"""

    def __init__(self, progress_grid):
        self.progress_grid = progress_grid
        self.progress_reads = 0

    def read_progress_grid(self, spreadsheet_id):
        self.progress_reads += 1
        return [list(r) for r in self.progress_grid]

    def read_mapping_grid(self, spreadsheet_id):
        return [pss.MAPPING_HEADER]

    def apply_value_ranges(self, spreadsheet_id, ranges):
        pass

    def append_progress_rows(self, spreadsheet_id, rows):
        pass


class DisabledTodoistManager:
    async def for_guild(self, guild_id):
        return None


def _make_cog(monkeypatch, client):
    bot = SimpleNamespace(db=None, guilds=[],
                          todoist_manager=DisabledTodoistManager())
    cog = Progress(bot, client_factory=lambda: client)

    async def _sid(db, guild_id):
        return "SID"

    monkeypatch.setattr(progress_cog.progress_sync_service,
                        "get_spreadsheet_id", _sid)
    return cog


def _grid():
    return [HEADER,
            ["m1", "", "1", "", "本機"] + [""] * 8,
            ["p1", "m1", "1", "", "主翼", "", "", "0.5"] + [""] * 5]


def test_load_tree_uses_cache_after_first_read(monkeypatch):
    client = CountingSheetClient(_grid())
    cog = _make_cog(monkeypatch, client)

    async def _main():
        tree1 = await cog.load_tree(G1)
        tree2 = await cog.load_tree(G1)   # 2回目はシートを読まない
        assert tree1 is tree2
        assert [r.node_id for r in tree1.roots] == ["m1"]

    run(_main())
    assert client.progress_reads == 1


def test_force_refresh_rereads_sheet(monkeypatch):
    """🔄 再読込（force_refresh）は明示的にシートを読み直す。"""
    client = CountingSheetClient(_grid())
    cog = _make_cog(monkeypatch, client)

    async def _main():
        await cog.load_tree(G1)
        client.progress_grid.append(
            ["p2", "m1", "2", "", "尾翼"] + [""] * 8)
        tree = await cog.load_tree(G1, force_refresh=True)
        assert "p2" in tree.by_id
        # 再読込後はキャッシュも更新されている
        tree2 = await cog.load_tree(G1)
        assert tree2 is tree

    run(_main())
    assert client.progress_reads == 2


def test_run_sync_populates_cache(monkeypatch):
    """手動同期（/progress sync）後の表示はキャッシュを参照する。"""
    client = CountingSheetClient(_grid())
    cog = _make_cog(monkeypatch, client)

    async def _main():
        result = await cog._run_sync(G1)
        assert result is not None and result.tree is not None
        reads_after_sync = client.progress_reads
        tree = await cog.load_tree(G1)      # 追加の読み込みなし
        assert tree is result.tree
        assert client.progress_reads == reads_after_sync

    run(_main())
