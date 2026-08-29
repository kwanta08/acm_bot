"""
/data — サーバーデータのエクスポート（F2-2）。

公開配布する Bot として、導入サークルが「自分のサーバーのデータを
いつでも自分で持ち出せる」ことは必須要件（docs/PRIVACY.md）。

出力対象は repositories/table_repository.py のホワイトリスト。
`guild_id` 列と Todoist トークンのような機密列はホワイトリストに
含まれていないため、構造的に出力されない。
"""

from __future__ import annotations

import io
import zipfile

import discord
from discord import app_commands
from discord.ext import commands

from repositories.audit_log_repository import AuditLogRepository
from repositories.guild_repository import GuildRepository
from repositories.table_repository import (
    TABLES,
    TableRepository,
    rows_to_csv,
)
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import Level, ensure_guild, require_manage_guild_or

log = get_logger("data")

# Discord の添付上限（ブースト無しのサーバー）。超えたら分割せず案内に切り替える
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

EXPORT_README = """\
このアーカイブには、あなたの Discord サーバーで club-bot が保持している
データが CSV 形式で入っています。

- 文字コードは BOM 付き UTF-8 です。Excel でそのまま開けます。
- 他のサーバーのデータは含まれません。
- サーバー ID の列と、Todoist トークンのような認証情報は含まれません。

ファイル一覧:
{files}
"""


async def build_export_zip(db, guild_id: int) -> tuple[bytes, dict[str, int]]:
    """このサーバーの主要19テーブル（TABLES のホワイトリスト）を CSV にまとめた ZIP を作る。

    戻り値は (ZIP のバイト列, テーブルごとの行数)。
    """
    repo = TableRepository(db)
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, spec in TABLES.items():
            rows = await repo.list_all_rows(guild_id, key)
            counts[key] = len(rows)
            zf.writestr(f"{key}.csv", rows_to_csv(spec, rows))
        listing = "\n".join(
            f"  {key}.csv — {spec.label}（{counts[key]} 行）" for key, spec in TABLES.items()
        )
        zf.writestr("README.txt", EXPORT_README.format(files=listing))
    return buf.getvalue(), counts


def export_filename(guild_id: int) -> str:
    """添付ファイル名。サーバー名は入れない（転送されたときの情報漏れを避ける）。"""
    return f"club-bot-export-{guild_id}.zip"


def confirmation_matches(expected_name: str, given: str) -> bool:
    """削除確認で打たせたサーバー名が一致するか。

    ボタン1つで消えないよう、サーバー名の完全一致を要求する
    （前後の空白だけは許容する）。
    """
    return (given or "").strip() == (expected_name or "").strip()


