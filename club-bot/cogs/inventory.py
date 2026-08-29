"""
Inventory コグ（資材・消耗品の在庫。G4-8）

人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。
発注判断は「残量が閾値を割った」という、bot が自動で見張れる条件。

- /stock list          : 在庫一覧（閾値割れを強調。全員）
- /stock add           : 品目の登録・入庫（班長以上）
- /stock use           : 消費の記録（全員）
- /stock set-threshold : 発注アラートの閾値（班長以上）
- /stock remove        : 品目の無効化（班長以上。履歴は残す）

**品目名の初期値はコードに持たない。** 何を在庫管理するかはサークルごとに
違う（AGENTS.md「組織構造は可変」）。マスタ管理は layer_keta と同型で、
有効フラグとオートコンプリートを持つ。

閾値割れは (1) 割った瞬間に1回だけ告知、(2) 以降は朝の通知に含める
（`cogs/reminders.py` の daily_morning）。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.audit_log_repository import AuditLogRepository
from repositories.stock_repository import StockRepository
from services.stock_service import crossed_below, format_amount, is_low, low_items
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    error_embed,
    info_embed,
    success_embed,
)
from utils.logger import get_logger
from utils.notify import guild_channel, resolve_notice_channel_id
from utils.parser import now, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("inventory")

MAX_ITEM_NAME_LENGTH = 60
MAX_UNIT_LENGTH = 10
#: 一覧に並べる最大件数（Embed の field 上限に収める）
MAX_STOCK_FIELDS = MAX_EMBED_FIELDS - 1


def build_low_stock_lines(items: list[dict]) -> list[str]:
    """閾値割れ品目の告知本文（1品目1行）。reminders からも使う。"""
    return [
        f"⚠️ **{item['item_name']}**: 残り "
        f"{format_amount(item.get('quantity'), str(item.get('unit') or ''))}"
        f"（閾値 {format_amount(item.get('threshold'), str(item.get('unit') or ''))}）"
        for item in items
    ]


class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = StockRepository(bot.db)
        self.audit_repo = AuditLogRepository(bot.db)

    group = app_commands.Group(name="stock", description="資材・消耗品の在庫")

    # ---------- autocomplete ----------
    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        names = await self.repo.list_item_names(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()
        ][:25]

    # ---------- 閾値割れの告知 ----------
    async def announce_low_stock(self, guild: discord.Guild | None, guild_id: int, item: dict):
        """閾値を割ったことを1回だけ告知する。

        送信先が無いギルドでは**部員には沈黙し**、運用者向けのログにだけ残す
        （ADR 0023 と同じ作法）。送信に失敗したときは通知済みフラグを立てない
        ——立てると、次に割り込むまで二度と知らせられない。
        """
        channel_id = await resolve_notice_channel_id(self.bot.db, guild_id)
        channel = guild_channel(guild, channel_id)
        if channel is None:
            log.info("在庫アラートの送信先が無い (guild=%s)", guild_id)
            await self.bot.log_to_channel(
                f"[在庫] 「{item['item_name']}」が閾値を割りましたが、"
                "告知先チャンネルが設定されていないため送信できませんでした。"
                "`/setup` でお知らせチャンネルを設定してください。",
                guild_id=guild_id,
            )
            return
        embed = info_embed(
            "🧾 在庫が閾値を割りました",
            "\n".join(build_low_stock_lines([item]))
            + "\n発注の要否を確認してください（`/stock list` で全体を確認できます）。",
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("在庫アラートの送信に失敗 (guild=%s): %s", guild_id, e)
            return
        await self.repo.set_low_notified(guild_id, int(item["stock_item_id"]), True)

    async def _after_movement(
        self, interaction: discord.Interaction, guild_id: int, item: dict, before: float
    ) -> dict:
        """増減後の状態を読み直し、閾値の出入りに応じて通知状態を更新する。"""
        updated = await self.repo.get_item(guild_id, item["item_name"])
        if updated is None:
            return item
        threshold = updated.get("threshold")
        after = updated.get("quantity")
        if not is_low(after, threshold):
            # 閾値以上へ戻った。次に割ったらまた1回知らせる
            if updated.get("low_notified_flag"):
                await self.repo.set_low_notified(guild_id, int(updated["stock_item_id"]), False)
            return updated
        if crossed_below(before, after, threshold) or not updated.get("low_notified_flag"):
            await self.announce_low_stock(interaction.guild, guild_id, updated)
        return updated

    # ---------- list ----------
    @group.command(name="list", description="在庫の一覧を表示します（閾値割れを強調）。")
    @require(Level.L1)
    async def stock_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        items = await self.repo.list_items(guild_id)
        if not items:
            await interaction.followup.send(
                embed=empty_state_embed(
                    "在庫一覧",
                    "登録済みの品目がありません。",
                    "/stock add",
                ),
                ephemeral=True,
            )
            return

        low = low_items(items)
        head = (
            f"閾値割れ **{len(low)} 件** / 全 {len(items)} 品目"
            if low
            else f"全 {len(items)} 品目（閾値割れはありません）"
        )
        embed = info_embed("在庫一覧", head)
        for item in items[:MAX_STOCK_FIELDS]:
            unit = str(item.get("unit") or "")
            mark = "⚠️ " if is_low(item.get("quantity"), item.get("threshold")) else ""
            value = f"残り {format_amount(item.get('quantity'), unit)}"
            # **閾値未設定を「閾値 0」と書かない**（ADR 0021）
            value += (
                f" / 閾値 {format_amount(item.get('threshold'), unit)}"
                if item.get("threshold") is not None
                else " / 閾値 未設定"
            )
            if item.get("note"):
                value += f"\n{item['note']}"
            embed.add_field(name=f"{mark}{item['item_name']}", value=value, inline=False)
        add_truncation_note(embed, len(items), MAX_STOCK_FIELDS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- add ----------
    @group.command(name="add", description="品目を登録、または入庫を記録します（班長以上）。")
    @app_commands.describe(
        item="品目名（既存なら候補から選択）",
        amount="増やす数量",
        unit="単位（新規登録時のみ使用。既定は「個」）",
        note="メモ（任意）",
    )
    @app_commands.autocomplete(item=_item_autocomplete)
    @require(Level.L2)
    async def stock_add(
        self,
        interaction: discord.Interaction,
        item: str,
        amount: float,
        unit: str | None = None,
        note: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (item or "").strip()
        if not name or len(name) > MAX_ITEM_NAME_LENGTH:
            await interaction.followup.send(
                embed=error_embed(f"品目名は1〜{MAX_ITEM_NAME_LENGTH}文字で指定してください。"),
                ephemeral=True,
            )
            return
        if amount <= 0:
            await interaction.followup.send(
                embed=error_embed("入庫の数量は 0 より大きい値を指定してください。"),
                ephemeral=True,
            )
            return

        now_text = to_iso(now())
        existing = await self.repo.get_item(guild_id, name)
        before = float(existing["quantity"]) if existing else 0.0
        if existing is None:
            item_id = await self.repo.create_item(
                guild_id,
                name,
                (unit or "個").strip()[:MAX_UNIT_LENGTH] or "個",
                0.0,
                str(interaction.user.id),
                now_text,
                note=note,
            )
        else:
            item_id = int(existing["stock_item_id"])
        await self.repo.apply_movement(
            guild_id, item_id, amount, str(interaction.user.id), now_text, reason=note
        )
        updated = await self._after_movement(
            interaction, guild_id, {"item_name": name, "stock_item_id": item_id}, before
        )
        await self._audit(guild_id, interaction, "stock.add", name, f"+{amount}")
        await interaction.followup.send(
            embed=success_embed(
                "在庫を登録しました",
                f"**{name}**: {format_amount(before)} → "
                f"{format_amount(updated.get('quantity'), str(updated.get('unit') or ''))}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- use ----------
    @group.command(name="use", description="資材を使ったことを記録します。")
    @app_commands.describe(item="品目名", amount="使った数量", reason="用途（任意）")
    @app_commands.autocomplete(item=_item_autocomplete)
    @require(Level.L1)
    async def stock_use(
        self,
        interaction: discord.Interaction,
        item: str,
        amount: float,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if amount <= 0:
            await interaction.followup.send(
                embed=error_embed("使った数量は 0 より大きい値を指定してください。"),
                ephemeral=True,
            )
            return
        existing = await self.repo.get_item(guild_id, (item or "").strip())
        if existing is None or not existing.get("active_flag"):
            await interaction.followup.send(
                embed=error_embed(
                    f"品目「{item}」は登録されていません。`/stock add` で登録してください。"
                ),
                ephemeral=True,
            )
            return

        before = float(existing["quantity"])
        now_text = to_iso(now())
        await self.repo.apply_movement(
            guild_id,
            int(existing["stock_item_id"]),
            -amount,
            str(interaction.user.id),
            now_text,
            reason=reason,
        )
        updated = await self._after_movement(interaction, guild_id, existing, before)
        unit = str(updated.get("unit") or "")
        body = (
            f"**{existing['item_name']}**: {format_amount(before, unit)} → "
            f"{format_amount(updated.get('quantity'), unit)}"
        )
        if before - amount < 0:
            # 在庫が負にならないよう 0 で止めている。黙って丸めない
            body += f"\n（記録した消費 {format_amount(amount, unit)} は残量を上回っています）"
        await interaction.followup.send(
            embed=success_embed(
                "在庫を記録しました", body, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    # ---------- set-threshold ----------
    @group.command(
        name="set-threshold", description="発注アラートの閾値を設定します（班長以上）。"
    )
    @app_commands.describe(item="品目名", threshold="この数量以下になったら知らせる（0未満で解除）")
    @app_commands.autocomplete(item=_item_autocomplete)
    @require(Level.L2)
    async def stock_set_threshold(
        self, interaction: discord.Interaction, item: str, threshold: float
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (item or "").strip()
        existing = await self.repo.get_item(guild_id, name)
        if existing is None:
            await interaction.followup.send(
                embed=error_embed(f"品目「{item}」は登録されていません。"), ephemeral=True
            )
            return
        # 負の値は「閾値の解除」。0 は有効な閾値（在庫が尽きたら知らせる）
        value = None if threshold < 0 else float(threshold)
        await self.repo.set_threshold(guild_id, name, value, to_iso(now()))
        await self.repo.set_low_notified(guild_id, int(existing["stock_item_id"]), False)
        await self._audit(guild_id, interaction, "stock.set-threshold", name, str(value))
        unit = str(existing.get("unit") or "")
        body = (
            f"**{name}** の閾値を解除しました。"
            if value is None
            else f"**{name}** の閾値を {format_amount(value, unit)} にしました。"
        )
        await interaction.followup.send(
            embed=success_embed("閾値を設定しました", body, executor=interaction.user.display_name),
            ephemeral=True,
        )

    # ---------- remove ----------
    @group.command(name="remove", description="品目を無効化します（班長以上。履歴は残ります）。")
    @app_commands.describe(item="無効化する品目名")
    @app_commands.autocomplete(item=_item_autocomplete)
    @require(Level.L2)
    async def stock_remove(self, interaction: discord.Interaction, item: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (item or "").strip()
        if not await self.repo.deactivate_item(guild_id, name):
            await interaction.followup.send(
                embed=error_embed(f"品目「{item}」は登録されていません。"), ephemeral=True
            )
            return
        await self._audit(guild_id, interaction, "stock.remove", name, "")
        await interaction.followup.send(
            embed=success_embed(
                "品目を無効化しました",
                f"**{name}**（増減の履歴は残しています）",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    async def _audit(
        self, guild_id: int, interaction: discord.Interaction, action: str, target: str, detail: str
    ) -> None:
        """監査ログへ記録する（記録の失敗で操作自体は止めない）。"""
        try:
            await self.audit_repo.record(
                guild_id,
                actor_id=str(interaction.user.id),
                action=action,
                target=target,
                detail=detail,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("監査ログの記録に失敗 (guild=%s): %s", guild_id, type(e).__name__)


async def setup(bot: commands.Bot):
    await bot.add_cog(Inventory(bot))
