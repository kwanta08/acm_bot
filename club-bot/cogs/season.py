"""
/season — 年度替わり（世代交代）。

サークルには毎年必ず代替わりが来るのに、これまで区切りを表す仕組みが
無かった。年度の境界を記録し、卒業者を仕分け、班長を新体制のために
一度リセットする。

**卒業者は削除しない。** 過去の作業記録に残る担当者名が引けなくなるため、
status を alumni に動かして既定の一覧・検索から外すだけにする。
"""

from __future__ import annotations

import io

import discord
from discord import app_commands
from discord.ext import commands

from cogs.data import MAX_ATTACHMENT_BYTES, build_export_zip
from repositories.audit_log_repository import AuditLogRepository
from repositories.member_repository import MemberRepository
from repositories.season_repository import SeasonRepository
from services.season_service import (
    MAX_SELECTABLE,
    RolloverResult,
    perform_rollover,
)
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import Level, ensure_guild, is_admin, require
from utils.views import ConfirmView

log = get_logger("season")

# 卒業者の選択（最大25名）は5分では足りないことがあるため長めに取る。
# タイムアウト時の画面反映は ConfirmView（TimeoutAwareView）が行う
_VIEW_TIMEOUT = 900.0


def snapshot_filename(guild_id: int) -> str:
    """年度スナップショットのファイル名（サーバー名は入れない）。"""
    return f"club-bot-season-snapshot-{guild_id}.zip"


def rollover_result_embed(result: RolloverResult, executor: str | None = None) -> discord.Embed:
    """年度替わりの結果 Embed。"""
    lines = []
    if result.ended_season:
        lines.append(f"終了した年度: **{result.ended_season}**")
    lines.append(f"新しい年度: **{result.new_season}**")
    lines.append(f"卒業に切り替えた人数: **{len(result.alumni)} 名**")
    lines.append(f"班長フラグのリセット: **{result.leaders_reset} 件**")
    lines.append("")
    lines.append(
        "卒業者のデータは削除していません（過去の記録の担当者名を"
        "残すため）。既定の一覧・検索からは外れます。"
    )
    lines.append("")
    # 班長ロール（settings の LEADER_ROLE_IDS）はこの処理では触らない。
    # 勝手に消すと、新体制が設定するまで全班長が L1 に降格するため
    # （ADR 0024: 状態が変わるのは明示的に選ばれたときだけ）。
    # 代わりに、毎年積み上がることを利用者へ知らせる。
    lines.append(
        "**班長ロールの見直しをしてください。** 班長の権限（L2）の根拠は"
        "班長ロールで、ここでは変更していません。"
        "`/setup` の「班長ロール」で選び直すか、`/set_role action:remove` で"
        "旧体制のロールを外してください。"
    )
    return success_embed("年度を切り替えました", "\n".join(lines), executor=executor)


class RolloverView(ConfirmView):
    """卒業者を選んでから確定するウィザード。

    確認の作法（実行者チェック・確定 / やめる・二重実行の防止）は
    `ConfirmView` に集約したので、ここは**卒業者を選ぶ UI** だけを足す。
    """

    def __init__(self, cog: Season, guild_id: int, new_season_name: str, owner_id: int):
        self._cog = cog
        self._guild_id = guild_id
        self._name = new_season_name
        self._selected: list[str] = []
        super().__init__(
            owner_id,
            self._build_preview(),
            self._run_rollover,
            timeout=_VIEW_TIMEOUT,
            cancel_message="年度は切り替えていません。",
        )

        self.picker = discord.ui.UserSelect(
            placeholder="卒業する人を選ぶ（選ばなければ全員が継続）",
            min_values=0,
            max_values=MAX_SELECTABLE,
        )
        self.picker.callback = self._on_pick
        self.add_item(self.picker)

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        if not await self._is_owner(interaction):
            return
        self._selected = [str(u.id) for u in self.picker.values]
        self.preview_embed = self._build_preview()
        try:
            await interaction.response.edit_message(embed=self.preview_embed, view=self)
        except discord.HTTPException as e:
            log.warning("/season rollover の選択反映に失敗: %s", e)

    def _build_preview(self) -> discord.Embed:
        body = (
            f"新しい年度: **{self._name}**\n"
            f"卒業として仕分ける人数: **{len(self._selected)} 名**\n\n"
            "「確定する」を押すと、現在の年度を終了して新しい年度を開始し、"
            "選んだ人を卒業に切り替え、**班長フラグを全員リセット**します。"
        )
        return info_embed("年度替わりの確認", body)

    async def _run_rollover(self, interaction: discord.Interaction) -> None:
        await self._cog.finish_rollover(interaction, self._guild_id, self._name, self._selected)


