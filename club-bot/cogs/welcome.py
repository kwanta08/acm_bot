"""
Welcome コグ（新入生オンボーディング。G3-6）

新歓期には30〜50人が入るのに、bot は新入生の存在を知らず、幹部が
`/member register` を1人ずつ手打ちしていた。名簿に載らない人には
班別通知も出欠催促も届かない。

**既定は OFF**（ADR 0024「既定値で何も起きない状態から始める」）。
`WELCOME_ENABLED` が ON のギルドでだけ、参加者へ「班を選ぶ」ボタンを送る。

## 設計上の要点

- **参加しただけでは `members` に登録しない。** 登録は班を選んだときだけ。
  `on_member_join` は再参加でも発火するので、ここで登録すると訪問者や
  OB まで台帳に入り、名簿を母集団にしている未回答催促（G3-2 / ADR 0025）が
  誤爆する
- **`status` / `active_flag` には触れない。** 再参加した卒業生が自動で
  現役に戻る経路を作らない
- ボタンは `discord.ui.DynamicItem`。`custom_id` に guild_id と user_id を
  埋めるので、bot を再起動しても押せる（新歓期の再起動でボタンが死なない）。
  **user_id を埋めるのは、チャンネルへ落ちたボタンが「誰でも押せる班ロール
  自販機」になるのを防ぐため**（L2 の `/member register` を迂回させない）
- ロール付与には Manage Roles が要るが、最小権限の招待（ADR 0017）には
  含まれない。付与できないときは「登録は完了・ロールは未付与」で終わる
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from config import config
from repositories.member_repository import MemberRepository
from utils.embeds import error_embed, info_embed, success_embed
from utils.logger import get_logger

if TYPE_CHECKING:
    from utils.db import Database

log = get_logger("welcome")

#: Discord の Select は25件まで。班がそれ以上あるギルドでは切り詰める
MAX_TEAM_OPTIONS = 25


class TeamPickButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"welcome:team:(?P<guild_id>\d+):(?P<user_id>\d+)",
):
    """「班を選ぶ」ボタン。

    `DynamicItem` なので bot を再起動しても押せる（新歓期に再起動が
    入っても新入生のボタンが死なない）。`custom_id` に user_id を
    埋めてあるので、チャンネルへ落ちた場合でも本人しか押せない。
    """

    def __init__(self, guild_id: int, user_id: int):
        self.guild_id = guild_id
        self.user_id = user_id
        super().__init__(
            discord.ui.Button(
                label="班を選ぶ",
                style=discord.ButtonStyle.primary,
                custom_id=f"welcome:team:{guild_id}:{user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str], /):
        return cls(int(match["guild_id"]), int(match["user_id"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=error_embed("このボタンは押した本人のためのものです。"), ephemeral=True
            )
            return False
        # ギルド内から押された場合は、そのギルドのボタンであることも見る
        if interaction.guild_id is not None and interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                embed=error_embed("このボタンは別のサーバーのものです。"), ephemeral=True
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Welcome")
        if cog is None:
            log.warning("Welcome コグが読み込まれていません")
            return
        await cog.open_team_picker(interaction, self.guild_id, self.user_id)


class TeamPickView(discord.ui.View):
    """班を選ぶ Select（ボタンから開く。短命なので DynamicItem にしない）。"""

    def __init__(self, cog: Welcome, guild_id: int, user_id: int, teams: list[dict]):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.picker = discord.ui.Select(
            placeholder="所属する班を選んでください",
            options=[
                discord.SelectOption(label=str(t["team_name"])[:100], value=str(t["team_key"]))
                for t in teams[:MAX_TEAM_OPTIONS]
            ],
        )
        self.picker.callback = self._on_pick
        self.add_item(self.picker)

    async def _on_pick(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=error_embed("この操作は本人のみ行えます。"), ephemeral=True
            )
            return
        await self.cog.register_team(
            interaction, self.guild_id, self.user_id, self.picker.values[0]
        )


class Welcome(commands.Cog):
    """新入生オンボーディング コグ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.repo = MemberRepository(self.db)

    # ------------------------------------------------------------------
    # 参加時
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        try:
            gconf = await config.for_guild(guild.id, db=self.db)
        except Exception:  # noqa: BLE001  (1ギルドの設定エラーで他を止めない)
            log.warning("ギルド設定を解決できません (guild=%s)", guild.id)
            return
        if not gconf.welcome_enabled:
            return  # 既定 OFF。ON にしたギルドでだけ動く

        embed = info_embed(
            f"ようこそ {guild.name} へ",
            "下のボタンから所属する班を選んでください。\n"
            "登録すると、班あての連絡や出欠のお知らせが届くようになります。\n"
            "あとから変えたくなったら、同じボタンをもう一度押してください。",
        )
        view = discord.ui.View(timeout=None)
        view.add_item(TeamPickButton(guild.id, member.id))

        try:
            await member.send(embed=embed, view=view)
            return
        except discord.Forbidden:
            log.info("DM を受け取らない設定です (guild=%s, user=%s)", guild.id, member.id)
        except discord.HTTPException as e:
            log.warning("ようこそ DM の送信に失敗 (guild=%s, user=%s): %s", guild.id, member.id, e)

        await self._fallback_to_channel(member, gconf, embed, view)

    async def _fallback_to_channel(self, member, gconf, embed, view) -> None:
        """DM が届かない人へ、設定されたチャンネルで声をかける。

        **送り先は `WELCOME_CHANNEL_ID` だけ。** 未設定・解決不能なら何も
        しない（ADR 0023）。「送信できる最初のチャンネル」に落とすと、
        新入生が読めないチャンネルへ出したのに成功扱いになる。
        """
        channel = self._welcome_channel(member.guild, gconf, member)
        if channel is None:
            log.info(
                "DM 拒否だが案内チャンネルが無い (guild=%s, user=%s)", member.guild.id, member.id
            )
            return
        try:
            await channel.send(content=member.mention, embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("案内チャンネルへの送信に失敗 (guild=%s): %s", member.guild.id, e)

    @staticmethod
    def _welcome_channel(guild: discord.Guild, gconf, member):
        """`WELCOME_CHANNEL_ID` を解決する（同一ギルド内に限定）。

        設定値がカテゴリ・フォーラムを指していることがあるので `send` の
        有無を見る。Bot 側の送信権限と、**新入生に見えるか**の両方を検査する。
        """
        channel_id = getattr(gconf, "welcome_channel_id", None)
        if not channel_id:
            return None
        channel = guild.get_channel(int(channel_id))
        if channel is None or not hasattr(channel, "send"):
            return None
        me = guild.me
        if me is not None:
            mine = channel.permissions_for(me)
            if not (mine.view_channel and mine.send_messages and mine.embed_links):
                return None
        if member is not None and not channel.permissions_for(member).view_channel:
            # 本人に見えないチャンネルへ出しても届かない
            return None
        return channel

    # ------------------------------------------------------------------
    # ボタン → 班セレクト → 登録
    # ------------------------------------------------------------------
    async def open_team_picker(
        self, interaction: discord.Interaction, guild_id: int, user_id: int
    ) -> None:
        # 退出した人が DM に残ったボタンを押すことがある。登録側と同じ
        # 検査をここでも行う（班名の一覧を元部員へ見せない）
        if self._member_in_guild(guild_id, user_id) is None:
            await interaction.response.send_message(
                embed=error_embed("サーバーに参加していないため操作できません。"),
                ephemeral=interaction.guild_id is not None,
            )
            return
        teams = await self.repo.list_teams(guild_id)
        if not teams:
            await interaction.response.send_message(
                embed=info_embed(
                    "まだ班が登録されていません",
                    "管理者に `/setup` の「班を一括作成」を依頼してください。",
                ),
                ephemeral=interaction.guild_id is not None,
            )
            return

        body = "所属する班を選んでください。"
        if len(teams) > MAX_TEAM_OPTIONS:
            body += (
                f"\n（班が {len(teams)} 件あるため先頭 {MAX_TEAM_OPTIONS} 件を表示しています。"
                "見つからない場合は幹部に `/member register` を依頼してください）"
            )
        member = await self._member_of(guild_id, user_id)
        if member is not None and member.get("primary_team"):
            body += "\n\n⚠️ すでに班が登録されています。選び直すと**入れ替わります**。"

        view = TeamPickView(self, guild_id, user_id, teams)
        # ギルド内では他人に見せない。DM では ephemeral が使えない
        await interaction.response.send_message(
            embed=info_embed("班を選ぶ", body),
            view=view,
            ephemeral=interaction.guild_id is not None,
        )

    def _member_in_guild(self, guild_id: int, user_id: int):
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild else None

    async def _member_of(self, guild_id: int, user_id: int) -> dict | None:
        try:
            return await self.repo.get_member(guild_id, str(user_id))
        except Exception:  # noqa: BLE001
            return None

    async def register_team(
        self, interaction: discord.Interaction, guild_id: int, user_id: int, team_key: str
    ) -> None:
        """班を登録し、可能ならロールも付ける。"""
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if guild is None or member is None:
            await interaction.response.send_message(
                embed=error_embed("サーバーに参加していないため登録できません。"),
                ephemeral=interaction.guild_id is not None,
            )
            return

        teams = {t["team_key"]: t for t in await self.repo.list_teams(guild_id)}
        team = teams.get(team_key)
        if team is None:
            await interaction.response.send_message(
                embed=error_embed("その班は登録されていません。もう一度お試しください。"),
                ephemeral=interaction.guild_id is not None,
            )
            return

        # **ここで初めて台帳へ入れる**（参加しただけでは入れない）。
        # status / active_flag には触らない
        await self.repo.upsert_member(guild_id, str(user_id), member.display_name)
        await self.repo.set_primary_team(guild_id, str(user_id), team_key)

        role_note = ""
        if not team.get("member_role_id"):
            # ロールが紐付いていない班。_sync_roles は何もせず正常終了するので、
            # 戻り値だけを見ていると「付いた」と誤解させる。最小権限の招待
            # （ADR 0017）ではロールの自動作成が失敗するため、これは例外ではなく
            # 新規ギルドの既定に近い状態
            role_note = (
                "\n\n⚠️ この班にはロールが紐付いていないため、班ロールは付いていません。"
                "登録自体は完了しています。幹部に `/team-role` での紐付けを相談してください。"
            )
        elif not await self._sync_roles(guild, member, str(user_id)):
            # 最小権限の招待では Manage Roles が無い（ADR 0017）。
            # 権限を足す方向では解決しない
            role_note = (
                "\n\n⚠️ 班ロールは付けられませんでした"
                "（Bot に「ロールの管理」権限がありません）。"
                "登録自体は完了しています。幹部に相談してください。"
            )

        await interaction.response.send_message(
            embed=success_embed(
                "班を登録しました",
                f"**{team['team_name']}** に登録しました。{role_note}\n\n"
                "班を変えたくなったら、最初の「班を選ぶ」ボタンをもう一度押してください。",
            ),
            ephemeral=interaction.guild_id is not None,
        )

    async def _sync_roles(self, guild, member, user_id: str) -> bool:
        """班ロールを同期する。付けられなければ False。

        `cogs/members.py` の `_sync_roles` を再利用する（新しい付与経路を
        作らない）。本体は `discord.Forbidden` を捕まえていないので、
        呼び出し側であるここで握る。
        """
        cog = self.bot.get_cog("Members")
        if cog is None:
            log.warning("Members コグが読み込まれていません（ロール付与をスキップ）")
            return False
        try:
            await cog._sync_roles(guild, member, user_id)
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("班ロールの付与に失敗 (guild=%s, user=%s): %s", guild.id, user_id, e)
            return False
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
