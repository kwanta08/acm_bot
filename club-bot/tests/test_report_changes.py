"""`/report changes` と読み取り専用テーブルの追加のテスト（G4-3）。

`AuditLogRepository.list_recent` を呼ぶコードが bot 側に1つも無く、
`audit_log` は**書かれ続けているのに誰も読めない**状態だった。
`/report audit` が読んでいたのは `reminders_log`（bot が送った通知）で別物。

このファイルが特に固定しているもの:

1. **`/report changes` が読むのは `audit_log`、`/report notifications` が
   読むのは `reminders_log`。** 片方が両方を読んでいると、名前は直っても
   「どちらのログも同じ内容が出る」という元の混乱に戻る
2. **`settings` 表は L4 未満に見せない。** `GET /settings` がロール ID の
   実値を L4 にだけ返している（G1-6）のに、表グリッド経由で L1 に
   見えるのでは意味がない
3. **必要レベルは `TableSpec` が持つ**（ルータの if ではない）。
   表を足すときに書き忘れても既定は L1 なので、
   「機密なのに L1 で見える」表を足したらここが落ちるようにする
4. **認証情報のテーブル・列は今もホワイトリスト外**
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

import pytest

from cogs.reports import Reports
from repositories.audit_log_repository import AuditLogRepository
from repositories.name_cache_repository import ENTITY_USER, NameCacheRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.table_repository import TABLES, TableRepository
from utils.db import Database
from utils.parser import now, to_iso
from utils.permissions import Level, command_required_level

G1 = 100000000000000001
G2 = 200000000000000002

#: 秘密情報が入りうる列名の断片。ホワイトリストに現れてはいけない
SECRET_HINTS = ("token", "secret", "password", "credential", "api_key", "encrypted")


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
# 1. ホワイトリスト（構造的な保証）
# =====================================================================
def test_the_previously_unreachable_tables_are_now_in_the_whitelist():
    for key in ("audit_log", "seasons", "progress_milestones", "layer_keta"):
        assert key in TABLES, f"{key} が export・ダッシュボードから出せないまま"


def test_the_new_tables_are_read_only():
    """正本の入口は Discord コマンド側。表グリッドから直せる列を作らない。"""
    for key in (
        "audit_log",
        "seasons",
        "progress_milestones",
        "layer_keta",
        "settings",
    ):
        assert TABLES[key].editable_columns == (), f"{key} に編集可能な列がある"


def test_no_table_exposes_a_secret_looking_column():
    for spec in TABLES.values():
        for column in spec.columns:
            lowered = column.name.lower()
            assert not any(hint in lowered for hint in SECRET_HINTS), (
                f"{spec.key}.{column.name} は秘密情報を含みうる列名"
            )


def test_credential_tables_stay_out_of_the_whitelist():
    tables = {spec.table for spec in TABLES.values()}
    assert "todoist_configs" not in tables
    assert "guilds" not in tables


def test_settings_requires_admin_to_view():
    """ロール ID の実値を L4 にだけ返す `GET /settings` と揃える（G1-6）。"""
    assert TABLES["settings"].min_level == int(Level.L4)


def test_the_audit_log_table_requires_l3():
    """`/report changes` と同じレベル。画面と Discord で食い違わせない。"""
    assert TABLES["audit_log"].min_level == int(Level.L3)


def test_the_ordinary_tables_stay_visible_to_everyone():
    for key in ("members", "teams", "schedules", "layer_records", "progress"):
        assert TABLES[key].min_level == 1, f"{key} の閲覧レベルを上げてしまっている"


def test_every_table_can_be_listed_and_is_guild_scoped():
    """新しい表も他ギルドの行を返さないこと。"""

    async def _main():
        db = await _make_db()
        try:
            for guild_id, actor in ((G1, "A大学の幹部"), (G2, "B大学の幹部")):
                await AuditLogRepository(db).record(guild_id, actor, "setup", None, actor)
            repo = TableRepository(db)
            rows_a = await repo.list_rows(G1, "audit_log")
            assert [r["actor_id"] for r in rows_a] == ["A大学の幹部"]
            assert "B大学" not in str(rows_a)
        finally:
            await db.close()

    run(_main())


def test_settings_rows_are_scoped_by_setting_key():
    async def _main():
        db = await _make_db()
        try:
            await db.set_setting(G1, "CLUB_NAME", "A大学")
            await db.set_setting(G2, "CLUB_NAME", "B大学")
            repo = TableRepository(db)
            rows = await repo.list_rows(G1, "settings")
            assert [(r["setting_key"], r["setting_value"]) for r in rows] == [
                ("CLUB_NAME", "A大学")
            ]
            row = await repo.get_row(G1, "settings", "CLUB_NAME")
            assert row is not None and row["setting_value"] == "A大学"
            assert await repo.get_row(G1, "settings", "NOT_SET") is None
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. リポジトリ（絞り込みと候補）
# =====================================================================
async def _seed_audit(db: Database) -> AuditLogRepository:
    repo = AuditLogRepository(db)
    await repo.record(G1, "501", "setup.save", "CLUB_NAME", "A大学へ変更")
    await repo.record(G1, "502", "team.add", "主翼班", None)
    await repo.record(G1, "501", "dashboard.update", "members#1", "display_name を変更")
    await repo.record(G2, "999", "setup.save", "CLUB_NAME", "B大学へ変更")
    return repo


def test_list_recent_can_filter_by_actor():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_audit(db)
            rows = await repo.list_recent(G1, 10, actor_id="501")
            assert [r["action"] for r in rows] == ["dashboard.update", "setup.save"]
        finally:
            await db.close()

    run(_main())


def test_list_recent_does_not_cross_guilds_even_with_a_matching_actor():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_audit(db)
            await repo.record(G2, "501", "setup.save", "CLUB_NAME", "他ギルドでの操作")
            rows = await repo.list_recent(G1, 10, actor_id="501")
            assert all("他ギルド" not in str(r.get("detail") or "") for r in rows)
            assert len(rows) == 2
        finally:
            await db.close()

    run(_main())


def test_list_actors_returns_each_actor_once_newest_first():
    async def _main():
        db = await _make_db()
        try:
            repo = await _seed_audit(db)
            assert await repo.list_actors(G1) == ["501", "502"]
            assert await repo.list_actors(G2) == ["999"]
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 3. コマンド
# =====================================================================
class _Guild:
    def __init__(self, members: dict[int, str] | None = None):
        self.id = G1
        self._members = members or {}

    def get_member(self, user_id: int):
        name = self._members.get(user_id)
        return SimpleNamespace(display_name=name) if name else None


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
        self.namespace = SimpleNamespace()

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


def _cog(db: Database) -> Reports:
    return Reports(SimpleNamespace(db=db, guilds=[], user=None))


def test_changes_is_level_3():
    assert command_required_level(Reports.changes) == Level.L3


def test_notifications_kept_the_level_of_the_old_audit_command():
    assert command_required_level(Reports.notifications) == Level.L3


def test_the_old_audit_command_is_gone():
    assert not hasattr(Reports, "audit"), "/report audit が残っている（改名していない）"


def test_changes_shows_the_audit_log_not_the_reminders_log():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            # 同じギルドに通知ログも入れておく。混ざったら分かるようにする
            await RemindersLogRepository(db).add(
                G1, "schedule_unanswered", "sch_1", None, None, "success"
            )
            cog = _cog(db)
            interaction = _Interaction()
            await Reports.changes.callback(cog, interaction, limit=10, actor=None)
            text = interaction.text
            assert "setup.save" in text
            assert "schedule_unanswered" not in text, "通知ログが混ざっている"
        finally:
            await db.close()

    run(_main())


def test_notifications_shows_the_reminders_log_not_the_audit_log():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            await RemindersLogRepository(db).add(
                G1, "schedule_unanswered", "sch_1", None, None, "success"
            )
            cog = _cog(db)
            interaction = _Interaction()
            await Reports.notifications.callback(cog, interaction, limit=10)
            text = interaction.text
            assert "schedule_unanswered" in text
            assert "setup.save" not in text, "操作ログが混ざっている"
        finally:
            await db.close()

    run(_main())


def test_changes_resolves_the_actor_to_a_display_name():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            await NameCacheRepository(db).upsert(G1, ENTITY_USER, "502", "はなこ", to_iso(now()))
            cog = _cog(db)
            # 501 はギルドキャッシュから、502 は discord_name_cache から
            interaction = _Interaction(guild=_Guild({501: "たろう"}))
            await Reports.changes.callback(cog, interaction, limit=10, actor=None)
            text = interaction.text
            assert "たろう" in text, "ギルドキャッシュから解決していない"
            assert "はなこ" in text, "discord_name_cache から解決していない"
        finally:
            await db.close()

    run(_main())


def test_changes_can_filter_by_actor():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            cog = _cog(db)
            interaction = _Interaction()
            await Reports.changes.callback(cog, interaction, limit=10, actor="502")
            text = interaction.text
            assert "team.add" in text
            assert "setup.save" not in text
        finally:
            await db.close()

    run(_main())


def test_changes_shows_an_empty_state_with_a_next_command():
    async def _main():
        db = await _make_db()
        try:
            cog = _cog(db)
            interaction = _Interaction()
            await Reports.changes.callback(cog, interaction, limit=10, actor=None)
            assert "`/setup`" in interaction.text
        finally:
            await db.close()

    run(_main())


def test_changes_does_not_leak_another_guilds_log():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            cog = _cog(db)
            interaction = _Interaction()
            await Reports.changes.callback(cog, interaction, limit=25, actor=None)
            assert "B大学" not in interaction.text
        finally:
            await db.close()

    run(_main())


def test_actor_autocomplete_is_registered_on_changes():
    """G4-13 と同じ書き方。登録行を消したら落ちること。"""
    param = Reports.changes._params["actor"]
    assert param.autocomplete is not None
    assert param.autocomplete.__name__ == "_actor_autocomplete"


def test_actor_autocomplete_offers_only_actors_that_appear_in_the_log():
    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            await NameCacheRepository(db).upsert(G1, ENTITY_USER, "502", "はなこ", to_iso(now()))
            cog = _cog(db)
            interaction = _Interaction(guild=_Guild({501: "たろう"}))
            choices = await Reports._actor_autocomplete(cog, interaction, "")
            assert {c.value for c in choices} == {"501", "502"}
            assert {c.name for c in choices} == {"たろう", "はなこ"}

            filtered = await Reports._actor_autocomplete(cog, interaction, "はな")
            assert [c.value for c in filtered] == ["502"]
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 4. エクスポート
# =====================================================================
def test_export_zip_contains_the_new_tables():
    from cogs.data import build_export_zip

    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            await db.set_setting(G1, "CLUB_NAME", "A大学")
            data, counts = await build_export_zip(db, G1)
            assert counts["audit_log"] == 3, "操作ログが ZIP に入っていない"
            assert counts["settings"] == 1
            for key in (
                "audit_log",
                "seasons",
                "progress_milestones",
                "layer_keta",
                "settings",
            ):
                assert key in counts
            assert isinstance(data, bytes) and data
        finally:
            await db.close()

    run(_main())


def test_export_zip_never_carries_guild_id():
    from cogs.data import build_export_zip

    async def _main():
        db = await _make_db()
        try:
            await _seed_audit(db)
            _, counts = await build_export_zip(db, G1)
            assert counts  # 空振り防止
            for spec in TABLES.values():
                assert "guild_id" not in spec.column_names, spec.key
        finally:
            await db.close()

    run(_main())


def test_unknown_table_still_raises():
    from repositories.table_repository import UnknownTableError

    async def _main():
        db = await _make_db()
        try:
            repo = TableRepository(db)
            with pytest.raises(UnknownTableError):
                await repo.list_rows(G1, "todoist_configs")
        finally:
            await db.close()

    run(_main())
