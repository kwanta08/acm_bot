"""ヒヤリハット・事故報告（`/incident`。スキーマ v21）のテスト（G4-10）。

工房での切削・溶剤・高所作業・機体運搬・テストフライトと危険度が高く、
大学から安全管理体制の提示を求められることもある。
今は「危なかった」が雑談チャンネルに流れて消えている。

このファイルが特に固定しているもの:

1. **匿名報告で報告者名が Embed に出ないこと**（受入基準の中心）。
   一覧・通知の**両方**を見る。片方だけ検査すると、もう片方から漏れる
2. **匿名の約束を表示側の if ではなくデータ形状で守っていること。**
   `reporter_name` は匿名報告では NULL で保存され、取得系は
   `reporter_id` を返さない。エクスポートの列一覧にも入っていない
3. **報告者 ID は匿名でも保存されること**（虚偽・悪用への対処に要る）。
   隠された保持をしないよう docs/PRIVACY.md に明記してある
4. **Modal のエラーログに報告の中身を出さないこと**
"""

from __future__ import annotations

import asyncio
import inspect
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

from cogs.safety import ANONYMOUS_LABEL, IncidentModal, Safety, build_incident_embed
from config import config
from repositories.incident_repository import DISPLAY_COLUMNS, IncidentRepository
from repositories.settings_repository import SettingsRepository
from repositories.table_repository import TABLES
from utils.db import SCHEMA_VERSION, TABLE_DDL, TABLE_DDL_PG, Database
from utils.parser import now, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002
REPORTER_ID = "501"
REPORTER_NAME = "たろう"


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


async def _report(db: Database, *, anonymous: bool, guild_id: int = G1) -> int:
    return await IncidentRepository(db).report(
        guild_id,
        occurred_at="2026-08-29 15:30",
        place="工房のボール盤の前",
        description="切粉が目に入りかけた",
        injury="無し",
        prevention="保護メガネを常備する",
        reporter_id=REPORTER_ID,
        reporter_name=REPORTER_NAME,
        anonymous=anonymous,
        created_at=to_iso(now()),
    )


