"""DB 基盤強化（スキーマ v2: guilds 台帳 / audit_log / user_version）の単体テスト。

- 新規 DB に guilds / audit_log が作成され user_version=2 になること
- v1 相当 DB からの自動マイグレーション（台帳バックフィル）が冪等であること
- 2ギルドの監査ログ・通知ログがリポジトリ経由で混ざらないこと
- 招待直後の案内が確実に届き、自動セットアップのマーカーが
  「作成に成功したときだけ」立つこと（G3-5）

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite
import discord

from bot import (
    ADMIN_ROLE_NAME,
    AUTO_SETUP_COMPLETED_KEY,
    AUTO_SETUP_DONE_KEY,
    BOT_LOG_CHANNEL_NAME,
    EXEC_ROLE_NAME,
    MAX_NOTICE_CHANNEL_ATTEMPTS,
    ClubBot,
    build_setup_guidance,
)
from config import config
from repositories.audit_log_repository import AuditLogRepository
from repositories.guild_repository import GuildRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.settings_repository import SettingsRepository
from utils.db import SCHEMA_VERSION, TABLE_DDL, Database

G1 = 100000000000000001  # ギルド1
G2 = 200000000000000002  # ギルド2

# v1 相当（guilds / audit_log / skill_tags / todoist_configs / guild_directus_access
# 導入前）のテーブル群
V1_TABLES = [
    t
    for t in TABLE_DDL
    if t not in ("guilds", "audit_log", "skill_tags", "todoist_configs", "guild_directus_access")
]


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
def test_fresh_schema_is_v2():
    async def _main():
        db = await _connected_db()
        try:
            # guilds / audit_log が存在し、期待するカラムを持つ
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(guilds)")}
            assert {"guild_id", "guild_name", "joined_at", "setup_version"} <= cols
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(audit_log)")}
            assert {
                "audit_id",
                "guild_id",
                "actor_id",
                "action",
                "target",
                "detail",
                "created_at",
            } <= cols
            # スキーマバージョンが最新
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            # busy_timeout が設定されている（外部ツール同時アクセス対策）
            row = await db.fetchone("PRAGMA busy_timeout")
            assert row[0] == 5000
        finally:
            await db.close()

    run(_main())


def test_guild_registry_ensure():
    async def _main():
        db = await _connected_db()
        try:
            repo = GuildRepository(db)
            await repo.ensure(G1, "ギルド1")
            g = await repo.get(G1)
            assert g["guild_name"] == "ギルド1"
            assert g["setup_version"] == 2
            # 再登録は冪等で名称のみ更新される
            await repo.ensure(G1, "ギルド1改")
            assert (await repo.get(G1))["guild_name"] == "ギルド1改"
            assert len(await repo.list_all()) == 1
            # guild_id=0 は CHECK 制約で拒否される（レガシー sentinel を台帳に登録しない）
            raised = False
            try:
                await repo.ensure(0, "legacy")
            # CHECK 制約違反で何らかの例外が出ること自体を検証するため例外種別は問わない
            except Exception:  # noqa: BLE001
                raised = True
            assert raised, "guild_id=0 の登録が拒否されませんでした"
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# v1 -> v2 マイグレーション
# ---------------------------------------------------------------------
def test_v1_to_v2_migration_backfills_guilds():
    async def _main():
        path = _tmp_db_path()
        # v1 相当の DB を準備（guilds / audit_log 無し、user_version=0）
        conn = await aiosqlite.connect(path)
        for table in V1_TABLES:
            await conn.executescript(TABLE_DDL[table])
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'GUILD_NAME', '移行ギルド')",
            (G1,),
        )
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'TZ', 'Asia/Tokyo')",
            (G2,),
        )
        # guild_id=0（レガシー sentinel）は台帳に登録されないことを確認するための行
        await conn.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (0, 'TZ', 'Asia/Tokyo')"
        )
        await conn.commit()
        await conn.close()

        db = Database(path)
        await db.connect()
        try:
            # guilds / audit_log が作成された
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(audit_log)")}
            assert "guild_id" in cols
            # 台帳バックフィル: G1 は GUILD_NAME から、G2 は '(unknown)'
            repo = GuildRepository(db)
            assert (await repo.get(G1))["guild_name"] == "移行ギルド"
            assert (await repo.get(G2))["guild_name"] == "(unknown)"
            assert await repo.get(0) is None
            # バージョン更新
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
        finally:
            await db.close()

        # 再接続しても冪等（バージョン据え置き・データ不変）
        db2 = Database(path)
        await db2.connect()
        try:
            row = await db2.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            assert len(await GuildRepository(db2).list_all()) == 2
        finally:
            await db2.close()

    run(_main())


# ---------------------------------------------------------------------
# ギルド分離
# ---------------------------------------------------------------------
def test_audit_log_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = AuditLogRepository(db)
            id1 = await repo.record(G1, "u1", "team.add", target="design", detail="班を追加")
            await repo.record(G2, "u2", "todoist.setup")
            assert id1 >= 1
            logs1 = await repo.list_recent(G1)
            logs2 = await repo.list_recent(G2)
            assert len(logs1) == 1 and len(logs2) == 1
            assert logs1[0]["action"] == "team.add"
            assert logs2[0]["action"] == "todoist.setup"
            # 他ギルドのログが混入しない
            assert all(row["guild_id"] == G1 for row in logs1)
            assert all(row["guild_id"] == G2 for row in logs2)
        finally:
            await db.close()

    run(_main())


def test_reminders_log_repository_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = RemindersLogRepository(db)
            for i in range(3):
                await repo.add(G1, "task_overdue", f"t{i}", None, "ch1", "success")
            await repo.add(G2, "schedule_unanswered", "s1", None, "ch2", "failed", "err")
            logs1 = await repo.list_recent(G1, limit=10)
            logs2 = await repo.list_recent(G2, limit=10)
            assert len(logs1) == 3
            assert len(logs2) == 1
            # 新しい順
            assert logs1[0]["target_id"] == "t2"
            # limit が効く
            assert len(await repo.list_recent(G1, limit=2)) == 2
            # 失敗理由も保存される
            assert logs2[0]["error_message"] == "err"
        finally:
            await db.close()

    run(_main())


def test_schedule_list_all_isolation():
    async def _main():
        db = await _connected_db()
        try:
            repo = ScheduleRepository(db)
            for gid, sid in ((G1, "sch1"), (G2, "sch2")):
                await repo.create_schedule(
                    gid,
                    schedule_id=sid,
                    title=f"title-{sid}",
                    description=None,
                    place=None,
                    target_role_id=None,
                    deadline_iso="2099-01-01T00:00:00",
                    created_by="u1",
                    channel_id="ch",
                )
            await repo.close_schedule(G1, "sch1")
            # list_all はクローズ済みも含む
            assert [s["schedule_id"] for s in await repo.list_all(G1)] == ["sch1"]
            assert [s["schedule_id"] for s in await repo.list_all(G2)] == ["sch2"]
            # 他ギルドのスケジュールは見えない
            assert all(s["guild_id"] == G1 for s in await repo.list_all(G1))
        finally:
            await db.close()

    run(_main())


# ---------------------------------------------------------------------
# 招待直後の案内（G3-5）
#
# 案内は bot-log →（無ければ）guild.system_channel →（無ければ）送信可能な
# 最初のテキストチャンネル、の順で送る。ADR 0017 の最小権限招待では
# #bot-log が作られないため、bot-log 限定の log_to_channel では
# ほとんどのサーバーで無言で捨てられていた。
# ---------------------------------------------------------------------
def _http_error(status: int = 403) -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=status, reason="Forbidden"), "denied")


class FakeChannel:
    """送信可否と送信結果だけを持つテキストチャンネル。"""

    def __init__(self, channel_id: int, name: str = "general", *, sendable=True, fails=False):
        self.id = channel_id
        self.name = name
        self.sendable = sendable  # permissions_for が返す値
        self.fails = fails  # send が HTTPException を投げるか
        self.sent: list[str] = []
        self.attempts = 0

    def permissions_for(self, member):
        # 実 discord.TextChannel.permissions_for(None) は AttributeError になる。
        # guild.me が None のときに権限計算を飛ばしていることを検査できるよう、
        # フェイクでも同じように壊す
        if member is None:
            raise AttributeError("permissions_for(None)")
        return SimpleNamespace(view_channel=self.sendable, send_messages=self.sendable)

    async def send(self, message):
        self.attempts += 1
        if self.fails:
            raise _http_error()
        self.sent.append(message)


class FakeMember:
    def __init__(self, *, manage_roles: bool = False, manage_channels: bool = False):
        self.id = 4242
        self.guild_permissions = SimpleNamespace(
            manage_roles=manage_roles, manage_channels=manage_channels
        )


class FakeCategory:
    """send を持たないチャンネル（カテゴリ・フォーラム相当）。"""

    def __init__(self, channel_id: int, name: str = "カテゴリ"):
        self.id = channel_id
        self.name = name

    def permissions_for(self, member):
        if member is None:
            raise AttributeError("permissions_for(None)")
        return SimpleNamespace(view_channel=True, send_messages=True)


class FakeGuild:
    def __init__(
        self,
        guild_id: int = G1,
        *,
        me=None,
        system_channel=None,
        text_channels=(),
        roles=(),
        other_channels=(),
    ):
        self.id = guild_id
        self.name = f"ギルド{guild_id}"
        self.me = me
        self.system_channel = system_channel
        self.text_channels = list(text_channels)
        # text_channels には出ないが get_channel では引けるもの（カテゴリ等）
        self.other_channels = list(other_channels)
        self.roles = list(roles)
        self.created_roles: list = []
        self.created_channels: list = []

    def get_channel(self, channel_id: int):
        for channel in [*self.text_channels, *self.other_channels, self.system_channel]:
            if channel is not None and channel.id == channel_id:
                return channel
        return None

    def get_role(self, role_id: int):
        for role in self.roles:
            if role.id == role_id:
                return role
        return None

    async def create_role(self, name, mentionable=False, reason=None):
        role = SimpleNamespace(id=9000 + len(self.created_roles), name=name)
        self.created_roles.append(role)
        self.roles.append(role)
        return role

    async def create_text_channel(self, name, reason=None):
        channel = FakeChannel(8000 + len(self.created_channels), name=name)
        self.created_channels.append(channel)
        self.text_channels.append(channel)
        return channel


class _OfflineBot(ClubBot):
    """Discord へ接続しない ClubBot（on_ready / on_guild_join まで通す）。

    user / guilds / application_id は discord.Client のプロパティなので
    インスタンス属性では差し替えられない。ここでだけ上書きする。
    ClubBot.__init__（Gateway 前提の初期化）は呼ばないため、
    **on_ready に Client 内部へ触る処理（self.tree / self.get_channel /
    self.latency など）を足したらここも更新すること**（AttributeError で落ちる）。
    """

    def __init__(self, db: Database, guilds=()):
        self.db = db
        self.log_calls: list = []
        self.presence_calls: list = []
        self._fake_guilds = list(guilds)
        self._fake_user = SimpleNamespace(id=123456789012345678, name="club-bot")
        self._initial_guild_setup_done = False

    @property
    def user(self):
        return self._fake_user

    @property
    def guilds(self):
        return list(self._fake_guilds)

    @property
    def application_id(self):
        return self._fake_user.id

    # 運用ログ経路（bot-log 限定）が使われていないことを検査するため差し替える
    async def log_to_channel(self, message, guild_id=None):
        self.log_calls.append((guild_id, message))

    async def _clear_legacy_guild_commands(self, guild):
        return None

    async def change_presence(self, **kwargs):
        self.presence_calls.append(kwargs)


def _make_bot(db: Database, guilds=()) -> ClubBot:
    return _OfflineBot(db, guilds)


def _use_config_db(
    db: Database,
    global_bot_log_channel_id: int | None = None,
    global_exec_role_id: int | None = None,
    global_admin_role_id: int | None = None,
):
    """config を差し替え、後始末用の関数を返す。

    config.bot_log_channel_id / exec_role_id / admin_role_id は環境変数由来の
    グローバルフォールバックで、全ギルドの GuildConfig に配られる。
    テストごとに明示して他所へ持ち越さない。
    """
    saved = (
        config._db,
        config.bot_log_channel_id,
        config.exec_role_id,
        config.admin_role_id,
    )
    config._db = db
    config.bot_log_channel_id = global_bot_log_channel_id
    config.exec_role_id = global_exec_role_id
    config.admin_role_id = global_admin_role_id
    config.clear_guild_cache()

    def restore() -> None:
        (
            config._db,
            config.bot_log_channel_id,
            config.exec_role_id,
            config.admin_role_id,
        ) = saved
        config.clear_guild_cache()

    return restore


def test_setup_guidance_names_the_three_commands():
    for auto_setup_ok in (True, False):
        text = build_setup_guidance(auto_setup_ok)
        # `/setup` は `/setup-status` にも部分一致するので、行ごと検査する
        for line in ("1. `/setup` —", "2. `/setup-status` —", "3. `/help` —"):
            assert line in text, (auto_setup_ok, line)
    # 自動作成できていないときだけ復旧手順を添える
    assert "ロールの管理" in build_setup_guidance(False)
    assert "ロールの管理" not in build_setup_guidance(True)


def test_guild_join_notice_falls_back_to_system_channel():
    """bot-log が無いギルド（＝最小権限招待の既定）でも案内が届く。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(501, name="general")
            other = FakeChannel(502, name="雑談")
            guild = FakeGuild(me=FakeMember(), system_channel=system, text_channels=[other])
            bot = _make_bot(db)

            await bot.on_guild_join(guild)

            assert len(system.sent) == 1, system.sent
            assert other.sent == []
            for command in ("/setup", "/setup-status", "/help"):
                assert command in system.sent[0], command
            # bot-log 限定の運用ログ経路は使わない（無言で捨てられるため）
            assert bot.log_calls == []
        finally:
            restore()
            await db.close()

    run(_main())


