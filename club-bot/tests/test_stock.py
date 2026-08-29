"""資材・消耗品の在庫（`/stock`。スキーマ v19）のテスト（G4-8）。

人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。

このファイルが特に固定しているもの:

1. **閾値未設定の品目を「閾値割れではない」と決めつけない**（ADR 0021）。
   `threshold` は NULL 許容で、0 を既定値にしない
   （0 にすると「在庫0でも閾値割れではない」という嘘になる）
2. **即時通知は割り込むたびに1回だけ。** 割れたまま使い続けても連投しない。
   閾値以上へ戻れば、次に割ったときにまた1回飛ぶ
3. **朝の通知は割れが無い日には出さない**（ADR 0023）
4. **品目名の初期値をコードにも DDL にも持たない**（サークルごとに違う）
5. **在庫は負にならない**（-3 本という物理的にありえない値を作らない）
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

import discord

from cogs.inventory import Inventory
from cogs.reminders import Reminders
from repositories.settings_repository import SettingsRepository
from repositories.stock_repository import StockRepository
from services.stock_service import crossed_below, format_amount, is_low, low_items
from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database
from utils.parser import now, to_iso
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


# =====================================================================
# 1. スキーマ
# =====================================================================
def test_schema_version_is_19():
    assert SCHEMA_VERSION >= 19


def test_both_tables_are_declared_for_both_drivers():
    for name in ("stock_items", "stock_movements"):
        for ddl in (TABLE_DDL[name], TABLE_DDL_PG[name]):
            assert name in ddl


def test_the_threshold_is_nullable_and_has_no_default():
    """ADR 0021: 閾値未設定を 0 に丸めない。"""
    ddl = TABLE_DDL["stock_items"]
    m = re.search(r"threshold\s+REAL([^,\n]*)", ddl)
    assert m, "threshold 列が無い"
    assert "NOT NULL" not in m.group(1)
    assert "DEFAULT" not in m.group(1), "閾値に既定値を置いている"


def test_item_names_are_unique_per_guild():
    assert re.search(r"UNIQUE\s*\(guild_id,\s*item_name\)", TABLE_DDL["stock_items"])


def test_no_initial_items_are_seeded():
    """品目名の初期値をコードに持たない（サークルごとに違う）。"""

    async def _main():
        db = await _make_db()
        try:
            assert await db.fetchall("SELECT * FROM stock_items") == []
        finally:
            await db.close()

    run(_main())


def test_migrating_an_old_db_adds_empty_tables_without_touching_data():
    async def _main():
        path = _tmp_db_path()
        conn = sqlite3.connect(path)
        try:
            for name, ddl in TABLE_DDL.items():
                if name in ("stock_items", "stock_movements"):
                    continue
                conn.executescript(ddl)
            conn.execute(
                "INSERT INTO members (guild_id, user_id, display_name, joined_at)"
                " VALUES (?, 'u1', 'たろう', '2026-01-01')",
                (G1,),
            )
            conn.execute("PRAGMA user_version = 18")
            conn.commit()
        finally:
            conn.close()

        db = Database(path)
        await db.connect()
        try:
            assert (await db.fetchone("PRAGMA user_version"))[0] == SCHEMA_VERSION
            assert await db.fetchall("SELECT * FROM stock_items") == []
            member = await db.fetchone("SELECT * FROM members WHERE user_id = 'u1'")
            assert member["display_name"] == "たろう", "既存行が書き換わっている"
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. 判定（純関数）
# =====================================================================
def test_no_threshold_means_no_judgement():
    assert is_low(0, None) is False, "閾値未設定の品目を閾値割れにしている"
    assert is_low(None, 5) is False


def test_the_threshold_itself_counts_as_low():
    """「残り1本になったら発注」は、1本の時点で知らせないと納期に間に合わない。"""
    assert is_low(1, 1) is True
    assert is_low(1.1, 1) is False


def test_crossing_is_only_the_transition():
    assert crossed_below(2, 1, 1) is True
    assert crossed_below(1, 0, 1) is False, "既に割れている状態を再度の割り込みにしている"
    assert crossed_below(0, 2, 1) is False


def test_low_items_are_sorted_by_how_little_slack_is_left():
    items = [
        {"item_name": "b", "quantity": 1, "threshold": 5},
        {"item_name": "a", "quantity": 4, "threshold": 5},
        {"item_name": "c", "quantity": 9, "threshold": 5},
        {"item_name": "d", "quantity": 0, "threshold": None},
    ]
    assert [i["item_name"] for i in low_items(items)] == ["b", "a"]


def test_format_amount_hides_the_decimal_point_for_whole_numbers():
    assert format_amount(3.0, "m") == "3m"
    assert format_amount(2.5, "m") == "2.5m"
    assert format_amount(None) == "—"


# =====================================================================
# 3. リポジトリ
# =====================================================================
def test_quantity_never_goes_below_zero():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            item_id = await repo.create_item(G1, "プリプレグ", "m", 2.0, "u1", now_text)
            await repo.apply_movement(G1, item_id, -5.0, "u1", now_text)
            item = await repo.get_item(G1, "プリプレグ")
            assert item["quantity"] == 0.0, "在庫が負になっている"
            # 履歴には申告どおりの値が残る（丸めた事実を消さない）
            (movement,) = await repo.list_movements(G1, item_id)
            assert movement["delta"] == -5.0
        finally:
            await db.close()

    run(_main())


def test_items_are_scoped_to_the_guild():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", now_text)
            await repo.create_item(G2, "他大の資材", "個", 3.0, "u9", now_text)
            assert [i["item_name"] for i in await repo.list_items(G1)] == ["プリプレグ"]
            assert await repo.get_item(G1, "他大の資材") is None
        finally:
            await db.close()

    run(_main())


def test_the_same_name_can_exist_in_two_guilds():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", now_text)
            await repo.create_item(G2, "プリプレグ", "m", 1.0, "u9", now_text)
            assert (await repo.get_item(G1, "プリプレグ"))["quantity"] == 10.0
            assert (await repo.get_item(G2, "プリプレグ"))["quantity"] == 1.0
        finally:
            await db.close()

    run(_main())


def test_deactivating_keeps_the_row_and_the_movements():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            item_id = await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", now_text)
            await repo.apply_movement(G1, item_id, -1.0, "u1", now_text)
            assert await repo.deactivate_item(G1, "プリプレグ") is True
            assert await repo.list_items(G1) == []
            assert len(await repo.list_items(G1, active_only=False)) == 1
            assert len(await repo.list_movements(G1, item_id)) == 1, "履歴まで消している"
        finally:
            await db.close()

    run(_main())


def test_re_adding_a_deactivated_item_reactivates_it():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", now_text)
            await repo.deactivate_item(G1, "プリプレグ")
            await repo.create_item(G1, "プリプレグ", "m", 5.0, "u1", now_text)
            item = await repo.get_item(G1, "プリプレグ")
            assert item["active_flag"] == 1
            assert item["quantity"] == 15.0
        finally:
            await db.close()

    run(_main())


def test_a_threshold_can_be_cleared():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            now_text = to_iso(now())
            await repo.create_item(G1, "プリプレグ", "m", 1.0, "u1", now_text, threshold=5.0)
            assert is_low((await repo.get_item(G1, "プリプレグ"))["quantity"], 5.0)
            await repo.set_threshold(G1, "プリプレグ", None, now_text)
            item = await repo.get_item(G1, "プリプレグ")
            assert item["threshold"] is None
            assert is_low(item["quantity"], item["threshold"]) is False
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. コマンドと通知
# =====================================================================
class _Channel:
    def __init__(self, fail: type | None = None):
        self.id = 777
        self.fail = fail
        self.sent: list[dict] = []

    async def send(self, content=None, *, embed=None, **kwargs):
        if self.fail is discord.Forbidden:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="no"), "denied")
        if self.fail is discord.HTTPException:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")
        self.sent.append({"content": content, "embed": embed})
        return SimpleNamespace(id=1)


class _Guild:
    def __init__(self, guild_id: int = G1, channel=None):
        self.id = guild_id
        self.name = str(guild_id)
        self._channel = channel

    def get_channel(self, _cid):
        return self._channel


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
    def __init__(self, guild=None):
        self.guild = guild if guild is not None else _Guild()
        self.user = SimpleNamespace(
            id=501,
            display_name="tester",
            guild=SimpleNamespace(owner_id=501),
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


def _cog(db: Database, guild=None) -> Inventory:
    return Inventory(_Bot(db, [guild] if guild else []))


async def _announce_channel(db: Database, guild_id: int = G1) -> None:
    await SettingsRepository(db).set(guild_id, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")


def test_permission_levels():
    assert command_required_level(Inventory.stock_list) == Level.L1
    assert command_required_level(Inventory.stock_use) == Level.L1
    assert command_required_level(Inventory.stock_add) == Level.L2
    assert command_required_level(Inventory.stock_set_threshold) == Level.L2
    assert command_required_level(Inventory.stock_remove) == Level.L2


def test_the_cog_is_registered():
    from bot import COGS

    assert "cogs.inventory" in COGS


def test_add_then_list_shows_the_item():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            interaction = _Interaction()
            await Inventory.stock_add.callback(
                cog, interaction, item="プリプレグ", amount=10.0, unit="m"
            )
            listing = _Interaction()
            await Inventory.stock_list.callback(cog, listing)
            assert "プリプレグ" in listing.text
            assert "10m" in listing.text
            assert "閾値 未設定" in listing.text, "閾値未設定を 0 と表示している"
        finally:
            await db.close()

    run(_main())


def test_list_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Inventory.stock_list.callback(_cog(db), interaction)
            assert "`/stock add`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_crossing_the_threshold_announces_once():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            channel = _Channel()
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            repo = StockRepository(db)
            await repo.create_item(
                G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0
            )

            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=6.0
            )
            assert len(channel.sent) == 1, "閾値を割ったのに告知していない"

            # 割れたまま使い続けても連投しない
            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=1.0
            )
            assert len(channel.sent) == 1, "割れたまま使うたびに告知している"
        finally:
            await db.close()

    run(_main())


def test_no_announcement_before_the_threshold_is_crossed():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            channel = _Channel()
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            await StockRepository(db).create_item(
                G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0
            )
            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=1.0
            )
            assert channel.sent == []
        finally:
            await db.close()

    run(_main())


def test_an_item_without_a_threshold_never_announces():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            channel = _Channel()
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            await StockRepository(db).create_item(G1, "ウエス", "枚", 3.0, "u1", to_iso(now()))
            await Inventory.stock_use.callback(cog, _Interaction(guild), item="ウエス", amount=3.0)
            assert channel.sent == [], "閾値未設定の品目で告知している"
        finally:
            await db.close()

    run(_main())


def test_restocking_above_the_threshold_rearms_the_announcement():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            channel = _Channel()
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            repo = StockRepository(db)
            await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0)

            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=6.0
            )
            assert len(channel.sent) == 1
            await Inventory.stock_add.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=20.0
            )
            assert (await repo.get_item(G1, "プリプレグ"))["low_notified_flag"] == 0
            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=21.0
            )
            assert len(channel.sent) == 2, "入荷後に割り直しても告知していない"
        finally:
            await db.close()

    run(_main())


def test_a_failed_announcement_is_not_marked_as_sent():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            channel = _Channel(fail=discord.HTTPException)
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            repo = StockRepository(db)
            await repo.create_item(G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0)
            await Inventory.stock_use.callback(
                cog, _Interaction(guild), item="プリプレグ", amount=6.0
            )
            assert (await repo.get_item(G1, "プリプレグ"))["low_notified_flag"] == 0, (
                "送れていないのに通知済みにしている"
            )
        finally:
            await db.close()

    run(_main())


def test_no_channel_keeps_members_silent_but_tells_the_operator():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db, _Guild(channel=None))
            await StockRepository(db).create_item(
                G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0
            )
            await Inventory.stock_use.callback(
                cog, _Interaction(_Guild(channel=None)), item="プリプレグ", amount=6.0
            )
            assert cog.bot.logged, "運用者にも見えないまま届いていない"
        finally:
            await db.close()

    run(_main())


def test_using_more_than_the_stock_says_so():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            await StockRepository(db).create_item(G1, "ウエス", "枚", 2.0, "u1", to_iso(now()))
            interaction = _Interaction()
            await Inventory.stock_use.callback(cog, interaction, item="ウエス", amount=5.0)
            assert "上回っています" in interaction.text, "黙って 0 に丸めている"
        finally:
            await db.close()

    run(_main())


def test_using_an_unknown_item_is_refused():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Inventory.stock_use.callback(_cog(db), interaction, item="無い物", amount=1.0)
            assert "登録されていません" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_a_negative_threshold_clears_it():
    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            await repo.create_item(G1, "プリプレグ", "m", 1.0, "u1", to_iso(now()), threshold=5.0)
            await Inventory.stock_set_threshold.callback(
                _cog(db), _Interaction(), item="プリプレグ", threshold=-1
            )
            assert (await repo.get_item(G1, "プリプレグ"))["threshold"] is None
        finally:
            await db.close()

    run(_main())


def test_a_zero_threshold_is_a_real_threshold():
    """0 は「解除」ではなく「尽きたら知らせる」。"""

    async def _main():
        db = await _make_db()
        try:
            repo = StockRepository(db)
            await repo.create_item(G1, "ウエス", "枚", 3.0, "u1", to_iso(now()))
            await Inventory.stock_set_threshold.callback(
                _cog(db), _Interaction(), item="ウエス", threshold=0
            )
            assert (await repo.get_item(G1, "ウエス"))["threshold"] == 0.0
        finally:
            await db.close()

    run(_main())


def test_item_autocomplete_is_registered_on_every_command_that_takes_one():
    for command in (
        Inventory.stock_add,
        Inventory.stock_use,
        Inventory.stock_set_threshold,
        Inventory.stock_remove,
    ):
        param = command._params["item"]
        assert param.autocomplete is not None, command.name
        assert param.autocomplete.__name__ == "_item_autocomplete", command.name


# =====================================================================
# 5. 朝の通知
# =====================================================================
def _reminders(db, guilds) -> Reminders:
    return Reminders(_Bot(db, guilds))


def test_the_morning_notice_is_silent_when_nothing_is_low():
    """ADR 0023: 言うことが無い日は黙る。"""

    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            await StockRepository(db).create_item(
                G1, "プリプレグ", "m", 10.0, "u1", to_iso(now()), threshold=5.0
            )
            channel = _Channel()
            cog = _reminders(db, [_Guild(channel=channel)])
            assert await cog._notify_low_stock(G1) == 0
            assert channel.sent == []
        finally:
            await db.close()

    run(_main())


def test_the_morning_notice_lists_low_items_in_one_message():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db)
            repo = StockRepository(db)
            now_text = to_iso(now())
            await repo.create_item(G1, "プリプレグ", "m", 1.0, "u1", now_text, threshold=5.0)
            await repo.create_item(G1, "エポキシ", "kg", 0.2, "u1", now_text, threshold=1.0)
            await repo.create_item(G1, "ウエス", "枚", 100.0, "u1", now_text, threshold=10.0)
            channel = _Channel()
            cog = _reminders(db, [_Guild(channel=channel)])
            assert await cog._notify_low_stock(G1) == 2
            assert len(channel.sent) == 1, "品目ごとに連投している"
            body = channel.sent[0]["embed"].description
            assert "プリプレグ" in body and "エポキシ" in body
            assert "ウエス" not in body
        finally:
            await db.close()

    run(_main())


def test_the_morning_notice_is_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            await _announce_channel(db, G2)
            await StockRepository(db).create_item(
                G2, "他大のプリプレグ", "m", 1.0, "u9", to_iso(now()), threshold=5.0
            )
            channel = _Channel()
            cog = _reminders(db, [_Guild(G1, channel), _Guild(G2, _Channel())])
            assert await cog._notify_low_stock(G1) == 0
            assert channel.sent == []
        finally:
            await db.close()

    run(_main())


def test_the_morning_job_is_registered_in_the_daily_loop():
    import inspect

    source = inspect.getsource(Reminders.daily_morning.coro)
    assert "_notify_low_stock" in source, "朝のジョブ一覧に入っていない"
