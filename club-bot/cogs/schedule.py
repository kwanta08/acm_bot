"""
Schedule モジュール（仕様 11.2）。

日程調整・出欠投票。候補日ごとに1メッセージを投稿し状態を投票する。
1候補1ユーザー1状態。状態変更時は旧リアクションを自動除去する。
Bot 再起動後も on_raw_reaction_add/remove で処理可能。

マルチテナント版: 全データを interaction.guild.id（または payload.guild_id）
でスコープする。services/schedule_service.py には変更を加えないため、
Embed 生成には guild 固定プロキシ repo.for_guild(guild_id) を渡す。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.schedule_repository import ScheduleRepository
from services import schedule_service as svc
from services.schedule_service import build_emoji_maps, _resolve_name as _resolve_display_name
from utils.embeds import error_embed, info_embed, schedule_embed, success_embed
from utils.logger import get_logger
from utils.parser import InvalidDatetimeError, fmt_jp, from_iso, parse_datetime, parse_deadline, to_iso
from utils.permissions import Level, ensure_guild, require

log = get_logger("schedule")


class Schedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = ScheduleRepository(bot.db)

    group = app_commands.Group(name="schedule", description="日程調整・出欠管理")

    # ---------- create ----------
    @group.command(name="create", description="新規日程調整を作成します。")
    @app_commands.describe(
        title="イベント名",
        options="候補日時を ; 区切りで指定（例: 2026-07-03; 2026-07-04 19:00）",
        deadline="締切日時（例: 2026-07-02 または 2026-07-02 23:59）",
        description="詳細（任意）",
        place="場所（任意）",
        target_role="対象ロール（任意）",
        channel="投稿先チャンネル（任意）",
    )
    @require(Level.L2)
    async def create(self, interaction: discord.Interaction, title: str, options: str,
                     deadline: str, description: str | None = None,
                     place: str | None = None,
                     target_role: discord.Role | None = None,
                     channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        # 日時パース
        deadline_dt = parse_deadline(deadline)
        option_labels = svc.parse_options(options)
        if not option_labels:
            await interaction.followup.send(
                embed=error_embed("候補日時が空です。`;` 区切りで1件以上指定してください。"),
                ephemeral=True)
            return

        # 各候補のパース
        parsed_options = []
        for label in option_labels:
            try:
                start = parse_datetime(label)
            except InvalidDatetimeError:
                await interaction.followup.send(
                    embed=error_embed(
                        f"候補日時「{label}」の形式が不正です。"
                        f"`YYYY-MM-DD` または `YYYY-MM-DD HH:MM` 形式で指定してください。",
                        code="INVALID_DATETIME"),
                    ephemeral=True)
                return
            parsed_options.append((label, start))

        # 投稿先決定（ギルド別設定を参照）
        gconf = await config.for_guild(guild_id)
        target_channel = channel or (
            self.bot.get_channel(gconf.default_schedule_channel_id)
            if gconf.default_schedule_channel_id else interaction.channel)
        if target_channel is None:
            await interaction.followup.send(
                embed=error_embed("投稿先チャンネルが特定できません。channel を指定してください。"),
                ephemeral=True)
            return

        schedule_id = svc.new_schedule_id()
        await self.repo.create_schedule(
            guild_id,
            schedule_id=schedule_id, title=title, description=description, place=place,
            target_role_id=str(target_role.id) if target_role else None,
            deadline_iso=to_iso(deadline_dt),
            created_by=str(interaction.user.id),
            channel_id=str(target_channel.id),
        )

        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        scoped_repo = self.repo.for_guild(guild_id)

        # 候補ごとに1メッセージ投稿（仕様 11.2.3）
        emoji_maps = build_emoji_maps(self.bot, interaction.guild)
        all_emojis = emoji_maps["all_emojis"]

        for label, start in parsed_options:
            option_id = svc.new_option_id()
            await self.repo.add_option(guild_id, option_id, schedule_id, label,
                                       to_iso(start), None, None)
            opt = {"option_id": option_id, "label": label}
            embed = await svc.build_option_embed(scoped_repo, self.bot, schedule, opt,
                                                 interaction.guild)
            msg = await target_channel.send(embed=embed)
            await self.repo.set_option_message(guild_id, option_id, str(msg.id))
            for emoji in all_emojis:
                await msg.add_reaction(emoji)

        # シート作成（候補がすべてDB保存された後に行う）
        sheet_status = "未実行"
        if self.bot.sheets.enabled and config.schedule_sheets_enabled():
            try:
                saved_opts = await self.repo.list_options(guild_id, schedule_id)
                votes_map = {
                    opt["option_id"]: {"ok": [], "maybe": [], "ng": [], "unanswered": []}
                    for opt in saved_opts
                }
                actual_title = await self.bot.sheets.create_schedule_sheet(
                    title, saved_opts, votes_map)
                await self.repo.set_schedule_sheet_title(guild_id, schedule_id, actual_title)
                sheet_status = f"作成済み（{actual_title}）"
            except Exception as e:
                log.exception("スケジュールシート初期化失敗")
                sheet_status = f"失敗（{e}）"
        else:
            sheet_status = "無効（未設定）"

        await interaction.followup.send(
            embed=success_embed("日程調整を作成しました",
                                f"ID: `{schedule_id}`\n候補数: {len(parsed_options)}\n"
                                f"締切: {fmt_jp(deadline_dt)}\n投稿先: {target_channel.mention}\n"
                                f"シート: {sheet_status}",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- list ----------
    @group.command(name="list", description="開催中の日程調整一覧を表示します。")
    @require(Level.L1)
    async def list_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedules = await self.repo.list_open_schedules(guild_id)
        if not schedules:
            await interaction.followup.send(
                embed=info_embed("開催中の日程調整", "現在、開催中の投票はありません。"),
                ephemeral=True)
            return
        embed = schedule_embed("開催中の日程調整一覧")
        for s in schedules:
            embed.add_field(
                name=f"{s['title']}（`{s['schedule_id']}`）",
                value=f"締切: {fmt_jp(from_iso(s['deadline']))}",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- status ----------
    @group.command(name="status", description="特定投票の詳細を表示します。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L1)
    async def status(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True)
            return
        scoped_repo = self.repo.for_guild(guild_id)
        options = await self.repo.list_options(guild_id, schedule_id)
        for opt in options:
            embed = await svc.build_option_embed(scoped_repo, self.bot, schedule, opt,
                                                 interaction.guild)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="list-closed", description="締切済みの日程調整一覧を表示します。")
    @require(Level.L1)
    async def list_closed_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedules = await self.repo.list_closed_schedules(guild_id)
        if not schedules:
            await interaction.followup.send(
                embed=info_embed("締切済みの日程調整", "締切済みの投票はありません。"),
                ephemeral=True)
            return
        embed = schedule_embed("締切済みの日程調整一覧")
        for s in schedules:
            sheet_info = f" / シート: {s['sheet_title']}" if s.get("sheet_title") else ""
            embed.add_field(
                name=f"{s['title']}（`{s['schedule_id']}`）",
                value=f"締切: {fmt_jp(from_iso(s['deadline']))}{sheet_info}",
                inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- close ----------
    @group.command(name="close", description="日程調整を手動で締め切ります。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L2)
    async def close(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True)
            return
        await self.finalize_schedule(schedule)
        await interaction.followup.send(
            embed=success_embed("締め切りました", f"ID: `{schedule_id}`",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- remind ----------
    @group.command(name="remind", description="未回答者へ再通知します。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L2)
    async def remind(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True)
            return
        count = await self.notify_unanswered(schedule)
        await interaction.followup.send(
            embed=success_embed("未回答者へ再通知しました", f"対象: {count} 名",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- delete ----------
    @group.command(name="delete", description="日程調整を削除します（Discordメッセージ・シートも削除）。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L3)
    async def delete(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True)
            return

        # Discord上の候補メッセージを削除
        options = await self.repo.list_options(guild_id, schedule_id)
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        deleted_msgs = 0
        for opt in options:
            if not opt.get("message_id") or not channel:
                continue
            try:
                msg = await channel.fetch_message(int(opt["message_id"]))
                await msg.delete()
                deleted_msgs += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # スプレッドシートを削除
        sheet_deleted = False
        if schedule.get("sheet_title") and self.bot.sheets.enabled:
            try:
                await self.bot.sheets.delete_schedule_sheet(schedule["sheet_title"])
                sheet_deleted = True
            except Exception as e:
                log.warning("スケジュールシート削除失敗: %s", e)

        # DBから削除（外部キーCASCADEでoptions/votesも削除される）
        await self.repo.delete_schedule(guild_id, schedule_id)

        detail = f"ID: `{schedule_id}`\nDiscordメッセージ削除: {deleted_msgs} 件"
        detail += f"\nシート削除: {'成功' if sheet_deleted else '対象なし/失敗'}"

        await interaction.followup.send(
            embed=success_embed("削除しました", detail,
                                executor=interaction.user.display_name),
            ephemeral=True)

    @group.command(name="edit-deadline", description="開催中の日程調整の締切を変更します。")
    @app_commands.describe(schedule_id="投票 ID", deadline="新しい締切（例: 2026-07-20 または 2026-07-20 23:59）")
    @require(Level.L2)
    async def edit_deadline(self, interaction: discord.Interaction, schedule_id: str, deadline: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True)
            return
        if schedule["closed_flag"]:
            await interaction.followup.send(
                embed=error_embed("この投票は既に締切済みです。締切済みの投票は変更できません。"),
                ephemeral=True)
            return

        try:
            new_deadline_dt = parse_deadline(deadline)
        except InvalidDatetimeError:
            await interaction.followup.send(
                embed=error_embed(
                    f"締切「{deadline}」の形式が不正です。"
                    f"`YYYY-MM-DD` または `YYYY-MM-DD HH:MM` 形式で指定してください。",
                    code="INVALID_DATETIME"),
                ephemeral=True)
            return

        old_deadline_str = fmt_jp(from_iso(schedule["deadline"]))
        await self.repo.update_deadline(guild_id, schedule_id, to_iso(new_deadline_dt))

        # 更新後のスケジュール情報でEmbedを再取得
        updated_schedule = await self.repo.get_schedule(guild_id, schedule_id)

        # 各候補メッセージの締切表示を更新
        scoped_repo = self.repo.for_guild(guild_id)
        options = await self.repo.list_options(guild_id, schedule_id)
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        updated_msgs = 0
        if channel:
            for opt in options:
                if not opt.get("message_id"):
                    continue
                try:
                    msg = await channel.fetch_message(int(opt["message_id"]))
                    embed = await svc.build_option_embed(
                        scoped_repo, self.bot, updated_schedule, opt, interaction.guild)
                    await msg.edit(embed=embed)
                    updated_msgs += 1
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        # 変更をチャンネルに通知
        if channel:
            try:
                await channel.send(
                    f"【日程調整】「{schedule['title']}」の締切が変更されました。\n"
                    f"変更前: {old_deadline_str}\n変更後: {fmt_jp(new_deadline_dt)}")
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            embed=success_embed(
                "締切を変更しました",
                f"ID: `{schedule_id}`\n変更前: {old_deadline_str}\n"
                f"変更後: {fmt_jp(new_deadline_dt)}\n更新メッセージ: {updated_msgs} 件",
                executor=interaction.user.display_name),
            ephemeral=True)


    # ====================================================================
    # リアクション処理（raw イベント。Bot 再起動後も動作）
    # ====================================================================
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, added=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, added: bool):
        if self.bot.user and payload.user_id == self.bot.user.id:
            return
        if not payload.guild_id:
            return  # DM リアクションは対象外

        guild_id = payload.guild_id
        guild = self.bot.get_guild(guild_id)
        emoji_maps = build_emoji_maps(self.bot, guild)
        emoji_to_status = emoji_maps["emoji_to_status"]
        status_to_emoji = emoji_maps["status_to_emoji"]

        emoji_key = str(payload.emoji.id) if payload.emoji.id else str(payload.emoji)
        if emoji_key not in emoji_to_status:
            return

        option = await self.repo.get_option_by_message(guild_id, str(payload.message_id))
        if not option:
            return
        schedule = await self.repo.get_schedule(guild_id, option["schedule_id"])
        if not schedule or schedule["closed_flag"]:
            return

        user_id = str(payload.user_id)
        status = emoji_to_status[emoji_key]

        if added:
            await self.repo.set_vote(guild_id, option["option_id"], user_id, status)
            await self._remove_other_reactions(payload, keep_status=status,
                                               status_to_emoji=status_to_emoji)
        else:
            votes = await self.repo.list_votes(guild_id, option["option_id"])
            current = next((v for v in votes if v["user_id"] == user_id), None)
            if current and current["status"] == status:
                await self.repo.remove_vote(guild_id, option["option_id"], user_id)

        await self._refresh_option_message(payload, schedule, option)
        await self._sync_schedule_sheet(schedule, guild)

    async def _remove_other_reactions(self, payload: discord.RawReactionActionEvent,
                                      keep_status: str,
                                      status_to_emoji: dict[str, str | discord.PartialEmoji]):
        channel = self.bot.get_channel(payload.channel_id) or \
            await self.bot.fetch_channel(payload.channel_id)
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        member = payload.member or channel.guild.get_member(payload.user_id)
        if member is None:
            return

        keep_emoji = status_to_emoji[keep_status]
        keep_key = str(keep_emoji.id) if isinstance(keep_emoji, discord.PartialEmoji) else str(keep_emoji)

        schedule_keys = set()
        for emoji in status_to_emoji.values():
            key = str(emoji.id) if isinstance(emoji, discord.PartialEmoji) else str(emoji)
            schedule_keys.add(key)

        for reaction in message.reactions:
            reaction_key = (str(reaction.emoji.id)
                           if hasattr(reaction.emoji, "id") and reaction.emoji.id
                           else str(reaction.emoji))
            if reaction_key in schedule_keys and reaction_key != keep_key:
                try:
                    await message.remove_reaction(reaction.emoji, member)
                except (discord.Forbidden, discord.NotFound):
                    pass

    async def _refresh_option_message(self, payload, schedule, option):
        channel = self.bot.get_channel(payload.channel_id) or \
            await self.bot.fetch_channel(payload.channel_id)
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        guild = getattr(channel, "guild", None)
        guild_id = schedule.get("guild_id") or (guild.id if guild else 0)
        embed = await svc.build_option_embed(
            self.repo.for_guild(guild_id), self.bot, schedule, option, guild)
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    # 投票ごとのシート反映用
    async def _sync_schedule_sheet(self, schedule: dict, guild: discord.Guild | None):
        """現在の投票状況をスプレッドシートへ反映する。"""
        if not self.bot.sheets.enabled or not config.schedule_sheets_enabled():
            return
        if not schedule.get("sheet_title"):
            return

        guild_id = schedule["guild_id"]
        try:
            options = await self.repo.list_options(guild_id, schedule["schedule_id"])
            votes_map = {}
            for opt in options:
                votes = await self.repo.list_votes(guild_id, opt["option_id"])
                votes_map[opt["option_id"]] = {
                    "ok": [await _resolve_display_name(self.bot, guild, v["user_id"])
                           for v in votes if v["status"] == "ok"],
                    "maybe": [await _resolve_display_name(self.bot, guild, v["user_id"])
                              for v in votes if v["status"] == "maybe"],
                    "ng": [await _resolve_display_name(self.bot, guild, v["user_id"])
                           for v in votes if v["status"] == "ng"],
                    "unanswered": [],
                }
            await self.bot.sheets.update_schedule_sheet(
                schedule["sheet_title"], options, votes_map)
        except Exception as e:
            log.warning("スケジュール Sheets 同期失敗: %s", e)

    # ====================================================================
    # 締切・通知ヘルパー（Reminders から呼ばれる）
    # ====================================================================
    async def notify_unanswered(self, schedule: dict) -> int:
        """未回答者へ DM 通知。DM 不可ならチャンネルでメンション（仕様 11.2.5）。"""
        guild_id = schedule["guild_id"]
        guild = self.bot.get_guild(guild_id)
        if not guild or not schedule.get("target_role_id"):
            return 0
        role = guild.get_role(int(schedule["target_role_id"]))
        if not role:
            return 0

        answered = await self.repo.list_voters_for_schedule(guild_id, schedule["schedule_id"])
        targets = [m for m in role.members if not m.bot and str(m.id) not in answered]

        deadline = fmt_jp(from_iso(schedule["deadline"]))
        text = (f"【日程調整リマインド】\n「{schedule['title']}」が未回答です。\n"
                f"締切: {deadline}\n投票チャンネルでリアクションをお願いします。")

        failed_mentions = []
        for m in targets:
            try:
                await m.send(text)
            except (discord.Forbidden, discord.HTTPException):
                failed_mentions.append(m.mention)

        if failed_mentions:
            channel = self.bot.get_channel(int(schedule["channel_id"]))
            if channel:
                await channel.send(
                    f"未回答リマインド（DM不可）: {' '.join(failed_mentions)}\n{text}")
        return len(targets)

    async def finalize_schedule(self, schedule: dict):
        """締切処理: クローズ→結果要約投稿（仕様 11.2.5）。"""
        guild_id = schedule["guild_id"]
        await self.repo.close_schedule(guild_id, schedule["schedule_id"])
        guild = self.bot.get_guild(guild_id)
        embed = await svc.build_summary_embed(
            self.repo.for_guild(guild_id), self.bot, schedule, guild)
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

        await self._sync_schedule_sheet(schedule, guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Schedule(bot))