def test_guild_join_notice_tells_how_to_recover_without_permissions():
    """権限が無いときは、`/setup` で指定する手順と権限付与の両方を案内に含める。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(511, name="general")
            guild = FakeGuild(me=FakeMember(), system_channel=system)
            bot = _make_bot(db)

            await bot.on_guild_join(guild)

            message = system.sent[0]
            assert "ロールの管理" in message
            assert "チャンネルの管理" in message
            # 確実に効く手段（自分で作って /setup で指定）を必ず示す
            assert "/setup` で指定" in message
        finally:
            restore()
            await db.close()

    run(_main())


def test_no_permission_does_not_mark_auto_setup_done():
    """権限不足で何も作れなかったギルドでは AUTO_SETUP_DONE を立てない。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(me=FakeMember(), system_channel=FakeChannel(521))
            bot = _make_bot(db)

            assert await bot._ensure_guild_setup(guild) is False

            assert guild.created_roles == []
            assert guild.created_channels == []
            repo = SettingsRepository(db)
            assert await repo.get(G1, AUTO_SETUP_DONE_KEY) is None
            assert await repo.get(G1, AUTO_SETUP_COMPLETED_KEY) is None
            assert await repo.get(G1, "EXEC_ROLE_ID") is None
            assert await repo.get(G1, "ADMIN_ROLE_ID") is None
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") is None
            # 台帳登録・デフォルト設定（(a) の処理）は権限に関係なく行われる
            assert await repo.get(G1, "GUILD_NAME") == guild.name
            assert await GuildRepository(db).get(G1) is not None
        finally:
            restore()
            await db.close()

    run(_main())


