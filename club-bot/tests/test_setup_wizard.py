"""/setup 設定ウィザードの単体テスト。

- save_setting がギルド別 settings に保存され config.for_guild で解決されること
- ギルド間で設定が混ざらないこと
- /setup で扱えないキーが拒否されること
- Embed が未設定項目を明示すること
- 権限判定（/setup は L4 管理者限定）: 非管理者が拒否されること

実行: venv/bin/python -m pytest tests/
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.setup_wizard import (  # noqa: E402
    SetupWizard, build_setup_embed, parse_team_names,
)
from config import GuildConfig, config  # noqa: E402
from repositories.member_repository import MemberRepository  # noqa: E402
from repositories.settings_repository import SettingsRepository  # noqa: E402
from services import team_service  # noqa: E402
from utils.db import Database  # noqa: E402
from utils.permissions import Level, get_level, has_level  # noqa: E402

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2


def run(coro):
    return asyncio.run(coro)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 側で新規作成させる
    return path


async def _make_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def _make_cog(db: Database) -> SetupWizard:
    # commands.Cog は bot 参照を保持するだけなので SimpleNamespace で代用
    return SetupWizard(SimpleNamespace(db=db))


def _cleanup_config() -> None:
    # グローバル config のキャッシュ・保持接続を他テストへ持ち越さない
    config._db = None
    config.clear_guild_cache()


def test_save_setting_persists_and_resolves():
    db = run(_make_db())
    try:
        cog = _make_cog(db)
        run(cog.save_setting(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID", "111"))
        run(cog.save_setting(G1, "ADMIN_ROLE_ID", "222"))

        repo = SettingsRepository(db)
        assert run(repo.get(G1, "DEFAULT_ANNOUNCE_CHANNEL_ID")) == "111"
        assert run(repo.get(G1, "ADMIN_ROLE_ID")) == "222"

        # config.for_guild でギルド別に解決される
        gconf = run(config.for_guild(G1, db=db, force_reload=True))
        assert gconf.default_announce_channel_id == 111
        assert gconf.admin_role_id == 222
    finally:
        run(db.close())
        _cleanup_config()


def test_save_setting_guild_isolation():
    db = run(_make_db())
    try:
        cog = _make_cog(db)
        run(cog.save_setting(G1, "BOT_LOG_CHANNEL_ID", "333"))

        repo = SettingsRepository(db)
        assert run(repo.get(G2, "BOT_LOG_CHANNEL_ID")) is None

        gconf2 = run(config.for_guild(G2, db=db, force_reload=True))
        assert gconf2.bot_log_channel_id is None
    finally:
        run(db.close())
        _cleanup_config()


def test_save_setting_rejects_unknown_key():
    db = run(_make_db())
    try:
        cog = _make_cog(db)
        try:
            run(cog.save_setting(G1, "TODOIST_API_TOKEN", "x"))
            assert False, "ValueError が送出されるべき"
        except ValueError:
            pass
    finally:
        run(db.close())
        _cleanup_config()


def test_build_setup_embed_marks_missing():
    gconf = GuildConfig(guild_id=G1, default_announce_channel_id=111)
    embed = build_setup_embed(gconf)
    desc = embed.description or ""
    assert "<#111>" in desc                    # 設定済みはメンション表示
    assert "⚠️ 未設定" in desc                  # 未設定項目を明示
    assert "未設定が 7 件" in desc               # 全8項目中1件のみ設定済み

    full = GuildConfig(
        guild_id=G1,
        bot_log_channel_id=1, default_announce_channel_id=2,
        default_schedule_channel_id=3, default_progress_channel_id=4,
        default_task_channel_id=5, today_label_channel_id=6,
        exec_role_id=7, admin_role_id=8)
    embed_full = build_setup_embed(full)
    assert "未設定" not in (embed_full.description or "")
    assert "すべての項目が設定済み" in embed_full.description


def _fake_member(user_id: int, owner_id: int, administrator: bool = False):
    return SimpleNamespace(
        id=user_id,
        guild=SimpleNamespace(owner_id=owner_id),
        roles=[],
        guild_permissions=SimpleNamespace(administrator=administrator),
    )


def test_non_admin_is_rejected():
    """/setup の前提権限（L4）に非管理者が届かないこと。"""
    gconf = GuildConfig(guild_id=G1, admin_role_id=999)

    # ロールを持たない一般メンバー → L1（L4 未満で拒否）
    member = _fake_member(user_id=1, owner_id=42)
    assert get_level(member, gconf) == Level.L1
    assert not has_level(member, gconf, Level.L4)

    # サーバーオーナー → L4
    owner = _fake_member(user_id=42, owner_id=42)
    assert has_level(owner, gconf, Level.L4)

    # Discord 管理者権限持ち → L4
    admin = _fake_member(user_id=7, owner_id=42, administrator=True)
    assert has_level(admin, gconf, Level.L4)


# ------------------------------------------------------------------
# 班の一括作成（/setup の「班を一括作成」ステップ）
# ------------------------------------------------------------------

def test_parse_team_names():
    assert parse_team_names("設計班, 製造班, 広報班") == ["設計班", "製造班", "広報班"]
    # 全角カンマ・読点・改行も区切りとして扱う
    assert parse_team_names("A班，B班、C班\nD班") == ["A班", "B班", "C班", "D班"]
    # 空要素・重複は除去し、入力順を保持
    assert parse_team_names(" A班 ,, B班 ,A班,") == ["A班", "B班"]
    assert parse_team_names("") == []
    assert parse_team_names(" , 、") == []
    # 長すぎる班名は ValueError
    try:
        parse_team_names("あ" * 51)
        assert False, "ValueError が送出されるべき"
    except ValueError:
        pass


def test_register_teams_assigns_slugs():
    db = run(_make_db())
    try:
        cog = _make_cog(db)
        teams = run(cog.register_teams(G1, ["設計班", "製造班"], actor_id="u1"))
        assert teams == [
            {"slug": "team1", "name": "設計班"},
            {"slug": "team2", "name": "製造班"},
        ]

        repo = MemberRepository(db)
        assert (run(repo.get_team(G1, "team1")))["team_name"] == "設計班"
        assert run(repo.list_teams(G2)) == []  # 他ギルドには影響しない

        # 既存キーと重ならないよう続きから採番される
        more = run(cog.register_teams(G1, ["広報班"]))
        assert more == [{"slug": "team3", "name": "広報班"}]

        # 空リストは何もしない（監査ログも記録しない）
        assert run(cog.register_teams(G1, [])) == []
    finally:
        run(db.close())
        _cleanup_config()


def test_register_teams_avoids_existing_slug():
    db = run(_make_db())
    try:
        repo = MemberRepository(db)
        # team1 が /team-add 等で既に使われている場合は team2 から始める
        run(repo.upsert_team(G1, "team1", "既存班"))
        cog = _make_cog(db)
        teams = run(cog.register_teams(G1, ["新規班"]))
        assert teams == [{"slug": "team2", "name": "新規班"}]
        assert (run(repo.get_team(G1, "team1")))["team_name"] == "既存班"
    finally:
        run(db.close())
        _cleanup_config()


def test_zero_teams_commands_helpers_work():
    """班0件の状態でも参照系ヘルパーが例外なく動作すること。"""
    db = run(_make_db())
    try:
        assert run(team_service.team_choices(db, G1, "")) == []
        assert run(team_service.team_name_map(db, G1)) == {}
        assert run(MemberRepository(db).list_teams(G1)) == []
    finally:
        run(db.close())
        _cleanup_config()


# ------------------------------------------------------------------
# サークル名（CLUB_NAME）
# ------------------------------------------------------------------

def test_club_name_fallback():
    """未設定時は汎用表現「サークル」にフォールバックすること。"""
    gconf = GuildConfig(guild_id=G1)
    assert gconf.club_name is None
    assert gconf.club_name_or_default == "サークル"

    gconf_named = GuildConfig(guild_id=G1, club_name="テストサークル")
    assert gconf_named.club_name_or_default == "テストサークル"


def test_club_name_saved_and_resolved_per_guild():
    """CLUB_NAME がギルド別に保存され config.for_guild で解決されること。"""
    db = run(_make_db())
    try:
        cog = _make_cog(db)
        run(cog.save_setting(G1, "CLUB_NAME", "ギルド1サークル"))

        gconf1 = run(config.for_guild(G1, db=db, force_reload=True))
        assert gconf1.club_name == "ギルド1サークル"
        assert gconf1.club_name_or_default == "ギルド1サークル"

        # 他ギルドは未設定のまま（フォールバック）
        gconf2 = run(config.for_guild(G2, db=db, force_reload=True))
        assert gconf2.club_name is None
        assert gconf2.club_name_or_default == "サークル"
    finally:
        run(db.close())
        _cleanup_config()


def test_setup_embed_shows_club_name():
    """セットアップ Embed にサークル名が表示されること。"""
    embed = build_setup_embed(GuildConfig(guild_id=G1, club_name="表示サークル"))
    assert "表示サークル" in (embed.description or "")
    # 未設定でもフォールバック表示で例外にならない
    embed_default = build_setup_embed(GuildConfig(guild_id=G1))
    assert "サークル名" in (embed_default.description or "")
