"""タスク通知の「親タスク」パンくず表示のテスト。

前半は services/parent_chain.py の解決ロジック（純粋関数・async）を、
後半は push_project_tasks への統合（通知の埋め込みに親タスク行が入ること・
解決失敗でも通知が送られること）を検証する。ツリーはテスト用フィクスチャで
構築し、Todoist API はモックする。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs import progress as progress_cog
from cogs.progress import Progress
from config import GuildConfig
from repositories.progress_repository import ProgressRepository
from services import parent_chain as pc
from services.parent_chain import (
    MAX_FIELD_VALUE_LEN,
    MAX_NODE_NAME_LEN,
    MAX_PARENT_DEPTH,
    ParentEntry,
    format_breadcrumb,
    parent_chain,
    resolve_parent_field,
)
from services.progress_tree import ProgressNode, ProgressTree, build_and_aggregate
from services.todoist_task_service import task_url
from utils.db import Database
from utils.parser import now

G1 = 111
NOW = "2026-08-11 10:00"
ANCHORS = {"wing"}


def run(coro):
    return asyncio.run(coro)


def _node(node_id, parent=None, *, name=None, source="manual", td_id=""):
    return ProgressNode(
        node_id=node_id,
        parent_id=parent,
        name=name or node_id,
        source=source,
        todoist_task_id=td_id,
    )


def _tree():
    """本機 > 主翼(対応表アンカー) > 第2リブ > 桁受け加工 > 接着 の縦一列。"""
    return build_and_aggregate(
        [
            _node("m1", name="本機"),
            _node("wing", "m1", name="主翼"),
            _node("td_100", "wing", name="第2リブ", source="todoist", td_id="100"),
            _node("td_200", "td_100", name="桁受け加工", source="todoist", td_id="200"),
            _node("td_300", "td_200", name="接着", source="todoist", td_id="300"),
        ]
    )


@dataclass
class FakeRawTask:
    """Todoist SDK の Task 互換（id / parent_id だけ使う）。"""

    id: str
    parent_id: str | None = None
    content: str = ""


class FakeGetTask:
    """get_task のモック。呼び出し回数を数え、挙動を差し替えられる。"""

    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, task_id: str):
        self.calls.append(task_id)
        if self.error is not None:
            raise self.error
        return self.result


# ---------------------------------------------------------------------
# 解決ロジック（resolve_parent_field / parent_chain）
# ---------------------------------------------------------------------
def test_top_level_task_has_no_parent_field():
    """直近の親が対応表ノード自身（= トップレベルタスク）ならフィールドを出さない。"""
    get_task = FakeGetTask()
    value = run(resolve_parent_field(_tree(), FakeRawTask("100"), ANCHORS, get_task))
    assert value is None
    assert get_task.calls == []


def test_single_parent_renders_root_and_parent_with_link():
    """親1段 → `<ルート> > <親>`。直近の親（td_）には Todoist リンクが付く。"""
    value = run(resolve_parent_field(_tree(), FakeRawTask("200"), ANCHORS, FakeGetTask()))
    assert value == f"主翼 > [第2リブ]({task_url('100')})"


def test_multi_level_chain_is_joined_from_root():
    """親多段 → 全階層がルート（アンカー）から順に連結される。"""
    value = run(resolve_parent_field(_tree(), FakeRawTask("300"), ANCHORS, FakeGetTask()))
    assert value == f"主翼 > 第2リブ > [桁受け加工]({task_url('200')})"


def test_chain_stops_at_linked_node():
    """対応表で紐付けたノード（主翼）で停止し、それより上（本機）は辿らない。"""
    value = run(resolve_parent_field(_tree(), FakeRawTask("300"), ANCHORS, FakeGetTask()))
    assert value.startswith("主翼")
    assert "本機" not in value


def test_manual_parent_gets_no_link():
    """手入力ノード（ソース=manual）にはリンクを張らない。"""
    tree = build_and_aggregate(
        [
            _node("wing", name="主翼"),
            _node("n_sub", "wing", name="治具まわり"),
            _node("td_500", "n_sub", name="組み立て", source="todoist", td_id="500"),
        ]
    )
    # td_500 の親は手入力ノード（直近の親にリンクを張らない）
    value = run(resolve_parent_field(tree, FakeRawTask("500"), ANCHORS, FakeGetTask()))
    assert value == "主翼 > 治具まわり"
    assert "](" not in value


def test_cycle_is_cut_with_warning_and_no_exception(caplog):
    """循環参照 → 例外を投げずに打ち切り、ログに警告が出る。"""
    tree = ProgressTree()
    for node in (
        _node("td_a", "td_b", name="桁A", source="todoist", td_id="a"),
        _node("td_b", "td_a", name="桁B", source="todoist", td_id="b"),
        _node("td_t", "td_a", name="接着", source="todoist", td_id="t"),
    ):
        tree.by_id[node.node_id] = node
    with caplog.at_level(logging.WARNING, logger="parent_chain"):
        value = run(resolve_parent_field(tree, FakeRawTask("t"), ANCHORS, FakeGetTask()))
    assert value is not None  # 打ち切りまでに辿れた分は表示する
    assert any("循環" in r.message for r in caplog.records)


def test_depth_limit_cuts_the_chain(caplog):
    """深さ上限（MAX_PARENT_DEPTH=10）を超えたら打ち切り、警告を出す。"""
    nodes = [_node("root", name="機体ルート")]
    parent = "root"
    for i in range(1, 15):
        nodes.append(
            _node(f"td_c{i}", parent, name=f"部品{i}", source="todoist", td_id=f"c{i}")
        )
        parent = f"td_c{i}"
    tree = build_and_aggregate(nodes)
    with caplog.at_level(logging.WARNING, logger="parent_chain"):
        chain = parent_chain(tree, "td_c14", set())
    assert len(chain) == MAX_PARENT_DEPTH
    assert any("深さ上限" in r.message for r in caplog.records)
    # 直近の親側（td_c14）が残り、遠い側が打ち切られる
    assert chain[-1].node_id == "td_c14"


# ---------------------------------------------------------------------
# パンくずの整形（format_breadcrumb）
# ---------------------------------------------------------------------
def test_long_breadcrumb_elides_middle_and_keeps_root_and_parent():
    """1024 文字を超えるパンくずは中間が … に省略され、上限内に収まる。"""
    root_name = "R" * MAX_NODE_NAME_LEN
    parent_name = "P" * MAX_NODE_NAME_LEN
    entries = (
        [ParentEntry(root_name)]
        + [ParentEntry(f"{i:02d}" + "中" * (MAX_NODE_NAME_LEN - 2)) for i in range(8)]
        + [ParentEntry(parent_name, url=task_url("1"))]
    )
    raw = " > ".join(e.name for e in entries)
    assert len(raw) > MAX_FIELD_VALUE_LEN  # 前提: 素の連結では上限超え

    value = format_breadcrumb(entries)
    assert len(value) <= MAX_FIELD_VALUE_LEN
    assert "…" in value
    assert value.startswith(root_name)  # ルートは必ず残る
    assert parent_name in value  # 直近の親は必ず残る


def test_long_node_name_is_clipped():
    """個々のノード名が長い場合は末尾を省略する。"""
    value = format_breadcrumb([ParentEntry("あ" * 300), ParentEntry("親")])
    first = value.split(" > ")[0]
    assert len(first) == MAX_NODE_NAME_LEN
    assert first.endswith("…")


# ---------------------------------------------------------------------
# キャッシュミス時のフォールバック（get_task）
# ---------------------------------------------------------------------
def test_cache_miss_calls_get_task_once_and_shows_the_name():
    """親がツリーに無い → get_task を1回だけ呼び、取れた名前を直近の親にする。"""
    get_task = FakeGetTask(result=SimpleNamespace(content="桁受け加工"))
    value = run(
        resolve_parent_field(_tree(), FakeRawTask("999", parent_id="888"), ANCHORS, get_task)
    )
    assert value == f"[桁受け加工]({task_url('888')})"
    assert get_task.calls == ["888"]


def test_cache_miss_with_parent_in_tree_walks_the_tree():
    """タスク自身が未同期でも、親がツリーに在ればそこから遡る（API は呼ばない）。"""
    get_task = FakeGetTask()
    value = run(
        resolve_parent_field(_tree(), FakeRawTask("999", parent_id="200"), ANCHORS, get_task)
    )
    assert value == f"主翼 > 第2リブ > [桁受け加工]({task_url('200')})"
    assert get_task.calls == []


def test_cache_miss_fetch_failure_omits_field(caplog):
    """get_task 失敗 → フィールド省略・ログ記録。例外は外へ漏らさない。"""
    get_task = FakeGetTask(error=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING, logger="parent_chain"):
        value = run(
            resolve_parent_field(
                _tree(), FakeRawTask("999", parent_id="888"), ANCHORS, get_task
            )
        )
    assert value is None
    assert get_task.calls == ["888"]
    assert any("親タスク" in r.message for r in caplog.records)


def test_cache_miss_without_parent_is_top_level():
    """未同期かつ Todoist 上も親なし → トップレベル扱いでフィールドなし。"""
    get_task = FakeGetTask()
    value = run(resolve_parent_field(_tree(), FakeRawTask("999"), ANCHORS, get_task))
    assert value is None
    assert get_task.calls == []


# ---------------------------------------------------------------------
# push_project_tasks への統合（通知の埋め込み）
# ---------------------------------------------------------------------
def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


@dataclass
class FakeProject:
    id: str
    name: str


class FakeTodoist:
    enabled = True

    def __init__(self, projects, tasks_by_project, get_task: FakeGetTask | None = None):
        self._projects = projects
        self._tasks = tasks_by_project
        self.get_task = get_task or FakeGetTask()

    async def get_projects(self):
        return self._projects

    async def get_tasks(self, project_id=None):
        return self._tasks.get(project_id, [])


class FakeChannel:
    def __init__(self):
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs.get("embed") or (args[0] if args else None))


@dataclass
class FakeBot:
    channels: dict = field(default_factory=dict)
    logged: list = field(default_factory=list)
    guilds: list = field(default_factory=list)
    db: object = None

    def __post_init__(self):
        bot = self

        class _Manager:
            async def for_guild(self, guild_id):
                return bot.todoist

        self.todoist_manager = _Manager()
        self.todoist = None

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append(message)


def _due_task(task_id: str, content: str, parent_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        content=content,
        parent_id=parent_id,
        priority=1,
        due=SimpleNamespace(date=now().date() + timedelta(days=1)),
    )


async def _make_cog(monkeypatch, bot):
    """主翼アンカー配下に td_100 > td_200 を持つ DB とコグを用意する。"""
    db = Database(_tmp_db_path())
    await db.connect()
    bot.db = db
    repo = ProgressRepository(db)
    await repo.upsert_node(G1, "wing", name="主翼", now_text=NOW)
    await repo.upsert_node(
        G1,
        "td_100",
        parent_id="wing",
        name="第2リブ",
        source="todoist",
        todoist_task_id="100",
        now_text=NOW,
    )
    await repo.upsert_node(
        G1,
        "td_200",
        parent_id="td_100",
        name="桁受け加工",
        source="todoist",
        todoist_task_id="200",
        now_text=NOW,
    )
    await repo.upsert_todoist_link(G1, "A班", "wing", NOW, notify_channel_id="100")

    async def _gconf(guild_id):
        return GuildConfig(guild_id=guild_id)

    monkeypatch.setattr(progress_cog.config, "for_guild", _gconf)
    return Progress(bot), db


def test_notification_embeds_parent_breadcrumb(monkeypatch):
    """通知の埋め込みに親タスクのパンくずが入り、トップレベルには付かない。"""

    async def _main():
        bot = FakeBot(channels={100: FakeChannel()})
        bot.todoist = FakeTodoist(
            [FakeProject("1", "A班")],
            {
                "1": [
                    _due_task("100", "第2リブ"),  # トップレベル（親はアンカー）
                    _due_task("200", "桁受け加工", parent_id="100"),  # 親1段
                    _due_task("300", "接着", parent_id="200"),  # 未同期の子（親多段）
                ]
            },
        )
        cog, db = await _make_cog(monkeypatch, bot)
        try:
            sent = await cog.push_project_tasks(G1)
            assert sent == 1
            embed = bot.channels[100].sent[0]
            desc = embed.description
            assert f"親タスク: 主翼 > [第2リブ]({task_url('100')})" in desc
            assert f"親タスク: 主翼 > 第2リブ > [桁受け加工]({task_url('200')})" in desc
            # トップレベルタスク（第2リブ）には親タスク行が付かない
            assert desc.count("親タスク:") == 2
            # プロジェクト名の表示は現行のまま
            assert "📂 A班" in desc
            # ツリーで解決できたので API フォールバックは呼ばれない
            assert bot.todoist.get_task.calls == []
        finally:
            await db.close()

    run(_main())


def test_notification_survives_parent_resolution_failure(monkeypatch):
    """親の解決（get_task）に失敗しても通知は送信され、フィールドは省略される。"""

    async def _main():
        bot = FakeBot(channels={100: FakeChannel()})
        bot.todoist = FakeTodoist(
            [FakeProject("1", "A班")],
            {"1": [_due_task("900", "バリ取り", parent_id="999")]},
            get_task=FakeGetTask(error=RuntimeError("boom")),
        )
        cog, db = await _make_cog(monkeypatch, bot)
        try:
            sent = await cog.push_project_tasks(G1)
            assert sent == 1  # 通知は落ちない
            desc = bot.channels[100].sent[0].description
            assert "バリ取り" in desc
            assert "親タスク:" not in desc
            assert bot.todoist.get_task.calls == ["999"]  # 1回だけ
        finally:
            await db.close()

    run(_main())


def test_notification_without_parents_is_unchanged(monkeypatch):
    """親なしタスクだけの通知（既存挙動）に回帰がない。"""

    async def _main():
        bot = FakeBot(channels={100: FakeChannel()})
        bot.todoist = FakeTodoist(
            [FakeProject("1", "A班")],
            {"1": [_due_task("100", "第2リブ")]},
        )
        cog, db = await _make_cog(monkeypatch, bot)
        try:
            sent = await cog.push_project_tasks(G1)
            assert sent == 1
            desc = bot.channels[100].sent[0].description
            assert "第2リブ" in desc
            assert "📂 A班" in desc
            assert "親タスク" not in desc
            assert pc.PARENT_FIELD_NAME not in desc
        finally:
            await db.close()

    run(_main())