class Season(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="season", description="年度（世代）の管理")

    @property
    def db(self):
        return self.bot.db

    @group.command(name="list", description="登録済みの年度を表示します。")
    @require(Level.L1)
    async def season_list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await SeasonRepository(self.db).list_all(guild_id)
        if not rows:
            await interaction.followup.send(
                embed=info_embed(
                    "年度が登録されていません",
                    "`/season new` で最初の年度を作成できます。\n"
                    "年度名は自由です（例: `2026年度`、`第30代`）。",
                )
            )
            return
        lines = []
        for row in rows:
            mark = "▶ 現役" if row["ended_at"] is None else "　 終了"
            period = str(row["started_at"])[:10]
            if row["ended_at"]:
                period += f" 〜 {str(row['ended_at'])[:10]}"
            lines.append(f"{mark}　**{row['name']}**（{period}）")
        counts = await MemberRepository(self.db).count_by_status(guild_id)
        footer = f"\n\n現役 {counts.get('active', 0)} 名 / 卒業 {counts.get('alumni', 0)} 名"
        await interaction.followup.send(embed=info_embed("📅 年度一覧", "\n".join(lines) + footer))

    @group.command(name="new", description="新しい年度を開始します（現在の年度は終了します）。")
    @app_commands.describe(name="新しい年度の名前（例: 2027年度 / 第31代）")
    @app_commands.check(is_admin)
    async def season_new(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("年度名を入力してください。"), ephemeral=True
            )
            return
        repo = SeasonRepository(self.db)
        if await repo.get_by_name(guild_id, name):
            await interaction.followup.send(
                embed=error_embed(f"「{name}」はすでに登録されています。"), ephemeral=True
            )
            return

        # 現年度を**即終了**するコマンドなので、何が終わるかを先に見せる
        current = await repo.current(guild_id)
        body = f"新しい年度: **{name}**\n"
        if current:
            body += f"終了する年度: **{current['name']}**\n"
        else:
            body += "終了する年度: なし（最初の年度）\n"
        body += (
            "\n卒業者の仕分けと班長リセットは行いません"
            "（まとめて行う場合は `/season rollover`）。"
        )

        async def _do_start(confirm_interaction: discord.Interaction) -> None:
            try:
                ended, _ = await repo.start_new(guild_id, name)
            except ValueError:
                await confirm_interaction.followup.send(
                    embed=error_embed(f"「{name}」はすでに登録されています。"), ephemeral=True
                )
                return

            await self._audit(
                guild_id, confirm_interaction, "season.new", name, f"前年度: {ended or 'なし'}"
            )
            desc = f"新しい年度: **{name}**"
            if ended:
                desc = f"**{ended}** を終了しました。\n" + desc
            await confirm_interaction.followup.send(
                embed=success_embed(
                    "年度を開始しました", desc, executor=confirm_interaction.user.display_name
                ),
                ephemeral=True,
            )

        view = ConfirmView(
            interaction.user.id,
            info_embed("年度の開始を確認してください", body),
            _do_start,
            cancel_message="年度は変更していません。",
        )
        view.message = await interaction.followup.send(
            embed=view.preview_embed, view=view, ephemeral=True
        )

    @group.command(
        name="rollover", description="年度を切り替え、卒業者の仕分けと班長リセットを行います。"
    )
    @app_commands.describe(name="新しい年度の名前（例: 2027年度 / 第31代）")
    @app_commands.check(is_admin)
    async def season_rollover(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = name.strip()
        if not name:
            await interaction.followup.send(
                embed=error_embed("年度名を入力してください。"), ephemeral=True
            )
            return
        if await SeasonRepository(self.db).get_by_name(guild_id, name):
            await interaction.followup.send(
                embed=error_embed(f"「{name}」はすでに登録されています。"), ephemeral=True
            )
            return

        members = await MemberRepository(self.db).list_members(guild_id)
        body = (
            f"新しい年度: **{name}**\n"
            f"現役メンバー: **{len(members)} 名**\n\n"
            "下のメニューで卒業する人を選んでください"
            f"（一度に選べるのは {MAX_SELECTABLE} 名までです）。\n"
            "選ばなければ全員が継続として扱われます。"
        )
        view = RolloverView(self, guild_id, name, interaction.user.id)
        try:
            view.message = await interaction.followup.send(
                embed=info_embed("年度替わり", body), view=view, ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning("/season rollover の表示に失敗 (guild=%s): %s", guild_id, e)

    async def finish_rollover(
        self, interaction: discord.Interaction, guild_id: int, name: str, alumni_user_ids: list[str]
    ) -> None:
        """ウィザードの確定処理。"""
        try:
            result = await perform_rollover(self.db, guild_id, name, alumni_user_ids)
        except ValueError:
            await self._send(interaction, error_embed(f"「{name}」はすでに登録されています。"))
            return

        await self._audit(guild_id, interaction, "season.rollover", name, result.summary())
        log.info("年度を切り替えました (guild=%s): %s", guild_id, result.summary())
        try:
            await self.bot.log_to_channel(f"[Season] {result.summary()}", guild_id=guild_id)
        except Exception as e:  # noqa: BLE001  (通知失敗で処理は止めない)
            log.warning("bot-log への投稿に失敗 (guild=%s): %s", guild_id, type(e).__name__)

        embed = rollover_result_embed(result, executor=interaction.user.display_name)
        file = await self._snapshot_file(guild_id, embed)
        await self._send(interaction, embed, file=file)

    async def _snapshot_file(self, guild_id: int, embed: discord.Embed) -> discord.File | None:
        """年度スナップショット（/data export と同じ ZIP）を作る。

        引き継ぎのために、切り替えた時点の主要7テーブル（/data export と同じ範囲）を渡す。
        エクスポート処理は再実装せず cogs/data.py のものを共有する。
        """
        try:
            payload, _ = await build_export_zip(self.db, guild_id)
        except Exception as e:  # noqa: BLE001  (年度切り替え自体は成功させる)
            log.warning(
                "年度スナップショットの作成に失敗 (guild=%s): %s", guild_id, type(e).__name__
            )
            return None
        if len(payload) > MAX_ATTACHMENT_BYTES:
            embed.add_field(
                name="年度スナップショット",
                value="データが 8MB を超えるため添付できませんでした。"
                "`/data export` または Web ダッシュボードから取得してください。",
                inline=False,
            )
            return None
        embed.add_field(
            name="年度スナップショット",
            value="添付の ZIP が切り替え時点のデータです。引き継ぎ用に保存してください。",
            inline=False,
        )
        return discord.File(io.BytesIO(payload), filename=snapshot_filename(guild_id))

    async def _audit(
        self, guild_id: int, interaction: discord.Interaction, action: str, target: str, detail: str
    ) -> None:
        try:
            await AuditLogRepository(self.db).record(
                guild_id,
                actor_id=str(interaction.user.id),
                action=action,
                target=target,
                detail=detail,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("監査ログの記録に失敗 (guild=%s): %s", guild_id, type(e).__name__)

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
            log.warning("/season の応答送信に失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Season(bot))