def test_auto_setup_retries_after_permissions_granted():
    """権限を付けて再招待すれば作成が走る（マーカーで永久に止まらない）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(me=FakeMember())
            bot = _make_bot(db)
            assert await bot._ensure_guild_setup(guild) is False

            # 管理者が「ロールの管理」「チャンネルの管理」を付けて再招待した
            guild.me = FakeMember(manage_roles=True, manage_channels=True)
            assert await bot._ensure_guild_setup(guild) is True

            repo = SettingsRepository(db)
            assert [r.name for r in guild.created_roles] == [EXEC_ROLE_NAME, ADMIN_ROLE_NAME]
            assert [c.name for c in guild.created_channels] == [BOT_LOG_CHANNEL_NAME]
            assert await repo.get(G1, "EXEC_ROLE_ID") == str(guild.created_roles[0].id)
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") == str(guild.created_channels[0].id)
            assert await repo.get(G1, AUTO_SETUP_COMPLETED_KEY) is not None
            assert await repo.get(G1, AUTO_SETUP_DONE_KEY) is not None
        finally:
            restore()
            await db.close()

    run(_main())


def test_auto_setup_is_idempotent():
    """2周目は何も作らない（完了マーカーと、設定済みかどうかの二段構え）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(me=FakeMember(manage_roles=True, manage_channels=True))
            bot = _make_bot(db)
            assert await bot._ensure_guild_setup(guild) is True
            repo = SettingsRepository(db)
            marker = await repo.get(G1, AUTO_SETUP_COMPLETED_KEY)
            assert marker is not None
            assert await repo.get(G1, AUTO_SETUP_DONE_KEY) is not None

            assert await bot._ensure_guild_setup(guild) is True

            assert len(guild.created_roles) == 2
            assert len(guild.created_channels) == 1
            assert await repo.get(G1, AUTO_SETUP_COMPLETED_KEY) == marker
        finally:
            restore()
            await db.close()

    run(_main())


