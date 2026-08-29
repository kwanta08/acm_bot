"""`/me` 個人サマリーのテスト（G4-4）。

部員視点の入口が無かった。未回答の投票・積層実績・担当ノードが
それぞれ別コマンドに散らばっている。新入生が
「今日自分は何をすればいいか」を1コマンドで確認できなかった。

タスクは `/me` に出ない（スキーマ v22 でローカルの tasks テーブルを廃止し、
正本を Todoist へ移した。Todoist に Discord ユーザー単位の担当が無い）。

このファイルが特に固定しているもの:

1. **新しいテーブルを作っていないこと。** 既存クエリの合成だけで済ませる
   のがこのタスクの前提（マイグレーション不要）
2. **`user` 引数は L2 以上のみ。** ここが緩むと、一般部員が他人の
   出欠回答状況と担当ノードを引けるようになる
3. **未回答は投票「単位」**（候補単位ではない）。候補単位で数えると
   「3候補中2つだけ答えた」人が未回答として出る
4. **他ギルドの行が混ざらないこと**
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.me import Me
from config import config
from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.schedule_repository import ScheduleRepository
from utils.db import TABLE_DDL, Database
from utils.parser import TZ, now, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
ME = "501"
OTHER = "502"


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def _today_iso(days: int = 0) -> str:
    from datetime import timedelta

    return (now() + timedelta(days=days)).date().isoformat()


async def _seed(db: Database, guild_id: int = G1, prefix: str = "sch") -> None:
    """1ギルド分のデータを入れる。

    `schedules.schedule_id` は**グローバルな主キー**（guild_id を含まない）
    なので、2ギルド分を入れるときは prefix を変える。
    """
    sched = ScheduleRepository(db)
    from datetime import datetime, timedelta

    deadline = datetime(2030, 1, 10, 23, 59, tzinfo=TZ)
    await sched.create_schedule(
        guild_id, f"{prefix}_1", "秋合宿", None, "部室", None, to_iso(deadline), "tester", "555"
    )
    await sched.add_option(
        guild_id, f"{prefix}_1_o1", f"{prefix}_1", "10/1", to_iso(deadline + timedelta(days=1)), None, None
    )
    await sched.create_schedule(
        guild_id,
        f"{prefix}_2",
        "作業日",
        None,
        "工房",
        None,
        to_iso(deadline + timedelta(days=1)),
        "tester",
        "555",
    )
    for n, offset in ((1, 5), (2, 6)):
        await sched.add_option(
            guild_id,
            f"{prefix}_2_o{n}",
            f"{prefix}_2",
            f"10/{offset}",
            to_iso(deadline + timedelta(days=offset)),
            None,
            None,
        )
    # 2つ目の予定には**候補2つのうち1つだけ**回答済み。
    # 「未回答」を候補単位で数える実装だと、ここが未回答として出る
    await sched.set_vote(guild_id, f"{prefix}_2_o1", ME, "ok")

    layers = LayerSessionRepository(db)
    started = now().replace(day=1, hour=9, minute=0, second=0, microsecond=0)
    await layers.add_record(guild_id, ME, "主桁1", "1", to_iso(started), to_iso(started), 60)
    await layers.add_record(guild_id, ME, "主桁1", "2", to_iso(started), to_iso(started), 30)
    await layers.add_record(guild_id, OTHER, "主桁1", "3", to_iso(started), to_iso(started), 999)

    progress = ProgressRepository(db)
    await progress.upsert_node(
        guild_id, "n1", sort_order=1.0, name="主桁", assignee="たろう", status="製作中"
    )
    await progress.upsert_node(
        guild_id, "n2", sort_order=2.0, name="リブ", assignee="たろう", status="完了"
    )
    await progress.upsert_node(
        guild_id, "n3", sort_order=3.0, name="尾翼", assignee="はなこ", status="製作中"
    )

    await MemberRepository(db).upsert_member(guild_id, ME, "たろう")


# =====================================================================
# 1. 新しいテーブルを作っていない
# =====================================================================
def test_no_new_table_was_added_for_me():
    """G4-4 は「既存クエリの合成のみ」。新しい表を足していないこと。"""
    assert "me" not in TABLE_DDL
    assert not any(key.startswith("me_") for key in TABLE_DDL)


def test_me_is_registered_as_a_cog():
    from bot import COGS

    assert "cogs.me" in COGS, "Cog を bot.py へ登録し忘れている（コマンドが同期されない）"


def test_me_is_level_1():
    assert command_required_level(Me.me) == Level.L1


# =====================================================================
# 2. 集計（コマンドを通さない）
# =====================================================================
def _cog(db: Database) -> Me:
    return Me(SimpleNamespace(db=db, guilds=[], user=None))


def test_unanswered_counts_by_schedule_not_by_option():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            rows = await cog.unanswered_schedules(G1, ME)
            assert [r["schedule_id"] for r in rows] == ["sch_1"], (
                "回答済みの予定まで未回答に数えている（候補単位で見ている）"
            )
        finally:
            await db.close()

    run(_main())


def test_unanswered_ignores_closed_and_deleted_schedules():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            repo = ScheduleRepository(db)
            await repo.close_schedule(G1, "sch_1")
            cog = _cog(db)
            assert await cog.unanswered_schedules(G1, ME) == []
        finally:
            await db.close()

    run(_main())


def test_unanswered_is_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db, guild_id=G1, prefix="a")
            await _seed(db, guild_id=G2, prefix="b")
            cog = _cog(db)
            assert [r["schedule_id"] for r in await cog.unanswered_schedules(G2, ME)] == ["b_1"]
            assert [r["schedule_id"] for r in await cog.unanswered_schedules(G1, ME)] == ["a_1"]
        finally:
            await db.close()

    run(_main())


def test_assigned_nodes_skips_finished_ones():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            nodes = await cog.assigned_nodes(G1, {"たろう"})
            assert [n["node_id"] for n in nodes] == ["n1"], "完了済みノードまで出している"
        finally:
            await db.close()

    run(_main())


def test_assigned_nodes_returns_nothing_for_an_empty_name_set():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            assert await cog.assigned_nodes(G1, set()) == []
            assert await cog.assigned_nodes(G1, {"", "  "}) == []
        finally:
            await db.close()

    run(_main())


def test_layer_summary_counts_only_that_person_this_month():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            assert await cog.layer_summary(G1, ME) == (2, 90)
            assert await cog.layer_summary(G1, "999") == (0, 0)
        finally:
            await db.close()

    run(_main())


def test_layer_summary_excludes_last_month():
    async def _main():
        db = await _make_db()
        try:
            from datetime import datetime

            old = datetime(2020, 1, 5, 10, 0, tzinfo=TZ)
            await LayerSessionRepository(db).add_record(
                G1, ME, "旧桁", "1", to_iso(old), to_iso(old), 500
            )
            cog = _cog(db)
            assert await cog.layer_summary(G1, ME) == (0, 0)
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. コマンド
# =====================================================================
class _Member:
    def __init__(self, user_id: int, name: str, level: Level = Level.L1):
        self.id = user_id
        self.display_name = name
        self.roles = []
        self._level = level
        self.guild = SimpleNamespace(owner_id=None)
        self.guild_permissions = SimpleNamespace(
            administrator=level >= Level.L4, manage_guild=level >= Level.L4
        )


class _Interaction:
    def __init__(self, guild_id: int = G1, user: _Member | None = None):
        self.guild = SimpleNamespace(id=guild_id)
        self.user = user or _Member(int(ME), "たろう")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._noop, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _noop(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        parts = [embed.title or "", embed.description or ""]
        parts += [f"{f.name}\n{f.value}" for f in embed.fields]
        return "\n".join(parts)


def _permission(cog: Me, allowed: bool) -> list[str]:
    """`/me` の権限判定をインスタンス単位で差し替え、渡された ID を記録する。

    **モジュール変数への `mock.patch` は使わない。** `tests/test_docs_commands.py`
    などが `unload_extension` を通ると `sys.modules["cogs.me"]` の実体が
    入れ替わり、この場で import 済みのクラスが見ているモジュールとは
    別のオブジェクトへパッチが当たる（単体では緑、フルセットでだけ赤になる）。
    インスタンス属性の差し替えならその影響を受けない。

    必要レベルそのものは `VIEW_OTHERS_LEVEL` のテストが固定する。
    """
    asked: list[str] = []

    async def _may_view(_interaction, user_id: str) -> bool:
        asked.append(str(user_id))
        return allowed

    cog.may_view = _may_view
    return asked


def test_the_level_required_for_others_is_l2():
    """**要求レベルそのものを固定する。**

    ここが L1 に下がると、一般部員が他人の担当タスクと出欠回答状況を引ける。
    """
    from cogs.me import VIEW_OTHERS_LEVEL

    assert VIEW_OTHERS_LEVEL == Level.L2


def test_may_view_always_allows_yourself():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            interaction = _Interaction()
            assert await cog.may_view(interaction, ME) is True
        finally:
            await db.close()

    run(_main())


def test_may_view_refuses_someone_else_without_the_level():
    """判定に失敗したときは**通さない**（fail-open にしない）。"""

    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            interaction = _Interaction()
            assert await cog.may_view(interaction, OTHER) is False
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_me_shows_every_section():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            _permission(cog, True)
            interaction = _Interaction()
            await Me.me.callback(cog, interaction, user=None)
            text = interaction.text
            assert "秋合宿" in text
            assert "作業日" not in text, "回答済みの投票が未回答として出ている"
            assert "2 層" in text
            assert "主桁" in text
            assert "リブ" not in text.split("担当中の進捗ノード")[-1], "完了ノードが出ている"
        finally:
            await db.close()

    run(_main())


def test_me_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            _permission(cog, True)
            interaction = _Interaction()
            await Me.me.callback(cog, interaction, user=None)
            assert "`/schedule create`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_a_plain_member_cannot_look_at_someone_else():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            interaction = _Interaction()
            target = _Member(int(OTHER), "はなこ")
            asked = _permission(cog, False)
            await Me.me.callback(cog, interaction, user=target)
            text = interaction.text
            assert "班長以上" in text
            assert "はなこ" not in text, "権限が無いのに他人の内容が出ている"
            assert asked == [str(OTHER)], "判定にかけた相手が違う"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_leader_can_look_at_someone_else():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            interaction = _Interaction(user=_Member(int(ME), "たろう", level=Level.L2))
            target = _Member(int(OTHER), "はなこ")
            asked = _permission(cog, True)
            await Me.me.callback(cog, interaction, user=target)
            text = interaction.text
            assert "はなこ" in text
            assert asked == [str(OTHER)]
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_me_does_not_leak_another_guilds_rows():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db, guild_id=G1)
            sched = ScheduleRepository(db)
            await sched.create_schedule(
                G2,
                "b_1",
                "B大学の部会",
                None,
                "部室",
                None,
                "2030-01-10T23:59:00+09:00",
                "tester",
                "555",
            )
            await sched.add_option(G2, "b_1_o1", "b_1", "10/1", "2030-01-11T00:00:00", None, None)
            cog = _cog(db)
            _permission(cog, True)
            interaction = _Interaction(guild_id=G1)
            await Me.me.callback(cog, interaction, user=None)
            assert "B大学" not in interaction.text
        finally:
            await db.close()

    run(_main())


def test_me_uses_the_roster_name_for_assigned_nodes():
    """Discord の表示名を変えても、台帳の登録名で担当ノードを拾えること。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            # Discord 側の表示名だけ変わった状態
            _permission(cog, True)
            interaction = _Interaction(user=_Member(int(ME), "改名した名前"))
            await Me.me.callback(cog, interaction, user=None)
            assert "主桁" in interaction.text, "台帳の display_name で解決していない"
        finally:
            await db.close()

    run(_main())
