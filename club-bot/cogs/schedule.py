"""
Schedule モジュール（仕様 11.2）。

日程調整・出欠投票。候補日ごとに1メッセージを投稿し状態を投票する。
1候補1ユーザー1状態。状態変更時は旧リアクションを自動除去する。
Bot 再起動後も on_raw_reaction_add/remove で処理可能。

マルチテナント版: 全データを interaction.guild.id（または payload.guild_id）
でスコープする。Embed 生成（services/schedule_service.py）は **guild_id を
明示引数で受け取る**——repo.for_guild() のプロキシは渡さない
（ADR 0009 の完了条件2。G4-12 で実施）。未回答者の母集団を決める
select_unanswered_targets は、ギルドも DB も触らない純関数として
同じモジュールに置いてある。投票メッセージの「未回答者数」も
この関数を通すので、催促の DM が飛ぶ相手と食い違わない（G4-12）。
"""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.settings_repository import SettingsRepository
from services import schedule_service as svc
from services.schedule_service import build_emoji_maps
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    error_embed,
    info_embed,
    schedule_embed,
    success_embed,
)
from utils.logger import get_logger
from utils.notify import dm_each_with_channel_fallback
from utils.parser import (
    InvalidDatetimeError,
    fmt_jp,
    from_iso,
    parse_datetime,
    parse_deadline,
    to_iso,
)
from utils.permissions import Level, ensure_guild, has_level, is_admin, require
from utils.views import ConfirmView

log = get_logger("schedule")

# 出欠ステータスの表示名と、ギルド別設定（settings テーブル）のキー
STATUS_LABELS = {"ok": "参加", "maybe": "未定", "ng": "不参加"}
EMOJI_SETTING_KEYS = {
    "ok": "SCHEDULE_EMOJI_OK_ID",
    "maybe": "SCHEDULE_EMOJI_MAYBE_ID",
    "ng": "SCHEDULE_EMOJI_NG_ID",
}

STATUS_CHOICES = [
    app_commands.Choice(name="参加（既定 ✅）", value="ok"),
    app_commands.Choice(name="未定（既定 ❓）", value="maybe"),
    app_commands.Choice(name="不参加（既定 ❌）", value="ng"),
]


def filter_emoji_choices(emojis, current: str) -> list[app_commands.Choice[str]]:
    """サーバーのカスタム絵文字を名前で部分一致フィルタし Choice を返す。

    Discord の制約（候補は最大25件）に合わせて切り詰める。
    Choice の value には絵文字 ID を持たせ、実行時に
    guild.get_emoji(int(value)) で解決する。
    """
    query = (current or "").strip().strip(":").lower()
    out: list[app_commands.Choice[str]] = []
    for emoji in emojis:
        name = str(getattr(emoji, "name", ""))
        if query and query not in name.lower():
            continue
        out.append(app_commands.Choice(name=f":{name}:"[:100], value=str(emoji.id)))
        if len(out) >= 25:
            break
    return out


def schedule_choices(
    rows: list[dict], current: str, limit: int = 25, prefix: str | None = None
) -> list[tuple[str, str]]:
    """オートコンプリート用の (表示名, schedule_id) 一覧を返す。

    表示名は「イベント名（〜締切）」。ID を手で写させない（G2-2）。
    締切済みの行には [終了] を付け、開催中と見分けられるようにする。
    prefix を渡すとそれを優先する（論理削除は必ず closed_flag が立つので、
    /schedule restore の候補が全部 [終了] になって手がかりが消えるため）。
    current による絞り込みはイベント名・ID の部分一致。
    """
    needle = (current or "").strip().lower()
    out: list[tuple[str, str]] = []
    for row in rows:
        title = str(row.get("title") or row["schedule_id"])
        try:
            deadline = fmt_jp(from_iso(str(row["deadline"])))
        except (ValueError, KeyError):
            deadline = "?"
        mark = prefix if prefix is not None else ("[終了] " if row.get("closed_flag") else "")
        label = f"{mark}{title}（〜{deadline}）"
        if needle and needle not in label.lower() and needle not in row["schedule_id"].lower():
            continue
        out.append((label[:100], row["schedule_id"]))
        if len(out) >= limit:
            break
    return out


def schedule_list_value(row: dict) -> str:
    """一覧 Embed の1件分の本文（締切＋確定日）。

    確定日は `add_field` を増やさず**既存の value へ追記**する
    （field を増やすと MAX_EMBED_FIELDS の打ち切り閾値が実質半分になる）。
    日時は候補の label（利用者の生入力。年なし・時刻なしもありうる）ではなく
    正規化済みの start_at を使い、締切と同じ表記に揃える。
    """
    try:
        lines = [f"締切: {fmt_jp(from_iso(row['deadline']))}"]
    except (TypeError, ValueError):
        lines = ["締切: ?"]
    start_at = row.get("confirmed_start_at")
    if start_at:
        try:
            lines.append(f"**確定: {fmt_jp(from_iso(str(start_at)))}**")
        except (TypeError, ValueError):
            # 1件の壊れた値で一覧全体を落とさない
            log.warning(
                "確定日時を解釈できません (schedule=%s): %r", row.get("schedule_id"), start_at
            )
    return "\n".join(lines)


def resolve_emoji_input(guild: discord.Guild, raw: str) -> discord.Emoji | None:
    """emoji オプションの入力値をサーバーのカスタム絵文字へ解決する。

    オートコンプリート選択時は絵文字 ID（数字）。候補を選ばず名前を
    手入力した場合にも対応する（`:name:` / name）。解決できなければ None。
    """
    value = (raw or "").strip()
    if value.isdigit():
        return guild.get_emoji(int(value))
    name = value.strip(":")
    return discord.utils.get(guild.emojis, name=name)


