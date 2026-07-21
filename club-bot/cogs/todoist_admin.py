"""
Todoist 管理モジュール。

Todoist API トークンをギルド単位で登録・確認・削除する管理者向けコマンド群。

セキュリティ方針:
- トークンはスラッシュコマンドの引数では受け取らない
  （コマンドのオプション値は Discord の履歴に残るため）。
  /todoist-setup は引数なしで実行し、ephemeral なボタンから Modal を開き、
  Modal の入力欄でトークンを受け取る（Modal の入力値は履歴に残らない）。
- トークンは Fernet で暗号化して todoist_configs に保存する（平文を DB に保存しない）。
- 暗号鍵 ENCRYPTION_KEY は .env のみに保持する。
- トークン文字列は応答・Embed・ログ・例外・監査ログに一切出力しない
  （マスク表示すら行わず、トークン由来の情報を表示しない）。
- 応答はすべて ephemeral。Modal 送信者がコマンド実行者と同一であることを検証する。
- Modal のタイムアウト・キャンセルでは DB を変更しない。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.audit_log_repository import AuditLogRepository
from repositories.todoist_config_repository import TodoistConfigRepository
from services import todoist_service
from utils import crypto
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

log = get_logger("todoist_admin")

DEFAULT_LABEL_NAME = "今日やること"


class TodoistSetupModal(discord.ui.Modal, title="Todoist トークン登録"):
    """トークンを安全に入力するための Modal（入力値はチャンネル履歴に残らない）。"""

    token_input = discord.ui.TextInput(
        label="Todoist API トークン",
        placeholder="Todoist 設定 > インテグレーション で取得したトークン",
        required=True,
        min_length=1,
        max_length=200,
    )
    project_input = discord.ui.TextInput(
        label="プロジェクトID（任意）",
        required=False,
        max_length=50,
    )
    label_input = discord.ui.TextInput(
        label="「今日やること」ラベル名（任意）",
        required=False,
        default=DEFAULT_LABEL_NAME,
        max_length=50,
    )

    def __init__(self, cog: "TodoistAdmin", guild_id: int, owner_id: int,
                 existing: dict | None):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.existing = existing

    async def on_submit(self, interaction: discord.Interaction):
        # コマンド実行者と同一であることを検証
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        token = (self.token_input.value or "").strip()
        project_id = (self.project_input.value or "").strip() or None
        label_name = (self.label_input.value or "").strip() or None
        await self.cog.register_token(
            interaction, guild_id=self.guild_id, token=token,
            project_id=project_id, label_name=label_name, existing=self.existing)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception) -> None:
        # トークンを含めず、型名のみ記録する
        log.warning("Todoist 登録 Modal でエラー (guild=%s): %s",
                    self.guild_id, type(error).__name__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=error_embed("登録に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True)
            else:
                await interaction.response.send_message(
                    embed=error_embed("登録に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True)
        except Exception:  # noqa: BLE001
            pass


class TodoistSetupView(discord.ui.View):
    """/todoist-setup の ephemeral メッセージに付けるボタン View。"""

    def __init__(self, cog: "TodoistAdmin", guild_id: int, owner_id: int,
                 existing: dict | None):
        super().__init__(timeout=300)  # 5分で無効化（タイムアウト時は DB 不変）
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.existing = existing

    @discord.ui.button(label="トークンを入力する", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction,
                         button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("このボタンはコマンドの実行者のみ使用できます。"),
                ephemeral=True)
            return
        await interaction.response.send_modal(
            TodoistSetupModal(self.cog, self.guild_id, self.owner_id, self.existing))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class TodoistAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = TodoistConfigRepository(bot.db)
        self.audit_repo = AuditLogRepository(bot.db)

    # ==================================================================
    # /todoist-setup（引数なし → ボタン → Modal でトークン入力）
    # ==================================================================
    @app_commands.command(name="todoist-setup",
                          description="このサーバーの Todoist API トークンを登録します（管理者）。")
    @app_commands.check(is_admin)
    async def todoist_setup(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        if not crypto.is_encryption_ready():
            await interaction.response.send_message(
                embed=error_embed(
                    "暗号鍵 `ENCRYPTION_KEY` が未設定または不正です。\n"
                    "サーバー管理者に `.env` への設定を依頼してください。\n"
                    "（生成: `python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\"`）"),
                ephemeral=True)
            return

        existing = await self.repo.get(guild_id)
        view = TodoistSetupView(self, guild_id, interaction.user.id, existing)
        desc = ("下のボタンを押すと入力フォーム（Modal）が開きます。\n"
                "Todoist API トークンを入力してください。\n\n"
                "※ 入力内容はチャンネルの履歴に残りません。\n"
                "※ トークンは暗号化して保存され、表示・ログには出力されません。\n"
                "※ フォームは5分で無効になります。")
        if existing:
            desc = "現在の設定は上書きされます。\n\n" + desc
        await interaction.response.send_message(
            embed=info_embed("Todoist トークン登録", desc),
            view=view, ephemeral=True)

    async def register_token(self, interaction: discord.Interaction, *,
                             guild_id: int, token: str,
                             project_id: str | None, label_name: str | None,
                             existing: dict | None) -> None:
        """Modal 送信後の登録処理（検証・暗号化・保存・監査ログ）。

        平文トークンはこの関数スコープでのみ使用し、保存・出力しない。
        """
        if not token:
            await interaction.followup.send(
                embed=error_embed("トークンが空です。"), ephemeral=True)
            return

        valid = await todoist_service.validate_token(token)
        if not valid:
            await interaction.followup.send(
                embed=error_embed(
                    "Todoist トークンが無効です。トークンを確認して再試行してください。",
                    code="TODOIST_API_FAILED"),
                ephemeral=True)
            return

        encrypted = crypto.encrypt_token(token)
        del token  # 平文参照を早期に破棄

        label = (label_name
                 or (existing["today_label_name"] if existing else DEFAULT_LABEL_NAME))
        project = (project_id
                   or (existing["project_id"] if existing else None)) or None

        await self.repo.upsert(guild_id, encrypted, project, label,
                               str(interaction.user.id))
        await self.audit_repo.record(
            guild_id, str(interaction.user.id), "todoist.setup",
            detail="トークンを更新" if existing else "トークンを新規登録")

        desc = "このサーバーの Todoist 連携を有効化しました。"
        if project:
            desc += f"\nプロジェクトID: `{project}`"
        desc += f"\nラベル名: `{label}`"
        desc += "\n\n※ トークンは暗号化して保存され、表示・ログには出力されません。"
        await interaction.followup.send(
            embed=success_embed(
                "Todoist を設定しました" + ("（上書き）" if existing else ""),
                desc, executor=interaction.user.display_name),
            ephemeral=True)

    # ==================================================================
    # /todoist-status
    # ==================================================================
    @app_commands.command(name="todoist-status",
                          description="このサーバーの Todoist 連携状態を表示します（管理者）。")
    @app_commands.check(is_admin)
    async def todoist_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        enc_ok = crypto.is_encryption_ready()
        cfg = await self.repo.get(guild_id)
        if not cfg:
            await interaction.followup.send(
                embed=info_embed(
                    "Todoist 連携状態",
                    "このサーバーでは Todoist が未設定です。\n"
                    "管理者が `/todoist-setup` でトークンを登録してください。"),
                ephemeral=True)
            return

        # 復号可否のみ検査し、内容は表示しない
        decrypt_ok = False
        if enc_ok:
            try:
                crypto.decrypt_token(cfg["api_token_encrypted"])
                decrypt_ok = True
            except crypto.TokenDecryptError:
                decrypt_ok = False

        embed = info_embed("Todoist 連携状態")
        embed.add_field(
            name="状態",
            value="✅ 有効" if cfg["enabled_flag"] else "⛔ 無効", inline=True)
        embed.add_field(
            name="プロジェクトID",
            value=f"`{cfg['project_id']}`" if cfg["project_id"] else "未設定", inline=True)
        embed.add_field(
            name="ラベル名", value=f"`{cfg['today_label_name']}`", inline=True)
        embed.add_field(
            name="暗号鍵（ENCRYPTION_KEY）",
            value="✅ 設定済み" if enc_ok else "❌ 未設定/不正", inline=True)
        embed.add_field(
            name="トークン復号テスト",
            value="✅ 復号可能" if decrypt_ok else "❌ 復号不可（再登録が必要）",
            inline=True)
        embed.add_field(
            name="最終更新", value=cfg["updated_at"], inline=False)
        embed.set_footer(text="トークン本体はセキュリティのため表示されません")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /todoist-remove
    # ==================================================================
    @app_commands.command(name="todoist-remove",
                          description="このサーバーの Todoist 設定を削除します（管理者）。")
    @app_commands.check(is_admin)
    async def todoist_remove(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        deleted = await self.repo.delete(guild_id)
        if not deleted:
            await interaction.followup.send(
                embed=info_embed("Todoist 設定",
                                 "このサーバーには Todoist 設定が登録されていません。"),
                ephemeral=True)
            return
        await self.audit_repo.record(guild_id, str(interaction.user.id),
                                     "todoist.remove")
        await interaction.followup.send(
            embed=success_embed("Todoist 設定を削除しました",
                                "このサーバーの Todoist 連携は無効になりました。\n"
                                "再登録は `/todoist-setup` で行えます。",
                                executor=interaction.user.display_name),
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TodoistAdmin(bot))