def test_existing_role_with_same_name_is_not_bound():
    """同名ロールに ID を紐付けない（そのロールを持つ人へ黙って権限を配らない）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(
                me=FakeMember(manage_roles=True, manage_channels=True),
                roles=[SimpleNamespace(id=1301, name=EXEC_ROLE_NAME)],
            )
            bot = _make_bot(db)
            repo = SettingsRepository(db)
            gconf = await config.for_guild(G1)

            assert await bot._auto_create_roles(guild, repo, gconf) is False

            assert [r.name for r in guild.created_roles] == [ADMIN_ROLE_NAME]
            assert await repo.get(G1, "EXEC_ROLE_ID") is None
            assert await repo.get(G1, "ADMIN_ROLE_ID") is not None
        finally:
            restore()
            await db.close()

    run(_main())


def test_existing_bot_log_channel_is_adopted_when_sendable():
    """同名の #bot-log は、送信できることを確かめてから採用する。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            existing = FakeChannel(1201, name=BOT_LOG_CHANNEL_NAME)
            # manage_channels は無い（自分では作れない）
            guild = FakeGuild(me=FakeMember(), text_channels=[existing])
            bot = _make_bot(db)
            repo = SettingsRepository(db)
            gconf = await config.for_guild(G1)

            assert await bot._auto_create_log_channel(guild, repo, gconf) is True
            assert guild.created_channels == []
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") == str(existing.id)

            # 送信できない同名チャンネルは採用しない
            db2 = await _connected_db()
            try:
                locked = FakeChannel(1202, name=BOT_LOG_CHANNEL_NAME, sendable=False)
                guild2 = FakeGuild(me=FakeMember(), text_channels=[locked])
                repo2 = SettingsRepository(db2)
                assert await bot._auto_create_log_channel(guild2, repo2, gconf) is False
                assert await repo2.get(G1, "BOT_LOG_CHANNEL_ID") is None
            finally:
                await db2.close()
        finally:
            restore()
            await db.close()

    run(_main())


