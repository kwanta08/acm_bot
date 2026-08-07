"""
Directus 管理モジュール。

各サーバーの管理者がセルフサービスで Directus（DB の閲覧・編集 UI）の
アカウントを発行・確認・失効できる管理者向けコマンド群。

発行されたアカウントには Directus 側でカスタムフィールド `guild_id` が
設定され、行レベル権限フィルタによって **自分のサーバーのデータだけ** が
見える状態になる（Directus 側の設定手順は docs/DIRECTUS.md）。

セキュリティ方針:
- メールアドレスはスラッシュコマンドの引数では受け取らない
  （コマンドのオプション値は Discord の履歴に残るため）。
  /directus-setup は引数なしで実行し、ephemeral なボタンから Modal を開く。
- パスワードは bot が生成も保存もしない（Directus のメール招待フローに委ねる）。
  directus_access テーブルには秘密情報を保存しない。
- 管理トークンは応答・Embed・ログ・監査ログに一切出力しない。
- 応答はすべて ephemeral。Modal 送信者がコマンド実行者と同一であることを検証する。
- Modal のタイムアウト・キャンセルでは DB を変更しない。
"""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from repositories.audit_log_repository import AuditLogRepository
from repositories.directus_access_repository import (
    STATUS_REVOKED,
    DirectusAccessRepository,
)
from services import directus_service
from services.directus_service import (
    DirectusEmailInUse,
    DirectusError,
    DirectusUnavailable,
)
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

log = get_logger("directus_admin")

# 簡易的なメールアドレス形式チェック（厳密な検証は Directus 側に委ねる）
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# status の表示名
_STATUS_LABELS = {
    "invited": "✉️ 招待済み（メールから初期設定を完了してください）",
    "active": "✅ 利用中",
    "revoked": "⛔ 失効済み",
}

_NOT_CONFIGURED_DESC = (
    "この Bot ではまだ Directus 連携が有効になっていません。\n"
    "Bot の運用者が `.env` に `DIRECTUS_URL` / `DIRECTUS_ADMIN_TOKEN` / "
    "`DIRECTUS_ROLE_ID` を設定すると利用できます。\n\n"
    "詳しい手順は `docs/DIRECTUS.md` を参照してください。"
)