class DeleteConfirmModal(discord.ui.Modal):
    """サーバー名を打たせる確認モーダル。"""

    def __init__(self, cog: Data, guild_id: int, guild_name: str):
        super().__init__(title="データ削除の確認")
        self._cog = cog
        self._guild_id = guild_id
        self._guild_name = guild_name
        self.answer = discord.ui.TextInput(
            label="確認のためサーバー名を入力してください",
            placeholder=guild_name[:100],
            required=True,
            max_length=100,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog.handle_delete_confirm(
            interaction, self._guild_id, self._guild_name, self.answer.value
        )


class Data(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="data", description="サーバーデータの書き出し")

    @group.command(
        name="export", description="このサーバーのデータを ZIP（CSV 群）で書き出します。"
    )
    @require_manage_guild_or(Level.L4)
    async def export(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            log.warning("/data export の defer に失敗 (guild=%s): %s", guild_id, e)
            return

        payload, counts = await build_export_zip(self.bot.db, guild_id)
        total_rows = sum(counts.values())
        await self._audit(
            guild_id,
            interaction,
            "data.export",
            f"{len(counts)} テーブル / {total_rows} 行 ({len(payload)} バイト)",
        )

        if len(payload) > MAX_ATTACHMENT_BYTES:
            await self._send(
                interaction,
                error_embed(
                    "データが Discord の添付上限（8MB）を超えました。\n"
                    "分割は行いません。Web ダッシュボードの CSV 出力から"
                    "テーブルごとに取得してください。"
                ),
            )
            return

        breakdown = "\n".join(f"・{spec.label}: {counts[key]} 行" for key, spec in TABLES.items())
        embed = success_embed(
            "データを書き出しました",
            f"{len(counts)} テーブル / 合計 {total_rows} 行\n\n{breakdown}",
            executor=interaction.user.display_name,
        )
        await self._send(
            interaction,
            embed,
            file=discord.File(io.BytesIO(payload), filename=export_filename(guild_id)),
        )

    # ------------------------------------------------------------------
    # 削除（F2-3）。ここでは予約だけを行い、実削除は日次ジョブが担当する
    # ------------------------------------------------------------------
    @group.command(
        name="delete",
        description="このサーバーのデータを削除します（確認のため名前の入力が必要）。",
    )
    @require_manage_guild_or(Level.L4)
    async def delete(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            await interaction.response.send_modal(
                DeleteConfirmModal(self, guild_id, interaction.guild.name)
            )
        except discord.HTTPException as e:
            log.warning("/data delete のモーダル表示に失敗 (guild=%s): %s", guild_id, e)

    async def handle_delete_confirm(
        self, interaction: discord.Interaction, guild_id: int, guild_name: str, given: str
    ) -> None:
        """モーダルの入力を照合し、一致していれば削除を予約する。"""
        if not confirmation_matches(guild_name, given):
            await self._respond(
                interaction,
                error_embed("サーバー名が一致しませんでした。**削除は行っていません。**"),
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            log.warning("/data delete の defer に失敗 (guild=%s): %s", guild_id, e)
            return

        # 消す前に最後のバックアップを渡す
        payload, counts = await build_export_zip(self.bot.db, guild_id)
        purge_at = await GuildRepository(self.bot.db).request_purge(guild_id)
        total_rows = sum(counts.values())
        await self._audit(
            guild_id,
            interaction,
            "data.delete.requested",
            f"purge_after={purge_at} / {total_rows} 行",
        )
        log.info("データ削除が要求されました (guild=%s, purge_after=%s)", guild_id, purge_at)

        embed = info_embed(
            "データの削除を受け付けました",
            f"このサーバーのデータ（{total_rows} 行）は次回の定期処理で削除されます。\n"
            "**添付の ZIP が最後のバックアップです。**必ず保存してください。\n\n"
            "取り消す場合は、削除が実行される前に `/data delete-cancel` を実行してください。",
            executor=interaction.user.display_name,
        )
        file = None
        if len(payload) <= MAX_ATTACHMENT_BYTES:
            file = discord.File(io.BytesIO(payload), filename=export_filename(guild_id))
        else:
            embed.add_field(
                name="バックアップを添付できませんでした",
                value="データが 8MB を超えています。削除が実行される前に "
                "Web ダッシュボードから CSV を取得してください。",
                inline=False,
            )
        await self._send(interaction, embed, file=file)

    @group.command(name="delete-cancel", description="予約済みのデータ削除を取り消します。")
    @require_manage_guild_or(Level.L4)
    async def delete_cancel(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            log.warning("/data delete-cancel の defer に失敗 (guild=%s): %s", guild_id, e)
            return

        cancelled = await GuildRepository(self.bot.db).cancel_purge(guild_id)
        if not cancelled:
            await self._send(
                interaction,
                info_embed("削除は予約されていません", "取り消す対象がありませんでした。"),
            )
            return

        await self._audit(guild_id, interaction, "data.delete.cancelled", "削除予約を取り消した")
        log.info("データ削除の予約が取り消されました (guild=%s)", guild_id)
        await self._send(
            interaction,
            success_embed(
                "データ削除を取り消しました",
                "予約されていた削除を取り消しました。データはそのまま残ります。",
                executor=interaction.user.display_name,
            ),
        )

    async def _audit(
        self, guild_id: int, interaction: discord.Interaction, action: str, detail: str
    ) -> None:
        """監査ログへ記録する（記録の失敗で操作自体は止めない）。"""
        try:
            await AuditLogRepository(self.bot.db).record(
                guild_id,
                actor_id=str(interaction.user.id),
                action=action,
                target="all",
                detail=detail,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("監査ログの記録に失敗 (guild=%s): %s", guild_id, type(e).__name__)

    async def _respond(self, interaction: discord.Interaction, embed: discord.Embed) -> None:
        """まだ応答していない interaction へ ephemeral で返す。"""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            log.warning("/data の応答送信に失敗: %s", e)

    async def _send(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        file: discord.File | None = None,
    ) -> None:
        kwargs = {"embed": embed, "ephemeral": True}
        if file is not None:
            kwargs["file"] = file
        try:
            await interaction.followup.send(**kwargs)
        except discord.HTTPException as e:
            log.warning("/data の応答送信に失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Data(bot))