def _embed_text(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{f.name}\n{f.value}" for f in embed.fields]
    return "\n".join(parts)


# =====================================================================
# 1. スキーマ
# =====================================================================
def test_schema_version_is_21():
    assert SCHEMA_VERSION >= 21


def test_the_table_is_declared_for_both_drivers():
    for ddl in (TABLE_DDL["incidents"], TABLE_DDL_PG["incidents"]):
        assert "incidents" in ddl


def test_the_reporter_name_is_nullable_but_the_id_is_not():
    """匿名報告では名前が NULL。ID は必ず残す（虚偽・悪用への対処）。"""
    ddl = TABLE_DDL["incidents"]
    name = re.search(r"reporter_name\s+TEXT([^,\n]*)", ddl)
    assert name and "NOT NULL" not in name.group(1)
    ident = re.search(r"reporter_id\s+TEXT([^,\n]*)", ddl)
    assert ident and "NOT NULL" in ident.group(1)


def test_migrating_a_v20_db_adds_an_empty_table_without_touching_data():
    async def _main():
        path = _tmp_db_path()
        conn = sqlite3.connect(path)
        try:
            for name, ddl in TABLE_DDL.items():
                if name == "incidents":
                    continue
                conn.executescript(ddl)
            conn.execute(
                "INSERT INTO tools (guild_id, tool_name, created_by, created_at)"
                " VALUES (?, 'トルクレンチ', 'u1', '2026-01-01')",
                (G1,),
            )
            conn.execute("PRAGMA user_version = 20")
            conn.commit()
        finally:
            conn.close()

        db = Database(path)
        await db.connect()
        try:
            assert (await db.fetchone("PRAGMA user_version"))[0] == SCHEMA_VERSION
            assert await db.fetchall("SELECT * FROM incidents") == []
            tool = await db.fetchone("SELECT * FROM tools WHERE tool_name = 'トルクレンチ'")
            assert tool is not None, "既存行が消えている"
        finally:
            await db.close()

    run(_main())


def test_the_incident_migration_is_its_own_version():
    from utils.db import Database as DB

    v20 = inspect.getsource(DB._migrate_v20_tools)
    v21 = inspect.getsource(DB._migrate_v21_incidents)
    assert "incidents" not in v20, "v20 に incidents を混ぜている"
    assert "incidents" in v21


# =====================================================================
# 2. 匿名の扱い（データ形状で守る）
# =====================================================================
def test_an_anonymous_report_stores_the_id_but_not_the_name():
    async def _main():
        db = await _make_db()
        try:
            incident_id = await _report(db, anonymous=True)
            raw = await db.fetchone(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            )
            assert raw["reporter_id"] == REPORTER_ID, "報告者 ID を保存していない"
            assert raw["reporter_name"] is None, "匿名なのに名前を保存している"
            assert raw["anonymous_flag"] == 1
        finally:
            await db.close()

    run(_main())


def test_a_named_report_keeps_the_name():
    async def _main():
        db = await _make_db()
        try:
            incident_id = await _report(db, anonymous=False)
            raw = await db.fetchone(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            )
            assert raw["reporter_name"] == REPORTER_NAME
            assert raw["anonymous_flag"] == 0
        finally:
            await db.close()

    run(_main())


def test_the_repository_never_returns_the_reporter_id():
    """表示層に「うっかり出す」経路そのものを作らない。"""
    assert "reporter_id" not in DISPLAY_COLUMNS

    async def _main():
        db = await _make_db()
        try:
            await _report(db, anonymous=False)
            (row,) = await IncidentRepository(db).list_recent(G1)
            assert "reporter_id" not in row
            single = await IncidentRepository(db).get(G1, int(row["incident_id"]))
            assert "reporter_id" not in single
        finally:
            await db.close()

    run(_main())


def test_the_export_whitelist_excludes_the_reporter_id():
    """ダッシュボードと /data export に構造的に出ないこと（ADR 0016）。"""
    spec = TABLES["incidents"]
    assert "reporter_id" not in spec.column_names
    assert "reporter_name" in spec.column_names
    assert spec.editable_columns == (), "安全報告を書き換えられる"
    assert spec.min_level == int(Level.L3), "全員に見える表になっている"


def test_reports_are_guild_scoped():
    async def _main():
        db = await _make_db()
        try:
            await _report(db, anonymous=False, guild_id=G2)
            assert await IncidentRepository(db).list_recent(G1) == []
            assert await IncidentRepository(db).count(G1) == 0
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. 表示
# =====================================================================
def test_an_anonymous_report_shows_no_name_in_the_embed():
    async def _main():
        db = await _make_db()
        try:
            incident_id = await _report(db, anonymous=True)
            row = await IncidentRepository(db).get(G1, incident_id)
            text = _embed_text(build_incident_embed(row, title="報告"))
            assert REPORTER_NAME not in text, "匿名報告に報告者名が出ている"
            assert REPORTER_ID not in text, "匿名報告に報告者 ID が出ている"
            assert ANONYMOUS_LABEL in text
            # 中身は出る（匿名なのは報告者だけ）
            assert "切粉" in text
        finally:
            await db.close()

    run(_main())


def test_the_flag_wins_even_if_a_name_somehow_survived():
    """**二重の守り。** 保存側（NULL）と表示側（フラグ）の両方で匿名を守る。

    リポジトリ経由なら匿名報告の `reporter_name` は必ず NULL になるが、
    移行スクリプトや将来の別経路で名前が入った行が生まれても、
    表示は匿名のままであること。片方だけだと、片方が壊れた瞬間に漏れる。
    """
    row = {
        "description": "切粉が目に入りかけた",
        "anonymous_flag": 1,
        "reporter_name": REPORTER_NAME,
    }
    assert REPORTER_NAME not in _embed_text(build_incident_embed(row, title="報告"))
    assert ANONYMOUS_LABEL in _embed_text(build_incident_embed(row, title="報告"))


def test_a_named_report_shows_the_name():
    async def _main():
        db = await _make_db()
        try:
            incident_id = await _report(db, anonymous=False)
            row = await IncidentRepository(db).get(G1, incident_id)
            assert REPORTER_NAME in _embed_text(build_incident_embed(row, title="報告"))
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. コマンド
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
    def __init__(self, guild_id: int = G1, channel=None, role=None):
        self.id = guild_id
        self.name = str(guild_id)
        self._channel = channel
        self._role = role

    def get_channel(self, _cid):
        return self._channel

    def get_role(self, _rid):
        return self._role


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
    def __init__(self, guild=None, user_id: int = int(REPORTER_ID)):
        self.guild = guild if guild is not None else _Guild()
        self.user = SimpleNamespace(
            id=user_id,
            display_name=REPORTER_NAME,
            guild=SimpleNamespace(owner_id=user_id),
            roles=[],
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )
        self.sent: list[dict] = []
        self.modals: list = []
        self.response = SimpleNamespace(
            defer=self._noop,
            send_modal=self._send_modal,
            send_message=self._send,
            is_done=lambda: True,
        )
        self.followup = SimpleNamespace(send=self._send)

    async def _noop(self, *args, **kwargs):
        return None

    async def _send_modal(self, modal):
        self.modals.append(modal)

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def text(self) -> str:
        return _embed_text(self.sent[-1]["embed"])


def _cog(db: Database, guild=None) -> Safety:
    return Safety(_Bot(db, [guild] if guild else []))


def test_permission_levels():
    assert command_required_level(Safety.incident_report) == Level.L1
    assert command_required_level(Safety.incident_list) == Level.L3


def test_the_cog_is_registered():
    from bot import COGS

    assert "cogs.safety" in COGS


def test_report_opens_a_modal_with_the_five_fields():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Safety.incident_report.callback(_cog(db), interaction, anonymous=True)
            (modal,) = interaction.modals
            labels = [child.label for child in modal.children]
            assert labels == ["発生日時", "場所", "何が起きたか", "けがの有無", "再発防止案（任意）"]
            assert modal.anonymous is True
        finally:
            await db.close()

    run(_main())


async def _submit(cog: Safety, interaction: _Interaction, *, anonymous: bool) -> None:
    await cog.save_report(
        interaction,
        guild_id=G1,
        occurred_at="2026-08-29 15:30",
        place="工房",
        description="切粉が目に入りかけた",
        injury="無し",
        prevention="保護メガネ",
        anonymous=anonymous,
    )


def test_an_anonymous_submission_never_names_the_reporter_in_the_notice():
    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            config.clear_guild_cache()
            channel = _Channel()
            guild = _Guild(channel=channel)
            cog = _cog(db, guild)
            interaction = _Interaction(guild)
            await _submit(cog, interaction, anonymous=True)

            assert len(channel.sent) == 1, "幹部へ通知していない"
            notice = _embed_text(channel.sent[0]["embed"])
            assert REPORTER_NAME not in notice, "通知に報告者名が出ている"
            assert REPORTER_ID not in notice
            assert ANONYMOUS_LABEL in notice
            assert "切粉" in notice
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_named_submission_names_the_reporter_in_the_notice():
    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            config.clear_guild_cache()
            channel = _Channel()
            guild = _Guild(channel=channel)
            await _submit(_cog(db, guild), _Interaction(guild), anonymous=False)
            assert REPORTER_NAME in _embed_text(channel.sent[0]["embed"])
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_notice_mentions_the_exec_role_when_set():
    async def _main():
        db = await _make_db()
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            await repo.set(G1, "EXEC_ROLE_ID", "900")
            config.clear_guild_cache()
            channel = _Channel()
            guild = _Guild(channel=channel, role=SimpleNamespace(mention="<@&900>"))
            await _submit(_cog(db, guild), _Interaction(guild), anonymous=False)
            assert channel.sent[0]["content"] == "<@&900>"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_the_report_is_saved_even_when_the_notice_cannot_be_sent():
    """通知先が無くても報告は残る（記録が本体、通知は付随）。"""

    async def _main():
        db = await _make_db()
        try:
            config.clear_guild_cache()
            guild = _Guild(channel=None)
            cog = _cog(db, guild)
            await _submit(cog, _Interaction(guild), anonymous=False)
            assert await IncidentRepository(db).count(G1) == 1
            assert cog.bot.logged, "運用者にも見えないまま共有できていない"
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_a_failed_notice_still_keeps_the_report():
    async def _main():
        db = await _make_db()
        try:
            await SettingsRepository(db).set(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "777")
            config.clear_guild_cache()
            guild = _Guild(channel=_Channel(fail=discord.HTTPException))
            cog = _cog(db, guild)
            await _submit(cog, _Interaction(guild), anonymous=False)
            assert await IncidentRepository(db).count(G1) == 1
        finally:
            config.clear_guild_cache()
            await db.close()

    run(_main())


def test_list_hides_the_name_of_anonymous_reports():
    async def _main():
        db = await _make_db()
        try:
            await _report(db, anonymous=True)
            interaction = _Interaction()
            await Safety.incident_list.callback(_cog(db), interaction, limit=10)
            text = interaction.text
            assert "切粉" in text
            assert REPORTER_NAME not in text, "一覧に報告者名が出ている"
            assert REPORTER_ID not in text
            assert ANONYMOUS_LABEL in text
        finally:
            await db.close()

    run(_main())


def test_list_shows_the_name_of_named_reports():
    async def _main():
        db = await _make_db()
        try:
            await _report(db, anonymous=False)
            interaction = _Interaction()
            await Safety.incident_list.callback(_cog(db), interaction, limit=10)
            assert REPORTER_NAME in interaction.text
        finally:
            await db.close()

    run(_main())


def test_list_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            interaction = _Interaction()
            await Safety.incident_list.callback(_cog(db), interaction, limit=10)
            assert "`/incident report`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_list_does_not_leak_another_guilds_reports():
    async def _main():
        db = await _make_db()
        try:
            await _report(db, anonymous=False, guild_id=G2)
            interaction = _Interaction()
            await Safety.incident_list.callback(_cog(db), interaction, limit=10)
            assert "切粉" not in interaction.text
        finally:
            await db.close()

    run(_main())


def test_the_modal_only_accepts_the_person_who_opened_it():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            modal = IncidentModal(cog, G1, owner_id=501, anonymous=False)
            other = _Interaction(user_id=999)
            await modal.on_submit(other)
            assert "実行者のみ" in other.text
            assert await IncidentRepository(db).count(G1) == 0
        finally:
            await db.close()

    run(_main())


def test_the_modal_error_handler_does_not_log_the_report_body():
    """匿名報告の内容が運用ログへ漏れないこと。"""
    source = inspect.getsource(IncidentModal.on_error)
    for field in ("description_input", "place_input", "injury_input", "prevention_input"):
        assert field not in source, f"{field} の値をログに出しうる"
    assert "type(error).__name__" in source
