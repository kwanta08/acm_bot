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

from config import MULTI_ROLE_KEYS, GuildConfig, config
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
    # 班長（L2）の判定は LEADER_ROLE_IDS だけを根拠にしている
    # （utils/permissions.has_level）のに、ウィザードから設定できなかった
    ("LEADER_ROLE_IDS", "班長ロール"),
]
ALL_SETUP_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS + ROLE_SETTINGS}
# セレクト以外（Modal 入力）で設定できるキー
EXTRA_SETUP_KEYS: set[str] = {"CLUB_NAME"}
_CHANNEL_KEYS: set[str] = {k for k, _ in CHANNEL_SETTINGS}
_ROLE_KEYS: set[str] = {k for k, _ in ROLE_SETTINGS}

# RoleSelect が一度に選べる上限。受入基準は 5 だが、/setup は**上書き**保存
# なので、5 にすると班長ロールを6件以上運用しているギルドで L2 判定の根拠を
# 黙って切り捨てることになる。上限まで広げ、それでも超える場合は
# /setup からの上書きを断って /set_role へ誘導する。
MAX_MULTI_ROLE_VALUES = 25

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


def build_setup_embed(
    gconf: GuildConfig, selected_key: str | None = None, notice: str | None = None
) -> discord.Embed:
    """
    ギルド別設定の一覧 Embed を生成する。
    未設定項目には「未設定」を明示する。

    selected_key を渡すと「いま何を設定しようとしているか」を添える。
    班長ロールは**上書き**保存なので、現在値が見えたまま選び直させる
    （案内文だけに頼らず、消えるものが画面に出ている状態にする）。
    """
    lines: list[str] = []
    # サークル名は任意設定（未設定時は汎用表現にフォールバックするため
    # 未設定カウントには含めない）
    lines.append(f"**サークル名**: {gconf.club_name_or_default}")
    missing = 0
    for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS:
        value = getattr(gconf, key.lower())
        # 班長ロールだけ list[int]。空リストは「未設定」であって
        # `value is None` では拾えない（<@&[]> と描画されてしまう）
        if key in MULTI_ROLE_KEYS:
            role_ids = list(value or [])
            if role_ids:
                lines.append(f"**{label}**: " + " ".join(f"<@&{rid}>" for rid in role_ids))
            else:
                lines.append(f"**{label}**: ⚠️ 未設定")
                missing += 1
            continue
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

    if selected_key:
        label = dict(CHANNEL_SETTINGS + ROLE_SETTINGS).get(selected_key, selected_key)
        if selected_key in MULTI_ROLE_KEYS:
            summary += (
                f"\n\n選択中: **{label}** — 選んだロールで**置き換わります**"
                "（1つだけ外すときは `/set_role action:remove`）。"
            )
        else:
            summary += f"\n\n選択中: **{label}** — 選んだロールで置き換わります。"
    if notice:
        summary += f"\n\n{notice}"

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
        await interaction.response.edit_message(
            embed=build_setup_embed(gconf, selected_key=self.selected_key), view=self
        )

    async def _current_role_ids(self, key: str) -> list[int]:
        gconf = await config.for_guild(self.guild_id, db=self.cog.db, force_reload=True)
        return list(getattr(gconf, key.lower(), None) or [])

    @discord.ui.select(
        placeholder="設定したい項目を選択…",
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in CHANNEL_SETTINGS + ROLE_SETTINGS
        ],
    )
    async def select_item(self, interaction: discord.Interaction, select: discord.ui.Select):
        """設定する項目を選ぶ。

        **元メッセージの View を送り直す**のが要点。Python 側で
        max_values を変えても、クライアントが持っているコンポーネント定義は
        max_values=1 のままなので複数選択が一生発生しない。
        """
        self.selected_key = select.values[0]
        notice: str | None = None

        if self.selected_key in MULTI_ROLE_KEYS:
            self.select_role.max_values = MAX_MULTI_ROLE_VALUES
            current = await self._current_role_ids(self.selected_key)
            # 上限を超えて保存されているギルドでは、選ばせてから
            # 黙って切り捨てない（選ぶ前に断る）
            if len(current) > MAX_MULTI_ROLE_VALUES:
                self.select_role.disabled = True
                notice = (
                    f"⚠️ この項目には {len(current)} 件が保存されており、"
                    f"一度に選べる上限（{MAX_MULTI_ROLE_VALUES} 件）を超えています。"
                    "ここで上書きすると一部が消えるため、`/set_role action:remove` で"
                    "減らしてから設定してください。"
                )
            else:
                self.select_role.disabled = False
        else:
            self.select_role.max_values = 1
            self.select_role.disabled = False

        gconf = await config.for_guild(self.guild_id, db=self.cog.db)
        try:
            await interaction.response.edit_message(
                embed=build_setup_embed(gconf, selected_key=self.selected_key, notice=notice),
                view=self,
            )
        except discord.HTTPException as e:
            log.warning("/setup の項目選択の反映に失敗 (guild=%s): %s", self.guild_id, e)

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

        role_ids = [str(role.id) for role in select.values]
        if self.selected_key in MULTI_ROLE_KEYS:
            # 選び直しは上書き（受入基準）。上限超過は select_item で
            # 断っているが、コンポーネント定義が古いまま送られる場合に
            # 備えて保存側でも見る
            current = await self._current_role_ids(self.selected_key)
            if len(current) > MAX_MULTI_ROLE_VALUES:
                await interaction.response.send_message(
                    embed=error_embed(
                        f"保存済みの {len(current)} 件が上限を超えているため、"
                        "ここでは上書きできません。`/set_role action:remove` で"
                        "減らしてから設定してください。"
                    ),
                    ephemeral=True,
                )
                return
            value = ",".join(role_ids)
        else:
            if len(role_ids) > 1:
                await interaction.response.send_message(
                    embed=error_embed("この項目に指定できるロールは1つだけです。"),
                    ephemeral=True,
                )
                return
            value = role_ids[0]

        await self.cog.save_setting(self.guild_id, self.selected_key, value)
        log.info("/setup で保存 (guild=%s): %s=%s", self.guild_id, self.selected_key, value)
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
