"""
<<<<<<< HEAD
鳥人間サークル統合運用 Discord Bot エントリーポイント（改訂版）

- .env 読み込み・必須設定検証（欠落時は起動停止）
- SQLite 初期化・初期チーム・初期メンバー投入
=======
鳥人間サークル統合運用 Discord Bot エントリーポイント（マルチテナント版）

- .env 読み込み・必須設定検証（DISCORD_TOKEN のみ必須。GUILD_ID は後方互換用の任意指定）
- SQLite 初期化・ギルドごとの初期チーム/初期設定投入
>>>>>>> 803617a (v4.0)
- 各 Cog 読み込み
- スラッシュコマンド同期（参加中の全ギルド）
- on_guild_join による新規ギルド自動セットアップ
- グローバルエラーハンドラ
（改訂版: 設定をデータベースから読み込み、ボットコマンドでカスタマイズ可能に）
"""
from __future__ import annotations

import asyncio
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import INITIAL_TEAMS, config
from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from services.sheets_service import SheetsService
from services.todoist_service import TodoistService
from utils.db import Database
from utils.embeds import error_embed
from utils.logger import get_logger, setup_logging
from utils.parser import InvalidDatetimeError, now, to_iso
from utils.permissions import PermissionDenied

log = get_logger("bot")

COGS = [
    "cogs.core",
    "cogs.schedule",
    "cogs.tasks",
    "cogs.members",
    "cogs.reminders",
    "cogs.reports",
    "cogs.sheets",
    "cogs.layer_tracking",
    "cogs.settings",  # 設定管理コグを追加
]

# on_guild_join / 起動時の自動セットアップで投入するギルド別デフォルト設定
# （ID 系は自動作成に成功した場合のみ保存される）
AUTO_SETUP_DONE_KEY = "AUTO_SETUP_DONE"
BOT_LOG_CHANNEL_NAME = "bot-log"
EXEC_ROLE_NAME = "幹部"
ADMIN_ROLE_NAME = "Bot管理者"


class ClubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True            # Guild Members
        intents.messages = True           # Guild Messages
        intents.message_content = True    # Message Content（最小限）
        intents.reactions = True          # Guild Message Reactions
        intents.dm_messages = True        # Direct Messages
        super().__init__(command_prefix="!club ", intents=intents, help_command=None)

        self.db = Database(config.db_path)
        self.todoist = TodoistService()
        self.sheets = SheetsService()
        self._initial_guild_setup_done = False

    async def setup_hook(self) -> None:
<<<<<<< HEAD
        # DB 接続・スキーマ初期化
        await self.db.connect()
        
        # データベースから設定を読み込む（環境変数が優先）
        await config.load_from_db(self.db)
        
        # サービスの設定を再読み込み
        self.todoist.reload_config()
        self.sheets.reload_config()
        
        # 初期チーム投入
        await self._seed_teams()
=======
        # DB 接続・スキーマ初期化（旧 DB は guild_id 自動マイグレーション）
        await self.db.connect()

        # データベースから設定を読み込む（環境変数が優先。
        # GUILD_ID 指定時はそのギルドの設定をグローバル設定としても読み込む）
        await config.load_from_db(self.db)

        # サービスの設定を再読み込み
        self.todoist.reload_config()
        self.sheets.reload_config()

        # レガシーギルド（GUILD_ID 指定時）の初期チーム投入
        if config.guild_id:
            await self._seed_teams(config.guild_id)
