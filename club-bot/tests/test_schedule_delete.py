"""`/schedule delete` の論理削除と `/schedule restore` のテスト（G3-3）。

従来は投票メッセージを削除してから DB を CASCADE 削除しており、
**票データが完全に消えていた**。誰がいつ参加と答えたかは、Discord 側の
メッセージを消した時点で他のどこにも残らない。
`/team-remove` `/skill-remove` `/layer keta-remove` は既に論理削除方式なので、
方針の統一にもなる。

**削除時に closed_flag も立てるのが設計の要。** 投票メッセージを消した
時点で投票は現実に終わっているので嘘ではなく、こうしておくと自動催促・
自動締切・開催中一覧が既存の条件式だけで止まる。復元用の抑止フラグを
別に足す必要がなく、restore は deleted_flag を戻すだけで済む。
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

import discord

from cogs.schedule import Schedule
from repositories.schedule_repository import ScheduleRepository
from repositories.table_repository import TableRepository
from utils.db import SCHEMA_VERSION, Database
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002


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


def _msg_id(schedule_id: str, index: int) -> int:
    """_seed が候補へ入れる message_id（予定ごとに別の値になる）。

    hash() は PYTHONHASHSEED でプロセスごとに変わるので使わない。
    """
    return 9000 + (sum(ord(c) for c in schedule_id) % 100) * 10 + index


async def _seed(db: Database, guild_id: int = G1, schedule_id: str = "sch_1") -> ScheduleRepository:
    """予定1件・候補2件・票2件を作る。"""
    repo = ScheduleRepository(db)
    await repo.create_schedule(
        guild_id,
        schedule_id,
        "秋合宿",
        None,
        "部室",
        None,
        "2026-10-01T23:59:00",
        "tester",
        "555",
    )
    # message_id は schedule_id から導出する（固定値にすると、予定を2件
    # 作ったときに get_option_by_message が取り違える）
    base = _msg_id(schedule_id, 0)
    await repo.add_option(
        guild_id,
        f"{schedule_id}_o1",
        schedule_id,
        "10/1",
        "2026-10-01T18:00:00",
        None,
        str(base + 1),
    )
    await repo.add_option(
        guild_id,
        f"{schedule_id}_o2",
        schedule_id,
        "10/2",
        "2026-10-02T18:00:00",
        None,
        str(base + 2),
    )
    await repo.set_vote(guild_id, f"{schedule_id}_o1", "1", "ok")
    await repo.set_vote(guild_id, f"{schedule_id}_o2", "2", "ng")
    return repo


# =====================================================================
# 1. リポジトリ
# =====================================================================
def test_votes_survive_the_deletion():
    """**このタスクの主目的。** 削除しても票データが残ること。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")

            votes = await repo.list_schedule_votes(G1, "sch_1")
            assert len(votes) == 2, "票が消えている"
            assert await repo.list_voters_for_schedule(G1, "sch_1") == {"1", "2"}
            assert len(await repo.list_options(G1, "sch_1")) == 2, "候補が消えている"
        finally:
            await db.close()

    run(_main())


def test_deleted_schedules_disappear_from_every_list():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")

            assert await repo.get_schedule(G1, "sch_1") is None
            assert await repo.list_open_schedules(G1) == []
            assert await repo.list_closed_schedules(G1) == []
            assert await repo.list_all(G1) == [], "集計（list_all）から外れていない"
            assert await repo.list_due_schedules(G1, "2027-01-01T00:00:00") == []
            assert (
                await repo.list_reminder_candidates(
                    G1, "2026-01-01T00:00:00", "2027-01-01T00:00:00"
                )
                == []
            )
            # 削除済みだけを引く経路では見える
            assert [r["schedule_id"] for r in await repo.list_deleted_schedules(G1)] == ["sch_1"]
            assert await repo.get_schedule(G1, "sch_1", include_deleted=True) is not None
        finally:
            await db.close()

    run(_main())


