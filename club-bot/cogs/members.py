"""
Members モジュール（仕様 11.4）。

班所属・班長・技能タグ・支援候補検索を管理する。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import INITIAL_TEAMS, SKILL_TAGS
from repositories.member_repository import MemberRepository
from utils.embeds import error_embed, info_embed, member_embed, success_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso
from utils.permissions import Level, require

log = get_logger("members")

TEAM_CHOICES = [app_commands.Choice(name=name, value=key) for key, name in INITIAL_TEAMS]
SKILL_CHOICES = [app_commands.Choice(name=s, value=s) for s in SKILL_TAGS]
TEAM_NAME = {key: name for key, name in INITIAL_TEAMS}


class Members(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = MemberRepository(bot.db)

    group = app_commands.Group(name="member", description="メンバー・班・技能管理")
    skill_group = app_commands.Group(name="skill", description="技能タグ管理", parent=group)

    # ---------- register ----------
    @group.command(name="register", description="新規メンバーを登録します。")
    @app_commands.describe(user="対象ユーザー", team="主所属班")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L2)
    async def register(self, interaction: discord.Interaction, user: discord.Member,
                       team: app_commands.Choice[str] | None = None):
        await interaction.response.defer(ephemeral=True)
        await self.repo.upsert_member(
            str(user.id), user.display_name, team.value if team else None)
        desc = f"{user.display_name}"
        if team:
            desc += f" / 主所属: {team.name}"
        await interaction.followup.send(
            embed=success_embed("メンバーを登録しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_members_sheet()

    # ---------- profile ----------
    @group.command(name="profile", description="メンバー情報を表示します。")
    @app_commands.describe(user="対象ユーザー（省略時は自分）")
    @require(Level.L1)
    async def profile(self, interaction: discord.Interaction, user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        m = await self.repo.get_member(str(target.id))
        if not m:
            await interaction.followup.send(
                embed=info_embed("未登録", f"{target.display_name} はまだ登録されていません。\n"
                                          "`/member register` で登録できます。"),
                ephemeral=True)
            return
        primary = TEAM_NAME.get(m.get("primary_team"), m.get("primary_team") or "—")
        secondary = "、".join(TEAM_NAME.get(t, t) for t in m["secondary_teams"]) or "—"
        skills = "、".join(m["skills"]) or "—"
        embed = member_embed(f"メンバー情報: {m['display_name']}")
        embed.add_field(name="主所属班", value=primary, inline=True)
        embed.add_field(name="副所属班", value=secondary, inline=True)
        embed.add_field(name="班長", value="はい" if m["is_leader"] else "いいえ", inline=True)
        embed.add_field(name="技能", value=skills, inline=False)
        embed.add_field(name="入部日", value=fmt_jp(from_iso(m["joined_at"])), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- assign-team ----------
    @group.command(name="assign-team", description="所属班を設定します。")
    @app_commands.describe(user="対象ユーザー", team="主所属班")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L2)
    async def assign_team(self, interaction: discord.Interaction, user: discord.Member,
                          team: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        await self.repo.upsert_member(str(user.id), user.display_name)
        await self.repo.set_primary_team(str(user.id), team.value)
        await interaction.followup.send(
            embed=success_embed("所属班を設定しました",
                                f"{user.display_name} → {team.name}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_members_sheet()

    # ---------- set-channel ----------
    @group.command(name="set-channel",
                   description="班の通知先チャンネルを設定します（タスクの班別通知に使用）。")
    @app_commands.describe(team="対象の班", channel="通知を送るチャンネル")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L3)
    async def set_channel(self, interaction: discord.Interaction,
                          team: app_commands.Choice[str],
                          channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        await self.repo.upsert_team(team.value, team.name, channel_id=str(channel.id))
        await interaction.followup.send(
            embed=success_embed("班の通知チャンネルを設定しました",
                                f"{team.name}班 → {channel.mention}\n"
                                f"今後、この班のタスク通知（朝の7日以内・夜の超過）はこのチャンネルに届きます。",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ---------- set-leader ----------
    @group.command(name="set-leader", description="班長フラグを設定します。")
    @app_commands.describe(user="対象ユーザー", is_leader="班長にするか")
    @require(Level.L3)
    async def set_leader(self, interaction: discord.Interaction, user: discord.Member,
                         is_leader: bool):
        await interaction.response.defer(ephemeral=True)
        await self.repo.upsert_member(str(user.id), user.display_name)
        await self.repo.set_leader(str(user.id), is_leader)
        await interaction.followup.send(
            embed=success_embed("班長設定を更新しました",
                                f"{user.display_name} → {'班長' if is_leader else '一般'}",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_members_sheet()

    # ---------- skill add / remove ----------
    @skill_group.command(name="add", description="技能タグを追加します。")
    @app_commands.describe(skill="技能タグ", user="対象（省略時は自分）")
    @app_commands.choices(skill=SKILL_CHOICES)
    @require(Level.L1)
    async def skill_add(self, interaction: discord.Interaction,
                        skill: app_commands.Choice[str],
                        user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        await self.repo.upsert_member(str(target.id), target.display_name)
        await self.repo.add_skill(str(target.id), skill.value)
        await interaction.followup.send(
            embed=success_embed("技能を追加しました",
                                f"{target.display_name} に「{skill.name}」",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_members_sheet()

    @skill_group.command(name="remove", description="技能タグを削除します。")
    @app_commands.describe(skill="技能タグ", user="対象（省略時は自分）")
    @app_commands.choices(skill=SKILL_CHOICES)
    @require(Level.L1)
    async def skill_remove(self, interaction: discord.Interaction,
                           skill: app_commands.Choice[str],
                           user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        await self.repo.remove_skill(str(target.id), skill.value)
        await interaction.followup.send(
            embed=success_embed("技能を削除しました",
                                f"{target.display_name} から「{skill.name}」",
                                executor=interaction.user.display_name),
            ephemeral=True)
        await self._sync_members_sheet()

    # ---------- support ----------
    @group.command(name="support", description="班・技能から支援候補を検索します。")
    @app_commands.describe(team="班で絞り込み（任意）", skill="技能で絞り込み（任意）")
    @app_commands.choices(team=TEAM_CHOICES, skill=SKILL_CHOICES)
    @require(Level.L2)
    async def support(self, interaction: discord.Interaction,
                      team: app_commands.Choice[str] | None = None,
                      skill: app_commands.Choice[str] | None = None):
        await interaction.response.defer(ephemeral=True)
        if not team and not skill:
            await interaction.followup.send(
                embed=error_embed("班または技能のいずれかを指定してください。"), ephemeral=True)
            return
        candidates = await self.repo.search_support(
            team.value if team else None, skill.value if skill else None)
        cond = []
        if team:
            cond.append(f"班={team.name}")
        if skill:
            cond.append(f"技能={skill.name}")
        embed = member_embed(f"支援候補検索（{' / '.join(cond)}）")
        if not candidates:
            embed.description = "該当者が見つかりませんでした。"
        else:
            for m in candidates[:25]:
                primary = TEAM_NAME.get(m.get("primary_team"), m.get("primary_team") or "—")
                skills = "、".join(m["skills"]) or "—"
                embed.add_field(
                    name=m["display_name"],
                    value=f"主所属: {primary} / 技能: {skills}",
                    inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- 内部 ----------
    async def _sync_members_sheet(self):
        sheets_cog = self.bot.get_cog("Sheets")
        if sheets_cog:
            try:
                await sheets_cog.sync_members()
            except Exception as e:  # noqa: BLE001
                log.warning("メンバー Sheets 同期失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Members(bot))
