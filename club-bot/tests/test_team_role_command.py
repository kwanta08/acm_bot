"""`/team-role` が 3 種類のロール列すべてを設定できることのテスト。

`teams.leader_role_id` は `/team-list` に表示されるのに、Discord からもダッシュボード
からも設定する手段が無い状態だった（`set_team_roles()` に引数が無く、`/team-role` の
choices も primary / secondary だけで、`upsert_team(leader_role_id=...)` を渡す
呼び出しも存在しない）。表示だけされて設定できない列を作らない。

班長ロールは **表示用**で、`_sync_roles()` の自動付与にも L2 判定にも使わない
（L2 の根拠は settings の `LEADER_ROLE_IDS`）。混同すると「設定したのに班長が
何もできない」になるため、案内文でそれを伝えることまでを固定する。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from cogs.teams import Teams
from repositories.audit_log_repository import AuditLogRepository
from repositories.member_repository import MemberRepository
from utils.db import Database

G1 = 100000000000000001
G2 = 200000000000000002


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


class _Role:
    """discord.Role の最小スタブ（id / name / mention だけ使う）。"""

    def __init__(self, role_id: int, name: str):
        self.id = role_id
        self.name = name
        self.mention = f"<@&{role_id}>"


class _Interaction:
    def __init__(self, guild_id: int = G1):
        self.user = SimpleNamespace(id=42, display_name="山田")
        self.guild = SimpleNamespace(id=guild_id)
        self.sent: list = []
        self.response = SimpleNamespace(defer=self._defer)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_description(self) -> str:
        return self.sent[-1]["embed"].description or ""


async def _make_cog() -> tuple[Teams, Database, MemberRepository]:
    db = Database(_tmp_db_path())
    await db.connect()
    cog = Teams(SimpleNamespace(db=db))
    repo = MemberRepository(db)
    await repo.upsert_team(G1, "wing", "翼班")
    return cog, db, repo


async def _call(cog: Teams, role_id: int, role_type: str, *, guild_id: int = G1):
    interaction = _Interaction(guild_id)
    await Teams.team_role.callback(
        cog, interaction, team="wing", role=_Role(role_id, f"role{role_id}"), role_type=role_type
    )
    return interaction


# ---------------------------------------------------------------------
# Discord 側に選択肢として出ていること
#
# ハンドラが "leader" を処理できても choices に無ければ、利用者は
# その値を選べない（コールバックを直接呼ぶテストだけでは気づけない）。
# ---------------------------------------------------------------------
def test_role_type_choices_include_all_three():
    role_type = next(p for p in Teams.team_role.parameters if p.name == "role_type")
    assert {c.value for c in role_type.choices} == {"primary", "secondary", "leader"}


# ---------------------------------------------------------------------
# 3 種別それぞれが対応する列だけを更新する
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("role_type", "column"),
    [
        ("primary", "member_role_id"),
        ("secondary", "secondary_role_id"),
        ("leader", "leader_role_id"),
    ],
)
def test_each_role_type_sets_its_own_column(role_type, column):
    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await _call(cog, 777, role_type)

            team = await repo.get_team(G1, "wing")
            assert team[column] == "777"
            # 指定していない列は触らない
            others = {"member_role_id", "secondary_role_id", "leader_role_id"} - {column}
            assert all(team[name] is None for name in others)
        finally:
            await db.close()

    run(_main())


def test_role_type_defaults_to_primary():
    """既定は primary（choices を経由しない呼び出しでも副所属に落ちない）。"""

    async def _main():
        cog, db, repo = await _make_cog()
        try:
            interaction = _Interaction()
            await Teams.team_role.callback(
                cog, interaction, team="wing", role=_Role(777, "翼班")
            )
            team = await repo.get_team(G1, "wing")
            assert team["member_role_id"] == "777"
            assert team["secondary_role_id"] is None
        finally:
            await db.close()

    run(_main())


def test_three_role_types_coexist():
    """3 種別を続けて設定しても互いを上書きしない。"""

    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await _call(cog, 111, "primary")
            await _call(cog, 222, "secondary")
            await _call(cog, 333, "leader")

            team = await repo.get_team(G1, "wing")
            assert team["member_role_id"] == "111"
            assert team["secondary_role_id"] == "222"
            assert team["leader_role_id"] == "333"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 班長ロールは権限ではない、と案内文で伝える
# ---------------------------------------------------------------------
def test_leader_role_message_says_it_grants_no_permission():
    """`/set_role` への誘導が無いと「班長にしたのに何もできない」になる。"""

    async def _main():
        cog, db, _repo = await _make_cog()
        try:
            interaction = await _call(cog, 333, "leader")
            description = interaction.last_description
            assert "班長ロール" in description
            assert "自動付与はしません" in description
            assert "/set_role" in description
        finally:
            await db.close()

    run(_main())


def test_synced_role_message_still_promises_auto_assignment():
    """主所属・副所属は従来どおり自動付与されることを伝える。"""

    async def _main():
        cog, db, _repo = await _make_cog()
        try:
            for role_type in ("primary", "secondary"):
                interaction = await _call(cog, 111, role_type)
                assert "自動で付与・剥奪" in interaction.last_description
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 監査ログ・ギルド分離・未登録班
# ---------------------------------------------------------------------
def test_leader_role_change_is_audited():
    async def _main():
        cog, db, _repo = await _make_cog()
        try:
            await _call(cog, 333, "leader")
            logs = await AuditLogRepository(db).list_recent(G1, limit=10)
            assert logs[0]["action"] == "team.role"
            assert "班長ロール" in logs[0]["detail"]
        finally:
            await db.close()

    run(_main())


def test_leader_role_is_guild_scoped():
    """同じ slug の班が別ギルドにあっても巻き込まない。"""

    async def _main():
        cog, db, repo = await _make_cog()
        try:
            await repo.upsert_team(G2, "wing", "主翼班")
            await _call(cog, 333, "leader")

            assert (await repo.get_team(G1, "wing"))["leader_role_id"] == "333"
            assert (await repo.get_team(G2, "wing"))["leader_role_id"] is None
        finally:
            await db.close()

    run(_main())


def test_unknown_team_is_rejected():
    async def _main():
        cog, db, _repo = await _make_cog()
        try:
            interaction = _Interaction()
            await Teams.team_role.callback(
                cog, interaction, team="missing", role=_Role(333, "x"), role_type="leader"
            )
            assert "登録されていません" in interaction.last_description
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# リポジトリ単体
# ---------------------------------------------------------------------
def test_set_team_roles_accepts_leader_role_id():
    async def _main():
        db = Database(_tmp_db_path())
        await db.connect()
        try:
            repo = MemberRepository(db)
            await repo.upsert_team(G1, "wing", "翼班")

            assert await repo.set_team_roles(G1, "wing", leader_role_id="333") is True
            assert (await repo.get_team(G1, "wing"))["leader_role_id"] == "333"
            # 未登録の班は False（member_role_id と同じ契約）
            assert await repo.set_team_roles(G1, "unknown", leader_role_id="9") is False
        finally:
            await db.close()

    run(_main())
