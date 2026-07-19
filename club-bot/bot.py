"""
鳥人間サークル統合運用 Discord Bot エントリーポイント（改訂版）

- .env 読み込み・必須設定検証（欠落時は起動停止）
- SQLite 初期化・初期チーム・初期メンバー投入
- 各 Cog 読み込み
- スラッシュコマンド同期
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
from services.sheets_service import SheetsService
from services.todoist_service import TodoistService
from utils.db import Database
from utils.embeds import error_embed
from utils.logger import get_logger, setup_logging
from utils.parser import InvalidDatetimeError
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

    async def setup_hook(self) -> None:
        # DB 接続・スキーマ初期化
        await self.db.connect()
        
        # データベースから設定を読み込む（環境変数が優先）
        await config.load_from_db(self.db)
        
        # サービスの設定を再読み込み
        self.todoist.reload_config()
        self.sheets.reload_config()
        
        # 初期チーム投入
        await self._seed_teams()

        # Cog 読み込み
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Cog 読み込み: %s", cog)
            except Exception as e:  # noqa: BLE001
                log.exception("Cog 読み込み失敗 %s: %s", cog, e)

        # スラッシュコマンド同期（GUILD_ID 指定で即時反映）
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

    async def _seed_teams(self) -> None:
        """
        初期チームを投入（改訂版 10.1）
        """
        repo = MemberRepository(self.db)
        for key, name in INITIAL_TEAMS:
            await repo.upsert_team(key, name)
        log.info("初期チームを確認・投入しました（%d チーム）", len(INITIAL_TEAMS))

    async def on_ready(self) -> None:
        log.info("ログイン完了: %s (id=%s)", self.user, self.user.id if self.user else "?")
        await self.change_presence(activity=discord.Game(name="鳥人間サークル運営"))
        # 起動ログをチャンネルへ
        await self.log_to_channel(f"Bot を起動しました: {self.user}")

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
            await self.log_to_channel(f"[ERROR] {interaction.command}: {original!r}")

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

    bot = ClubBot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("停止シグナルを受信しました。")
