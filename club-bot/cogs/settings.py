"""
Settings コグ（マルチテナント版）

ボットの設定をコマンドで管理するためのモジュール。
設定はギルドごと（guild_id 単位）に保存され、他ギルドへは影響しない。
管理者のみが設定を変更できる。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import MULTI_ROLE_KEYS, config
from repositories.settings_repository import SettingsRepository
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import ensure_guild, is_admin

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("settings")


def split_role_tokens(raw: str) -> tuple[list[str], list[str]]:
    """ロールIDの入力を (数字トークン, 不正トークン) に分ける。

    docs/GUIDE.md が「複数の班長ロールはカンマ区切り」と案内しているため
    カンマ区切りを受ける。`<@&123>` のメンション形式も剥がす。
    数字にならないトークンは**捨てずに返す**（呼び出し側が利用者へ報告する）。
    """
    ids: list[str] = []
    invalid: list[str] = []
    for part in (raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        if token.startswith("<@&") and token.endswith(">"):
            token = token[3:-1].strip()
        if token.isdigit():
            if token not in ids:
                ids.append(token)
        else:
            invalid.append(token)
    return ids, invalid


@dataclass
class RoleIdMerge:
    """複数値ロール設定の編集結果。

    `changed` が False のときは**保存しない**。何も変えていない操作で
    既存値の非数値トークンだけが黙って消えるのを防ぐ（ADR 0024）。
    """

    values: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    def to_value(self) -> str:
        return ",".join(self.values)


def merge_role_ids(current_raw: str, tokens: list[str], *, remove: bool) -> RoleIdMerge:
    """現在値へロールIDを足す / 外す。重複は保存時にだけ取り除く。

    既存値の正規化（重複除去）は**この保存のときだけ**行い、
    マイグレーションでの一括正規化はしない（ADR 0024）。
    """
    current, dropped = split_role_tokens(current_raw)
    result = RoleIdMerge(values=list(current), dropped=dropped)
    for token in tokens:
        if remove:
            if token in result.values:
                result.values.remove(token)
                result.removed.append(token)
            else:
                result.not_found.append(token)
        elif token in result.values:
            result.duplicates.append(token)
        else:
            result.values.append(token)
            result.added.append(token)
    return result


def stale_role_warning(key: str, leftover: list[str]) -> str | None:
    """保存後も解決結果に残っている ID があるときの案内文。

    原因は2つあり、直し方が違う。取り違えると「存在しない .env の行を
    直せ」という嘘の案内になる。
      1. 環境変数が設定されている → .env を直さないと再起動しても直らない
      2. GUILD_ID 指定のレガシーギルドで、起動時に読んだ値がプロセス内に
         残っている（config.load_from_db は一度入った値を減らさない）
         → 再起動だけで直る
    """
    if not leftover:
        return None
    ids = "・".join(sorted(leftover))
    if os.getenv(key):
        return (
            f"⚠️ 環境変数 `{key}` が優先されているため、この設定は反映されていません"
            f"（いま有効: {ids}）。`.env` を修正して bot を再起動してください。"
        )
    return (
        f"⚠️ 起動時に読み込まれた旧設定が残っているため、この設定は反映されていません"
        f"（いま有効: {ids}）。bot の再起動で反映されます。"
    )


class Settings(commands.Cog):
    """ボット設定管理コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.settings_repo = SettingsRepository(self.db)

    async def _after_change(self, guild_id: int) -> None:
        """設定変更後の反映処理: ギルド別キャッシュ破棄 + グローバル再読込。"""
        config.invalidate_guild(guild_id)
        # レガシーギルドのグローバル設定を再読込
        await config.load_from_db(self.db)

    @app_commands.command(name="settings_list", description="全ての設定を表示します")
    @app_commands.check(is_admin)
    async def settings_list(self, interaction: discord.Interaction):
        """全ての設定を表示する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            settings = await self.settings_repo.get_all(guild_id)

            if not settings:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # 設定をカテゴリ別に整理
            categories = {"チャンネル": [], "ロール": [], "共通": [], "その他": []}

            channel_keys = {
                "BOT_LOG_CHANNEL_ID",
                "DEFAULT_ANNOUNCE_CHANNEL_ID",
                "DEFAULT_SCHEDULE_CHANNEL_ID",
                "DEFAULT_PROGRESS_CHANNEL_ID",
                "DEFAULT_TASK_CHANNEL_ID",
                "TODAY_LABEL_CHANNEL_ID",
            }
            role_keys = {
                "EXEC_ROLE_ID",
                "ADMIN_ROLE_ID",
                "LEADER_ROLE_IDS",
                "PRIMARY_TEAM_ROLE_IDS",
                "SECONDARY_TEAM_ROLE_IDS",
            }
            common_keys = {"TZ", "DB_PATH"}

            for key, value in settings.items():
                if key in channel_keys:
                    categories["チャンネル"].append((key, value))
                elif key in role_keys:
                    categories["ロール"].append((key, value))
                elif key in common_keys:
                    categories["共通"].append((key, value))
                elif key.startswith("TODOIST_"):
                    # レガシーの平文 Todoist 設定は表示しない
                    # （/todoist-setup での暗号化登録に置き換わった）
                    categories["その他"].append(
                        (key, "（廃止: /todoist-setup で再登録してください）")
                    )
                elif key.startswith("SHEET_") or key in (
                    "SPREADSHEET_ID",
                    "LAYER_SPREADSHEET_ID",
                    "PROGRESS_SPREADSHEET_ID",
                    "GOOGLE_CREDENTIALS_PATH",
                ):
                    # 旧 Sheets 連携の設定キーは廃止（移行スクリプトでのみ使用）。
                    # CSV 出力は Web ダッシュボードと /report export-tasks へ移行
                    categories["その他"].append((key, "（廃止: Sheets 連携は撤去されました）"))
                else:
                    categories["その他"].append((key, value))

            # Embed 作成
            embeds = []
            for category, items in categories.items():
                if not items:
                    continue

                description = "\n".join([f"**{key}**: `{value}`" for key, value in items])
                embed = info_embed(f"設定 - {category}", description)
                embeds.append(embed)

            if not embeds:
                embed = info_embed("設定", "保存されている設定はありません")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            for embed in embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            log.exception("設定一覧取得エラー")
            embed = error_embed("設定一覧の取得に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_get", description="指定した設定値を取得します")
    @app_commands.describe(setting_key="設定キー")
    @app_commands.check(is_admin)
    async def settings_get(self, interaction: discord.Interaction, setting_key: str):
        """指定した設定値を取得する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            value = await self.settings_repo.get(guild_id, setting_key)

            if setting_key.startswith("TODOIST_"):
                # レガシーの平文 Todoist 設定は値を表示しない
                embed = info_embed(
                    setting_key,
                    "このキーは廃止されました。`/todoist-setup` / `/todoist-status` "
                    "を使用してください（値は表示されません）",
                )
            elif value is None:
                # 環境変数をチェック
                import os

                env_value = os.getenv(setting_key)
                if env_value:
                    embed = info_embed(setting_key, f"値: `{env_value}`\n（環境変数から取得）")
                else:
                    embed = info_embed(setting_key, "設定されていません")
            else:
                embed = info_embed(setting_key, f"値: `{value}`")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            log.exception("設定取得エラー")
            embed = error_embed(f"設定 `{setting_key}` の取得に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_set", description="設定値を保存します")
    @app_commands.describe(setting_key="設定キー", value="設定値")
    @app_commands.check(is_admin)
    async def settings_set(self, interaction: discord.Interaction, setting_key: str, value: str):
        """設定値を保存する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_key, value)
            embed = success_embed(
                "設定保存完了",
                f"**{setting_key}** = `{value}`\nをこのサーバーの設定として保存しました",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # ギルド別キャッシュとグローバル設定を更新
            await self._after_change(guild_id)

        except Exception:
            log.exception("設定保存エラー")
            embed = error_embed(f"設定 `{setting_key}` の保存に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="settings_delete", description="設定値を削除します")
    @app_commands.describe(setting_key="設定キー")
    @app_commands.check(is_admin)
    async def settings_delete(self, interaction: discord.Interaction, setting_key: str):
        """設定値を削除する"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            deleted = await self.settings_repo.delete(guild_id, setting_key)

            if deleted:
                embed = success_embed("設定削除完了", f"**{setting_key}** を削除しました")
            else:
                embed = info_embed("設定削除", f"**{setting_key}** は存在しませんでした")

            await interaction.followup.send(embed=embed, ephemeral=True)

            # ギルド別キャッシュとグローバル設定を更新
            await self._after_change(guild_id)

        except Exception:
            log.exception("設定削除エラー")
            embed = error_embed(f"設定 `{setting_key}` の削除に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # 便利なショートカットコマンド

    @app_commands.command(name="set_channel", description="チャンネルIDを設定します")
    @app_commands.describe(channel_type="チャンネルタイプ", channel_id="チャンネルID")
    @app_commands.choices(
        channel_type=[
            app_commands.Choice(name="Botログ", value="BOT_LOG_CHANNEL_ID"),
            app_commands.Choice(name="お知らせ", value="DEFAULT_ANNOUNCE_CHANNEL_ID"),
            app_commands.Choice(name="スケジュール", value="DEFAULT_SCHEDULE_CHANNEL_ID"),
            app_commands.Choice(name="進捗", value="DEFAULT_PROGRESS_CHANNEL_ID"),
            app_commands.Choice(name="タスク", value="DEFAULT_TASK_CHANNEL_ID"),
            app_commands.Choice(name="今日やること", value="TODAY_LABEL_CHANNEL_ID"),
        ]
    )
    @app_commands.check(is_admin)
    async def set_channel(
        self, interaction: discord.Interaction, channel_type: str, channel_id: str
    ):
        """チャンネルIDを設定するショートカット"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            # channel_id がメンション形式の場合、IDを抽出
            if channel_id.startswith("<#") and channel_id.endswith(">"):
                channel_id = channel_id[2:-1]

            await self.settings_repo.set(guild_id, channel_type, channel_id)
            embed = success_embed(
                "チャンネル設定完了",
                f"**{channel_type}** = `{channel_id}`\nをこのサーバーの設定として保存しました",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

        except Exception:
            log.exception("チャンネル設定エラー")
            embed = error_embed(f"チャンネル `{channel_type}` の設定に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_role", description="ロールIDを設定します")
    @app_commands.describe(
        role_type="ロールタイプ",
        role_id="ロールID（班長ロールはカンマ区切りで複数指定できます）",
        action="追加するか削除するか（削除できるのは班長ロールのみ）",
    )
    @app_commands.choices(
        role_type=[
            app_commands.Choice(name="実行役", value="EXEC_ROLE_ID"),
            app_commands.Choice(name="管理者", value="ADMIN_ROLE_ID"),
            app_commands.Choice(name="リーダー", value="LEADER_ROLE_IDS"),
        ],
        action=[
            app_commands.Choice(name="追加", value="add"),
            app_commands.Choice(name="削除", value="remove"),
        ],
    )
    @app_commands.check(is_admin)
    async def set_role(
        self,
        interaction: discord.Interaction,
        role_type: str,
        role_id: str,
        action: str = "add",
    ):
        """ロールIDを設定するショートカット。

        班長ロール（LEADER_ROLE_IDS）は複数値なので add / remove を受ける。
        従来は追記専用で、1つ外すには全消しするしかなかった
        （その間、全班長が L1 に降格していた）。
        """
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            tokens, invalid = split_role_tokens(role_id)
            if invalid or not tokens:
                await self._send_error(
                    interaction,
                    "ロールIDは数字で指定してください"
                    + (f"（解釈できませんでした: {'・'.join(invalid)}）" if invalid else "。")
                    + "\nロールを右クリック →「ロールIDをコピー」で取得できます。",
                )
                return

            if role_type in MULTI_ROLE_KEYS:
                await self._set_multi_role(interaction, guild_id, role_type, tokens, action)
                return

            if action == "remove":
                await self._send_error(
                    interaction,
                    "削除に対応しているのは班長ロールだけです。"
                    "ほかの項目は別のロールを指定して上書きするか、"
                    "`/settings_delete` で削除してください。",
                )
                return
            if len(tokens) > 1:
                await self._send_error(interaction, "この項目に指定できるロールは1つだけです。")
                return

            await self.settings_repo.set(guild_id, role_type, tokens[0])
            await self._after_change(guild_id)
            await interaction.followup.send(
                embed=success_embed(
                    "ロール設定完了",
                    f"**{role_type}** = `{tokens[0]}`\nをこのサーバーの設定として保存しました",
                ),
                ephemeral=True,
            )
        except Exception:
            log.exception("ロール設定エラー")
            embed = error_embed(f"ロール `{role_type}` の設定に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def _send_error(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.followup.send(embed=error_embed(message), ephemeral=True)

    async def _set_multi_role(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        key: str,
        tokens: list[str],
        action: str,
    ) -> None:
        """複数値ロール設定（班長ロール）の追加・削除。"""
        before = await self.settings_repo.get(guild_id, key, "") or ""
        merge = merge_role_ids(before, tokens, remove=(action == "remove"))

        if not merge.changed:
            reason = (
                f"{'・'.join(merge.not_found)} は設定されていません。"
                if merge.not_found
                else f"{'・'.join(merge.duplicates)} はすでに設定されています。"
            )
            await interaction.followup.send(
                embed=info_embed(
                    "変更していません",
                    f"{reason}\n現在の値: `{before or '（未設定）'}`",
                ),
                ephemeral=True,
            )
            return

        await self.settings_repo.set(guild_id, key, merge.to_value())
        await self._after_change(guild_id)

        # 保存しても実効設定が変わらないことがある（env / 起動時の値が優先）。
        # 成功と言い切ると G2-7 と同型の嘘になるため、解決結果を見て案内する
        gconf = await config.for_guild(guild_id, db=self.db, force_reload=True)
        resolved = [str(rid) for rid in getattr(gconf, key.lower(), None) or []]
        # 「外した ID が残っているか」だけでは足りない。DB を空にすると
        # config は env / 起動時のグローバル値へフォールバックするため、
        # **保存していない別のロール**が L2 を得ることがある
        warning = stale_role_warning(key, [rid for rid in resolved if rid not in set(merge.values)])

        lines = [
            f"変更前: `{before or '（未設定）'}`",
            f"変更後: `{merge.to_value() or '（未設定）'}`",
        ]
        if merge.added:
            lines.append(f"追加: {'・'.join(merge.added)}")
        if merge.removed:
            lines.append(f"削除: {'・'.join(merge.removed)}")
        if merge.duplicates:
            lines.append(f"すでに設定済みだったため無視: {'・'.join(merge.duplicates)}")
        if merge.not_found:
            lines.append(f"設定されていなかったため無視: {'・'.join(merge.not_found)}")
        if merge.dropped:
            lines.append(f"数字でないため保存対象から外した既存の値: {'・'.join(merge.dropped)}")
        if warning:
            lines.append("")
            lines.append(warning)

        log.info("%s を更新 (guild=%s): %s -> %s", key, guild_id, before, merge.to_value())
        await interaction.followup.send(
            embed=success_embed("班長ロールを更新しました", "\n".join(lines)),
            ephemeral=True,
        )

    @app_commands.command(name="set_common", description="共通設定をします")
    @app_commands.describe(setting_type="設定タイプ", value="設定値")
    @app_commands.choices(
        setting_type=[
            app_commands.Choice(name="タイムゾーン", value="TZ"),
            app_commands.Choice(name="データベースパス", value="DB_PATH"),
        ]
    )
    @app_commands.check(is_admin)
    async def set_common(self, interaction: discord.Interaction, setting_type: str, value: str):
        """共通設定をするショートカット"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        try:
            await self.settings_repo.set(guild_id, setting_type, value)
            embed = success_embed("共通設定完了", f"**{setting_type}** = `{value}`\nを保存しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 設定を更新
            await self._after_change(guild_id)

        except Exception:
            log.exception("共通設定エラー")
            embed = error_embed(f"共通設定 `{setting_type}` に失敗しました")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @set_channel.autocomplete("channel_id")
    async def channel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """チャンネルIDのオートコンプリート"""
        # 現在のギルドのチャンネルを取得
        if interaction.guild is None:
            return []

        channels = interaction.guild.text_channels
        choices = []

        for channel in channels:
            if current.lower() in channel.name.lower() or current in str(channel.id):
                choices.append(
                    app_commands.Choice(
                        name=f"#{channel.name} ({channel.id})", value=str(channel.id)
                    )
                )

        return choices[:25]  # 最大25件

    @set_role.autocomplete("role_id")
    async def role_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """ロールIDのオートコンプリート"""
        if interaction.guild is None:
            return []

        roles = interaction.guild.roles
        choices = []

        for role in roles:
            if current.lower() in role.name.lower() or current in str(role.id):
                choices.append(
                    app_commands.Choice(name=f"{role.name} ({role.id})", value=str(role.id))
                )

        return choices[:25]  # 最大25件


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