def test_delete_also_closes_the_vote():
    """削除は締切も立てる（投票メッセージが消えた時点で投票は終わっている）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            row = await repo.get_schedule(G1, "sch_1", include_deleted=True)
            assert row["closed_flag"] == 1
            assert row["deleted_flag"] == 1
        finally:
            await db.close()

    run(_main())


def test_restore_only_touches_deleted_flag():
    """復元は `deleted_flag` 以外の列を1つも動かさない。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            before = await repo.get_schedule(G1, "sch_1", include_deleted=True)

            assert await repo.restore_schedule(G1, "sch_1") is True
            after = await repo.get_schedule(G1, "sch_1", include_deleted=True)

            assert after["deleted_flag"] == 0
            changed = {k for k in before if before[k] != after[k]}
            assert changed == {"deleted_flag"}, f"余計な列を書き換えている: {changed}"
        finally:
            await db.close()

    run(_main())


def test_restored_schedules_do_not_resume_voting():
    """復元しても催促も自動締切も走らない（投票メッセージが戻らないため）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            await repo.restore_schedule(G1, "sch_1")

            assert await repo.list_open_schedules(G1) == [], "開催中に戻っている"
            assert await repo.list_due_schedules(G1, "2027-01-01T00:00:00") == []
            assert (
                await repo.list_reminder_candidates(
                    G1, "2026-01-01T00:00:00", "2027-01-01T00:00:00"
                )
                == []
            )
            # 締切済みとして読める
            assert [r["schedule_id"] for r in await repo.list_closed_schedules(G1)] == ["sch_1"]
            assert len(await repo.list_schedule_votes(G1, "sch_1")) == 2
        finally:
            await db.close()

    run(_main())


def test_restore_is_scoped_to_the_guild():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, guild_id=G2, schedule_id="sch_2")
            await repo.soft_delete_schedule(G1, "sch_1")

            assert await repo.restore_schedule(G2, "sch_1") is False
            assert await repo.get_schedule(G1, "sch_1") is None, "他ギルドから戻せている"
            # 他ギルドの予定は削除されていない
            other = await repo.get_schedule(G2, "sch_2", include_deleted=True)
            assert other["deleted_flag"] == 0
        finally:
            await db.close()

    run(_main())


def test_restore_of_a_live_schedule_is_a_noop():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            assert await repo.restore_schedule(G1, "sch_1") is False
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. ダッシュボード
# =====================================================================
def test_deleted_schedules_are_not_offered_as_vote_sheets():
    """出欠回答のタブから削除済みの予定が消えること。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            table = TableRepository(db)
            assert [s["id"] for s in await table.list_sheets(G1, "schedule_votes")] == ["sch_1"]

            await repo.soft_delete_schedule(G1, "sch_1")
            assert await table.list_sheets(G1, "schedule_votes") == []
        finally:
            await db.close()

    run(_main())


def test_dashboard_cannot_edit_the_deleted_flag():
    """L2 が L3 の削除・復元を取り消せないこと（編集不可の列）。"""
    from repositories.table_repository import TABLES

    spec = TABLES["schedules"]
    column = next(c for c in spec.columns if c.name == "deleted_flag")
    assert not column.editable


# =====================================================================
# 3. コマンド
# =====================================================================
class _Message:
    def __init__(self, message_id: int, fail: bool = False):
        self.id = message_id
        self.fail = fail
        self.deleted = False

    async def delete(self):
        if self.fail:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="no"), "forbidden")
        self.deleted = True


class _Channel:
    def __init__(self, messages):
        self.id = 555
        self._messages = {m.id: m for m in messages}

    async def fetch_message(self, message_id: int):
        if message_id not in self._messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="gone"), "not found")
        return self._messages[message_id]


def _member(user_id: int = 501, *, admin: bool = True):
    """権限判定（utils/permissions.get_level）が読む属性を持つメンバー。

    admin=False は一般部員（L1）。/schedule status のような L1 コマンドから
    削除済み ID を打った人が、実行できない L3 コマンドを案内されないことを
    見るために使う。
    """
    return SimpleNamespace(
        id=user_id,
        display_name="tester",
        guild=SimpleNamespace(owner_id=user_id if admin else 999),
        roles=[],
        guild_permissions=SimpleNamespace(administrator=admin, manage_guild=admin),
    )


class _Interaction:
    def __init__(self, user_id: int = 501, *, admin: bool = True):
        self.guild = SimpleNamespace(id=G1)
        self.user = _member(user_id, admin=admin)
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)

    @property
    def text(self) -> str:
        embed = self.sent[-1]["embed"]
        return (embed.title or "") + "\n" + (embed.description or "")


class _Emoji:
    """payload.emoji のダブル（`str()` で絵文字そのものになる）。"""

    def __init__(self, text: str):
        self.id = None
        self._text = text

    def __str__(self) -> str:
        return self._text