def test_deleted_log_channel_setting_is_not_recreated():
    """管理者が消した設定を、次の起動で勝手に復活させない。

    完了マーカーが立っているギルドでは自動作成をやり直さない
    （`/settings_delete` やダッシュボードで空値 PATCH すると設定行は消える）。
    """

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(me=FakeMember(manage_roles=True, manage_channels=True))
            bot = _make_bot(db)
            assert await bot._ensure_guild_setup(guild) is True
            repo = SettingsRepository(db)

            # 「運用ログは要らない」と管理者が設定を消した
            await repo.delete(G1, "BOT_LOG_CHANNEL_ID")
            config.invalidate_guild(G1)

            assert await bot._ensure_guild_setup(guild) is True
            assert len(guild.created_channels) == 1, guild.created_channels
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") is None
        finally:
            restore()
            await db.close()

    run(_main())


def test_env_configured_roles_are_not_shadowed():
    """env だけで運用しているギルドに、空のロールを作って実効設定を奪わない。

    settings 行だけを見ると未設定に見えるが、`config.for_guild` の解決順は
    ギルド別 DB 設定 > 環境変数。作って保存すると settings が env を上書きし、
    幹部が L3 を失う。
    """

    async def _main():
        db = await _connected_db()
        exec_role = SimpleNamespace(id=7001, name="うちの幹部")
        admin_role = SimpleNamespace(id=7002, name="うちの管理者")
        restore = _use_config_db(
            db, global_exec_role_id=exec_role.id, global_admin_role_id=admin_role.id
        )
        try:
            guild = FakeGuild(
                me=FakeMember(manage_roles=True, manage_channels=True),
                roles=[exec_role, admin_role],
            )
            bot = _make_bot(db)
            repo = SettingsRepository(db)

            assert await bot._auto_create_roles(guild, repo, await config.for_guild(G1)) is True

            assert guild.created_roles == []
            assert await repo.get(G1, "EXEC_ROLE_ID") is None
            assert await repo.get(G1, "ADMIN_ROLE_ID") is None
        finally:
            restore()
            await db.close()

    run(_main())


def test_configured_but_missing_role_is_not_recreated():
    """settings の ID が解決できなくても作り直さない（毎起動作り続けないため）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "EXEC_ROLE_ID", "12345")  # 既に消えたロール
            await repo.set(G1, "ADMIN_ROLE_ID", "12346")
            config.invalidate_guild(G1)
            guild = FakeGuild(me=FakeMember(manage_roles=True, manage_channels=True))
            bot = _make_bot(db)

            assert await bot._auto_create_roles(guild, repo, await config.for_guild(G1)) is False

            assert guild.created_roles == []
            assert await repo.get(G1, "EXEC_ROLE_ID") == "12345"
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_skips_channels_that_cannot_receive_messages():
    """bot-log の設定がカテゴリを指していても、案内が消えない。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            category = FakeCategory(1401)
            system = FakeChannel(1402, name="general")
            await SettingsRepository(db).set(G1, "BOT_LOG_CHANNEL_ID", str(category.id))
            config.invalidate_guild(G1)
            guild = FakeGuild(
                me=FakeMember(), system_channel=system, other_channels=[category]
            )
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert system.sent == ["案内"]
        finally:
            restore()
            await db.close()

    run(_main())