class DirectusSetupModal(discord.ui.Modal, title="Directus アカウント発行"):
    """メールアドレスを入力するための Modal（入力値は履歴に残らない）。"""

    email_input = discord.ui.TextInput(
        label="招待メールの送信先アドレス",
        placeholder="例: club-admin@example.com",
        required=True,
        min_length=5,
        max_length=200,
    )

    def __init__(self, cog: DirectusAdmin, guild_id: int, owner_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        # コマンド実行者と同一であることを検証
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = await self.cog.issue_access(
            self.guild_id, (self.email_input.value or "").strip(),
            actor_id=str(interaction.user.id),
            actor_name=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction,
                       error: Exception) -> None:
        # メールアドレスを含めず、型名のみ記録する
        log.warning("Directus 発行 Modal でエラー (guild=%s): %s",
                    self.guild_id, type(error).__name__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=error_embed("発行に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True)
            else:
                await interaction.response.send_message(
                    embed=error_embed("発行に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True)
        # エラー通知の送信自体に失敗した場合はこれ以上できることがないため握りつぶす
        except Exception:  # noqa: BLE001, S110
            pass


class DirectusSetupView(discord.ui.View):
    """/directus-setup の ephemeral メッセージに付けるボタン View。"""

    def __init__(self, cog: DirectusAdmin, guild_id: int, owner_id: int):
        super().__init__(timeout=300)  # 5分で無効化（タイムアウト時は DB 不変）
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id

    @discord.ui.button(label="メールアドレスを入力する",
                       style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction,
                         button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("このボタンはコマンドの実行者のみ使用できます。"),
                ephemeral=True)
            return
        await interaction.response.send_modal(
            DirectusSetupModal(self.cog, self.guild_id, self.owner_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class DirectusAdmin(commands.Cog):
    """Directus アクセス発行コグ。

    client_factory を渡すと Directus クライアントを差し替えられる
    （テストで外部 HTTP を発生させないため）。未指定なら環境変数から生成する。
    """

    def __init__(self, bot: commands.Bot, client_factory=None):
        self.bot = bot
        self.repo = DirectusAccessRepository(bot.db)
        self.audit_repo = AuditLogRepository(bot.db)
        self._client_factory = client_factory or directus_service.client_from_config

    def _client(self):
        """Directus クライアントを返す。未設定なら None（例外にしない）。"""
        try:
            return self._client_factory()
        except DirectusUnavailable:
            return None

    # ==================================================================
    # 業務ロジック（Embed を返す。コマンド本体から呼び出す）
    # ==================================================================
    async def issue_access(self, guild_id: int, email: str, actor_id: str,
                           actor_name: str | None = None) -> discord.Embed:
        """Directus アカウントを招待し、発行状況を記録する。"""
        client = self._client()
        if client is None:
            return info_embed("Directus 連携は未設定です", _NOT_CONFIGURED_DESC)

        if not _EMAIL_RE.match(email):
            return error_embed("メールアドレスの形式が正しくありません。")

        try:
            user_id = await client.invite_user(email, guild_id)
        except DirectusEmailInUse:
            return error_embed(
                "このメールアドレスは既に Directus に登録されています。\n"
                "別のアドレスを使うか、Bot の運用者に既存アカウントの"
                "取り扱いを相談してください。\n"
                "（1つのアカウントは1つのサーバーにしか紐付けられません）",
                code="DIRECTUS_EMAIL_IN_USE")
        except (DirectusError, DirectusUnavailable):
            return error_embed(
                "Directus への招待に失敗しました。時間をおいて再試行するか、"
                "Bot の運用者に連絡してください。",
                code="DIRECTUS_API_FAILED")

        await self.repo.upsert(guild_id, user_id, email, actor_id)
        await self.audit_repo.record(guild_id, actor_id, "directus.setup",
                                     detail="アクセスを発行")

        desc = (
            "招待メールを送信しました。メールのリンクからパスワードを設定すると"
            "ログインできます。\n\n"
            f"Directus の URL: {client.base_url}\n\n"
            "※ ログイン後に見えるのは **このサーバーのデータだけ** です。\n"
            "※ パスワードは Bot 側では生成・保存していません。\n"
            "※ メールが届かない場合は Bot の運用者に連絡してください"
            "（招待リンクを手動で発行できます）。"
        )
        return success_embed("Directus アカウントを発行しました", desc,
                             executor=actor_name)

    async def revoke_access(self, guild_id: int, actor_id: str,
                            actor_name: str | None = None) -> discord.Embed:
        """発行済みアカウントを失効（Directus 側で停止）する。"""
        record = await self.repo.get(guild_id)
        if not record:
            return info_embed(
                "Directus アクセス",
                "このサーバーには Directus アカウントが発行されていません。")

        client = self._client()
        if client is None:
            return info_embed("Directus 連携は未設定です", _NOT_CONFIGURED_DESC)

        try:
            await client.revoke_user(record["directus_user_id"])
        except (DirectusError, DirectusUnavailable):
            return error_embed(
                "Directus 側での停止に失敗しました。時間をおいて再試行するか、"
                "Bot の運用者に連絡してください。",
                code="DIRECTUS_API_FAILED")

        await self.repo.set_status(guild_id, STATUS_REVOKED)
        await self.audit_repo.record(guild_id, actor_id, "directus.revoke",
                                     detail="アクセスを失効")
        return success_embed(
            "Directus アカウントを失効しました",
            "このアカウントではログインできなくなりました。\n"
            "再発行は `/directus-setup` で行えます。",
            executor=actor_name)

    async def build_status_embed(self, guild_id: int) -> discord.Embed:
        """発行状況の Embed を組み立てる。"""
        if not directus_service.is_configured() and self._client() is None:
            return info_embed("Directus 連携は未設定です", _NOT_CONFIGURED_DESC)

        record = await self.repo.get(guild_id)
        if not record:
            return info_embed(
                "Directus アクセス状況",
                "このサーバーには Directus アカウントが発行されていません。\n"
                "管理者が `/directus-setup` で発行できます。")

        embed = info_embed("Directus アクセス状況")
        embed.add_field(name="メールアドレス", value=record["email"], inline=False)
        embed.add_field(
            name="状態",
            value=_STATUS_LABELS.get(record["status"], record["status"]),
            inline=False)
        embed.add_field(name="発行日時", value=record["created_at"], inline=True)
        embed.add_field(name="最終更新", value=record["updated_at"], inline=True)
        embed.set_footer(text="見えるのはこのサーバーのデータのみです")
        return embed

    # ==================================================================
    # /directus-setup（引数なし → ボタン → Modal でメール入力）
    # ==================================================================
    @app_commands.command(
        name="directus-setup",
        description="このサーバー用の Directus 閲覧アカウントを発行します（管理者）。")
    @app_commands.check(is_admin)
    async def directus_setup(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        if self._client() is None:
            await interaction.response.send_message(
                embed=info_embed("Directus 連携は未設定です", _NOT_CONFIGURED_DESC),
                ephemeral=True)
            return

        existing = await self.repo.get(guild_id)
        desc = ("下のボタンを押すと入力フォーム（Modal）が開きます。\n"
                "招待メールの送信先アドレスを入力してください。\n\n"
                "※ 入力内容はチャンネルの履歴に残りません。\n"
                "※ パスワードは Directus 側で設定します（Bot は保存しません）。\n"
                "※ フォームは5分で無効になります。")
        if existing:
            desc = ("既にアカウントが発行されています"
                    f"（{existing['email']}）。\n"
                    "続行すると新しいアドレスで発行し直します。\n\n") + desc
        await interaction.response.send_message(
            embed=info_embed("Directus アカウント発行", desc),
            view=DirectusSetupView(self, guild_id, interaction.user.id),
            ephemeral=True)

    # ==================================================================
    # /directus-status
    # ==================================================================
    @app_commands.command(
        name="directus-status",
        description="このサーバーの Directus アクセス状況を表示します（管理者）。")
    @app_commands.check(is_admin)
    async def directus_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        embed = await self.build_status_embed(guild_id)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # /directus-revoke
    # ==================================================================
    @app_commands.command(
        name="directus-revoke",
        description="このサーバーの Directus アカウントを失効します（管理者）。")
    @app_commands.check(is_admin)
    async def directus_revoke(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        embed = await self.revoke_access(
            guild_id, str(interaction.user.id),
            actor_name=interaction.user.display_name)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DirectusAdmin(bot))