def _cog(db: Database, channel=None) -> Schedule:
    bot = SimpleNamespace(
        db=db,
        guilds=[],
        user=None,
        get_channel=lambda _cid: channel,
        get_guild=lambda _g: None,
    )
    return Schedule(bot)


def test_delete_removes_the_vote_messages():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            messages = [_Message(_msg_id("sch_1", 1)), _Message(_msg_id("sch_1", 2))]
            cog = _cog(db, channel=_Channel(messages))
            interaction = _Interaction()
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")
            view = interaction.sent[-1]["view"]
            confirm = _Interaction()
            await view.confirm.callback(confirm)

            assert all(m.deleted for m in messages), "投票メッセージが残っている"
            assert "2 件" in confirm.text
        finally:
            await db.close()

    run(_main())


def test_delete_reports_messages_it_could_not_remove():
    """権限不足で残ったメッセージを黙って隠さないこと。

    残ったメッセージのリアクションは（予定が削除済みなので）押しても
    何も起きない。利用者から見れば bot の沈黙になる。
    """

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            messages = [_Message(_msg_id("sch_1", 1)), _Message(_msg_id("sch_1", 2), fail=True)]
            cog = _cog(db, channel=_Channel(messages))
            interaction = _Interaction()
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")
            view = interaction.sent[-1]["view"]
            confirm = _Interaction()
            await view.confirm.callback(confirm)

            text = confirm.text
            assert "1 件のメッセージを削除できませんでした" in text
            # 削除自体は成功している（票は残る）
            repo = ScheduleRepository(db)
            assert (await repo.get_schedule(G1, "sch_1", include_deleted=True))["deleted_flag"] == 1
            assert len(await repo.list_schedule_votes(G1, "sch_1")) == 2
        finally:
            await db.close()

    run(_main())


def test_deleting_a_schedule_tells_the_user_votes_are_kept():
    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db, channel=_Channel([]))
            interaction = _Interaction()
            await Schedule.delete.callback(cog, interaction, schedule_id="sch_1")
            text = interaction.text
            assert "restore" in text, "戻せることを案内していない"
            assert "締め切られます" in text
        finally:
            await db.close()

    run(_main())


def test_restore_command_brings_the_schedule_back():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.restore.callback(cog, interaction, schedule_id="sch_1")

            assert (await repo.get_schedule(G1, "sch_1")) is not None
            text = interaction.text
            assert "投票は再開しません" in text
            assert "2" in text, "残っている回答者数を出していない"
        finally:
            await db.close()

    run(_main())


def test_commands_on_a_deleted_schedule_explain_why():
    """ID 直打ちの人に「見つかりません」で終わらせない。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction()  # L4（restore を実行できる）
            await Schedule.status.callback(cog, interaction, schedule_id="sch_1")
            text = interaction.text
            assert "削除されています" in text
            assert "/schedule restore" in text
        finally:
            await db.close()

    run(_main())


def test_a_plain_member_is_not_told_to_run_a_command_they_cannot_use():
    """L1 の利用者に L3 のコマンドを「次の1コマンド」として出さない。

    /schedule status は L1 なので、削除済み ID を打つのは一般部員でも
    起こる。実行できない `/schedule restore` を案内すると行き止まりになる。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            cog = _cog(db)
            interaction = _Interaction(admin=False)
            await Schedule.status.callback(cog, interaction, schedule_id="sch_1")
            text = interaction.text
            assert "削除されています" in text
            assert "依頼してください" in text, "誰に頼めばいいかを出していない"
            assert "/schedule list-closed" in text, "L1 が打てる次の一手が無い"
        finally:
            await db.close()

    run(_main())


def test_delete_and_restore_require_l3():
    """権限はコマンドのデコレータで担保されていること。"""
    assert command_required_level(Schedule.delete) == Level.L3
    assert command_required_level(Schedule.restore) == Level.L3


