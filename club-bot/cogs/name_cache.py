"""Discord 表示名キャッシュの同期（ダッシュボードの ID → 名前解決用）。

ダッシュボードは Bot トークンを持たない別プロセスで、Discord API から
名前を取得できない。そこで bot 側がギルドキャッシュの内容を
discord_name_cache テーブルへ書き込み、ダッシュボードは表示時に
そこを読む（dashboard/display.py）。

同期の方針:
- 起動（on_ready）と新規参加（on_guild_join）で全同期。on_ready は
  再接続のたびに走るが冪等（切断中の変更もここで追い付く）
- 以降は gateway のイベントで差分更新（API を追加で呼ばない＝N+1 なし）
- ユーザー行は退会しても消さない（過去の出欠・作業記録に最後に
  知られた名前を出すため）。チャンネル行は削除イベントで消す
- 1ギルドの同期失敗が他ギルドへ影響しないよう、ギルド単位で握りつぶす

スラッシュコマンドは持たない。
"""

from __future__ import annotations

import discord
from discord.ext import commands

from repositories.name_cache_repository import (
    ENTITY_CHANNEL,
    ENTITY_USER,
    NameCacheRepository,
)
from utils.logger import get_logger
from utils.parser import now, to_iso

log = get_logger("name_cache")


def member_cache_name(member: object) -> str:
    """キャッシュへ書く「そのギルドでの表示名」を決める。

    優先順位はダッシュボードの要件どおり:
    サーバーニックネーム → グローバル表示名 → ユーザー名。
    （discord.Member.display_name と同じ規則だが、優先順位をここで
    明示してテスト可能にする。）
    """
    return (
        getattr(member, "nick", None)
        or getattr(member, "global_name", None)
        or str(getattr(member, "name", ""))
    )


class NameCache(commands.Cog):
    """ギルドキャッシュ → discord_name_cache テーブルの同期。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.repo = NameCacheRepository(bot.db)

    # ------------------------------------------------------------------
    # 共通処理
    # ------------------------------------------------------------------
    async def _sync_guild(self, guild: discord.Guild) -> None:
        """1ギルドの全チャンネル・全メンバーの名前を同期する（冪等）。"""
        try:
            # members intent 有効でも未チャンクの場合がある（大規模ギルド等）
            if not guild.chunked:
                await guild.chunk(cache=True)
        except discord.HTTPException as e:
            log.warning("メンバー一覧の取得に失敗 (guild=%s): %s", guild.id, e)

        now_iso = to_iso(now())
        try:
            # チャンネルは全入れ替え（bot 停止中に削除された行を残さない）。
            # スレッドに投稿された日程調整もあるためスレッドも含める
            channels = [(str(c.id), c.name) for c in (*guild.channels, *guild.threads)]
            await self.repo.replace_all(guild.id, ENTITY_CHANNEL, channels, now_iso)
            # ユーザーは追記・上書きのみ（退会者の最後の名前を残す）
            members = [(str(m.id), member_cache_name(m)) for m in guild.members]
            await self.repo.upsert_many(guild.id, ENTITY_USER, members, now_iso)
            log.info(
                "表示名キャッシュを同期しました (guild=%s, channels=%d, members=%d)",
                guild.id,
                len(channels),
                len(members),
            )
        except Exception:  # 1ギルドの失敗で他を止めない
            log.exception("表示名キャッシュの同期に失敗しました (guild=%s)", guild.id)

    async def _upsert(self, guild_id: int, entity_type: str, entity_id: int, name: str) -> None:
        try:
            await self.repo.upsert(guild_id, entity_type, str(entity_id), name, to_iso(now()))
        except Exception:  # キャッシュ更新の失敗で bot を止めない
            log.exception(
                "表示名キャッシュの更新に失敗しました (guild=%s, %s=%s)",
                guild_id,
                entity_type,
                entity_id,
            )

    async def _delete(self, guild_id: int, entity_type: str, entity_id: int) -> None:
        try:
            await self.repo.delete(guild_id, entity_type, str(entity_id))
        except Exception:  # キャッシュ削除の失敗で bot を止めない
            log.exception(
                "表示名キャッシュの削除に失敗しました (guild=%s, %s=%s)",
                guild_id,
                entity_type,
                entity_id,
            )

    # ------------------------------------------------------------------
    # 全同期（起動・参加）
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in list(self.bot.guilds):
            await self._sync_guild(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._sync_guild(guild)

    # ------------------------------------------------------------------
    # チャンネル・スレッドの差分更新
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._upsert(channel.guild.id, ENTITY_CHANNEL, channel.id, channel.name)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        if before.name != after.name:
            await self._upsert(after.guild.id, ENTITY_CHANNEL, after.id, after.name)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        # 消すことで表示側の「不明なチャンネル」フォールバックに落ちる
        await self._delete(channel.guild.id, ENTITY_CHANNEL, channel.id)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        await self._upsert(thread.guild.id, ENTITY_CHANNEL, thread.id, thread.name)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        if before.name != after.name:
            await self._upsert(after.guild.id, ENTITY_CHANNEL, after.id, after.name)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        await self._delete(thread.guild.id, ENTITY_CHANNEL, thread.id)

    # ------------------------------------------------------------------
    # メンバーの差分更新
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._upsert(member.guild.id, ENTITY_USER, member.id, member_cache_name(member))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """ニックネームの変更を反映する。"""
        if member_cache_name(before) != member_cache_name(after):
            await self._upsert(after.guild.id, ENTITY_USER, after.id, member_cache_name(after))

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        """グローバル表示名・ユーザー名の変更を、共通のギルドへ反映する。

        ニックネームを付けているギルドでは表示が変わらないため、
        member_cache_name() の結果が変わったギルドだけ更新する。
        """
        for guild in list(self.bot.guilds):
            member = guild.get_member(after.id)
            if member is not None:
                await self._upsert(guild.id, ENTITY_USER, member.id, member_cache_name(member))

    # on_member_remove は意図的に扱わない: 退会者の行を残し、過去の
    # 出欠・作業記録に「最後に知られた名前」を表示し続けるため。


async def setup(bot: commands.Bot):
    await bot.add_cog(NameCache(bot))
