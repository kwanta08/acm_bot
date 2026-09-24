"""DM とチャンネルメンションを使い分ける通知ヘルパ。

未回答リマインド（cogs/schedule.py）にあった「DM を試み、拒否されたら
チャンネルで1通にまとめてメンションする」実装を切り出したもの。
タスクの担当割り当て通知（cogs/tasks.py）と共用する（G2-3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import discord

from utils.logger import get_logger

log = get_logger(__name__)


# 「サークル全体への告知」の送信先を解決する順序（G4-8）。
#
# 綴りの違う2つの進捗キーが並んでいるのは歴史的な事情による
# （services/progress_sync_service.DEFAULT_CHANNEL_KEYS と同じ理由）。
NOTICE_CHANNEL_KEYS = (
    "DEFAULT_ANNOUNCE_CHANNEL_ID",
    "PROGRESS_DEFAULT_CHANNEL_ID",
    "DEFAULT_PROGRESS_CHANNEL_ID",
    "DEFAULT_TASK_CHANNEL_ID",
)


async def resolve_notice_channel_id(db, guild_id: int) -> int | None:
    """告知の送信先チャンネル ID を settings から解決する。未設定なら None。

    **チャンネルの解決はしない。** 呼び出し側が `guild_channel(guild, id)` か
    `guild_channel_by_id(bot, guild_id, id)` で**同じギルド内に限定して**引くこと
    （bot 全体のキャッシュから引くと他テナントへ流れる。`guild.get_channel` は
    スレッドを解決しない）。
    """
    from repositories.settings_repository import SettingsRepository

    repo = SettingsRepository(db)
    for key in NOTICE_CHANNEL_KEYS:
        raw = (await repo.get(guild_id, key) or "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def guild_channel(guild, channel_id):
    """同一ギルド内でチャンネル（スレッド含む）を解決する（他ギルドへ流さない）。

    スレッドに投稿された予定・通知があるので `get_channel_or_thread` を使う
    （`get_channel` はスレッドを解決しない）。`get_channel` へのフォールバックも、
    メソッドが無いときに黙って None を返す枝も**持たない**。本番の `discord.Guild`
    は必ず `get_channel_or_thread` を持つため、そうした枝は到達しない枝になり、
    テストのフェイクだけを「チャンネルが引けない」理由で緑にする穴になる。

    送れないもの（カテゴリ等）と数字として読めない ID は None。
    """
    if guild is None or not channel_id:
        return None
    try:
        channel = guild.get_channel_or_thread(int(channel_id))
    except (TypeError, ValueError):
        return None
    return channel if channel is not None and hasattr(channel, "send") else None


def guild_channel_by_id(bot, guild_id, channel_id):
    """`guild_id` のギルドの**中だけ**でチャンネルを解決する。

    `bot.get_channel()`（bot 全体のキャッシュ）は使わない。`config.for_guild()` は
    環境変数の値を**全ギルドの GuildConfig へ配る**ため、bot 全体のキャッシュから
    引くと、あるギルドの設定値が指す他ギルドのチャンネルへ通知が流れる（H1-1）。

    ギルドが見えない・そのギルドに無い場合は None を返して**送らない**。
    誤送信よりログが出ないほうがましなので「たぶんこれだろう」でフォールバック
    しない（`bot.py` の `_log_channel_for` と同じ判断）。
    """
    if bot is None or guild_id is None or not channel_id:
        return None
    try:
        guild = bot.get_guild(int(guild_id))
    except (TypeError, ValueError):
        return None
    if guild is None:
        # ギルドが見えないと「そのギルドのチャンネルか」を確かめられない
        log.debug("チャンネルの解決先ギルドが見つかりません (guild=%s)", guild_id)
        return None
    channel = guild_channel(guild, channel_id)
    if channel is None:
        # レガシー運用で .env のチャンネル ID が他サーバーを指している、等
        log.debug(
            "チャンネル ID がこのサーバーの送信先ではありません (guild=%s, channel=%s)",
            guild_id,
            channel_id,
        )
    return channel


@dataclass
class NotifyOutcome:
    """通知の結果。呼び出し側は failed だけ見れば「届かなかった人」が分かる。"""

    dm_sent: list = field(default_factory=list)
    fell_back: list = field(default_factory=list)  # DM 不可 → チャンネルで届いた
    failed: list = field(default_factory=list)  # どちらでも届かなかった

    @property
    def delivered(self) -> int:
        return len(self.dm_sent) + len(self.fell_back)


async def dm_each_with_channel_fallback(
    members: list,
    text: str,
    fallback_channel,
    *,
    fallback_note: str = "（DM不可のためこちらでお知らせします）",
) -> NotifyOutcome:
    """各メンバーへ DM し、拒否された人はチャンネルで1通にまとめて知らせる。

    - DM は1人ずつ試す（1人の Forbidden で他の人へ送れなくならないように）
    - チャンネルへの投稿は**1通にまとめる**（人数分の連投でチャンネルを
      流さない。未回答リマインドの既存の作法）
    - チャンネルが無い・投稿も失敗した人は failed に入る。呼び出し側は
      failed を見て「届いていない」ことを利用者に伝える
    """
    outcome = NotifyOutcome()
    for member in members:
        try:
            await member.send(text)
            outcome.dm_sent.append(member)
        except (discord.Forbidden, discord.HTTPException):
            outcome.failed.append(member)

    if not outcome.failed:
        return outcome

    if fallback_channel is None:
        return outcome

    mentions = " ".join(m.mention for m in outcome.failed)
    try:
        await fallback_channel.send(f"{fallback_note}\n{mentions}\n{text}")
    except discord.HTTPException as e:
        log.warning("チャンネルへのフォールバック通知に失敗: %s", e)
        return outcome

    outcome.fell_back = outcome.failed
    outcome.failed = []
    return outcome