def test_reacting_to_a_deleted_schedule_does_not_record_a_vote():
    """削除済み予定のメッセージが残っていても票が入らないこと。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            cog = _cog(db, channel=_Channel([]))
            payload = SimpleNamespace(
                user_id=42,
                guild_id=G1,
                message_id=_msg_id("sch_1", 1),
                channel_id=555,
                emoji=_Emoji("✅"),
                member=None,
            )
            await cog._handle_reaction(payload, added=True)
            assert len(await repo.list_schedule_votes(G1, "sch_1")) == 2, "票が増えている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. マイグレーション（v16 → v17）
# =====================================================================
def test_v17_adds_both_columns_to_an_existing_db():
    """**両方の列**が入ること。

    confirmed_option_id は G3-4 が使うが、同じ版に入れておかないと
    v17 済みの DB は二度と v17 の処理を通らず、後から足しても既存 DB に
    列が無いままになる（_migrate_versioned は version >= SCHEMA_VERSION で
    早期 return する）。
    """

    async def _main():
        db = await _make_db()
        try:
            # v16 相当の DB を作る（列を落として user_version を戻す）
            await db.execute("ALTER TABLE schedules DROP COLUMN deleted_flag")
            await db.execute("ALTER TABLE schedules DROP COLUMN confirmed_option_id")
            await db.execute("PRAGMA user_version = 16")
            cols = await db._table_columns("schedules")
            assert "deleted_flag" not in cols

            await db._migrate_versioned()

            cols = await db._table_columns("schedules")
            assert "deleted_flag" in cols
            assert "confirmed_option_id" in cols, "G3-4 の列が入っていない"
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()

    run(_main())


def test_v17_does_not_mark_existing_schedules_as_deleted():
    """マイグレーションを当てただけでは、どの予定も削除済みにならない。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await db.execute("ALTER TABLE schedules DROP COLUMN deleted_flag")
            await db.execute("PRAGMA user_version = 16")
            await db._migrate_versioned()

            row = await repo.get_schedule(G1, "sch_1")
            assert row is not None, "既存の予定が削除済みになった"
            assert row["deleted_flag"] == 0
            assert len(await repo.list_schedule_votes(G1, "sch_1")) == 2
        finally:
            await db.close()

    run(_main())


def test_v17_migration_is_idempotent():
    async def _main():
        db = await _make_db()
        try:
            await db._migrate_v17_schedule_confirmed()
            await db._migrate_v17_schedule_confirmed()
            cols = await db._table_columns("schedules")
            assert cols.count("deleted_flag") == 1
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 5. deleted_flag のガードそのもの
#
# soft_delete_schedule は closed_flag も立てるので、削除経路だけを使う
# テストでは **deleted_flag = 1 かつ closed_flag = 0** の行が生まれず、
# list_open_schedules などの `AND deleted_flag = 0` が一度も踏まれない
# （closed_flag だけで除外され続けるため、ガードを外しても緑になる）。
#
# この状態は机上の話ではない: ダッシュボードの schedules 表は
# list_rows が guild_id しか絞らないので削除済みの行も並び、
# closed_flag は editable=True（L2）なので「締切済み」を外せる。
# ガードが無ければ、投票メッセージを消した予定が開催中一覧へ復活し、
# 自動締切と自動催促が動き出す。
# =====================================================================
async def _unclose_deleted(db: Database, schedule_id: str = "sch_1") -> None:
    """ダッシュボードで「締切済み」を外された削除済み予定を作る。"""
    await db.execute(
        "UPDATE schedules SET closed_flag = 0 WHERE guild_id = ? AND schedule_id = ?",
        (G1, schedule_id),
    )


def test_open_list_excludes_a_deleted_schedule_even_if_it_is_not_closed():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            await _unclose_deleted(db)
            assert await repo.list_open_schedules(G1) == [], "削除済みが開催中一覧に復活している"
        finally:
            await db.close()

    run(_main())


def test_due_list_excludes_a_deleted_schedule_even_if_it_is_not_closed():
    """自動締切が削除済みを拾わないこと（拾うと集計がチャンネルへ流れる）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            await _unclose_deleted(db)
            assert await repo.list_due_schedules(G1, "2027-01-01T00:00:00") == []
        finally:
            await db.close()

    run(_main())


def test_reminder_candidates_exclude_a_deleted_schedule_even_if_it_is_not_closed():
    """自動催促が削除済みを拾わないこと（拾うと消えた投票の DM が飛ぶ）。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            await _unclose_deleted(db)
            assert (
                await repo.list_reminder_candidates(
                    G1, "2026-01-01T00:00:00", "2027-01-01T00:00:00"
                )
                == []
            )
        finally:
            await db.close()

    run(_main())


