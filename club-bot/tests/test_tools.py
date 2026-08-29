"""工具・機材の貸出（`/tool`。スキーマ v20）のテスト（G4-9）。

`/layer start` → `/layer end` とまったく同じ「開始 → 進行中 → 終了」モデル。
借りたまま返らない工具は、次に使う人の作業日をそのまま潰す。

このファイルが特に固定しているもの:

1. **貸出中かどうかは `tool_loans.returned_at IS NULL` で決まる。**
   `tools` 側のフラグにすると、工具を消したときに貸出の事実まで消える
2. **返却予定日が未設定の貸出は督促しない**（ADR 0021）。
   予定日当日も督促しない（「本日中に返す」人への誤報になる）
3. **督促は1貸出につき1回**（G4-2 と同じ形）。送れたときだけフラグを立てる
4. **v19 に足さず v20 を切ったこと。** 既存 DB へ届かない版へ
   テーブルを足すと本番だけ落ちる（gotcha `bot-wont-start-undefined-column`）
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import discord

from cogs.inventory import Inventory
from cogs.reminders import Reminders
from repositories.tool_repository import ToolRepository
from services.tool_service import loan_status_label, overdue_loans
from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database
from utils.parser import now, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
TODAY = date(2026, 8, 31)


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


def _loan(
    loan_id: int,
    *,
    due: str | None = None,
    returned: str | None = None,
    notified: int = 0,
    user_id: str = "501",
    tool_name: str = "トルクレンチ",
) -> dict:
    return {
        "loan_id": loan_id,
        "tool_id": loan_id,
        "tool_name": tool_name,
        "user_id": user_id,
        "borrowed_at": "2026-08-01T10:00:00+09:00",
        "due_date": due,
        "returned_at": returned,
        "overdue_notified_flag": notified,
    }


# =====================================================================
# 1. スキーマ
# =====================================================================
def test_schema_version_is_20():
    assert SCHEMA_VERSION >= 20


def test_the_tool_tables_are_declared_for_both_drivers():
    for name in ("tools", "tool_loans"):
        for ddl in (TABLE_DDL[name], TABLE_DDL_PG[name]):
            assert name in ddl


def test_the_due_date_and_return_are_nullable():
    """予定日未設定の貸出を「本日返却」にしない（ADR 0021）。"""
    ddl = TABLE_DDL["tool_loans"]
    for column in ("due_date", "returned_at"):
        m = re.search(rf"{column}\s+TEXT([^,\n]*)", ddl)
        assert m, column
        assert "NOT NULL" not in m.group(1), f"{column} が NOT NULL になっている"


def test_tools_are_unique_per_guild():
    assert re.search(r"UNIQUE\s*\(guild_id,\s*tool_name\)", TABLE_DDL["tools"])


def test_the_tools_migration_is_its_own_version():
    """**v19（在庫）に足していないこと。**

    `_migrate_versioned()` は `version >= SCHEMA_VERSION` で早期 return する。
    v19 済みの DB は二度と v19 を通らないので、後から v19 へ CREATE を足すと
    新規 DB にだけテーブルがある状態になる。
    """
    import inspect

    from utils.db import Database as DB

    v19 = inspect.getsource(DB._migrate_v19_stock)
    v20 = inspect.getsource(DB._migrate_v20_tools)
    assert "tools" not in v19, "v19 に工具テーブルを混ぜている"
    assert "tools" in v20 and "tool_loans" in v20


def test_migrating_a_v19_db_adds_the_tool_tables_without_touching_data():
    async def _main():
        path = _tmp_db_path()
        conn = sqlite3.connect(path)
        try:
            for name, ddl in TABLE_DDL.items():
                if name in ("tools", "tool_loans"):
                    continue
                conn.executescript(ddl)
            conn.execute(
                "INSERT INTO stock_items (guild_id, item_name, unit, quantity,"
                " created_by, created_at, updated_at)"
                " VALUES (?, 'プリプレグ', 'm', 10, 'u1', '2026-01-01', '2026-01-01')",
                (G1,),
            )
            conn.execute("PRAGMA user_version = 19")
            conn.commit()
        finally:
            conn.close()

        db = Database(path)
        await db.connect()
        try:
            assert (await db.fetchone("PRAGMA user_version"))[0] == SCHEMA_VERSION
            assert await db.fetchall("SELECT * FROM tools") == []
            item = await db.fetchone("SELECT * FROM stock_items WHERE item_name = 'プリプレグ'")
            assert item["quantity"] == 10, "既存行が書き換わっている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. 督促の判定（純関数）
# =====================================================================
def test_a_loan_without_a_due_date_is_never_chased():
    assert overdue_loans([_loan(1, due=None)], TODAY) == []


def test_the_due_date_itself_is_not_overdue():
    """「本日中に返す」人へ朝に「超過しています」と送らない。"""
    assert overdue_loans([_loan(1, due=TODAY.isoformat())], TODAY) == []


def test_a_day_past_the_due_date_is_overdue():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    (loan,) = overdue_loans([_loan(1, due=yesterday)], TODAY)
    assert loan.days_over == 1


def test_returned_loans_are_not_chased():
    old = (TODAY - timedelta(days=10)).isoformat()
    assert overdue_loans([_loan(1, due=old, returned="2026-08-30T10:00:00+09:00")], TODAY) == []


def test_already_notified_loans_are_not_chased_again():
    """1貸出につき1回（G4-2 と同じ形）。"""
    old = (TODAY - timedelta(days=10)).isoformat()
    assert overdue_loans([_loan(1, due=old, notified=1)], TODAY) == []
    # 一覧用に全件が欲しいときは only_unnotified=False
    assert len(overdue_loans([_loan(1, due=old, notified=1)], TODAY, only_unnotified=False)) == 1


def test_overdue_loans_are_sorted_by_how_late_they_are():
    loans = [
        _loan(1, due=(TODAY - timedelta(days=2)).isoformat(), tool_name="a"),
        _loan(2, due=(TODAY - timedelta(days=9)).isoformat(), tool_name="b"),
    ]
    assert [loan.loan_id for loan in overdue_loans(loans, TODAY)] == [2, 1]


def test_a_broken_due_date_is_skipped_not_raised():
    assert overdue_loans([_loan(1, due="いつか")], TODAY) == []


def test_the_status_label_tells_the_truth_about_a_missing_due_date():
    assert loan_status_label(None, TODAY) == "貸出可"
    assert "返却予定日なし" in loan_status_label(_loan(1, due=None), TODAY)
    assert "超過" in loan_status_label(
        _loan(1, due=(TODAY - timedelta(days=3)).isoformat()), TODAY
    )
    assert "超過" not in loan_status_label(
        _loan(1, due=(TODAY + timedelta(days=3)).isoformat()), TODAY
    )


# =====================================================================
# 3. リポジトリ
# =====================================================================
async def _register(db: Database, guild_id: int = G1, name: str = "トルクレンチ") -> int:
    return await ToolRepository(db).add_tool(guild_id, name, "u1", to_iso(now()), None)


def test_a_loan_is_open_until_it_is_returned():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            tool_id = await _register(db)
            assert await repo.get_open_loan(G1, tool_id) is None
            loan_id = await repo.borrow(G1, tool_id, "501", to_iso(now()), None, None)
            assert await repo.get_open_loan(G1, tool_id) is not None
            assert await repo.give_back(G1, loan_id, to_iso(now())) is True
            assert await repo.get_open_loan(G1, tool_id) is None
        finally:
            await db.close()

    run(_main())


def test_returning_twice_is_refused():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            tool_id = await _register(db)
            loan_id = await repo.borrow(G1, tool_id, "501", to_iso(now()), None, None)
            assert await repo.give_back(G1, loan_id, to_iso(now())) is True
            assert await repo.give_back(G1, loan_id, to_iso(now())) is False
        finally:
            await db.close()

    run(_main())


def test_another_guild_cannot_return_the_loan():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            tool_id = await _register(db, G1)
            loan_id = await repo.borrow(G1, tool_id, "501", to_iso(now()), None, None)
            assert await repo.give_back(G2, loan_id, to_iso(now())) is False
            assert await repo.get_open_loan(G1, tool_id) is not None
        finally:
            await db.close()

    run(_main())


def test_deactivating_a_tool_keeps_its_loans():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            tool_id = await _register(db)
            await repo.borrow(G1, tool_id, "501", to_iso(now()), None, None)
            assert await repo.deactivate_tool(G1, "トルクレンチ") is True
            assert await repo.list_tools(G1) == []
            assert len(await repo.list_open_loans(G1)) == 1, "貸出の事実まで消えている"
        finally:
            await db.close()

    run(_main())


def test_open_loans_are_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            await repo.borrow(G1, await _register(db, G1), "501", to_iso(now()), None, None)
            await repo.borrow(
                G2, await _register(db, G2, "他大の工具"), "999", to_iso(now()), None, None
            )
            assert [loan["tool_name"] for loan in await repo.list_open_loans(G1)] == [
                "トルクレンチ"
            ]
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. コマンド
# =====================================================================
class _Guild:
    def __init__(self, guild_id: int = G1, members: dict[int, object] | None = None):
        self.id = guild_id
        self.name = str(guild_id)
        self._members = members or {}

    def get_channel(self, _cid):
        return None

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class _Bot:
    def __init__(self, db, guilds=None):
        self.db = db
        self.guilds = guilds or []
        self.logged: list[tuple] = []

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_channel(self, _cid):
        return None

    def get_cog(self, _name):
        return None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


class _Interaction:
    def __init__(self, guild=None, user_id: int = 501):
        self.guild = guild if guild is not None else _Guild()
        self.user = SimpleNamespace(
            id=user_id,
            display_name="tester",
            guild=SimpleNamespace(owner_id=user_id),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
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


def _cog(db: Database) -> Inventory:
    return Inventory(_Bot(db))


def test_tool_permission_levels():
    assert command_required_level(Inventory.tool_list) == Level.L1
    assert command_required_level(Inventory.tool_borrow) == Level.L1
    assert command_required_level(Inventory.tool_return) == Level.L1
    assert command_required_level(Inventory.tool_add) == Level.L2
    assert command_required_level(Inventory.tool_remove) == Level.L2


def test_tool_is_in_the_same_cog_as_stock():
    """受入基準どおり cogs/inventory.py にまとめること。

    別 Cog に分けると、朝の通知（在庫の閾値割れ・工具の督促）の
    どちらか片方だけを登録し忘れる形が生まれる。
    """
    assert Inventory.stock_list.callback.__module__ == "cogs.inventory"
    assert Inventory.tool_list.callback.__module__ == "cogs.inventory"


def test_borrow_then_list_shows_the_borrower():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            await Inventory.tool_borrow.callback(
                cog, _Interaction(), tool="トルクレンチ", due="2030-01-01"
            )
            listing = _Interaction()
            await Inventory.tool_list.callback(cog, listing)
            assert "トルクレンチ" in listing.text
            assert "貸出中" in listing.text
            assert "<@501>" in listing.text
        finally:
            await db.close()

    run(_main())


def test_borrowing_a_tool_that_is_out_is_refused():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            await Inventory.tool_borrow.callback(cog, _Interaction(), tool="トルクレンチ")
            second = _Interaction(user_id=502)
            await Inventory.tool_borrow.callback(cog, second, tool="トルクレンチ")
            assert "貸出中" in second.text
            assert "<@501>" in second.text, "誰が借りているか出ていない"
        finally:
            await db.close()

    run(_main())


def test_someone_else_can_record_the_return():
    """現物が戻れば返却。借りた人が不在でも台帳を合わせられること。"""

    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            await Inventory.tool_borrow.callback(cog, _Interaction(), tool="トルクレンチ")
            other = _Interaction(user_id=502)
            await Inventory.tool_return.callback(cog, other, tool="トルクレンチ")
            assert "返却を記録" in other.text
            assert await ToolRepository(db).list_open_loans(G1) == []
        finally:
            await db.close()

    run(_main())


def test_returning_a_tool_that_is_not_out_says_so():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            interaction = _Interaction()
            await Inventory.tool_return.callback(cog, interaction, tool="トルクレンチ")
            assert "貸出中ではありません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_an_invalid_due_date_is_refused_without_recording():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            interaction = _Interaction()
            await Inventory.tool_borrow.callback(
                cog, interaction, tool="トルクレンチ", due="来週くらい"
            )
            assert "形式で指定" in interaction.text
            assert await ToolRepository(db).list_open_loans(G1) == [], "不正な入力で記録している"
        finally:
            await db.close()

    run(_main())


def test_borrowing_without_a_due_date_says_it_is_unset():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await Inventory.tool_add.callback(cog, _Interaction(), tool="トルクレンチ")
            interaction = _Interaction()
            await Inventory.tool_borrow.callback(cog, interaction, tool="トルクレンチ")
            assert "返却予定日は未設定" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_borrowing_an_unknown_tool_is_refused():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Inventory.tool_borrow.callback(_cog(db), interaction, tool="無い工具")
            assert "登録されていません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_tool_list_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Inventory.tool_list.callback(_cog(db), interaction)
            assert "`/tool add`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_tool_autocomplete_is_registered_where_it_is_needed():
    for command in (Inventory.tool_borrow, Inventory.tool_return, Inventory.tool_remove):
        param = command._params["tool"]
        assert param.autocomplete is not None, command.name
        assert param.autocomplete.__name__ == "_tool_autocomplete", command.name


# =====================================================================
# 5. 督促（DM）
# =====================================================================
class _Member:
    def __init__(self, user_id: int, fail: type | None = None):
        self.id = user_id
        self.display_name = f"member{user_id}"
        self.fail = fail
        self.dms: list[str] = []

    async def send(self, content=None, **kwargs):
        if self.fail is discord.Forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="no"), "blocked")
        if self.fail is discord.HTTPException:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")
        self.dms.append(content)


async def _overdue_loan(db: Database, days: int = 3, guild_id: int = G1) -> int:
    repo = ToolRepository(db)
    tool_id = await repo.add_tool(guild_id, "トルクレンチ", "u1", to_iso(now()), None)
    due = (now().date() - timedelta(days=days)).isoformat()
    return await repo.borrow(guild_id, tool_id, "501", to_iso(now()), due, None)


def test_an_overdue_loan_gets_one_dm():
    async def _main():
        db = await _make_db()
        try:
            await _overdue_loan(db)
            member = _Member(501)
            cog = Reminders(_Bot(db, [_Guild(members={501: member})]))
            assert await cog._notify_overdue_tools(G1) == 1
            assert len(member.dms) == 1
            assert "/tool return" in member.dms[0]

            assert await cog._notify_overdue_tools(G1) == 0, "毎朝送っている"
            assert len(member.dms) == 1
        finally:
            await db.close()

    run(_main())


def test_a_forbidden_dm_is_not_retried():
    async def _main():
        db = await _make_db()
        try:
            loan_id = await _overdue_loan(db)
            cog = Reminders(_Bot(db, [_Guild(members={501: _Member(501, discord.Forbidden)})]))
            await cog._notify_overdue_tools(G1)
            (loan,) = await ToolRepository(db).list_open_loans(G1)
            assert loan["loan_id"] == loan_id
            assert loan["overdue_notified_flag"] == 1, "拒否されたのに毎朝試そうとしている"
        finally:
            await db.close()

    run(_main())


def test_a_transient_dm_failure_is_retried():
    async def _main():
        db = await _make_db()
        try:
            await _overdue_loan(db)
            flaky = _Member(501, discord.HTTPException)
            cog = Reminders(_Bot(db, [_Guild(members={501: flaky})]))
            await cog._notify_overdue_tools(G1)
            (loan,) = await ToolRepository(db).list_open_loans(G1)
            assert loan["overdue_notified_flag"] == 0, "一時障害を送信済みにしている"

            flaky.fail = None
            assert await cog._notify_overdue_tools(G1) == 1
        finally:
            await db.close()

    run(_main())


def test_a_missing_member_keeps_the_loan_chaseable():
    async def _main():
        db = await _make_db()
        try:
            await _overdue_loan(db)
            cog = Reminders(_Bot(db, [_Guild(members={})]))
            assert await cog._notify_overdue_tools(G1) == 0
            (loan,) = await ToolRepository(db).list_open_loans(G1)
            assert loan["overdue_notified_flag"] == 0, "届いていないのに送信済みにしている"
        finally:
            await db.close()

    run(_main())


def test_nothing_is_sent_when_nothing_is_overdue():
    async def _main():
        db = await _make_db()
        try:
            repo = ToolRepository(db)
            tool_id = await repo.add_tool(G1, "トルクレンチ", "u1", to_iso(now()), None)
            await repo.borrow(G1, tool_id, "501", to_iso(now()), "2099-01-01", None)
            member = _Member(501)
            cog = Reminders(_Bot(db, [_Guild(members={501: member})]))
            assert await cog._notify_overdue_tools(G1) == 0
            assert member.dms == []
        finally:
            await db.close()

    run(_main())


def test_the_overdue_job_is_registered_in_the_daily_loop():
    import inspect

    source = inspect.getsource(Reminders.daily_morning.coro)
    assert "_notify_overdue_tools" in source, "朝のジョブ一覧に入っていない"
