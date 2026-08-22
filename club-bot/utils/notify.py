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