def test_reacting_to_a_deleted_unclosed_schedule_does_not_record_a_vote():
    """締切が外れていても、削除済みなら票を受け付けないこと。

    closed_flag が立っている状態で検査しても、それは closed_flag の
    再確認にしかならない（deleted_flag のガードを外しても緑になる）。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await repo.soft_delete_schedule(G1, "sch_1")
            await _unclose_deleted(db)
            # 票が入ってしまった場合にそこまで進めるよう、
            # fetch_message が NotFound で静かに終わるチャンネルを渡す
            cog = _cog(db, channel=_Channel([]))
            payload = SimpleNamespace(
                user_id=42,
                guild_id=G1,
                message_id=_msg_id("sch_1", 1),
                channel_id=555,
                emoji=_Emoji("✅"),
                member=None,
            )
            await cog._handle_reaction(payload, added=True)
            assert len(await repo.list_schedule_votes(G1, "sch_1")) == 2, "票が増えている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 6. /schedule restore のオートコンプリートと no-op 応答
# =====================================================================
def test_restore_autocomplete_offers_only_deleted_schedules():
    """削除済みは他のどの一覧にも出ないので、ここが壊れると生 ID が要る。"""

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, schedule_id="sch_live")
            await repo.soft_delete_schedule(G1, "sch_1")

            cog = _cog(db)
            interaction = SimpleNamespace(guild=SimpleNamespace(id=G1))
            choices = await cog._schedule_ac_deleted(interaction, "")
            assert [c.value for c in choices] == ["sch_1"], "生きている予定が復元候補に出ている"
            # 「終了」ではなく「削除済み」と分かること
            assert choices[0].name.startswith("[削除済み] ")
        finally:
            await db.close()

    run(_main())


def test_restore_autocomplete_is_registered_on_the_command():
    """登録し忘れると、候補が出ないまま誰も気づかない。"""
    autocomplete = Schedule.restore._params["schedule_id"].autocomplete
    assert autocomplete is not None, "restore に schedule_id のオートコンプリートが無い"
    assert autocomplete.__name__ == "_schedule_ac_deleted", (
        "restore の候補が削除済み以外から作られている"
    )


def test_restore_of_a_live_schedule_tells_the_user_nothing_changed():
    """削除されていない予定を restore しても「戻しました」と言わない。"""

    async def _main():
        db = await _make_db()
        try:
            await _seed(db)
            cog = _cog(db)
            interaction = _Interaction()
            await Schedule.restore.callback(cog, interaction, schedule_id="sch_1")
            text = interaction.text
            assert "戻しました" not in text
            assert "削除されていません" in text
        finally:
            await db.close()

    run(_main())


def test_soft_delete_does_not_reach_another_guilds_schedule():
    """他ギルドの guild_id で削除しても何も起きないこと。

    既存のギルド分離テストは他ギルドに**別の** schedule_id を置くので、
    `WHERE guild_id = ?` を外した SQL に一度も触れない
    （AGENTS.md 絶対ルール3 の回帰ネットとしての穴）。
    同じ schedule_id を2ギルドに置く形では検査できない
    ——`schedules.schedule_id` は PRIMARY KEY で、schema 上そもそも作れない。
    そこで「持ち主でないギルドから消しにいく」向きで踏む。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)  # G1 だけが持っている
            await repo.soft_delete_schedule(G2, "sch_1")

            row = await repo.get_schedule(G1, "sch_1")
            assert row is not None, "他ギルドから削除できてしまっている"
            assert row["deleted_flag"] == 0
            assert row["closed_flag"] == 0, "他ギルドから締切にできてしまっている"
        finally:
            await db.close()

    run(_main())


def test_status_and_delete_autocomplete_hide_deleted_schedules():
    """削除済みは `/schedule status` `/schedule delete` の候補にも出さない。

    どちらのコマンドも削除済みには使えない（get_schedule が既定で除外する）
    ので、候補に出すと選ばせてから断ることになる。
    """

    async def _main():
        db = await _make_db()
        try:
            repo = await _seed(db)
            await _seed(db, schedule_id="sch_live")
            await repo.soft_delete_schedule(G1, "sch_1")

            cog = _cog(db)
            interaction = SimpleNamespace(guild=SimpleNamespace(id=G1))
            choices = await cog._schedule_ac_all(interaction, "")
            assert [c.value for c in choices] == ["sch_live"]
        finally:
            await db.close()

    run(_main())