def test_legacy_done_marker_does_not_block_retry():
    """旧 AUTO_SETUP_DONE が立っていても、自動セットアップを1度は再試行する。

    旧実装は権限不足で何も作れなかったギルドにもマーカーを立てていた。
    新キーを別名にしたのはこのため（同じキーを使い回すと、G3-5 が救おうと
    している当事者が永久に復旧できない）。
    """

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, AUTO_SETUP_DONE_KEY, "2024-01-01T00:00:00+09:00")
            guild = FakeGuild(me=FakeMember(manage_roles=True, manage_channels=True))
            bot = _make_bot(db)

            assert await bot._ensure_guild_setup(guild) is True

            assert len(guild.created_roles) == 2, guild.created_roles
            assert len(guild.created_channels) == 1
            assert await repo.get(G1, AUTO_SETUP_COMPLETED_KEY) is not None
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_uses_the_channel_adopted_during_a_failed_setup():
    """自動セットアップが未完了でも、その途中で入った設定を案内に反映する。

    ロールは権限不足で作れず（ok=False）、既存の #bot-log だけ採用された場合。
    ギルド設定キャッシュを成否によらず捨てていないと、案内が
    採用したチャンネルではなく system_channel に落ちる。
    """

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            bot_log = FakeChannel(1501, name=BOT_LOG_CHANNEL_NAME)
            system = FakeChannel(1502, name="general")
            guild = FakeGuild(
                me=FakeMember(),  # manage_roles も manage_channels も無い
                system_channel=system,
                text_channels=[bot_log],
            )
            bot = _make_bot(db)

            await bot.on_guild_join(guild)

            repo = SettingsRepository(db)
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") == str(bot_log.id)
            assert await repo.get(G1, AUTO_SETUP_COMPLETED_KEY) is None  # ロールは未設定
            assert len(bot_log.sent) == 1, bot_log.sent
            assert system.sent == []
            # ロールが揃っていないので復旧手順を添える
            assert "ロールの管理" in bot_log.sent[0]
        finally:
            restore()
            await db.close()

    run(_main())


def test_env_configured_log_channel_is_not_duplicated():
    """env で設定されたログチャンネルがこのギルドに実在するなら、作り直さない。"""

    async def _main():
        db = await _connected_db()
        existing = FakeChannel(1601, name="運用ログ")
        restore = _use_config_db(db, global_bot_log_channel_id=existing.id)
        try:
            guild = FakeGuild(
                me=FakeMember(manage_roles=True, manage_channels=True),
                text_channels=[existing],
            )
            bot = _make_bot(db)
            repo = SettingsRepository(db)

            result = await bot._auto_create_log_channel(guild, repo, await config.for_guild(G1))

            assert result is True
            assert guild.created_channels == []
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") is None
        finally:
            restore()
            await db.close()

    run(_main())