>>>>>>> 803617a (v4.0)

        # Cog 読み込み
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Cog 読み込み: %s", cog)
            except Exception as e:  # noqa: BLE001
                log.exception("Cog 読み込み失敗 %s: %s", cog, e)

        # スラッシュコマンド同期
        # - GUILD_ID 指定時: そのギルドへ即時反映（後方互換）
        # - それ以外: グローバル同期 + on_ready で参加中全ギルドへ個別同期
        if config.guild_id:
            guild = discord.Object(id=config.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("スラッシュコマンドを同期（guild=%s）: %d 件", config.guild_id, len(synced))
        else:
            synced = await self.tree.sync()
            log.info("スラッシュコマンドをグローバル同期: %d 件", len(synced))

        # グローバルエラーハンドラ
        self.tree.error(self.on_app_command_error)

<<<<<<< HEAD
    async def _seed_teams(self) -> None:
        """
        初期チームを投入（改訂版 10.1）
        """
        repo = MemberRepository(self.db)
        for key, name in INITIAL_TEAMS:
            await repo.upsert_team(key, name)
        log.info("初期チームを確認・投入しました（%d チーム）", len(INITIAL_TEAMS))
=======
    async def _seed_teams(self, guild_id: int) -> None:
        """
        初期チームを投入（改訂版 10.1）。guild_id 単位で冪等。
        """
        repo = MemberRepository(self.db)
        for key, name in INITIAL_TEAMS:
            await repo.upsert_team(guild_id, key, name)
        log.info("初期チームを確認・投入しました（guild=%s, %d チーム）",
                 guild_id, len(INITIAL_TEAMS))
>>>>>>> 803617a (v4.0)

    # ------------------------------------------------------------------
    # ギルド自動セットアップ
    # ------------------------------------------------------------------
    async def _ensure_guild_setup(self, guild: discord.Guild) -> None:
        """
        ギルドの初期セットアップを冪等に行う。

        (a) settings にギルド用デフォルト設定を INSERT（未存在時のみ）
        (b) INITIAL_TEAMS を guild_id 付きで作成
        (c) 初回のみ: ロール（幹部/Bot管理者/各班リーダー/各班）と
            bot-log チャンネルを自動作成し、ID を settings に保存
            （権限不足・API 失敗時はログに残して続行）
        """
        repo = SettingsRepository(self.db)

        # (a) デフォルト設定（存在しないキーのみ。ID 系は env フォールバックを
        #     活かすため空値は入れない）
        try:
            await repo.set_if_absent(guild.id, "GUILD_NAME", guild.name)
            await repo.set_if_absent(guild.id, "SETUP_VERSION", "1")
            await repo.set_if_absent(guild.id, "SETUP_AT", to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("ギルド初期設定の保存に失敗 (guild=%s): %s", guild.id, e)

        # (b) 初期チーム（冪等）
        try:
            await self._seed_teams(guild.id)
        except Exception as e:  # noqa: BLE001
            log.warning("初期チーム投入に失敗 (guild=%s): %s", guild.id, e)

        # (c) ロール・ログチャンネルの自動作成（初回のみ）
        try:
            done = await repo.get(guild.id, AUTO_SETUP_DONE_KEY)
        except Exception as e:  # noqa: BLE001
            log.warning("自動セットアップ状態の取得に失敗 (guild=%s): %s", guild.id, e)
            return
        if done:
            return

        me = guild.me
        if me is not None:
            perms = me.guild_permissions
            if perms.manage_roles:
                await self._auto_create_roles(guild, repo)
            else:
                log.warning("ロール自動作成をスキップ（manage_roles 権限なし, guild=%s）", guild.id)
            if perms.manage_channels:
                await self._auto_create_log_channel(guild, repo)
            else:
                log.warning("ログチャンネル自動作成をスキップ（manage_channels 権限なし, guild=%s）",
                            guild.id)

        try:
            await repo.set(guild.id, AUTO_SETUP_DONE_KEY, to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("自動セットアップ完了マーカーの保存に失敗 (guild=%s): %s", guild.id, e)
        config.invalidate_guild(guild.id)
        log.info("ギルド自動セットアップが完了しました: %s (id=%s)", guild.name, guild.id)

    async def _auto_create_roles(self, guild: discord.Guild,
                                 repo: SettingsRepository) -> None:
        """幹部/Bot管理者/各班リーダー/各班ロールを作成し ID を settings に保存する。"""
        async def create_role(name: str) -> discord.Role | None:
            try:
                role = await guild.create_role(name=name, mentionable=True,
                                               reason="club-bot 自動セットアップ")
                log.info("ロール作成: %s (%s) [guild=%s]", role.name, role.id, guild.id)
                return role
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("ロール作成失敗: %s [guild=%s]: %s", name, guild.id, e)
                return None

        role = await create_role(EXEC_ROLE_NAME)
        if role is not None:
            await repo.set_if_absent(guild.id, "EXEC_ROLE_ID", str(role.id))

        role = await create_role(ADMIN_ROLE_NAME)
        if role is not None:
            await repo.set_if_absent(guild.id, "ADMIN_ROLE_ID", str(role.id))

        leader_ids: list[str] = []
        primary_map: list[str] = []
        member_repo = MemberRepository(self.db)
        for team_key, team_name in INITIAL_TEAMS:
            leader = await create_role(f"{team_name}班リーダー")
            if leader is not None:
                leader_ids.append(str(leader.id))
                # teams テーブルの leader_role_id も更新（冪等 upsert）
                try:
                    await member_repo.upsert_team(guild.id, team_key, team_name,
                                                  leader_role_id=str(leader.id))
                except Exception as e:  # noqa: BLE001
                    log.warning("班リーダーロール ID の保存に失敗 (guild=%s, %s): %s",
                                guild.id, team_key, e)
            member_role = await create_role(f"{team_name}班")
            if member_role is not None:
                primary_map.append(f"{team_key}:{member_role.id}")

        if leader_ids:
            await repo.set_if_absent(guild.id, "LEADER_ROLE_IDS", ",".join(leader_ids))
        if primary_map:
            await repo.set_if_absent(guild.id, "PRIMARY_TEAM_ROLE_IDS", ",".join(primary_map))

    async def _auto_create_log_channel(self, guild: discord.Guild,
                                       repo: SettingsRepository) -> None:
        """bot-log チャンネルを作成し ID を settings に保存する。"""
        try:
            channel = await guild.create_text_channel(
                BOT_LOG_CHANNEL_NAME, reason="club-bot 自動セットアップ")
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("ログチャンネル作成失敗 [guild=%s]: %s", guild.id, e)
            return
        log.info("ログチャンネル作成: #%s (%s) [guild=%s]", channel.name, channel.id, guild.id)
        await repo.set_if_absent(guild.id, "BOT_LOG_CHANNEL_ID", str(channel.id))

    async def _sync_guild_commands(self, guild: discord.Guild) -> None:
        """ギルドへスラッシュコマンドを同期する。"""
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("スラッシュコマンドを同期（guild=%s）: %d 件", guild.id, len(synced))
        except Exception as e:  # noqa: BLE001
            log.warning("コマンド同期失敗（guild=%s）: %s", guild.id, e)

    # ------------------------------------------------------------------
    # イベント
    # ------------------------------------------------------------------
    async def on_ready(self) -> None:
        log.info("ログイン完了: %s (id=%s)", self.user, self.user.id if self.user else "?")
        await self.change_presence(activity=discord.Game(name="鳥人間サークル運営"))

        # 参加中の全ギルドをセットアップ（起動時に初期チームの存在を保証）し、
        # コマンドをギルド同期する。初回の on_ready のみ実行し、
        # それ以降の新規参加は on_guild_join で処理する。
        if not self._initial_guild_setup_done:
            self._initial_guild_setup_done = True
            for guild in list(self.guilds):
                try:
                    await self._ensure_guild_setup(guild)
                except Exception as e:  # noqa: BLE001
                    log.exception("ギルドセットアップ失敗 %s (id=%s): %s",
                                  guild.name, guild.id, e)
                await self._sync_guild_commands(guild)

        # 起動ログをチャンネルへ
        await self.log_to_channel(f"Bot を起動しました: {self.user}")

<<<<<<< HEAD
    async def log_to_channel(self, message: str) -> None:
        """#bot-log チャンネルへログを投稿する（改訂版 11.1.2）"""
        if not config.bot_log_channel_id:
            return
        channel = self.get_channel(config.bot_log_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(config.bot_log_channel_id)
            except Exception:
                return
=======
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """新規ギルド参加時の自動セットアップ（招待するだけで利用開始できる）。"""
        log.info("新規ギルドに参加しました: %s (id=%s)", guild.name, guild.id)
>>>>>>> 803617a (v4.0)
        try:
            await self._ensure_guild_setup(guild)
        except Exception as e:  # noqa: BLE001
            log.exception("on_guild_join セットアップ失敗 (guild=%s): %s", guild.id, e)
        await self._sync_guild_commands(guild)
        await self.log_to_channel(
            f"新規ギルドに参加し、自動セットアップを実行しました: {guild.name} (id={guild.id})",
            guild_id=guild.id)

    async def log_to_channel(self, message: str, guild_id: int | None = None) -> None:
        """
        #bot-log チャンネルへログを投稿する（改訂版 11.1.2）。

        guild_id 指定時はそのギルドのログチャンネルのみ。
        未指定時は参加中の全ギルドのログチャンネルへブロードキャストする。
        """
        channel_ids: list[int] = []
        if guild_id is not None:
            try:
                gconf = await config.for_guild(guild_id)
            except Exception:  # noqa: BLE001
                return
            if gconf.bot_log_channel_id:
                channel_ids.append(gconf.bot_log_channel_id)
        else:
            for guild in list(self.guilds):
                try:
                    gconf = await config.for_guild(guild.id)
                except Exception:  # noqa: BLE001
                    continue
                if gconf.bot_log_channel_id and gconf.bot_log_channel_id not in channel_ids:
                    channel_ids.append(gconf.bot_log_channel_id)
            # 起動直後など guilds キャッシュが空の場合はレガシー設定へフォールバック
            if not channel_ids and config.bot_log_channel_id:
                channel_ids.append(config.bot_log_channel_id)

        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except Exception:  # noqa: BLE001
                    continue
            try:
                await channel.send(f"```\n{message[:1900]}\n```")
            except Exception as e:  # noqa: BLE001
                log.warning("bot-log への投稿失敗: %s", e)

    async def on_app_command_error(self, interaction: discord.Interaction,
                                   error: app_commands.AppCommandError) -> None:
        """
        全スラッシュコマンドのエラーを集約（改訂版 14）
        """
        # ラップされた元例外を取り出す
        original = getattr(error, "original", error)

        if isinstance(error, PermissionDenied):
            embed = error_embed(str(error), code="PERMISSION_DENIED")
        elif isinstance(original, InvalidDatetimeError):
            embed = error_embed(str(original), code="INVALID_DATETIME")
        elif isinstance(error, app_commands.CommandOnCooldown):
            embed = error_embed("実行間隔が短すぎます。少々待って再試行してください。")
        else:
            embed = error_embed("予期せぬエラーが発生しました。時間をおいて再試行してください。")
            log.exception("未処理のコマンドエラー: %s", original)
            await self.log_to_channel(
                f"[ERROR] {interaction.command}: {original!r}",
                guild_id=interaction.guild.id if interaction.guild else None)

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def main() -> None:
    setup_logging()

    # .env の読み込み元を確認（デバッグ用）
    _env_src = config.loaded_env_path()
    if _env_src:
        log.info(".env を読み込みました: %s", _env_src)
    else:
        log.info(".env ファイルは見つかりませんでした（OS 環境変数のみで動作します）")

    missing = config.validate()
    if missing:
        log.error("必須設定が不足しています: %s", ", ".join(missing))
        if _env_src:
            log.error("読み込んだ .env: %s（この中の記載を確認してください）", _env_src)
        else:
            log.error(
                ".env が見つかりませんでした。config.py と同じ階層、"
                "またはその1つ上（プロジェクト直下）に .env を置いてください。"
            )
        log.error(".env を確認してください。起動を中止します。")
        sys.exit(1)

    if not config.guild_id:
        log.info("GUILD_ID 未指定: マルチテナントモードで起動します"
                 "（参加中の全ギルドで独立して動作します）")

    bot = ClubBot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("停止シグナルを受信しました。")