# =====================================================================
# ボタン投票（ui_style='buttons'）の部品
#
# 全候補を1メッセージ（投票ボード）に集約し、候補は inline field で
# 横に並べる。投票は「候補ボタン → 自分にだけ見えるステータス選択」の
# 2段階。どちらのボタンも discord.ui.DynamicItem（cogs/welcome.py と
# 同じ作法）なので、bot を再起動しても押せる。
#
# custom_id に guild_id は埋めない。候補の解決が
# repo.get_option(interaction.guild_id, option_id) で必ずギルドスコープに
# なるため、他ギルドの custom_id を持ち込んでも「見つかりません」で終わる。
# =====================================================================
class VoteOptionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sched:opt:(?P<option_id>[^:]+)",
):
    """投票ボードの候補ボタン。押すと自分にだけステータス選択が出る。"""

    def __init__(self, option_id: str, label: str = "候補"):
        self.option_id = option_id
        super().__init__(
            discord.ui.Button(
                # ラベルの上限は80文字。切り詰めても custom_id で候補は特定できる
                label=str(label)[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"sched:opt:{option_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        # 再起動後はラベルを復元できないが、表示は投稿済みメッセージ側に
        # 残っているので既定値でよい（処理は option_id だけで進む）
        return cls(match["option_id"])

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Schedule")
        if cog is None:
            log.warning("Schedule コグが読み込まれていません")
            return
        await cog.open_vote_picker(interaction, self.option_id)


class VoteStatusButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"sched:vote:(?P<option_id>[^:]+):(?P<status>ok|maybe|ng|clear)",
):
    """ステータス選択（ephemeral）の 参加/未定/不参加/取り消し ボタン。"""

    STYLES = {
        "ok": discord.ButtonStyle.success,
        "ng": discord.ButtonStyle.danger,
    }

    def __init__(self, option_id: str, status: str, emoji=None):
        self.option_id = option_id
        self.status = status
        label = "回答を取り消す" if status == "clear" else STATUS_LABELS[status]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=self.STYLES.get(status, discord.ButtonStyle.secondary),
                emoji=emoji,
                custom_id=f"sched:vote:{option_id}:{status}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        # 絵文字はギルド設定由来の飾りなので復元しない（既定表示に落ちる）
        return cls(match["option_id"], match["status"])

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Schedule")
        if cog is None:
            log.warning("Schedule コグが読み込まれていません")
            return
        await cog.apply_vote(interaction, self.option_id, self.status)


def build_status_picker_view(gconf, guild: discord.Guild | None, option_id: str) -> discord.ui.View:
    """ステータス選択の View（✅参加 / ❓未定 / ❌不参加 / 取り消し）。

    絵文字はギルド別設定を反映する。View 自体は ephemeral メッセージに
    付くが、中身が DynamicItem なので再起動後に押されても動く。
    """
    emojis = svc.get_schedule_emojis(gconf, guild)
    view = discord.ui.View(timeout=None)
    for status in ("ok", "maybe", "ng"):
        view.add_item(VoteStatusButton(option_id, status, emoji=emojis[status]))
    view.add_item(VoteStatusButton(option_id, "clear"))
    return view


class Schedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = ScheduleRepository(bot.db)

    group = app_commands.Group(name="schedule", description="日程調整・出欠管理")
    emoji_group = app_commands.Group(
        name="emoji", parent=group, description="出欠リアクション絵文字のサーバー別設定（管理者）"
    )

    # ---------- create ----------
    @group.command(name="create", description="新規日程調整を作成します。")
    @app_commands.describe(
        title="イベント名",
        options="候補日時を ; 区切りで指定（例: 2026-07-03; 2026-07-04 19:00）",
        deadline="締切日時（例: 2026-07-02 または 2026-07-02 23:59）",
        description="詳細（任意）",
        place="場所（任意）",
        target_role="対象ロール（任意。未指定なら名簿の現役メンバーが催促の対象になります）",
        channel="投稿先チャンネル（任意）",
    )
    @require(Level.L2)
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        options: str,
        deadline: str,
        description: str | None = None,
        place: str | None = None,
        target_role: discord.Role | None = None,
        channel: discord.TextChannel | None = None,
    ):
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
                ephemeral=True,
            )
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
                        code="INVALID_DATETIME",
                    ),
                    ephemeral=True,
                )
                return
            parsed_options.append((label, start))

        # 投稿先決定（ギルド別設定を参照）
        gconf = await config.for_guild(guild_id)
        target_channel = channel or (
            self.bot.get_channel(gconf.default_schedule_channel_id)
            if gconf.default_schedule_channel_id
            else interaction.channel
        )
        if target_channel is None:
            await interaction.followup.send(
                embed=error_embed("投稿先チャンネルが特定できません。channel を指定してください。"),
                ephemeral=True,
            )
            return

        schedule_id = svc.new_schedule_id()
        ui_style = getattr(gconf, "schedule_ui_style", "buttons")
        await self.repo.create_schedule(
            guild_id,
            schedule_id=schedule_id,
            title=title,
            description=description,
            place=place,
            target_role_id=str(target_role.id) if target_role else None,
            deadline_iso=to_iso(deadline_dt),
            created_by=str(interaction.user.id),
            channel_id=str(target_channel.id),
            ui_style=ui_style,
        )
        for label, start in parsed_options:
            await self.repo.add_option(
                guild_id, svc.new_option_id(), schedule_id, label, to_iso(start), None, None
            )

        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        # 未回答者数は催促と同じ母集団で出す（G4-12）。名簿は予定ごとに
        # 1回だけ引き、候補の数だけ引き直さない
        roster_active, roster_retired = await self._roster_ids(guild_id)
        # 投稿は list_options の並び（start_at 昇順）で行う。入力順ではなく
        # 日付順に揃うので、ボードの field も従来の候補メッセージも
        # 時系列で読める
        options = await self.repo.list_options(guild_id, schedule_id)

        # 対象ロールへは先頭の1通だけでメンションする（候補の数だけ
        # 鳴らさない）。従来はメンションが無く、対象者は投票の開始に
        # 気付けなかった（G2-3）
        mention = (
            f"{target_role.mention} 日程調整「{title}」の投票が始まりました。"
            if target_role
            else None
        )

        if ui_style == "buttons":
            # 全候補を1メッセージ（投票ボード）に集約し、候補ボタンで投票する。
            # 候補が 25 件を超える分はページ分割（ボタンの上限が25）
            await self._post_vote_boards(
                guild_id,
                schedule,
                options,
                target_channel,
                interaction.guild,
                mention,
                roster_active,
                roster_retired,
                emojis=svc.get_schedule_emojis(gconf, interaction.guild),
            )
        else:
            # 候補ごとに1メッセージ投稿（仕様 11.2.3 の従来方式）
            # リアクション絵文字はギルド別設定（/schedule emoji set）を参照
            emoji_maps = build_emoji_maps(gconf, interaction.guild)
            all_emojis = emoji_maps["all_emojis"]

            for index, opt in enumerate(options):
                embed = await svc.build_option_embed(
                    self.repo,
                    guild_id,
                    self.bot,
                    schedule,
                    opt,
                    interaction.guild,
                    roster_active_ids=roster_active,
                    roster_retired_ids=roster_retired,
                )
                msg = await target_channel.send(
                    content=mention if index == 0 else None, embed=embed
                )
                await self.repo.set_option_message(guild_id, str(opt["option_id"]), str(msg.id))
                for emoji in all_emojis:
                    await msg.add_reaction(emoji)

        await interaction.followup.send(
            embed=success_embed(
                "日程調整を作成しました",
                f"ID: `{schedule_id}`\n候補数: {len(parsed_options)}\n"
                f"締切: {fmt_jp(deadline_dt)}\n投稿先: {target_channel.mention}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    async def _find_schedule(
        self, interaction: discord.Interaction, guild_id: int, schedule_id: str
    ) -> dict | None:
        """予定を引き、見つからなければ理由つきで返信して None を返す。

        削除済みはオートコンプリートに出ないので、ID を直に打った人だけが
        ここへ来る。「見つかりません」で終わらせず、戻し方を案内する。
        """
        schedule = await self.repo.get_schedule(guild_id, schedule_id)
        if schedule:
            return schedule
        deleted = await self.repo.get_schedule(guild_id, schedule_id, include_deleted=True)
        if deleted:
            # /schedule restore は L3。ここへ来るのは L1〜L3 の5コマンドなので、
            # 実行できない人に「これを打て」と案内しない
            gconf = await config.for_guild(guild_id)
            can_restore = has_level(interaction.user, gconf, Level.L3)
            situation = (
                f"**{deleted['title']}**（ID: `{schedule_id}`）は削除済みです。"
                "票データは残っています。"
            )
            if not can_restore:
                situation += "戻すには幹部に `/schedule restore` を依頼してください。"
            await interaction.followup.send(
                embed=empty_state_embed(
                    "この日程調整は削除されています",
                    situation,
                    "/schedule restore" if can_restore else "/schedule list-closed",
                ),
                ephemeral=True,
            )
            return None
        await interaction.followup.send(
            embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True
        )
        return None

    # ---------- schedule_id のオートコンプリート ----------
    async def _schedule_ac_open(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """開催中の投票のみ（close / remind / edit-deadline 用）。

        締切済みに close は意味がなく、remind は嘘の通知になるため出さない。
        """
        if interaction.guild is None:
            return []
        try:
            rows = await self.repo.list_open_schedules(interaction.guild.id)
        except Exception:  # noqa: BLE001  (補完は失敗しても致命的でない)
            return []
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in schedule_choices(rows, current)
        ]

    async def _option_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """確定する候補の一覧（先に選ばれた schedule_id に属するものだけ）。"""
        if interaction.guild is None:
            return []
        schedule_id = getattr(interaction.namespace, "schedule_id", None)
        if not schedule_id:
            return []
        try:
            options = await self.repo.list_options(interaction.guild.id, str(schedule_id))
        except Exception:  # noqa: BLE001  (補完は失敗しても致命的でない)
            return []
        needle = (current or "").strip().lower()
        out: list[app_commands.Choice[str]] = []
        for opt in options:
            try:
                label = fmt_jp(from_iso(str(opt["start_at"])))
            except (TypeError, ValueError, KeyError):
                label = str(opt.get("label") or opt["option_id"])
            if needle and needle not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=str(opt["option_id"])))
            if len(out) >= 25:
                break
        return out

    async def _schedule_ac_deleted(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """削除済みの投票のみ（restore 用）。"""
        if interaction.guild is None:
            return []
        try:
            rows = await self.repo.list_deleted_schedules(interaction.guild.id)
        except Exception:  # noqa: BLE001
            return []
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in schedule_choices(rows, current, prefix="[削除済み] ")
        ]

    async def _schedule_ac_all(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """締切済みも含む全投票（status / delete 用）。削除済みは含まない。"""
        if interaction.guild is None:
            return []
        try:
            rows = await self.repo.list_all(interaction.guild.id)
        except Exception:  # noqa: BLE001
            return []
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in schedule_choices(rows, current)
        ]

    # ==================================================================
    # /schedule emoji — 出欠リアクション絵文字のサーバー別設定
    # ==================================================================
    async def _emoji_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        return filter_emoji_choices(interaction.guild.emojis, current)

    @emoji_group.command(
        name="set", description="出欠リアクションにサーバーのカスタム絵文字を設定します（管理者）。"
    )
    @app_commands.describe(
        status="どの出欠ステータスの絵文字を変更するか",
        emoji="カスタム絵文字（名前の一部を入力して候補から選択）",
    )
    @app_commands.choices(status=STATUS_CHOICES)
    @app_commands.autocomplete(emoji=_emoji_ac)
    @app_commands.check(is_admin)
    async def emoji_set(self, interaction: discord.Interaction, status: str, emoji: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        resolved = resolve_emoji_input(interaction.guild, emoji)
        if resolved is None:
            await interaction.followup.send(
                embed=error_embed(
                    "このサーバーのカスタム絵文字が見つかりません。\n"
                    "絵文字名の一部を入力し、候補から選択してください。"
                ),
                ephemeral=True,
            )
            return

        await SettingsRepository(self.bot.db).set(
            guild_id, EMOJI_SETTING_KEYS[status], str(resolved.id)
        )
        config.invalidate_guild(guild_id)
        await interaction.followup.send(
            embed=success_embed(
                "リアクション絵文字を設定しました",
                f"{STATUS_LABELS[status]}: {resolved}\n"
                "この後に作成する日程調整から適用されます"
                "（投稿済みの投票メッセージは変わりません）。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @emoji_group.command(
        name="show", description="現在の出欠リアクション絵文字の設定を表示します（管理者）。"
    )
    @app_commands.check(is_admin)
    async def emoji_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        gconf = await config.for_guild(guild_id)
        emojis = svc.get_schedule_emojis(gconf, interaction.guild)
        configured = {
            "ok": gconf.schedule_emoji_ok_id,
            "maybe": gconf.schedule_emoji_maybe_id,
            "ng": gconf.schedule_emoji_ng_id,
        }
        lines = []
        for status in ("ok", "maybe", "ng"):
            emoji = emojis[status]
            if getattr(emoji, "id", None):
                note = "カスタム"
            elif configured[status]:
                note = "既定（設定済みの絵文字が見つからないためフォールバック中）"
            else:
                note = "既定"
            lines.append(f"{STATUS_LABELS[status]}: {emoji} — {note}")
        await interaction.followup.send(
            embed=info_embed(
                "出欠リアクション絵文字の設定",
                "\n".join(lines) + "\n\n変更: `/schedule emoji set` / 既定に戻す: "
                "`/schedule emoji reset`",
            ),
            ephemeral=True,
        )

    @emoji_group.command(
        name="reset", description="出欠リアクション絵文字を既定（✅❓❌）に戻します（管理者）。"
    )
    @app_commands.describe(status="対象ステータス（省略時は3つすべて）")
    @app_commands.choices(status=STATUS_CHOICES)
    @app_commands.check(is_admin)
    async def emoji_reset(self, interaction: discord.Interaction, status: str | None = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        targets = [status] if status else list(EMOJI_SETTING_KEYS)
        repo = SettingsRepository(self.bot.db)
        for s in targets:
            await repo.delete(guild_id, EMOJI_SETTING_KEYS[s])
        config.invalidate_guild(guild_id)
        await interaction.followup.send(
            embed=success_embed(
                "リアクション絵文字をリセットしました",
                "対象: "
                + "、".join(STATUS_LABELS[s] for s in targets)
                + "\nこの後に作成する日程調整から既定絵文字が使われます。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

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
                embed=empty_state_embed(
                    "開催中の日程調整", "現在、開催中の投票はありません。", "/schedule create"
                ),
                ephemeral=True,
            )
            return
        embed = schedule_embed("開催中の日程調整一覧")
        for s in schedules[:MAX_EMBED_FIELDS]:
            embed.add_field(
                name=f"{s['title']}（`{s['schedule_id']}`）",
                value=schedule_list_value(s),
                inline=False,
            )
        add_truncation_note(embed, len(schedules), MAX_EMBED_FIELDS, "締切が近い順に表示しています")
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
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return
        roster_active, roster_retired = await self._roster_ids(guild_id)
        options = await self.repo.list_options(guild_id, schedule_id)
        if schedule.get("ui_style") == "buttons":
            # ボタン式は候補を横並びにしたボードと同じ形で返す（1〜2通）。
            # 候補 0 件でも無言にしない（締切などの情報は出す）
            chunks = [
                options[i : i + svc.MAX_BOARD_OPTIONS]
                for i in range(0, len(options), svc.MAX_BOARD_OPTIONS)
            ] or [[]]
            gconf = await config.for_guild(guild_id)
            emojis = svc.get_schedule_emojis(gconf, interaction.guild)
            for page, chunk in enumerate(chunks, start=1):
                embed = await svc.build_vote_board_embed(
                    self.repo,
                    guild_id,
                    self.bot,
                    schedule,
                    chunk,
                    interaction.guild,
                    roster_active_ids=roster_active,
                    roster_retired_ids=roster_retired,
                    page=page,
                    total_pages=len(chunks),
                    emojis=emojis,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            return
        for opt in options:
            embed = await svc.build_option_embed(
                self.repo,
                guild_id,
                self.bot,
                schedule,
                opt,
                interaction.guild,
                roster_active_ids=roster_active,
                roster_retired_ids=roster_retired,
            )
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
                ephemeral=True,
            )
            return
        embed = schedule_embed("締切済みの日程調整一覧")
        for s in schedules[:MAX_EMBED_FIELDS]:
            embed.add_field(
                name=f"{s['title']}（`{s['schedule_id']}`）",
                value=schedule_list_value(s),
                inline=False,
            )
        add_truncation_note(embed, len(schedules), MAX_EMBED_FIELDS, "新しい順に表示しています")
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
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return
        await self.finalize_schedule(schedule)
        await interaction.followup.send(
            embed=success_embed(
                "締め切りました", f"ID: `{schedule_id}`", executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    # ---------- remind ----------
    @group.command(name="remind", description="未回答者へ再通知します。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L2)
    async def remind(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return
        if schedule["closed_flag"]:
            # 締切済みへの催促は「もう答えられない投票」への DM になる。
            # オートコンプリートは開催中しか出さないので踏みにくいが、
            # L2 が ID を直打ちすれば通っていた（G4-14）。
            # 文言は edit-deadline と揃える
            await interaction.followup.send(
                embed=error_embed(
                    "この投票は既に締切済みです。締切済みの投票には再通知できません。"
                ),
                ephemeral=True,
            )
            return
        count = await self.notify_unanswered(schedule)
        if count is None:
            # 従来はここで「対象: 0 名」の緑 Embed が出ていた（嘘の成功）
            await interaction.followup.send(
                embed=error_embed(
                    "未回答者を特定できません。\n"
                    "`/schedule create` の `target_role` で対象ロールを指定するか、"
                    "`/member register` で名簿を登録してください。\n"
                    "対象ロールに誰も付いていない場合もこのエラーになります。"
                    "ロールの付与状況も確認してください。"
                ),
                ephemeral=True,
            )
            return
        if count == 0:
            # 1通も送っていないので「再通知しました」とは言わない
            # （G2-3 が潰した嘘の成功と同じ形になる）
            await interaction.followup.send(
                embed=info_embed(
                    "未回答者は居ませんでした",
                    "対象者は全員回答済みです。DM は送っていません。",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "未回答者へ再通知しました",
                f"対象: {count} 名",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- delete ----------
    @group.command(
        name="delete",
        description="日程調整を削除します（投票メッセージも削除。票データは残ります）。",
    )
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L3)
    async def delete(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return

        # 何が起きるかを**やる前に**見せる。票は残るが、Discord 上の
        # 投票メッセージは戻せない
        options = await self.repo.list_options(guild_id, schedule_id)
        voters = await self.repo.list_voters_for_schedule(guild_id, schedule_id)
        body = (
            f"**{schedule['title']}**（ID: `{schedule_id}`）を削除します。\n"
            f"候補: **{len(options)}** 件 / 回答した人: **{len(voters)}** 名\n\n"
            "投票メッセージを削除し、**この投票は締め切られます**。\n"
            "票データは残るので `/schedule restore` で戻せます"
            "（**投票メッセージは戻りません**）。"
        )

        async def _do_delete(confirm_interaction: discord.Interaction) -> None:
            # Discord上の候補メッセージを削除
            channel = self.bot.get_channel(int(schedule["channel_id"]))
            deleted_msgs = 0
            failed_msgs = 0
            for opt in options:
                if not opt.get("message_id"):
                    continue
                if not channel:
                    failed_msgs += 1
                    continue
                try:
                    msg = await channel.fetch_message(int(opt["message_id"]))
                    await msg.delete()
                    deleted_msgs += 1
                except discord.NotFound:
                    # 既に消えている。残らないので失敗として数えない
                    pass
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.warning(
                        "投票メッセージの削除に失敗 (guild=%s, schedule=%s): %s",
                        guild_id,
                        schedule_id,
                        e,
                    )
                    failed_msgs += 1

            # DB は論理削除（票データは残す）
            await self.repo.soft_delete_schedule(guild_id, schedule_id)

            detail = (
                f"ID: `{schedule_id}`\n投票メッセージ削除: {deleted_msgs} 件\n"
                f"票データ（{len(voters)} 名分）は残しました。"
                "`/schedule restore` で戻せます。"
            )
            if failed_msgs:
                # 残ったメッセージのリアクションは無反応になる。黙らない
                detail += (
                    f"\n\n⚠️ **{failed_msgs} 件のメッセージを削除できませんでした**"
                    "（権限不足・チャンネル不明など）。チャンネルに残ったままなので、"
                    "手で削除してください。押しても反応しません。"
                )
            await confirm_interaction.followup.send(
                embed=success_embed(
                    "削除しました", detail, executor=confirm_interaction.user.display_name
                ),
                ephemeral=True,
            )

        view = ConfirmView(
            interaction.user.id,
            info_embed("日程調整の削除を確認してください", body),
            _do_delete,
            cancel_message="日程調整は削除していません。",
        )
        view.message = await interaction.followup.send(
            embed=view.preview_embed, view=view, ephemeral=True
        )

    # ---------- confirm ----------
    @group.command(name="confirm", description="投票の結果として確定した日程を登録します。")
    @app_commands.describe(schedule_id="投票 ID", option_id="確定した候補")
    @require(Level.L2)
    async def confirm(self, interaction: discord.Interaction, schedule_id: str, option_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return

        # 対象外の候補は SQL 側で弾かれる（set_confirmed_option の EXISTS）
        if not await self.repo.set_confirmed_option(guild_id, schedule_id, option_id):
            await interaction.followup.send(
                embed=error_embed(
                    "その候補はこの投票のものではありません。\n"
                    "候補は `option_id` のリストから選んでください。"
                ),
                ephemeral=True,
            )
            return

        updated = await self.repo.get_schedule(guild_id, schedule_id)
        option = next(
            (
                o
                for o in await self.repo.list_options(guild_id, schedule_id)
                if str(o["option_id"]) == option_id
            ),
            None,
        )
        when = self._fmt_option(option)
        announced = await self._announce_confirmation(
            guild_id,
            updated or schedule,
            f"【日程が決まりました】{schedule['title']}",
            f"日時: **{when}**" + (f"\n場所: {schedule['place']}" if schedule.get("place") else ""),
        )

        detail = f"**{schedule['title']}**（ID: `{schedule_id}`）\n確定: {when}"
        if not announced:
            detail += (
                "\n\n⚠️ **告知は送れませんでした。** 投稿チャンネルが見つからないか、"
                "Bot に送信権限がありません。"
            )
        if not schedule.get("closed_flag"):
            # 先に決まることはある。勝手に締め切らない（明示操作でだけ変える）
            detail += (
                "\n\nこの予定はまだ投票受付中です。"
                "締め切るには `/schedule close` を実行してください。"
            )
        await interaction.followup.send(
            embed=success_embed(
                "確定日程を登録しました", detail, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    @group.command(name="unconfirm", description="登録した確定日程を取り消します。")
    @app_commands.describe(schedule_id="投票 ID")
    @require(Level.L2)
    async def unconfirm(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return
        if not await self.repo.clear_confirmed_option(guild_id, schedule_id):
            await interaction.followup.send(
                embed=info_embed(
                    "確定していません",
                    f"**{schedule['title']}**（ID: `{schedule_id}`）に確定日程は登録されていません。",
                ),
                ephemeral=True,
            )
            return

        # 既に日付を告知しているので、黙って消すと部員側に誤情報が残る
        announced = await self._announce_confirmation(
            guild_id,
            schedule,
            f"【日程の確定を取り消しました】{schedule['title']}",
            "日程は未定に戻りました。決まり次第あらためてお知らせします。",
        )
        detail = f"**{schedule['title']}**（ID: `{schedule_id}`）"
        if not announced:
            detail += (
                "\n\n⚠️ **取り消しの告知は送れませんでした。** 部員には確定日程が"
                "告知されたままなので、チャンネルで直接お知らせしてください。"
            )
        await interaction.followup.send(
            embed=success_embed(
                "確定日程を取り消しました", detail, executor=interaction.user.display_name
            ),
            ephemeral=True,
        )

    @staticmethod
    def _fmt_option(option: dict | None) -> str:
        """候補の表示（正規化済みの start_at を優先）。"""
        if not option:
            return "?"
        try:
            return fmt_jp(from_iso(str(option["start_at"])))
        except (TypeError, ValueError, KeyError):
            return str(option.get("label") or "?")

    async def _announce_confirmation(
        self, guild_id: int, schedule: dict, heading: str, body: str
    ) -> bool:
        """投稿チャンネルへ告知する（同一ギルド内に限定して解決する）。

        **本文は description に渡す。** Embed の title は 100 文字で切られる
        （`utils/embeds._base`）ため、title に本文を入れるとイベント名が長い
        ギルドで日時や場所が黙って消える。

        戻り値は送れたかどうか。呼び出し側は失敗を実行者に伝える
        （成功と表示したまま部員に何も届かない状態を作らない）。
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False
        try:
            # スレッド内で /schedule create した予定は channel_id が
            # スレッド ID になる。get_channel はスレッドを解決しない
            channel = guild.get_channel_or_thread(int(schedule["channel_id"]))
        except (TypeError, ValueError):
            channel = None
        if channel is None or not hasattr(channel, "send"):
            log.info("確定の告知先が見つかりません (guild=%s)", guild_id)
            return False
        content = None
        if schedule.get("target_role_id"):
            try:
                role = guild.get_role(int(schedule["target_role_id"]))
            except (TypeError, ValueError):
                role = None
            if role:
                content = role.mention
        try:
            await channel.send(content=content, embed=schedule_embed(heading, body))
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("確定の告知に失敗 (guild=%s): %s", guild_id, e)
            return False
        return True

    # ---------- restore ----------
    @group.command(
        name="restore", description="削除した日程調整を戻します（票データも一緒に戻ります）。"
    )
    @app_commands.describe(schedule_id="削除した投票 ID")
    @require(Level.L3)
    async def restore(self, interaction: discord.Interaction, schedule_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self.repo.get_schedule(guild_id, schedule_id, include_deleted=True)
        if not schedule:
            await interaction.followup.send(
                embed=error_embed("指定 ID の投票が見つかりません。"), ephemeral=True
            )
            return
        if not await self.repo.restore_schedule(guild_id, schedule_id):
            await interaction.followup.send(
                embed=info_embed(
                    "削除されていません",
                    f"**{schedule['title']}**（ID: `{schedule_id}`）は削除されていません。",
                ),
                ephemeral=True,
            )
            return

        voters = await self.repo.list_voters_for_schedule(guild_id, schedule_id)
        await interaction.followup.send(
            embed=success_embed(
                "日程調整を戻しました",
                f"**{schedule['title']}**（ID: `{schedule_id}`）\n"
                f"回答した人: **{len(voters)}** 名\n\n"
                "**締切済みとして戻ります。投票は再開しません**"
                "（投票メッセージは戻らないため、**自動の**催促も送りません）。\n"
                "結果は `/schedule status` で読めます。"
                "再投票が必要なら `/schedule create` で作り直してください。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @group.command(name="edit-deadline", description="開催中の日程調整の締切を変更します。")
    @app_commands.describe(
        schedule_id="投票 ID", deadline="新しい締切（例: 2026-07-20 または 2026-07-20 23:59）"
    )
    @require(Level.L2)
    async def edit_deadline(
        self, interaction: discord.Interaction, schedule_id: str, deadline: str
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        schedule = await self._find_schedule(interaction, guild_id, schedule_id)
        if schedule is None:
            return
        if schedule["closed_flag"]:
            await interaction.followup.send(
                embed=error_embed("この投票は既に締切済みです。締切済みの投票は変更できません。"),
                ephemeral=True,
            )
            return

        try:
            new_deadline_dt = parse_deadline(deadline)
        except InvalidDatetimeError:
            await interaction.followup.send(
                embed=error_embed(
                    f"締切「{deadline}」の形式が不正です。"
                    f"`YYYY-MM-DD` または `YYYY-MM-DD HH:MM` 形式で指定してください。",
                    code="INVALID_DATETIME",
                ),
                ephemeral=True,
            )
            return

        old_deadline_str = fmt_jp(from_iso(schedule["deadline"]))
        await self.repo.update_deadline(guild_id, schedule_id, to_iso(new_deadline_dt))

        # 更新後のスケジュール情報でEmbedを再取得
        updated_schedule = await self.repo.get_schedule(guild_id, schedule_id)

        # 各投票メッセージの締切表示を更新
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        updated_msgs = 0
        if channel:
            if (updated_schedule or schedule).get("ui_style") == "buttons":
                # ボタン式は候補ではなくボード単位で描き直す
                updated_msgs = await self._refresh_all_vote_boards(
                    guild_id, updated_schedule or schedule
                )
            else:
                roster_active, roster_retired = await self._roster_ids(guild_id)
                options = await self.repo.list_options(guild_id, schedule_id)
                for opt in options:
                    if not opt.get("message_id"):
                        continue
                    try:
                        msg = await channel.fetch_message(int(opt["message_id"]))
                        embed = await svc.build_option_embed(
                            self.repo,
                            guild_id,
                            self.bot,
                            updated_schedule,
                            opt,
                            interaction.guild,
                            roster_active_ids=roster_active,
                            roster_retired_ids=roster_retired,
                        )
                        await msg.edit(embed=embed)
                        updated_msgs += 1
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

        # 変更をチャンネルに通知
        if channel:
            try:
                await channel.send(
                    f"【日程調整】「{schedule['title']}」の締切が変更されました。\n"
                    f"変更前: {old_deadline_str}\n変更後: {fmt_jp(new_deadline_dt)}"
                )
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            embed=success_embed(
                "締切を変更しました",
                f"ID: `{schedule_id}`\n変更前: {old_deadline_str}\n"
                f"変更後: {fmt_jp(new_deadline_dt)}\n更新メッセージ: {updated_msgs} 件",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

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
        gconf = await config.for_guild(guild_id)
        emoji_maps = build_emoji_maps(gconf, guild)
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
        if schedule.get("ui_style") == "buttons":
            # ボタン式の投票ボードに付いたリアクションは投票ではない。
            # message_id がボードを指すため、放っておくと誰かの落書き
            # リアクションが「ボードの先頭候補への投票」に化ける
            return

        user_id = str(payload.user_id)
        status = emoji_to_status[emoji_key]

        if added:
            await self.repo.set_vote(guild_id, option["option_id"], user_id, status)
            await self._remove_other_reactions(
                payload, keep_status=status, status_to_emoji=status_to_emoji
            )
        else:
            votes = await self.repo.list_votes(guild_id, option["option_id"])
            current = next((v for v in votes if v["user_id"] == user_id), None)
            if current and current["status"] == status:
                await self.repo.remove_vote(guild_id, option["option_id"], user_id)

        await self._refresh_option_message(payload, schedule, option)

    async def _remove_other_reactions(
        self,
        payload: discord.RawReactionActionEvent,
        keep_status: str,
        status_to_emoji: dict[str, str | discord.Emoji],
    ):
        channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(
            payload.channel_id
        )
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        member = payload.member or channel.guild.get_member(payload.user_id)
        if member is None:
            return

        keep_key = svc.emoji_key(status_to_emoji[keep_status])
        schedule_keys = {svc.emoji_key(e) for e in status_to_emoji.values()}

        for reaction in message.reactions:
            reaction_key = (
                str(reaction.emoji.id)
                if hasattr(reaction.emoji, "id") and reaction.emoji.id
                else str(reaction.emoji)
            )
            if reaction_key in schedule_keys and reaction_key != keep_key:
                try:
                    await message.remove_reaction(reaction.emoji, member)
                except (discord.Forbidden, discord.NotFound):
                    pass

    async def _refresh_option_message(self, payload, schedule, option):
        channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(
            payload.channel_id
        )
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        guild = getattr(channel, "guild", None)
        guild_id = schedule.get("guild_id") or (guild.id if guild else 0)
        roster_active, roster_retired = await self._roster_ids(guild_id)
        embed = await svc.build_option_embed(
            self.repo,
            guild_id,
            self.bot,
            schedule,
            option,
            guild,
            roster_active_ids=roster_active,
            roster_retired_ids=roster_retired,
        )
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    # ====================================================================
    # ボタン投票（ui_style='buttons'）
    # ====================================================================
    async def _post_vote_boards(
        self,
        guild_id: int,
        schedule: dict,
        options: list[dict],
        channel,
        guild: discord.Guild | None,
        mention: str | None,
        roster_active: set[str],
        roster_retired: set[str],
        emojis: dict | None = None,
    ) -> int:
        """投票ボードを投稿する（候補 25 件ごとに1メッセージ）。

        メンションは先頭の1通だけに付ける（リアクション式と同じ判断）。
        各候補の message_id には**載っているボードの ID** を入れる。
        削除・締切変更のフローが従来どおり message_id を辿れるようにするため
        （同じ ID が候補の数だけ並ぶが、重複は各フローが除外する）。
        """
        chunks = [
            options[i : i + svc.MAX_BOARD_OPTIONS]
            for i in range(0, len(options), svc.MAX_BOARD_OPTIONS)
        ]
        for page, chunk in enumerate(chunks, start=1):
            embed = await svc.build_vote_board_embed(
                self.repo,
                guild_id,
                self.bot,
                schedule,
                chunk,
                guild,
                roster_active_ids=roster_active,
                roster_retired_ids=roster_retired,
                page=page,
                total_pages=len(chunks),
                emojis=emojis,
            )
            view = discord.ui.View(timeout=None)
            for opt in chunk:
                view.add_item(VoteOptionButton(str(opt["option_id"]), str(opt["label"])))
            msg = await channel.send(
                content=mention if page == 1 else None, embed=embed, view=view
            )
            for opt in chunk:
                await self.repo.set_option_message(guild_id, str(opt["option_id"]), str(msg.id))
        return len(chunks)

    async def _resolve_vote_target(
        self, interaction: discord.Interaction, option_id: str
    ) -> tuple[dict, dict] | None:
        """ボタンの option_id を (候補, 予定) へ解決する。ダメなら理由を返信。

        検索は interaction.guild_id でスコープされるので、他ギルドの
        custom_id を持ち込んでも「見つかりません」で終わる（越境しない）。
        """
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=error_embed("この操作はサーバー内でのみ行えます。"), ephemeral=True
            )
            return None
        option = await self.repo.get_option(interaction.guild_id, option_id)
        schedule = None
        if option:
            schedule = await self.repo.get_schedule(interaction.guild_id, option["schedule_id"])
        if option is None or schedule is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "この候補は見つかりません。投票が削除された可能性があります。"
                ),
                ephemeral=True,
            )
            return None
        if schedule["closed_flag"]:
            await interaction.response.send_message(
                embed=error_embed("この投票は締切済みです。"), ephemeral=True
            )
            return None
        return option, schedule

    async def open_vote_picker(self, interaction: discord.Interaction, option_id: str) -> None:
        """候補ボタン → 自分にだけ見えるステータス選択を出す。

        Embed は候補1件の詳細（build_option_embed）。ボードでは名前を
        打ち切っているので、全員分の顔ぶれはここで見える。
        """
        resolved = await self._resolve_vote_target(interaction, option_id)
        if resolved is None:
            return
        option, schedule = resolved
        guild_id = interaction.guild_id

        roster_active, roster_retired = await self._roster_ids(guild_id)
        embed = await svc.build_option_embed(
            self.repo,
            guild_id,
            self.bot,
            schedule,
            option,
            interaction.guild,
            roster_active_ids=roster_active,
            roster_retired_ids=roster_retired,
        )
        votes = await self.repo.list_votes(guild_id, option_id)
        mine = next((v for v in votes if v["user_id"] == str(interaction.user.id)), None)
        note = (
            f"あなたの現在の回答: **{STATUS_LABELS[mine['status']]}**"
            if mine
            else "あなたはこの候補にまだ回答していません。"
        )
        gconf = await config.for_guild(guild_id)
        view = build_status_picker_view(gconf, interaction.guild, option_id)
        await interaction.response.send_message(
            content=note, embed=embed, view=view, ephemeral=True
        )

    async def apply_vote(
        self, interaction: discord.Interaction, option_id: str, status: str
    ) -> None:
        """ステータスボタン → 票を書き、ボードとステータス選択を描き直す。"""
        resolved = await self._resolve_vote_target(interaction, option_id)
        if resolved is None:
            return
        option, schedule = resolved
        guild_id = interaction.guild_id
        user_id = str(interaction.user.id)

        if status == "clear":
            await self.repo.remove_vote(guild_id, option_id, user_id)
            note = f"「{option['label']}」の回答を取り消しました。"
        else:
            await self.repo.set_vote(guild_id, option_id, user_id, status)
            note = f"「{option['label']}」に **{STATUS_LABELS[status]}** で回答しました。"

        # 自分への応答を先に返す（ボードの更新失敗で回答まで失敗に
        # 見せない。票は既に書けている）
        roster_active, roster_retired = await self._roster_ids(guild_id)
        embed = await svc.build_option_embed(
            self.repo,
            guild_id,
            self.bot,
            schedule,
            option,
            interaction.guild,
            roster_active_ids=roster_active,
            roster_retired_ids=roster_retired,
        )
        try:
            await interaction.response.edit_message(content=note, embed=embed)
        except discord.HTTPException as e:
            log.warning("ステータス選択の更新に失敗 (guild=%s): %s", guild_id, e)

        await self._refresh_vote_board(
            guild_id, schedule, option, roster_active=roster_active, roster_retired=roster_retired
        )

    async def _refresh_vote_board(
        self,
        guild_id: int,
        schedule: dict,
        option: dict,
        *,
        roster_active: set[str] | None = None,
        roster_retired: set[str] | None = None,
    ) -> bool:
        """候補が載っている投票ボードを最新の集計で描き直す。

        ページ構成（どの候補がどのメッセージに載っているか）は DB の
        message_id から復元する。戻り値は編集できたかどうか。
        """
        message_id = option.get("message_id")
        if not message_id:
            return False
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        if channel is None:
            return False

        options = await self.repo.list_options(guild_id, schedule["schedule_id"])
        board_ids: list[str] = []
        for o in options:
            mid = str(o.get("message_id") or "")
            if mid and mid not in board_ids:
                board_ids.append(mid)
        page_options = [o for o in options if str(o.get("message_id") or "") == str(message_id)]
        if not page_options:
            return False
        page = board_ids.index(str(message_id)) + 1 if str(message_id) in board_ids else 1

        if roster_active is None or roster_retired is None:
            roster_active, roster_retired = await self._roster_ids(guild_id)
        gconf = await config.for_guild(guild_id)
        embed = await svc.build_vote_board_embed(
            self.repo,
            guild_id,
            self.bot,
            schedule,
            page_options,
            getattr(channel, "guild", None),
            roster_active_ids=roster_active,
            roster_retired_ids=roster_retired,
            page=page,
            total_pages=max(len(board_ids), 1),
            emojis=svc.get_schedule_emojis(gconf, getattr(channel, "guild", None)),
        )
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning(
                "投票ボードの更新に失敗 (guild=%s, schedule=%s): %s",
                guild_id,
                schedule.get("schedule_id"),
                e,
            )
            return False
        return True

    async def _refresh_all_vote_boards(self, guild_id: int, schedule: dict) -> int:
        """予定の全投票ボードを描き直す（締切変更などの一括更新用）。"""
        options = await self.repo.list_options(guild_id, schedule["schedule_id"])
        seen: set[str] = set()
        updated = 0
        roster_active, roster_retired = await self._roster_ids(guild_id)
        for opt in options:
            mid = str(opt.get("message_id") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            if await self._refresh_vote_board(
                guild_id,
                schedule,
                opt,
                roster_active=roster_active,
                roster_retired=roster_retired,
            ):
                updated += 1
        return updated

    # ====================================================================
    # 締切・通知ヘルパー（Reminders から呼ばれる）
    # ====================================================================
    async def _roster_ids(self, guild_id: int) -> tuple[set[str], set[str]]:
        """名簿の (現役, 退部・休止と分かっている人) の ID 集合を返す。

        既存クエリ2回の差で作る（新しいクエリを足さない）。現役の条件は
        ダッシュボード側（ADR 0025）と同じ active_flag=1 かつ status='active'。
        """
        repo = MemberRepository(self.bot.db)
        active = {str(m["user_id"]) for m in await repo.list_members(guild_id)}
        everyone = {
            str(m["user_id"])
            for m in await repo.list_members(guild_id, active_only=False, include_alumni=True)
        }
        return active, everyone - active

    @staticmethod
    def _member_of(guild: discord.Guild, user_id: str):
        """名簿の user_id（TEXT 列）を Member へ解決する。数字でなければ None。"""
        try:
            return guild.get_member(int(user_id))
        except (TypeError, ValueError):
            log.warning("数字でない user_id を名簿で見つけました (guild=%s): %r", guild.id, user_id)
            return None

    async def notify_unanswered(self, schedule: dict) -> int | None:
        """未回答者へ DM 通知。DM 不可ならチャンネルでメンション（仕様 11.2.5）。

        **None は「対象を特定できない」**（ギルド不可視・対象ロール削除済み・
        対象ロールの保持者が1人も見えない・対象ロールが無く名簿も空・
        候補は居るが1人も解決できない）。
        0 は「対象は特定でき、未回答が0名」。従来はどちらも 0 を返していたため、
        呼び出し側が緑の成功 Embed で「対象: 0 名」と表示していた。

        母集団は select_unanswered_targets が決める（G3-2 / ADR 0025 の更新）。
        対象ロールがあるときはロール基準から名簿で退部と分かっている人を除き、
        無いときは名簿の現役を対象にする。
        """
        guild_id = schedule["guild_id"]
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None

        role_member_ids: set[str] | None = None
        if schedule.get("target_role_id"):
            role = guild.get_role(int(schedule["target_role_id"]))
            if not role:
                return None
            role_member_ids = {str(m.id) for m in role.members if not m.bot}
            if not role_member_ids:
                # ロールは生きているのに保持者が見えない。誰も付けていない
                # ロール（正常）とメンバーキャッシュの欠落を区別できないので、
                # 0 =「全員回答済み」とは主張しない。**名簿へフォールバック
                # しない**（班限定の予定で名簿全員へ飛ぶほうが被害が大きい）
                log.info(
                    "対象ロールにメンバーが居ません (guild=%s, schedule=%s, role=%s)",
                    guild_id,
                    schedule["schedule_id"],
                    schedule["target_role_id"],
                )
                return None

        roster_active, roster_retired = await self._roster_ids(guild_id)
        answered = await self.repo.list_voters_for_schedule(guild_id, schedule["schedule_id"])
        target_ids = svc.select_unanswered_targets(
            role_member_ids=role_member_ids,
            roster_active_ids=roster_active,
            roster_retired_ids=roster_retired,
            answered_ids=answered,
        )
        if target_ids is None:
            return None

        targets = []
        for user_id in sorted(target_ids):
            member = self._member_of(guild, user_id)
            if member is not None and not member.bot:
                targets.append(member)
        if target_ids and not targets:
            # 候補は居るのに1人も解決できなかった。0 を返すと
            # 「全員回答済み」という嘘になり、送信済みにもされてしまう
            log.warning(
                "未回答者を1人も解決できませんでした"
                " (guild=%s, schedule=%s, 候補=%d名, 名簿の現役=%d名)",
                guild_id,
                schedule["schedule_id"],
                len(target_ids),
                len(roster_active),
            )
            return None

        deadline = fmt_jp(from_iso(schedule["deadline"]))
        text = (
            f"【日程調整リマインド】\n「{schedule['title']}」が未回答です。\n"
            f"締切: {deadline}\n投票チャンネルでリアクションをお願いします。"
        )

        channel = self.bot.get_channel(int(schedule["channel_id"]))
        await dm_each_with_channel_fallback(
            targets, text, channel, fallback_note="未回答リマインド（DM不可）:"
        )
        return len(targets)

    async def finalize_schedule(self, schedule: dict):
        """締切処理: クローズ→結果要約投稿（仕様 11.2.5）。"""
        guild_id = schedule["guild_id"]
        await self.repo.close_schedule(guild_id, schedule["schedule_id"])
        guild = self.bot.get_guild(guild_id)
        embed = await svc.build_summary_embed(self.repo, guild_id, self.bot, schedule, guild)
        channel = self.bot.get_channel(int(schedule["channel_id"]))
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


# schedule_id のオートコンプリートを一括登録する（cogs/progress.py と同じ作法）。
# 開催中のみ: close / remind / edit-deadline、締切済みも含む: status / delete
Schedule.close.autocomplete("schedule_id")(Schedule._schedule_ac_open)
Schedule.remind.autocomplete("schedule_id")(Schedule._schedule_ac_open)
Schedule.edit_deadline.autocomplete("schedule_id")(Schedule._schedule_ac_open)
Schedule.status.autocomplete("schedule_id")(Schedule._schedule_ac_all)
Schedule.delete.autocomplete("schedule_id")(Schedule._schedule_ac_all)
Schedule.restore.autocomplete("schedule_id")(Schedule._schedule_ac_deleted)
Schedule.confirm.autocomplete("schedule_id")(Schedule._schedule_ac_all)
Schedule.confirm.autocomplete("option_id")(Schedule._option_ac)
Schedule.unconfirm.autocomplete("schedule_id")(Schedule._schedule_ac_all)


async def setup(bot: commands.Bot):
    await bot.add_cog(Schedule(bot))