def test_configured_but_missing_log_channel_is_not_recreated():
    """settings のログチャンネル ID が解決できなくても作り直さない。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            repo = SettingsRepository(db)
            await repo.set(G1, "BOT_LOG_CHANNEL_ID", "12345")  # 既に消えたチャンネル
            config.invalidate_guild(G1)
            guild = FakeGuild(me=FakeMember(manage_roles=True, manage_channels=True))
            bot = _make_bot(db)

            result = await bot._auto_create_log_channel(guild, repo, await config.for_guild(G1))

            assert result is False
            assert guild.created_channels == []
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") == "12345"
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_prefers_bot_log_channel():
    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            bot_log = FakeChannel(601, name=BOT_LOG_CHANNEL_NAME)
            system = FakeChannel(602, name="general")
            await SettingsRepository(db).set(G1, "BOT_LOG_CHANNEL_ID", str(bot_log.id))
            config.invalidate_guild(G1)
            guild = FakeGuild(me=FakeMember(), system_channel=system, text_channels=[bot_log])
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert bot_log.sent == ["案内"]
            assert system.sent == []
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_goes_to_the_log_channel_just_created():
    """自動作成に成功した直後の案内は、作られた #bot-log に届く。

    _ensure_guild_setup がギルド設定キャッシュを捨てているかを見る。
    キャッシュを**温めてから**実行しないと、invalidate_guild を消しても
    緑のままになる（起動中の bot では常に温まっている）。
    """

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(611, name="general")
            guild = FakeGuild(
                me=FakeMember(manage_roles=True, manage_channels=True), system_channel=system
            )
            bot = _make_bot(db)
            # BOT_LOG_CHANNEL_ID 未設定の状態をキャッシュへ載せる
            assert (await config.for_guild(G1)).bot_log_channel_id is None

            await bot.on_guild_join(guild)

            created = guild.created_channels[0]
            assert len(created.sent) == 1, created.sent
            assert system.sent == []
            # 自動作成に成功したので、復旧手順は案内に出さない
            assert "ロールの管理" not in created.sent[0]
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_does_not_retry_the_same_channel():
    """bot-log と system_channel が同じでも、同じチャンネルへ2回送らない。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            # bot-log として設定されたチャンネルが system_channel でもある
            shared = FakeChannel(621, name=BOT_LOG_CHANNEL_NAME, fails=True)
            fallback = FakeChannel(622, name="雑談")
            await SettingsRepository(db).set(G1, "BOT_LOG_CHANNEL_ID", str(shared.id))
            config.invalidate_guild(G1)
            guild = FakeGuild(
                me=FakeMember(), system_channel=shared, text_channels=[shared, fallback]
            )
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert shared.attempts == 1, shared.attempts
            assert fallback.sent == ["案内"]
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_is_attempted_when_bot_member_is_unknown():
    """guild.me が None（権限を計算できない）でも案内を捨てない。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(631, name="general")
            guild = FakeGuild(me=None, system_channel=system)
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert system.sent == ["案内"]
        finally:
            restore()
            await db.close()

    run(_main())


def test_auto_setup_creates_nothing_when_bot_member_is_unknown():
    """guild.me が None のときは作成も採用もしない（分からないまま進めない）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            existing = FakeChannel(641, name=BOT_LOG_CHANNEL_NAME)
            guild = FakeGuild(me=None, text_channels=[existing])
            bot = _make_bot(db)

            assert await bot._ensure_guild_setup(guild) is False

            repo = SettingsRepository(db)
            # ヘルパも直接呼ぶ（_ensure_guild_setup の except が AttributeError を
            # 握りつぶすので、経由するだけでは me の判定漏れを検出できない）
            gconf = await config.for_guild(G1)
            assert await bot._auto_create_roles(guild, repo, gconf) is False
            assert await bot._auto_create_log_channel(guild, repo, gconf) is False

            assert guild.created_roles == []
            assert guild.created_channels == []
            assert await repo.get(G1, "BOT_LOG_CHANNEL_ID") is None
            assert await repo.get(G1, AUTO_SETUP_DONE_KEY) is None
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_ignores_bot_log_channel_of_another_guild():
    """env の BOT_LOG_CHANNEL_ID は全ギルドに配られる。他ギルドへは送らない。"""

    async def _main():
        db = await _connected_db()
        # 別ギルドのチャンネル ID がグローバル設定として入っている状況
        restore = _use_config_db(db, global_bot_log_channel_id=999999)
        try:
            system = FakeChannel(701, name="general")
            guild = FakeGuild(me=FakeMember(), system_channel=system)
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert system.sent == ["案内"]
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_falls_back_to_first_sendable_text_channel():
    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(801, name="general", sendable=False)
            locked = FakeChannel(802, name="お知らせ", sendable=False)
            open_channel = FakeChannel(803, name="雑談")
            guild = FakeGuild(
                me=FakeMember(),
                system_channel=system,
                text_channels=[locked, open_channel],
            )
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is True
            assert open_channel.sent == ["案内"]
            # 送信できないチャンネルには API を叩きに行かない
            assert system.attempts == 0
            assert locked.attempts == 0
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_returns_false_when_nowhere_to_send():
    """送信先が1つも無くても例外を投げない（他の処理を止めない）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            guild = FakeGuild(
                me=FakeMember(),
                system_channel=FakeChannel(901, sendable=False),
                text_channels=[FakeChannel(902, name="雑談", sendable=False)],
            )
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is False
        finally:
            restore()
            await db.close()

    run(_main())


def test_notice_stops_after_attempt_limit():
    """送信を試す数に上限がある（403 を連発して制限に触れない）。"""

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            channels = [
                FakeChannel(1000 + i, name=f"ch{i}", fails=True)
                for i in range(MAX_NOTICE_CHANNEL_ATTEMPTS + 3)
            ]
            guild = FakeGuild(me=FakeMember(), text_channels=channels)
            bot = _make_bot(db)

            assert await bot.send_guild_notice(guild, "案内") is False
            assert sum(c.attempts for c in channels) == MAX_NOTICE_CHANNEL_ATTEMPTS
        finally:
            restore()
            await db.close()

    run(_main())


def test_setup_notice_is_not_resent_on_restart():
    """再起動（on_ready）で案内を送り直さない。

    マーカーを読まなくなった（毎起動リトライになる）ことの最大の事故モードが
    「起動のたびに案内を投稿する」なので、on_ready を実際に通して確かめる。
    """

    async def _main():
        db = await _connected_db()
        restore = _use_config_db(db)
        try:
            system = FakeChannel(1101, name="general")
            guild = FakeGuild(me=FakeMember(), system_channel=system)
            bot = _make_bot(db, guilds=[guild])

            await bot.on_guild_join(guild)
            assert len(system.sent) == 1

            # 再起動（初回 on_ready）と、その後の再接続による on_ready
            await bot.on_ready()
            bot._initial_guild_setup_done = False
            await bot.on_ready()

            assert len(system.sent) == 1, system.sent
        finally:
            restore()
            await db.close()

    run(_main())


if __name__ == "__main__":
    test_fresh_schema_is_v2()
    print("test_fresh_schema_is_v2: OK")
    test_guild_registry_ensure()
    print("test_guild_registry_ensure: OK")
    test_v1_to_v2_migration_backfills_guilds()
    print("test_v1_to_v2_migration_backfills_guilds: OK")
    test_audit_log_isolation()
    print("test_audit_log_isolation: OK")
    test_reminders_log_repository_isolation()
    print("test_reminders_log_repository_isolation: OK")
    test_schedule_list_all_isolation()
    print("test_schedule_list_all_isolation: OK")
    test_setup_guidance_names_the_three_commands()
    print("test_setup_guidance_names_the_three_commands: OK")
    test_guild_join_notice_falls_back_to_system_channel()
    print("test_guild_join_notice_falls_back_to_system_channel: OK")
    test_guild_join_notice_tells_how_to_recover_without_permissions()
    print("test_guild_join_notice_tells_how_to_recover_without_permissions: OK")
    test_no_permission_does_not_mark_auto_setup_done()
    print("test_no_permission_does_not_mark_auto_setup_done: OK")
    test_auto_setup_retries_after_permissions_granted()
    print("test_auto_setup_retries_after_permissions_granted: OK")
    test_auto_setup_is_idempotent()
    print("test_auto_setup_is_idempotent: OK")
    test_existing_role_with_same_name_is_not_bound()
    print("test_existing_role_with_same_name_is_not_bound: OK")
    test_existing_bot_log_channel_is_adopted_when_sendable()
    print("test_existing_bot_log_channel_is_adopted_when_sendable: OK")
    test_deleted_log_channel_setting_is_not_recreated()
    print("test_deleted_log_channel_setting_is_not_recreated: OK")
    test_env_configured_roles_are_not_shadowed()
    print("test_env_configured_roles_are_not_shadowed: OK")
    test_configured_but_missing_role_is_not_recreated()
    print("test_configured_but_missing_role_is_not_recreated: OK")
    test_notice_skips_channels_that_cannot_receive_messages()
    print("test_notice_skips_channels_that_cannot_receive_messages: OK")
    test_legacy_done_marker_does_not_block_retry()
    print("test_legacy_done_marker_does_not_block_retry: OK")
    test_notice_uses_the_channel_adopted_during_a_failed_setup()
    print("test_notice_uses_the_channel_adopted_during_a_failed_setup: OK")
    test_env_configured_log_channel_is_not_duplicated()
    print("test_env_configured_log_channel_is_not_duplicated: OK")
    test_configured_but_missing_log_channel_is_not_recreated()
    print("test_configured_but_missing_log_channel_is_not_recreated: OK")
    test_notice_prefers_bot_log_channel()
    print("test_notice_prefers_bot_log_channel: OK")
    test_notice_goes_to_the_log_channel_just_created()
    print("test_notice_goes_to_the_log_channel_just_created: OK")
    test_notice_does_not_retry_the_same_channel()
    print("test_notice_does_not_retry_the_same_channel: OK")
    test_notice_is_attempted_when_bot_member_is_unknown()
    print("test_notice_is_attempted_when_bot_member_is_unknown: OK")
    test_auto_setup_creates_nothing_when_bot_member_is_unknown()
    print("test_auto_setup_creates_nothing_when_bot_member_is_unknown: OK")
    test_notice_ignores_bot_log_channel_of_another_guild()
    print("test_notice_ignores_bot_log_channel_of_another_guild: OK")
    test_notice_falls_back_to_first_sendable_text_channel()
    print("test_notice_falls_back_to_first_sendable_text_channel: OK")
    test_notice_returns_false_when_nowhere_to_send()
    print("test_notice_returns_false_when_nowhere_to_send: OK")
    test_notice_stops_after_attempt_limit()
    print("test_notice_stops_after_attempt_limit: OK")
    test_setup_notice_is_not_resent_on_restart()
    print("test_setup_notice_is_not_resent_on_restart: OK")
    print("全テスト成功")
