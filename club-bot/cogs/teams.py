"""
Teams / Skills 管理モジュール。

班（teams）と技能タグ（skill_tags）のマスタをギルド単位で管理する
管理者向けコマンド群。config.py の固定配列（INITIAL_TEAMS / SKILL_TAGS）は
廃止され、新規ギルドは班・技能タグが空の状態で開始する。

権限: すべて Bot 管理者（L4）限定。admin_role_id 未設定でも
サーバーオーナーまたは Discord の管理者権限（Administrator）で実行できる
（utils/permissions.is_admin の既存仕様）。
"""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.audit_log_repository import AuditLogRepository
from repositories.member_repository import MemberRepository
from repositories.skill_tag_repository import SkillTagRepository
from services import team_service
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

log = get_logger("teams")

# 班識別子（slug）の書式: 先頭は英小文字または数字、以降は英小文字・数字・-・_、32文字以内
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_NAME_LENGTH = 50


class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = MemberRepository(bot.db)
        self.skill_repo = SkillTagRepository(bot.db)
        self.audit_repo = AuditLogRepository(bot.db)

    # ---------- autocomplete ----------
    async def _team_ac(self, interaction: discord.Interaction,
                       current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        return await team_service.team_choices(self.bot.db, interaction.guild.id, current)

    async def _skill_ac(self, interaction: discord.Interaction,
                        current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        return await team_service.skill_choices(self.bot.db, interaction.guild.id, current)

    # ==================================================================
    # 班管理
    # ==================================================================
    @app_commands.command(name="team-add", description="班を追加します（管理者）。")
    @app_commands.describe(slug="班の識別子（半角英小文字・数字・-・_、32文字以内）",
                           name="班の表示名")
    @app_commands.check(is_admin)
    async def team_add(self, interaction: discord.Interaction, slug: str, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        slug = slug.strip()
        name = name.strip()
        if not SLUG_PATTERN.match(slug):
            await interaction.followup.send(
                embed=error_embed(
                    "識別子は半角英小文字または数字で始まり、"
                    "英小文字・数字・`-`・`_` のみ（32文字以内）で指定してください。"),
                ephemeral=True)
            return
        if not name or len(name) > MAX_NAME_LENGTH:
            await interaction.followup.send(
                embed=error_embed(f"表示名は1〜{MAX_NAME_LENGTH}文字で指定してください。"),
                ephemeral=True)
            return

        existing = await self.repo.get_team(guild_id, slug)
        await self.repo.upsert_team(guild_id, slug, name)
        await self.audit_repo.record(guild_id, str(interaction.user.id), "team.add",
                                     target=slug, detail=f"表示名: {name}")

        if existing and not existing["active_flag"]:
            desc = f"無効化されていた班 **{name}**（`{slug}`）を再有効化しました"
        elif existing:
            desc = f"班 **{name}**（`{slug}`）の表示名を更新しました"
        else:
            desc = f"班 **{name}**（`{slug}`）を追加しました"
        await interaction.followup.send(
            embed=success_embed("班を登録しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)

    @app_commands.command(name="team-remove",
                          description="班を無効化します（論理削除。管理者）。")
    @app_commands.describe(slug="無効化する班の識別子",
                           confirm="所属メンバーがいても無効化する場合は True")
    @app_commands.autocomplete(slug=_team_ac)
    @app_commands.check(is_admin)
    async def team_remove(self, interaction: discord.Interaction, slug: str,
                          confirm: bool = False):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        team = await self.repo.get_team(guild_id, slug)
        if not team or not team["active_flag"]:
            await interaction.followup.send(
                embed=error_embed(f"班 `{slug}` は登録されていません。"), ephemeral=True)
            return

        count = await self.repo.count_primary_members(guild_id, slug)
        if count and not confirm:
            await interaction.followup.send(
                embed=error_embed(
                    f"**{team['team_name']}** には主所属メンバーが **{count} 名**います。\n"
                    "無効化してもメンバーの所属情報は保持されますが、"
                    "選択肢には表示されなくなります。\n"
                    "実行する場合は `confirm:True` を指定して再度実行してください。"),
                ephemeral=True)
            return

        await self.repo.deactivate_team(guild_id, slug)
        await self.audit_repo.record(guild_id, str(interaction.user.id), "team.remove",
                                     target=slug,
                                     detail=f"主所属メンバー {count} 名のまま無効化")
        await interaction.followup.send(
            embed=success_embed("班を無効化しました",
                                f"**{team['team_name']}**（`{slug}`）\n"
                                "再有効化する場合は `/team-add` で同じ識別子を登録してください。",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @app_commands.command(name="team-list", description="班の一覧を表示します（管理者）。")
    @app_commands.check(is_admin)
    async def team_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        teams = await self.repo.list_teams(guild_id, active_only=False)
        if not teams:
            await interaction.followup.send(
                embed=info_embed("班一覧",
                                 "登録されている班はありません。\n"
                                 "`/team-add` で追加してください。"),
                ephemeral=True)
            return

        members = await self.repo.list_members(guild_id)
        counts: dict[str, int] = {}
        for m in members:
            key = m.get("primary_team")
            if key:
                counts[key] = counts.get(key, 0) + 1

        embed = info_embed("班一覧")
        for t in teams[:25]:
            status = "✅ 有効" if t["active_flag"] else "⛔ 無効"
            lines = [f"slug: `{t['team_key']}` / {status} / 主所属: {counts.get(t['team_key'], 0)}名"]
            if t.get("member_role_id"):
                lines.append(f"班ロール: <@&{t['member_role_id']}>")
            if t.get("secondary_role_id"):
                lines.append(f"副所属ロール: <@&{t['secondary_role_id']}>")
            if t.get("leader_role_id"):
                lines.append(f"リーダーロール: <@&{t['leader_role_id']}>")
            if t.get("channel_id"):
                lines.append(f"通知ch: <#{t['channel_id']}>")
            embed.add_field(name=t["team_name"], value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="team-role",
                          description="班と Discord ロールを紐付けます（管理者）。")
    @app_commands.describe(team="班の識別子",
                           role="紐付ける Discord ロール",
                           role_type="primary=主所属ロール / secondary=副所属ロール（既定: primary）")
    @app_commands.autocomplete(team=_team_ac)
    @app_commands.choices(role_type=[
        app_commands.Choice(name="primary（主所属）", value="primary"),
        app_commands.Choice(name="secondary（副所属）", value="secondary"),
    ])
    @app_commands.check(is_admin)
    async def team_role(self, interaction: discord.Interaction, team: str,
                        role: discord.Role, role_type: str = "primary"):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        t = await self.repo.get_team(guild_id, team)
        if not t:
            await interaction.followup.send(
                embed=error_embed(
                    f"班 `{team}` は登録されていません。先に `/team-add` で追加してください。"),
                ephemeral=True)
            return

        if role_type == "primary":
            await self.repo.set_team_roles(guild_id, team, member_role_id=str(role.id))
            label = "主所属ロール"
        else:
            await self.repo.set_team_roles(guild_id, team, secondary_role_id=str(role.id))
            label = "副所属ロール"
        await self.audit_repo.record(guild_id, str(interaction.user.id), "team.role",
                                     target=team,
                                     detail=f"{label} を {role.name} ({role.id}) に設定")
        # ロール関連のギルド別設定キャッシュを破棄
        config.invalidate_guild(guild_id)
        await interaction.followup.send(
            embed=success_embed("班ロールを設定しました",
                                f"**{t['team_name']}** の{label} → {role.mention}\n"
                                "以降、所属変更時にこのロールが自動で付与・剥奪されます。",
                                executor=interaction.user.display_name),
            ephemeral=True)

    # ==================================================================
    # 技能タグ管理
    # ==================================================================
    @app_commands.command(name="skill-add", description="技能タグを追加します（管理者）。")
    @app_commands.describe(name="技能タグ名")
    @app_commands.check(is_admin)
    async def skill_add(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            await interaction.followup.send(
                embed=error_embed(f"タグ名は1〜{MAX_NAME_LENGTH}文字で指定してください。"),
                ephemeral=True)
            return

        existing = await self.skill_repo.get(guild_id, name)
        if existing and existing["active_flag"]:
            await interaction.followup.send(
                embed=info_embed("技能タグ", f"「{name}」は既に登録されています。"),
                ephemeral=True)
            return

        await self.skill_repo.add(guild_id, name, str(interaction.user.id))
        await self.audit_repo.record(guild_id, str(interaction.user.id), "skill.add",
                                     target=name)
        desc = (f"無効化されていた技能タグ「**{name}**」を再有効化しました"
                if existing else f"技能タグ「**{name}**」を追加しました")
        await interaction.followup.send(
            embed=success_embed("技能タグを登録しました", desc,
                                executor=interaction.user.display_name),
            ephemeral=True)

    @app_commands.command(name="skill-remove",
                          description="技能タグを無効化します（論理削除。管理者）。")
    @app_commands.describe(name="無効化する技能タグ名")
    @app_commands.autocomplete(name=_skill_ac)
    @app_commands.check(is_admin)
    async def skill_remove(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        ok = await self.skill_repo.deactivate(guild_id, name)
        if not ok:
            await interaction.followup.send(
                embed=error_embed(f"技能タグ「{name}」は登録されていません。"),
                ephemeral=True)
            return
        await self.audit_repo.record(guild_id, str(interaction.user.id), "skill.remove",
                                     target=name)
        await interaction.followup.send(
            embed=success_embed("技能タグを無効化しました",
                                f"「**{name}**」\n"
                                "既に付与されたメンバーの技能表示は保持されます。",
                                executor=interaction.user.display_name),
            ephemeral=True)

    @app_commands.command(name="skill-list", description="技能タグの一覧を表示します（管理者）。")
    @app_commands.check(is_admin)
    async def skill_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.skill_repo.list_all(guild_id)
        if not rows:
            await interaction.followup.send(
                embed=info_embed("技能タグ一覧",
                                 "登録されている技能タグはありません。\n"
                                 "`/skill-add` で追加してください。"),
                ephemeral=True)
            return

        members = await self.repo.list_members(guild_id)
        counts: dict[str, int] = {}
        for m in members:
            for s in m["skills"]:
                counts[s] = counts.get(s, 0) + 1

        lines = [
            f"{'✅' if r['active_flag'] else '⛔'} {r['skill_name']}（{counts.get(r['skill_name'], 0)}名）"
            for r in rows
        ]
        await interaction.followup.send(
            embed=info_embed("技能タグ一覧", "\n".join(lines)), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
