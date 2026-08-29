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

from cogs.setup_wizard import (
    MAX_MULTI_ROLE_VALUES,
    SetupWizard,
    SetupWizardView,
    build_setup_embed,
    parse_team_names,
)
from config import GuildConfig, config
from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from services import team_service
from utils.db import Database
from utils.permissions import Level, get_level, has_level

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
    assert "<#111>" in desc  # 設定済みはメンション表示
    assert "⚠️ 未設定" in desc  # 未設定項目を明示
    # 案内チャンネルはオンボーディングが OFF なので数えない（全10項目・8件）
    assert "未設定が 8 件" in desc

    full = GuildConfig(
        guild_id=G1,
        bot_log_channel_id=1,
        default_announce_channel_id=2,
        default_schedule_channel_id=3,
        default_progress_channel_id=4,
        default_task_channel_id=5,
        today_label_channel_id=6,
        exec_role_id=7,
        admin_role_id=8,
        leader_role_ids=[9],
        welcome_channel_id=10,
    )
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


# ------------------------------------------------------------------
# 班長ロール（LEADER_ROLE_IDS）— G3-1
#
# L2 判定の唯一の根拠なのに /setup から設定できなかった。
# 他のロール項目と違い **複数値**（カンマ区切り）を持つ。
# ------------------------------------------------------------------


class _SelectInteraction:
    """コンポーネント操作の interaction（edit_message を記録する）。"""

    def __init__(self, user_id: int = 501):
        self.user = SimpleNamespace(id=user_id, display_name="tester")
        self.edited: list[dict] = []
        self.messages: list[dict] = []
        self.response = SimpleNamespace(
            edit_message=self._edit,
            send_message=self._send,
            is_done=lambda: False,
        )

    async def _edit(self, **kwargs):
        self.edited.append(kwargs)

    async def _send(self, **kwargs):
        self.messages.append(kwargs)

    @property
    def edited_text(self) -> str:
        embed = self.edited[-1].get("embed")
        if embed is None:
            return ""
        return (embed.title or "") + "\n" + (embed.description or "")


def _view(db: Database) -> SetupWizardView:
    return SetupWizardView(_make_cog(db), G1, owner_id=501)


def test_multi_role_limit_is_the_role_select_maximum():
    """上限は RoleSelect の上限（25）であること。

    シンボル参照だけのテストでは、5 に戻しても緑のままになる。
    受入基準の 5 から意図的に広げた値なので、数値そのものを固定する
    （5 だと班長ロールを6件以上運用しているギルドで L2 判定の根拠を
    黙って切り捨てる）。
    """
    assert MAX_MULTI_ROLE_VALUES == 25


def test_build_setup_embed_lists_every_leader_role():
    """複数の班長ロールを並べて表示すること。"""
    desc = build_setup_embed(GuildConfig(guild_id=G1, leader_role_ids=[7, 8])).description or ""
    assert "<@&7>" in desc
    assert "<@&8>" in desc


def test_build_setup_embed_treats_empty_leader_roles_as_missing():
    """空リストを「未設定」に落とすこと。

    他の項目は None だが leader_role_ids は list[int] なので、
    `value is None` の判定だけでは `<@&[]>` と描画され、
    未設定カウントからも漏れる。
    """
    desc = build_setup_embed(GuildConfig(guild_id=G1)).description or ""
    assert "<@&[]>" not in desc
    assert "班長ロール**: ⚠️ 未設定" in desc


def test_build_setup_embed_shows_the_selected_item():
    """選択中の項目と「置き換わる」ことを画面に出す。"""
    full = GuildConfig(
        guild_id=G1,
        bot_log_channel_id=1,
        default_announce_channel_id=2,
        default_schedule_channel_id=3,
        default_progress_channel_id=4,
        default_task_channel_id=5,
        today_label_channel_id=6,
        exec_role_id=7,
        admin_role_id=8,
        leader_role_ids=[9],
        welcome_channel_id=10,
    )
    desc = build_setup_embed(full, selected_key="LEADER_ROLE_IDS").description or ""
    assert "班長ロール" in desc
    assert "置き換わ" in desc
    # 「すべて設定済み」の検査（既存テスト）を壊さない文言であること
    assert "未設定" not in desc


def test_select_item_switches_max_values_on_the_sent_view():
    """複数選択を**クライアントへ届く形で**有効にすること。

    Python 側で max_values を変えても、元メッセージのコンポーネント定義は
    max_values=1 のままなので複数選択は発生しない。edit_message で
    View を送り直していることまで検査する。
    """
    db = run(_make_db())
    try:
        view = _view(db)
        interaction = _SelectInteraction()
        run(
            SetupWizardView.select_item(
                view, interaction, SimpleNamespace(values=["LEADER_ROLE_IDS"])
            )
        )
        assert interaction.edited, "元メッセージを編集していない（View が送り直されていない）"
        sent_view = interaction.edited[-1]["view"]
        assert sent_view.select_role.max_values == MAX_MULTI_ROLE_VALUES

        # 単数キーへ切り替えたら 1 に戻る
        run(
            SetupWizardView.select_item(view, interaction, SimpleNamespace(values=["ADMIN_ROLE_ID"]))
        )
        assert interaction.edited[-1]["view"].select_role.max_values == 1
    finally:
        run(db.close())
        _cleanup_config()


def test_select_role_overwrites_leader_role_ids():
    """追記ではなく上書き（受入基準）。"""
    db = run(_make_db())
    try:
        repo = SettingsRepository(db)
        run(repo.set(G1, "LEADER_ROLE_IDS", "999"))
        view = _view(db)
        view.selected_key = "LEADER_ROLE_IDS"
        interaction = _SelectInteraction()
        roles = [SimpleNamespace(id=111), SimpleNamespace(id=222)]
        run(SetupWizardView.select_role(view, interaction, SimpleNamespace(values=roles)))
        assert run(repo.get(G1, "LEADER_ROLE_IDS")) == "111,222"
    finally:
        run(db.close())
        _cleanup_config()


def test_select_role_rejects_multiple_values_for_a_single_value_key():
    db = run(_make_db())
    try:
        view = _view(db)
        view.selected_key = "ADMIN_ROLE_ID"
        interaction = _SelectInteraction()
        roles = [SimpleNamespace(id=111), SimpleNamespace(id=222)]
        run(SetupWizardView.select_role(view, interaction, SimpleNamespace(values=roles)))
        assert run(SettingsRepository(db).get(G1, "ADMIN_ROLE_ID")) is None
        assert interaction.messages, "エラーを返していない"
    finally:
        run(db.close())
        _cleanup_config()


def test_select_item_blocks_overwrite_when_saved_roles_exceed_the_limit():
    """上限を超えて保存されているギルドでは、黙って切り捨てない。"""
    db = run(_make_db())
    try:
        ids = ",".join(str(1000 + i) for i in range(MAX_MULTI_ROLE_VALUES + 1))
        run(SettingsRepository(db).set(G1, "LEADER_ROLE_IDS", ids))
        view = _view(db)
        interaction = _SelectInteraction()
        run(
            SetupWizardView.select_item(
                view, interaction, SimpleNamespace(values=["LEADER_ROLE_IDS"])
            )
        )
        sent_view = interaction.edited[-1]["view"]
        assert sent_view.select_role.disabled, "選ばせてから拒否しない"
        assert "/set_role" in interaction.edited_text
    finally:
        run(db.close())
        _cleanup_config()
