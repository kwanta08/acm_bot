"""コマンドをまたいで使う共通 View。

## ConfirmView

破壊的操作の前に一度確認を挟む。取り返しのつかない操作に確認があるものと
無いものが混在していた（確認があったのは `/data delete`・`/season rollover`・
`/team-remove` の3つだけ）ため、確認の作法を1箇所へ集約する。

**確認は「押した人が実行者本人か」まで見る。** ephemeral な応答でも、
View のボタンは interaction を受け取れば誰でも押せる形になりうるので、
所有者チェックを View 側の既定動作にしておく（規律ではなく構造で守る）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from utils.embeds import error_embed, info_embed
from utils.logger import get_logger

log = get_logger(__name__)

# 確認を放置したときに View を畳むまでの秒数。
# 破壊的操作の確認なので、長く開けっ放しにしない。
DEFAULT_CONFIRM_TIMEOUT = 300.0

ConfirmCallback = Callable[[discord.Interaction], Awaitable[None]]


class ConfirmView(discord.ui.View):
    """「確定する / やめる」の2択を出し、確定時だけ処理を走らせる View。

    使い方::

        view = ConfirmView(interaction.user.id, preview, _do_delete)
        await interaction.followup.send(embed=view.preview_embed, view=view, ephemeral=True)

    `on_confirm` は確定ボタンの interaction を受け取る。
    このメソッドが呼ばれる時点で `interaction.response.defer()` は済んでいるので、
    応答は `interaction.followup.send()` で行う。

    サブクラスで `add_item()` すれば、確認の前に選択させる UI を足せる
    （`/season rollover` の卒業者選択がこの形）。
    """

    def __init__(
        self,
        owner_id: int,
        preview_embed: discord.Embed,
        on_confirm: ConfirmCallback,
        *,
        timeout: float = DEFAULT_CONFIRM_TIMEOUT,
        confirm_label: str = "確定する",
        cancel_label: str = "やめる",
        cancel_title: str = "中止しました",
        cancel_message: str = "何も変更していません。",
    ):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.preview_embed = preview_embed
        self.confirmed = False
        self.cancelled = False
        self._on_confirm = on_confirm
        self._cancel_title = cancel_title
        self._cancel_message = cancel_message
        self.confirm.label = confirm_label
        self.cancel.label = cancel_label

    # ------------------------------------------------------------------
    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        """実行者本人以外の押下を断る。"""
        if interaction.user.id == self.owner_id:
            return True
        try:
            await interaction.response.send_message(
                embed=error_embed("この操作は実行者のみ行えます。"), ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning("確認 View の拒否メッセージ送信に失敗: %s", e)
        return False

    def _disable_all(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    # ------------------------------------------------------------------
    @discord.ui.button(label="確定する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._is_owner(interaction):
            return
        # 連打で二重に走らせない（削除が2回走ると件数の報告も嘘になる）
        if self.confirmed:
            return
        self.confirmed = True
        self._disable_all()
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException as e:
            log.warning("確認 View の defer に失敗: %s", e)
            self.stop()
            return
        try:
            await self._on_confirm(interaction)
        finally:
            # 失敗しても押しっぱなしにしない
            self.stop()

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._is_owner(interaction):
            return
        self.cancelled = True
        self._disable_all()
        try:
            await interaction.response.edit_message(
                embed=info_embed(self._cancel_title, self._cancel_message), view=None
            )
        except discord.HTTPException as e:
            log.warning("確認 View の中止表示に失敗: %s", e)
        self.stop()
