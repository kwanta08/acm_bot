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

    # ---------- sync_roles ----------
    async def _sync_roles(self, guild: discord.Guild, member: discord.Member, user_id: str) -> None:
        from config import config

        m = await self.repo.get_member(user_id)
        if not m:
            return

        primary_map = config.primary_team_role_ids
        secondary_map = config.secondary_team_role_ids

        desired_primary_ids: set[int] = set()
        primary_team = m.get("primary_team")
        if primary_team and primary_team in primary_map:
            desired_primary_ids.add(primary_map[primary_team])

        desired_secondary_ids: set[int] = set()
        for team_key in m.get("secondary_teams", []):
            if team_key in secondary_map:
                desired_secondary_ids.add(secondary_map[team_key])

        managed_primary_ids = set(primary_map.values())
        managed_secondary_ids = set(secondary_map.values())
        current_role_ids = {role.id for role in member.roles}

        for role_id in managed_primary_ids:
            role = guild.get_role(role_id)
            if not role:
                continue
            if role_id in desired_primary_ids and role_id not in current_role_ids:
                await member.add_roles(role, reason="主所属班ロール同期")
            elif role_id not in desired_primary_ids and role_id in current_role_ids:
                await member.remove_roles(role, reason="主所属班ロール同期")

        for role_id in managed_secondary_ids:
            role = guild.get_role(role_id)
            if not role:
                continue
            if role_id in desired_secondary_ids and role_id not in current_role_ids:
                await member.add_roles(role, reason="副所属班ロール同期")
            elif role_id not in desired_secondary_ids and role_id in current_role_ids:
                await member.remove_roles(role, reason="副所属班ロール同期")
        
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
        await self._sync_roles(interaction.guild, user, str(user.id))
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
        await self._sync_roles(interaction.guild, user, str(user.id))
        await self._sync_members_sheet()
    
    
    # ---------- assign-sub-team ----------
    @group.command(name="assign-sub-team", description="副所属班を追加または削除します。")
    @app_commands.describe(user="対象ユーザー", team="副所属班", remove="削除する場合はTrue")
    @app_commands.choices(team=TEAM_CHOICES)
    @require(Level.L2)
    async def assign_sub_team(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        team: app_commands.Choice[str],
        remove: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        await self.repo.upsert_member(str(user.id), user.display_name)
        m = await self.repo.get_member(str(user.id))
        secondary_teams = list(m["secondary_teams"])

        if not remove and team.value == m.get("primary_team"):
            await interaction.followup.send(
                embed=error_embed("主所属班と同じ班は副所属班に設定できません。"),
                ephemeral=True,
            )
            return

        if remove:
            secondary_teams = [t for t in secondary_teams if t != team.value]
            action = "削除"
        else:
            if team.value not in secondary_teams:
                secondary_teams.append(team.value)
            action = "追加"

        await self.repo.set_secondary_teams(str(user.id), secondary_teams)
        await self._sync_roles(interaction.guild, user, str(user.id))

        await interaction.followup.send(
            embed=success_embed(
                f"副所属班を{action}しました",
                f"{user.display_name} → {team.name}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )
        await self._sync_members_sheet()

    # ---------- setup (統合コマンド) ----------
    @group.command(name="setup", description="主所属班・副所属班・班長を一括設定します。")
    @app_commands.describe(
        user="対象ユーザー",
        primary_team="主所属班",
        secondary_teams="副所属班（複数の場合はカンマ区切り、例: wing,tail）",
        is_leader="班長にするか（省略時は変更しない）",
    )
    @app_commands.choices(primary_team=TEAM_CHOICES)
    @require(Level.L3)
    async def setup_member(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        primary_team: app_commands.Choice[str] | None = None,
        secondary_teams: str | None = None,
        is_leader: bool | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.repo.upsert_member(str(user.id), user.display_name)

        changes: list[str] = []

        # 主所属班
        if primary_team is not None:
            await self.repo.set_primary_team(str(user.id), primary_team.value)
            changes.append(f"主所属班: {primary_team.name}")

        # 副所属班（カンマ区切りでパース）
        if secondary_teams is not None:
            raw_keys = [s.strip() for s in secondary_teams.split(",") if s.strip()]
            # バリデーション
            valid_keys = {key for key, _ in INITIAL_TEAMS}
            invalid = [k for k in raw_keys if k not in valid_keys]
            if invalid:
                await interaction.followup.send(
                    embed=error_embed(
                        f"無効な班キーが含まれています: {', '.join(invalid)}\n"
                        f"有効な値: {', '.join(valid_keys)}"
                    ),
                    ephemeral=True,
                )
                return

            # 主所属班と重複チェック
            current_primary = primary_team.value if primary_team else (
                (await self.repo.get_member(str(user.id)) or {}).get("primary_team")
            )
            if current_primary and current_primary in raw_keys:
                await interaction.followup.send(
                    embed=error_embed(
                        f"副所属班に主所属班（{TEAM_NAME.get(current_primary, current_primary)}）は設定できません。"
                    ),
                    ephemeral=True,
                )
                return

            await self.repo.set_secondary_teams(str(user.id), raw_keys)
            names = "、".join(TEAM_NAME.get(k, k) for k in raw_keys) or "なし"
            changes.append(f"副所属班: {names}")

        # 班長フラグ
        if is_leader is not None:
            await self.repo.set_leader(str(user.id), is_leader)
            changes.append(f"班長: {'はい' if is_leader else 'いいえ'}")

        if not changes:
            await interaction.followup.send(
                embed=info_embed("変更なし", "設定する項目を1つ以上指定してください。"),
                ephemeral=True,
            )
            return

        await self._sync_roles(interaction.guild, user, str(user.id))
        await self._sync_members_sheet()

        await interaction.followup.send(
            embed=success_embed(
                "メンバー設定を更新しました",
                f"**{user.display_name}**\n" + "\n".join(f"・{c}" for c in changes),
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

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
