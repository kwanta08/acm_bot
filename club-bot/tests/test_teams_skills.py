"""班（teams）・技能タグ（skill_tags）の DB 管理化テスト。

- skill_tags / teams の CRUD とギルド分離
- 班ロール紐付け（member_role_id / secondary_role_id）のギルド分離
- v2 -> v3 マイグレーション（teams カラム追加 + settings からのバックフィル）
- autocomplete 候補生成（team_service）の絞り込み・25件上限

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402

from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.skill_tag_repository import SkillTagRepository  # noqa: E402
from services import team_service  # noqa: E402
from utils.db import SCHEMA_VERSION, TABLE_DDL, Database  # noqa: E402

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2

# v2 以前の teams DDL（member_role_id 等のカラム追加前）
V2_TEAMS_DDL = """
CREATE TABLE teams (
    team_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL CHECK (guild_id >= 0),
    team_key       TEXT NOT NULL,
    team_name      TEXT NOT NULL,
    leader_role_id TEXT,
    channel_id     TEXT,
    active_flag    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (guild_id, team_key)
);
"""


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # connect() に作成させる
    return path


def run(coro):
    return asyncio.run(coro)


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


# ---------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------
def test_fresh_schema_has_skill_tags_and_team_columns():
    async def _main():
        db = await _connected_db()
        try:
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(skill_tags)")}
            assert {"skill_tag_id", "guild_id", "skill_name",
                    "active_flag", "created_by", "created_at"} <= cols
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(teams)")}
            assert {"member_role_id", "secondary_role_id",
                    "created_at", "updated_at"} <= cols
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# skill_tags CRUD とギルド分離
# ---------------------------------------------------------------------
def test_skill_tags_crud_and_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = SkillTagRepository(db)
            await repo.add(G1, "CAD", "u1")
            await repo.add(G1, "はんだ", "u1")
            await repo.add(G2, "CAD", "u2")  # 同名タグを別ギルドに登録

            assert sorted(await repo.list_active(G1)) == sorted(["CAD", "はんだ"])
            assert await repo.list_active(G2) == ["CAD"]

            # 一意制約: 同ギルドでの再登録は UPDATE（再有効化）になり重複しない
            await repo.add(G1, "CAD", "u1")
            assert len(await repo.list_all(G1)) == 2

            # 無効化はギルド単位
            assert await repo.deactivate(G1, "CAD") is True
            assert await repo.exists_active(G1, "CAD") is False
            assert await repo.exists_active(G2, "CAD") is True  # G2 には影響なし
            # 未登録の無効化は False
            assert await repo.deactivate(G1, "存在しない") is False
            # 再有効化
            await repo.add(G1, "CAD", "u1")
            assert await repo.exists_active(G1, "CAD") is True
            assert len(await repo.list_all(G1)) == 2
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# teams のロール紐付けとギルド分離
# ---------------------------------------------------------------------
def test_team_roles_and_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            await repo.upsert_team(G1, "design", "設計")
            await repo.upsert_team(G2, "design", "デザイン班")  # 同 slug を別ギルドに

            # ロール紐付け
            assert await repo.set_team_roles(G1, "design", member_role_id="111") is True
            assert await repo.set_team_roles(G1, "design", secondary_role_id="222") is True
            t1 = await repo.get_team(G1, "design")
            t2 = await repo.get_team(G2, "design")
            assert t1["member_role_id"] == "111"
            assert t1["secondary_role_id"] == "222"
            assert t2["member_role_id"] is None  # G2 には影響なし
            assert t2["team_name"] == "デザイン班"

            # 未登録の班への設定は False
            assert await repo.set_team_roles(G1, "unknown", member_role_id="9") is False

            # upsert は同ギルド内で slug 一意（表示名更新・再有効化）
            await repo.upsert_team(G1, "design", "設計班")
            assert (await repo.get_team(G1, "design"))["team_name"] == "設計班"
            assert (await repo.get_team(G1, "design"))["member_role_id"] == "111"  # 保持

            # 無効化と主所属メンバー数
            await repo.upsert_member(G1, "u1", "Taro", "design")
            await repo.upsert_member(G2, "u1", "Taro", "design")
            assert await repo.count_primary_members(G1, "design") == 1
            assert await repo.deactivate_team(G1, "design") is True
            assert [t["team_key"] for t in await repo.list_teams(G1)] == []
            assert [t["team_key"] for t in await repo.list_teams(G2)] == ["design"]
            # 無効化済みも含めた一覧では見える（表示用途）
            assert len(await repo.list_teams(G1, active_only=False)) == 1
            # 再有効化（メンバーの所属は保持されている）
            await repo.upsert_team(G1, "design", "設計班")
            assert (await repo.get_team(G1, "design"))["active_flag"] == 1
            assert await repo.count_primary_members(G1, "design") == 1
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# v2 -> v3 マイグレーション
# ---------------------------------------------------------------------
def test_v2_to_v3_migration_backfills_team_roles():
    async def _main():
        path = _tmp_db_path()
        # v2 相当の DB を準備（skill_tags 無し、teams は旧カラム構成、user_version=2）
        conn = await aiosqlite.connect(path)
        for name, ddl in TABLE_DDL.items():
            if name in ("skill_tags", "todoist_configs"):
                continue
            if name == "teams":
                await conn.executescript(V2_TEAMS_DDL)
                continue
            await conn.executescript(ddl)
        await conn.execute(
            "INSERT INTO teams (guild_id, team_key, team_name) VALUES (?, 'design', '設計')",
            (G1,))
        await conn.execute(
            "INSERT INTO teams (guild_id, team_key, team_name) VALUES (?, 'design', '設計')",
            (G2,))
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'PRIMARY_TEAM_ROLE_IDS', 'design:111,wing:222')", (G1,))
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'SECONDARY_TEAM_ROLE_IDS', 'design:333')", (G1,))
        await conn.execute("PRAGMA user_version = 2")
        await conn.commit()
        await conn.close()

        db = Database(path)
        await db.connect()
        try:
            # カラム追加
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(teams)")}
            assert {"member_role_id", "secondary_role_id", "created_at", "updated_at"} <= cols
            # skill_tags 作成
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(skill_tags)")}
            assert "guild_id" in cols
            # バックフィル: G1 の design に member/secondary が入る
            repo = MemberRepository(db)
            t1 = await repo.get_team(G1, "design")
            assert t1["member_role_id"] == "111"
            assert t1["secondary_role_id"] == "333"
            # wing は teams に無いので作成されない（紐付け先の班が無い）
            assert await repo.get_team(G1, "wing") is None
            # G2 には影響なし
            t2 = await repo.get_team(G2, "design")
            assert t2["member_role_id"] is None
            # バージョン更新
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()

        # 再接続しても冪等（バックフィルは IS NULL のみ対象なので上書きされない）
        db2 = Database(path)
        await db2.connect()
        try:
            row = await db2.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            t1 = await MemberRepository(db2).get_team(G1, "design")
            assert t1["member_role_id"] == "111"
        finally:
            await db2.close()
    run(_main())


# ---------------------------------------------------------------------
# autocomplete 候補生成（team_service）
# ---------------------------------------------------------------------
def test_team_service_choices():
    async def _main():
        db = await _connected_db()
        try:
            repo = MemberRepository(db)
            skill_repo = SkillTagRepository(db)
            for key, name in (("design", "設計"), ("wing", "翼"), ("cfrp", "CFRP")):
                await repo.upsert_team(G1, key, name)
            await repo.upsert_team(G2, "design", "設計")
            await repo.deactivate_team(G1, "cfrp")
            await skill_repo.add(G1, "CAD", "u1")
            await skill_repo.add(G1, "はんだ", "u1")
            await skill_repo.add(G2, "解析", "u2")

            # 班名マップはギルド単位（無効化済みも含む）
            names = await team_service.team_name_map(db, G1)
            assert names == {"design": "設計", "wing": "翼", "cfrp": "CFRP"}
            assert await team_service.team_name_map(db, G2) == {"design": "設計"}

            # autocomplete: 有効な班のみ・入力で絞り込み
            choices = await team_service.team_choices(db, G1, "")
            assert sorted(c.value for c in choices) == ["design", "wing"]
            choices = await team_service.team_choices(db, G1, "wi")
            assert [c.value for c in choices] == ["wing"]
            # 表示名でも絞り込める
            choices = await team_service.team_choices(db, G1, "設")
            assert [c.value for c in choices] == ["design"]
            # 他ギルドの班は出ない
            choices = await team_service.team_choices(db, G2, "")
            assert [c.value for c in choices] == ["design"]

            # 技能タグの候補もギルド単位
            choices = await team_service.skill_choices(db, G1, "")
            assert sorted(c.value for c in choices) == ["CAD", "はんだ"]
            choices = await team_service.skill_choices(db, G2, "")
            assert [c.value for c in choices] == ["解析"]

            # 25件上限（30件登録しても25件まで）
            for i in range(30):
                await skill_repo.add(G1, f"tag{i:02d}", "u1")
            choices = await team_service.skill_choices(db, G1, "")
            assert len(choices) == 25
            # 絞り込めば上限内でも目的のタグに届く
            choices = await team_service.skill_choices(db, G1, "tag29")
            assert [c.value for c in choices] == ["tag29"]
        finally:
            await db.close()
    run(_main())


if __name__ == "__main__":
    test_fresh_schema_has_skill_tags_and_team_columns()
    print("test_fresh_schema_has_skill_tags_and_team_columns: OK")
    test_skill_tags_crud_and_isolation()
    print("test_skill_tags_crud_and_isolation: OK")
    test_team_roles_and_isolation()
    print("test_team_roles_and_isolation: OK")
    test_v2_to_v3_migration_backfills_team_roles()
    print("test_v2_to_v3_migration_backfills_team_roles: OK")
    test_team_service_choices()
    print("test_team_service_choices: OK")
    print("全テスト成功")
