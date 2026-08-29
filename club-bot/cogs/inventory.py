"""
Inventory コグ（資材・消耗品の在庫 G4-8 ／ 工具・機材の貸出 G4-9）

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

- /tool list           : 工具一覧と貸出状況（全員）
- /tool borrow         : 借りる（全員）
- /tool return         : 返す（全員）
- /tool add            : 工具の登録（班長以上）
- /tool remove         : 工具の無効化（班長以上）

`/tool` は `/layer start` → `/layer end` と同じ「開始 → 進行中 → 終了」モデル。
貸出中かどうかは `tool_loans.returned_at IS NULL` で表す。

閾値割れは (1) 割った瞬間に1回だけ告知、(2) 以降は朝の通知に含める。
返却予定日の超過は本人へ DM（1貸出につき1回）。どちらも
`cogs/reminders.py` の daily_morning から呼ぶ。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.audit_log_repository import AuditLogRepository
from repositories.stock_repository import StockRepository
from repositories.tool_repository import ToolRepository
from services.stock_service import crossed_below, format_amount, is_low, low_items
from services.tool_service import loan_status_label
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
from utils.parser import InvalidDatetimeError, now, parse_deadline, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("inventory")

MAX_ITEM_NAME_LENGTH = 60
MAX_TOOL_NAME_LENGTH = 60
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
        self.tool_repo = ToolRepository(bot.db)
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

    # =================================================================
    # /tool — 工具・機材の貸出（G4-9）
    # =================================================================
    tool_group = app_commands.Group(name="tool", description="工具・機材の貸出")

    async def _tool_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        names = await self.tool_repo.list_tool_names(interaction.guild.id)
        return [
            app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()
        ][:25]

    @tool_group.command(name="list", description="工具の一覧と貸出状況を表示します。")
    @require(Level.L1)
    async def tool_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tools = await self.tool_repo.list_tools(guild_id)
        if not tools:
            await interaction.followup.send(
                embed=empty_state_embed(
                    "工具一覧", "登録済みの工具がありません。", "/tool add"
                ),
                ephemeral=True,
            )
            return
        loans = {
            int(loan["tool_id"]): loan for loan in await self.tool_repo.list_open_loans(guild_id)
        }
        today = now().date()
        embed = info_embed(
            "工具一覧", f"全 {len(tools)} 点 / 貸出中 {len(loans)} 点"
        )
        for tool in tools[:MAX_STOCK_FIELDS]:
            loan = loans.get(int(tool["tool_id"]))
            value = loan_status_label(loan, today)
            if loan is not None:
                value += f"\n借用者: <@{loan['user_id']}>"
            if tool.get("note"):
                value += f"\n{tool['note']}"
            embed.add_field(name=str(tool["tool_name"]), value=value, inline=False)
        add_truncation_note(embed, len(tools), MAX_STOCK_FIELDS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tool_group.command(name="add", description="工具を登録します（班長以上）。")
    @app_commands.describe(tool="工具名", note="メモ（保管場所など。任意）")
    @require(Level.L2)
    async def tool_add(
        self, interaction: discord.Interaction, tool: str, note: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (tool or "").strip()
        if not name or len(name) > MAX_TOOL_NAME_LENGTH:
            await interaction.followup.send(
                embed=error_embed(f"工具名は1〜{MAX_TOOL_NAME_LENGTH}文字で指定してください。"),
                ephemeral=True,
            )
            return
        await self.tool_repo.add_tool(
            guild_id, name, str(interaction.user.id), to_iso(now()), note
        )
        await self._audit(guild_id, interaction, "tool.add", name, note or "")
        await interaction.followup.send(
            embed=success_embed(
                "工具を登録しました", f"**{name}**", executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    @tool_group.command(name="remove", description="工具を無効化します（班長以上）。")
    @app_commands.describe(tool="無効化する工具名")
    @app_commands.autocomplete(tool=_tool_autocomplete)
    @require(Level.L2)
    async def tool_remove(self, interaction: discord.Interaction, tool: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (tool or "").strip()
        if not await self.tool_repo.deactivate_tool(guild_id, name):
            await interaction.followup.send(
                embed=error_embed(f"工具「{tool}」は登録されていません。"), ephemeral=True
            )
            return
        await self._audit(guild_id, interaction, "tool.remove", name, "")
        await interaction.followup.send(
            embed=success_embed(
                "工具を無効化しました",
                f"**{name}**（貸出の履歴は残しています）",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @tool_group.command(name="borrow", description="工具を借りたことを記録します。")
    @app_commands.describe(
        tool="工具名", due="返却予定日（YYYY-MM-DD。任意）", note="用途（任意）"
    )
    @app_commands.autocomplete(tool=_tool_autocomplete)
    @require(Level.L1)
    async def tool_borrow(
        self,
        interaction: discord.Interaction,
        tool: str,
        due: str | None = None,
        note: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (tool or "").strip()
        row = await self.tool_repo.get_tool(guild_id, name)
        if row is None or not row.get("active_flag"):
            await interaction.followup.send(
                embed=error_embed(
                    f"工具「{tool}」は登録されていません。`/tool add` で登録してください。"
                ),
                ephemeral=True,
            )
            return
        tool_id = int(row["tool_id"])
        open_loan = await self.tool_repo.get_open_loan(guild_id, tool_id)
        if open_loan is not None:
            await interaction.followup.send(
                embed=error_embed(
                    f"**{name}** は貸出中です（借用者: <@{open_loan['user_id']}>）。\n"
                    "返却されてから借りてください。"
                ),
                ephemeral=True,
            )
            return

        due_date = None
        if due:
            try:
                due_date = parse_deadline(due).date().isoformat()
            except InvalidDatetimeError:
                await interaction.followup.send(
                    embed=error_embed(
                        "返却予定日は `2026-09-01` の形式で指定してください。"
                    ),
                    ephemeral=True,
                )
                return

        await self.tool_repo.borrow(
            guild_id, tool_id, str(interaction.user.id), to_iso(now()), due_date, note
        )
        body = f"**{name}** を借りました。"
        # 予定日を決めていない貸出を「期限なし」と正直に書く（ADR 0021）
        body += f"\n返却予定: {due_date}" if due_date else "\n返却予定日は未設定です。"
        await interaction.followup.send(
            embed=success_embed("貸出を記録しました", body, executor=interaction.user.display_name),
            ephemeral=True,
        )

    @tool_group.command(name="return", description="工具を返したことを記録します。")
    @app_commands.describe(tool="工具名")
    @app_commands.autocomplete(tool=_tool_autocomplete)
    @require(Level.L1)
    async def tool_return(self, interaction: discord.Interaction, tool: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = (tool or "").strip()
        row = await self.tool_repo.get_tool(guild_id, name)
        if row is None:
            await interaction.followup.send(
                embed=error_embed(f"工具「{tool}」は登録されていません。"), ephemeral=True
            )
            return
        open_loan = await self.tool_repo.get_open_loan(guild_id, int(row["tool_id"]))
        if open_loan is None:
            await interaction.followup.send(
                embed=info_embed("返却の記録はありません", f"**{name}** は貸出中ではありません。"),
                ephemeral=True,
            )
            return
        # **借りた本人以外でも返却できる。** 工具は現物が戻れば返却であって、
        # 借りた人が不在のときに記録できないと台帳が実物とずれる
        await self.tool_repo.give_back(guild_id, int(open_loan["loan_id"]), to_iso(now()))
        body = f"**{name}** の返却を記録しました。"
        if str(open_loan["user_id"]) != str(interaction.user.id):
            body += f"\n（借用者: <@{open_loan['user_id']}>）"
        await interaction.followup.send(
            embed=success_embed("返却を記録しました", body, executor=interaction.user.display_name),
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
