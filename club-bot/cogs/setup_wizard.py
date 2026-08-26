"""
Setup コグ（/setup 設定ウィザード）

導入直後のサーバー向けに、ギルド別設定の状況を Embed で一覧し、
Select / ChannelSelect / RoleSelect で対話的に設定・保存する。

- 実行はギルド管理者（L4）のみ。DM からの実行は拒否する
- 設定はギルド別 settings テーブルに (guild_id, setting_key) で保存され、
  config.for_guild(guild_id) 経由で解決される（他ギルドへは影響しない）
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import GuildConfig, config
from repositories.audit_log_repository import AuditLogRepository
from repositories.member_repository import MemberRepository
from repositories.settings_repository import SettingsRepository
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin
from utils.views import TimeoutAwareView

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("setup_wizard")

# /setup で設定できる項目（キー, 表示名, 種別）
#   種別は "channel" / "role"。キー名の小文字化が GuildConfig の属性名と一致する
CHANNEL_SETTINGS: list[tuple[str, str]] = [
    ("DEFAULT_ANNOUNCE_CHANNEL_ID", "お知らせチャンネル"),
    ("DEFAULT_SCHEDULE_CHANNEL_ID", "日程調整チャンネル"),
    ("DEFAULT_PROGRESS_CHANNEL_ID", "進捗チャンネル"),
    ("DEFAULT_TASK_CHANNEL_ID", "タスク通知チャンネル"),
    ("TODAY_LABEL_CHANNEL_ID", "今日やること通知チャンネル"),
    ("BOT_LOG_CHANNEL_ID", "Botログチャンネル"),
]
ROLE_SETTINGS: list[tuple[str, str]] = [
    ("ADMIN_ROLE_ID", "Bot管理者ロール"),
    ("EXEC_ROLE_ID", "実行役ロール"),
]
ALL_SETUP_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS + ROLE_SETTINGS}
# セレクト以外（Modal 入力）で設定できるキー
EXTRA_SETUP_KEYS: set[str] = {"CLUB_NAME"}
_CHANNEL_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS}
_ROLE_KEYS: set[str] = {k for k, _ in ROLE_SETTINGS}

# 班名の最大文字数（cogs/teams.py の MAX_NAME_LENGTH と揃える）
MAX_TEAM_NAME_LENGTH = 50

# 班名入力の区切り文字（半角/全角カンマ・読点・改行）
_TEAM_NAME_SPLIT = re.compile(r"[,，、\n]")


def parse_team_names(text: str) -> list[str]:
    """
    カンマ区切り（全角・読点・改行も可）の班名文字列をパースする。
    前後空白を除去し、空要素と重複を取り除いて入力順を保持する。
    長すぎる班名が含まれる場合は ValueError。
    """
    names: list[str] = []
    seen: set[str] = set()
    for part in _TEAM_NAME_SPLIT.split(text or ""):
        name = part.strip()
        if not name or name in seen:
            continue
        if len(name) > MAX_TEAM_NAME_LENGTH:
            raise ValueError(f"班名は{MAX_TEAM_NAME_LENGTH}文字以内にしてください: {name[:20]}…")
        names.append(name)
        seen.add(name)
    return names


def build_setup_embed(gconf: GuildConfig) -> discord.Embed:
    """
    ギルド別設定の一覧 Embed を生成する。
    未設定項目には「未設定」を明示する。
    """
    lines: list[str] = []
    # サークル名は任意設定（未設定時は汎用表現にフォールバックするため
    # 未設定カウントには含めない）
    lines.append(f"**サークル名**: {gconf.club_name_or_default}")
    missing = 0
    for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS:
        value = getattr(gconf, key.lower())
        if value is None:
            lines.append(f"**{label}**: ⚠️ 未設定")
            missing += 1
        elif key in _CHANNEL_KEYS:
            lines.append(f"**{label}**: <#{value}>")
        else:
            lines.append(f"**{label}**: <@&{value}>")

    if missing:
        summary = f"未設定が {missing} 件あります。下のセレクトで項目を選んで設定してください。"
    else:
        summary = "すべての項目が設定済みです。"
    summary += "\n「班を一括作成」ボタンで、班と対応ロールをまとめて登録できます。"
    embed = info_embed("セットアップ状況", "\n".join(lines) + "\n\n" + summary)
    return embed


class ClubNameModal(discord.ui.Modal, title="サークル名の設定"):
    """サークル名を入力してギルド別設定に保存する Modal。"""

    name_input = discord.ui.TextInput(
        label="サークル名",
        placeholder="例: ○○大学 鳥人間サークル",
        required=True,
        max_length=50,
    )

    def __init__(self, cog: SetupWizard, guild_id: int, owner_id: int, current: str | None):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        if current:
            self.name_input.default = current

    async def on_submit(self, interaction: discord.Interaction):
        # コマンド実行者と同一であることを検証
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"), ephemeral=True
            )
            return
        name = (self.name_input.value or "").strip()
        if not name:
            await interaction.response.send_message(
                embed=error_embed("サークル名を入力してください。"), ephemeral=True
            )
            return

        await self.cog.save_setting(self.guild_id, "CLUB_NAME", name)
        log.info("/setup でサークル名を保存 (guild=%s)", self.guild_id)
        await interaction.response.send_message(
            embed=success_embed(
                "サークル名を設定しました",
                f"**{name}**\n週次レポート等のタイトルに表示されます。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.warning(
            "サークル名設定 Modal でエラー (guild=%s): %s", self.guild_id, type(error).__name__
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=error_embed("保存に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=error_embed("保存に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True,
                )
        # エラー通知の送信自体に失敗した場合はこれ以上できることがないため握りつぶす
        except Exception:  # noqa: BLE001, S110
            pass


class TeamBulkCreateModal(discord.ui.Modal, title="班の一括作成"):
    """班名をカンマ区切りで入力し、班と対応ロールを一括作成する Modal。"""

    names_input = discord.ui.TextInput(
        label="班名（カンマ区切り）",
        style=discord.TextStyle.paragraph,
        placeholder="例: 設計班, 製造班, 広報班",
        required=True,
        max_length=1000,
    )

    def __init__(self, cog: SetupWizard, guild_id: int, owner_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        # コマンド実行者と同一であることを検証
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"), ephemeral=True
            )
            return

        try:
            names = parse_team_names(self.names_input.value or "")
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
            return
        if not names:
            await interaction.response.send_message(
                embed=error_embed("班名を1つ以上入力してください。"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        teams = await self.cog.register_teams(
            self.guild_id, names, actor_id=str(interaction.user.id)
        )

        # 各班に対応する Discord ロールを作成して紐付ける
        # （権限不足等で失敗しても班の登録は維持し、失敗分だけ案内する）
        role_failed: list[str] = []
        guild = interaction.guild
        repo = MemberRepository(self.cog.db)
        for t in teams:
            if guild is None:
                role_failed.append(t["name"])
                continue
            try:
                role = await guild.create_role(
                    name=t["name"], mentionable=True, reason="/setup による班の一括作成"
                )
                await repo.set_team_roles(self.guild_id, t["slug"], member_role_id=str(role.id))
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("班ロール作成失敗 (guild=%s, team=%s): %s", self.guild_id, t["slug"], e)
                role_failed.append(t["name"])

        lines = [f"班 **{t['name']}**（`{t['slug']}`）" for t in teams]
        desc = "\n".join(lines)
        if role_failed:
            desc += (
                f"\n\n⚠️ ロールの自動作成に失敗: {', '.join(role_failed)}\n"
                "Bot に「ロールの管理」権限があるか確認し、"
                "`/team-role` で既存ロールを紐付けてください。"
            )
        else:
            desc += "\n\n各班のロールも作成し、主所属ロールとして紐付けました。"
        desc += "\n追加・変更は `/team-add` `/team-remove` `/team-role` でも行えます。"
        await interaction.followup.send(
            embed=success_embed(
                f"班を {len(teams)} 件登録しました", desc, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.warning("班一括作成 Modal でエラー (guild=%s): %s", self.guild_id, type(error).__name__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=error_embed("班の作成に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=error_embed("班の作成に失敗しました。時間をおいて再試行してください。"),
                    ephemeral=True,
                )
        # エラー通知の送信自体に失敗した場合はこれ以上できることがないため握りつぶす
        except Exception:  # noqa: BLE001, S110
            pass


class SetupWizardView(TimeoutAwareView):
    """/setup の ephemeral メッセージに付ける対話 View（5分で無効化）。"""

    def __init__(self, cog: SetupWizard, guild_id: int, owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.selected_key: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # コマンド実行者以外は操作不可
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("この操作はコマンドの実行者のみ可能です。"), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="サークル名を設定", style=discord.ButtonStyle.secondary, row=3)
    async def open_club_name_modal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        gconf = await config.for_guild(self.guild_id, db=self.cog.db)
        await interaction.response.send_modal(
            ClubNameModal(self.cog, self.guild_id, self.owner_id, gconf.club_name)
        )

    @discord.ui.button(label="班を一括作成", style=discord.ButtonStyle.primary, row=3)
    async def open_team_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            TeamBulkCreateModal(self.cog, self.guild_id, self.owner_id)
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """保存後に元メッセージの Embed を最新状態へ更新する。"""
        gconf = await config.for_guild(self.guild_id, db=self.cog.db, force_reload=True)
        await interaction.response.edit_message(embed=build_setup_embed(gconf), view=self)

    @discord.ui.select(
        placeholder="設定したい項目を選択…",
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS
        ],
    )
    async def select_item(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_key = select.values[0]
        label = dict(CHANNEL_SETTINGS + ROLE_SETTINGS)[self.selected_key]
        kind = "チャンネル" if self.selected_key in _CHANNEL_KEYS else "ロール"
        await interaction.response.send_message(
            embed=info_embed(
                "項目を選択しました",
                f"**{label}** を設定します。\n下の{kind}セレクトで対象を選んでください。",
            ),
            ephemeral=True,
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="チャンネルを選択（先に項目を選択）",
        channel_types=[discord.ChannelType.text],
    )
    async def select_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ):
        if self.selected_key not in _CHANNEL_KEYS:
            await interaction.response.send_message(
                embed=error_embed("先に「設定したい項目」でチャンネル系の項目を選択してください。"),
                ephemeral=True,
            )
            return
        channel = select.values[0]
        await self.cog.save_setting(self.guild_id, self.selected_key, str(channel.id))
        log.info("/setup で保存 (guild=%s): %s=%s", self.guild_id, self.selected_key, channel.id)
        await self._refresh(interaction)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="ロールを選択（先に項目を選択）",
    )
    async def select_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if self.selected_key not in _ROLE_KEYS:
            await interaction.response.send_message(
                embed=error_embed("先に「設定したい項目」でロール系の項目を選択してください。"),
                ephemeral=True,
            )
            return
        role = select.values[0]
        await self.cog.save_setting(self.guild_id, self.selected_key, str(role.id))
        log.info("/setup で保存 (guild=%s): %s=%s", self.guild_id, self.selected_key, role.id)
        await self._refresh(interaction)



class SetupWizard(commands.Cog):
    """初期設定ウィザード コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.settings_repo = SettingsRepository(self.db)
        self.member_repo = MemberRepository(self.db)
        self.audit_repo = AuditLogRepository(self.db)

    async def register_teams(
        self, guild_id: int, names: list[str], actor_id: str = ""
    ) -> list[dict[str, str]]:
        """
        班名リストから班を一括登録する。
        識別子（slug）は `team1` `team2` … を既存キーと重ならないよう自動採番する。
        戻り値は登録した班の {"slug", "name"} のリスト（入力順）。
        """
        existing = {
            t["team_key"] for t in await self.member_repo.list_teams(guild_id, active_only=False)
        }
        created: list[dict[str, str]] = []
        n = 1
        for name in names:
            while f"team{n}" in existing:
                n += 1
            slug = f"team{n}"
            await self.member_repo.upsert_team(guild_id, slug, name)
            existing.add(slug)
            created.append({"slug": slug, "name": name})
        if created:
            await self.audit_repo.record(
                guild_id,
                actor_id,
                "team.bulk_add",
                detail=f"{len(created)} 件: " + ", ".join(t["name"] for t in created),
            )
        return created

    async def save_setting(self, guild_id: int, key: str, value: str) -> None:
        """
        ギルド別 settings に値を保存し、解決キャッシュを更新する。
        /setup で扱わないキーは拒否する。
        """
        if key not in ALL_SETUP_KEYS | EXTRA_SETUP_KEYS:
            raise ValueError(f"/setup では設定できないキーです: {key}")
        await self.settings_repo.set(guild_id, key, value)
        config.invalidate_guild(guild_id)
        # レガシーギルドのグローバル設定を再読込
        await config.load_from_db(self.db)

    @app_commands.command(
        name="setup", description="このサーバーの初期設定を対話的に行います（管理者）。"
    )
    @app_commands.check(is_admin)
    async def setup(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            gconf = await config.for_guild(guild_id, db=self.db, force_reload=True)
            embed = build_setup_embed(gconf)
            view = SetupWizardView(self, guild_id, interaction.user.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            view.message = await interaction.original_response()
        except Exception:
            log.exception("/setup 表示エラー")
            embed = error_embed("セットアップ画面の表示に失敗しました")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupWizard(bot))
