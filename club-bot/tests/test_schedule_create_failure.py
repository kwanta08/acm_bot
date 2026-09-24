"""`/schedule create` が投稿に失敗したとき、ゾンビ投票を残さない（H1-2）。

以前は `create_schedule` / `add_option` を**投稿より先**に実行し、`channel.send()` を
捕捉していなかった。送信が Forbidden になると

1. 予定と候補が DB に入る
2. 利用者には「予期せぬエラーが発生しました」だけ
3. `message_id` の無い予定が残り、5分後の自動締切ループが拾う
4. 締切サマリーの投稿もまた失敗する

ここでは次を固定する。

- **DB へ書く前に** Bot 自身の権限（`permissions_for(guild.me)`）を検査し、足りなければ
  何も作らずに不足している権限名を返す
- それでも送信に失敗したら `soft_delete_schedule` で畳み（締切も立つ。ADR 0037）、
  投稿済みのメッセージを消してからエラーを返す
- 畳んだ予定は自動締切・自動催促・開催中一覧のどれにも出ない
- リアクション式の `add_reaction` の失敗は投票を無効にしない
- 成功と返したときは全候補に `message_id` が入っている

フェイクのチャンネルは **`guild.me` 以外の権限を訊かれたら AssertionError** を投げる。
実行者の権限を検査してしまう取り違えを、それらしい拒否に化けさせないため
（`discord.ClientException` の系統にしないのは、実装がそれを「確認できない」として
握るから）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from cogs.schedule import Schedule
from config import GuildConfig
from config import config as global_config
from repositories.schedule_repository import ScheduleRepository
from utils.db import Database
from utils.notify import missing_send_permission
from utils.parser import parse_deadline, to_iso

G1 = 111
DEADLINE = "2099-01-01 00:00"

ME = SimpleNamespace(id=999, name="bot", bot=True)

# 実物の permissions_for は権限を連鎖して落とす（send が無いと embed も落ちる、等）。
# テストの入力もその形にする（本番で出ない組み合わせを固定しない）。
ALL = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    add_reactions=True,
    read_message_history=True,
)
NO_VIEW = discord.Permissions.none()
NO_SEND = discord.Permissions(view_channel=True)
NO_EMBED = discord.Permissions(view_channel=True, send_messages=True, send_messages_in_threads=True)
THREAD_NO_SEND = discord.Permissions(view_channel=True, send_messages=True)


def run(coro):
    return asyncio.run(coro)


def _forbidden() -> discord.Forbidden:
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


def _http_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "failed")


# ---------------------------------------------------------------------
# フェイク
# ---------------------------------------------------------------------
class _Message:
    def __init__(self, message_id: int, channel):
        self.id = message_id
        self.channel = channel
        self.deleted = False
        self.reactions: list = []

    async def add_reaction(self, emoji):
        self.channel.reaction_calls += 1
        if self.channel.fail_reaction is not None:
            raise self.channel.fail_reaction()
        self.reactions.append(emoji)

    async def delete(self):
        if self.channel.fail_delete:
            raise _http_error()
        self.deleted = True


class _ChannelMixin:
    """テキストチャンネル・スレッド共通の振る舞い。"""

    def _setup(
        self,
        channel_id: int,
        perms: discord.Permissions = ALL,
        *,
        fail_on_send: int | None = None,
        send_error=_forbidden,
        fail_reaction=None,
        fail_delete: bool = False,
        perms_error: Exception | None = None,
        fetch_error=None,
    ):
        self.id = channel_id
        self._perms = perms
        self._perms_error = perms_error
        self.fetch_error = fetch_error
        self.fail_on_send = fail_on_send
        self.send_error = send_error
        self.fail_reaction = fail_reaction
        self.fail_delete = fail_delete
        self.asked: list = []
        self.sent: list[dict] = []
        self.messages: dict[int, _Message] = {}
        self.send_calls = 0
        self.reaction_calls = 0

    def permissions_for(self, member):
        self.asked.append(member)
        if member is not ME:
            raise AssertionError("Bot 自身（guild.me）以外の権限を検査している")
        if self._perms_error is not None:
            raise self._perms_error
        return self._perms

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        self.send_calls += 1
        if self.fail_on_send is not None and self.send_calls >= self.fail_on_send:
            raise self.send_error()
        msg = _Message(7000 + self.send_calls, self)
        self.messages[msg.id] = msg
        self.sent.append({"content": content, "embed": embed, "view": view})
        return msg

    async def fetch_message(self, message_id: int):
        if self.fetch_error is not None:
            raise self.fetch_error()
        msg = self.messages.get(int(message_id))
        if msg is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="gone"), "not found")
        return msg


class _Channel(_ChannelMixin):
    def __init__(self, channel_id: int = 555, perms=ALL, **kwargs):
        self._setup(channel_id, perms, **kwargs)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"


class _Thread(_ChannelMixin, discord.Thread):
    """`isinstance(channel, discord.Thread)` を通すスレッドのフェイク。"""

    def __init__(self, channel_id: int = 556, perms=ALL, **kwargs):
        self._setup(channel_id, perms, **kwargs)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"


class _Guild:
    def __init__(self):
        self.id = G1
        self.name = "g1"
        self.emojis = []
        self.me = ME

    def get_role(self, _role_id):
        return None

    def get_member(self, _user_id):
        return None

    def get_emoji(self, _emoji_id):
        return None

    def get_channel_or_thread(self, _channel_id):
        return None


class _Interaction:
    def __init__(self, guild, channel, *, followup_error=None):
        self.guild = guild
        self.guild_id = guild.id
        self.channel = channel
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self._followup_error = followup_error
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, *args, **kwargs):
        if self._followup_error is not None:
            raise self._followup_error()
        self.sent.append(kwargs)

    @property
    def last_embed(self) -> discord.Embed:
        return self.sent[-1]["embed"]

    @property
    def last_text(self) -> str:
        embed = self.last_embed
        return (embed.title or "") + "\n" + (embed.description or "")


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


@contextlib.asynccontextmanager
async def _env(style: str = "buttons"):
    """DB と、ui_style を固定したギルド設定を用意する。

    差し替えるのは `config` シングルトンの属性（モジュールグローバルへの
    mock.patch は使わない。フルセットでだけ赤くなる型を避ける）。
    """
    db = Database(_tmp_db_path())
    await db.connect()
    guild = _Guild()
    bot = SimpleNamespace(
        db=db,
        user=None,
        guilds=[guild],
        get_guild=lambda gid: guild if gid == G1 else None,
        get_user=lambda _uid: None,
    )
    original = global_config.for_guild

    async def _for_guild(gid, *args, **kwargs):
        return GuildConfig(guild_id=gid, schedule_ui_style=style)

    global_config.for_guild = _for_guild
    try:
        yield SimpleNamespace(db=db, guild=guild, cog=Schedule(bot), repo=ScheduleRepository(db))
    finally:
        global_config.for_guild = original
        await db.close()


def _options(count: int) -> str:
    """締切（2099-01-01 00:00）より後の候補を count 件。"""
    day = parse_deadline(DEADLINE) + timedelta(days=1, hours=10)
    return "; ".join((day + timedelta(days=i)).strftime("%Y-%m-%d %H:%M") for i in range(count))


async def _create(
    env, channel, *, options: int = 2, title: str = "定例会", followup_error=None
) -> _Interaction:
    interaction = _Interaction(env.guild, channel, followup_error=followup_error)
    await Schedule.create.callback(
        env.cog, interaction, title=title, options=_options(options), deadline=DEADLINE
    )
    return interaction


async def _count(db, table: str) -> int:
    rows = await db.fetchall(f"SELECT * FROM {table} WHERE guild_id = ?", (G1,))
    return len(rows)


async def _only_schedule(db) -> dict:
    """削除済みも含めて、作られた予定の行（1件だけのはず）。"""
    rows = await db.fetchall("SELECT * FROM schedules WHERE guild_id = ?", (G1,))
    assert len(rows) == 1, f"予定の行が {len(rows)} 件ある"
    return dict(rows[0])


def _loop_windows():
    """3つの一覧を引く時刻。締切を含む区間・締切を過ぎた時刻を同じ締切から計算する。"""
    deadline = parse_deadline(DEADLINE)
    return {
        "due_at": to_iso(deadline + timedelta(minutes=5)),
        "remind_from": to_iso(deadline - timedelta(hours=2)),
        "remind_to": to_iso(deadline + timedelta(hours=1)),
    }


# =====================================================================
# 1. 純粋関数: どの権限が最初に欠けているか
# =====================================================================
@pytest.mark.parametrize(
    ("perms", "thread", "expected"),
    [
        (ALL, False, None),
        (ALL, True, None),
        (NO_VIEW, False, "チャンネルを見る"),
        (NO_SEND, False, "メッセージを送信"),
        (NO_EMBED, False, "埋め込みリンク"),
        # スレッドでは send_messages ではなく send_messages_in_threads を見る
        (THREAD_NO_SEND, True, "スレッドでメッセージを送信"),
        (NO_EMBED, True, "埋め込みリンク"),
        (NO_VIEW, True, "チャンネルを見る"),
    ],
)
def test_missing_send_permission_names_the_first_gap(perms, thread, expected):
    """連鎖で落ちた後段（send が無いときの embed 等）は「分からない」ので挙げない。"""
    channel = _Thread(perms=perms) if thread else _Channel(perms=perms)
    assert missing_send_permission(channel, ME) == expected
    assert channel.asked == [ME]


def test_thread_needs_the_thread_permission_even_with_send_messages():
    """send_messages があってもスレッドでは送れない（逆向きの取り違えを検出する）。"""
    perms = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    assert missing_send_permission(_Channel(perms=perms), ME) is None
    assert missing_send_permission(_Thread(perms=perms), ME) == "スレッドでメッセージを送信"


# =====================================================================
# 2. 事前検査: 足りなければ何も作らない
# =====================================================================
@pytest.mark.parametrize(
    ("perms", "missing"),
    [
        (NO_VIEW, "チャンネルを見る"),
        (NO_SEND, "メッセージを送信"),
        (NO_EMBED, "埋め込みリンク"),
    ],
)
def test_create_refuses_when_bot_cannot_post(perms, missing):
    """DB に1行も書かず、投稿もせず、不足権限名を返す。

    フェイクの send は**成功する**。事前検査だけが投稿を止めている状態で見る
    （埋め込みリンクが無いと、Discord は送信を拒否せず埋め込みだけ落とすことがある）。
    """

    async def _main():
        async with _env() as env:
            channel = _Channel(perms=perms)
            interaction = await _create(env, channel)

            assert await _count(env.db, "schedules") == 0, "権限が足りないのに予定を作った"
            assert await _count(env.db, "schedule_options") == 0
            assert channel.send_calls == 0, "権限が足りないのに投稿した"
            assert channel.asked == [ME], "Bot 自身の権限を検査していない"
            assert "このチャンネル" in interaction.last_text
            assert f"不足: {missing}" in interaction.last_text
            assert "作成しました" not in interaction.last_text

    run(_main())


def test_create_refuses_in_a_thread_without_the_thread_permission():
    async def _main():
        async with _env() as env:
            thread = _Thread(perms=THREAD_NO_SEND)
            interaction = await _create(env, thread)

            assert await _count(env.db, "schedules") == 0
            assert thread.send_calls == 0
            assert "不足: スレッドでメッセージを送信" in interaction.last_text

    run(_main())


def test_create_refuses_when_permissions_cannot_be_resolved():
    """スレッドの親が未キャッシュ等で権限を計算できない → 確認できないので作らない。"""

    async def _main():
        async with _env() as env:
            thread = _Thread(perms_error=discord.ClientException("Parent channel not found"))
            interaction = await _create(env, thread)

            assert await _count(env.db, "schedules") == 0
            assert thread.send_calls == 0
            assert "権限を確認できませんでした" in interaction.last_text

    run(_main())


# =====================================================================
# 3. 送信の失敗: 畳んでからエラー。3つの一覧に出ない
# =====================================================================
@pytest.mark.parametrize("style", ["buttons", "reaction"])
@pytest.mark.parametrize("send_error", [_forbidden, _http_error])
def test_send_failure_folds_the_schedule(style, send_error):
    """検証 (a)(b)(c)。対照（健全な予定）を**同じテストで同じ経路・同じ締切**で作る。"""

    async def _main():
        async with _env(style) as env:
            healthy = _Channel(561)
            await _create(env, healthy, title="健全")
            healthy_id = (await env.repo.list_all(G1))[0]["schedule_id"]

            broken = _Channel(562, fail_on_send=1, send_error=send_error)
            interaction = await _create(env, broken, title="失敗")

            rows = await env.db.fetchall(
                "SELECT * FROM schedules WHERE guild_id = ? AND schedule_id <> ?",
                (G1, healthy_id),
            )
            assert len(rows) == 1
            folded = dict(rows[0])
            # (a) 畳まれている（論理削除 + 締切。close_schedule での代用ではない）
            assert folded["deleted_flag"] == 1
            assert folded["closed_flag"] == 1
            # (b) 成功ではなくエラーが返る
            assert "作成しました" not in interaction.last_text
            assert "取り消しました" in interaction.last_text
            # 検証 (b): 理由（403 Forbidden / 500 等）が利用者に伝わる
            assert str(send_error()) in interaction.last_text, "失敗の理由が伝わっていない"

            # (c) 3つの一覧は健全な予定だけを返す
            w = _loop_windows()
            due = {r["schedule_id"] for r in await env.repo.list_due_schedules(G1, w["due_at"])}
            remind = {
                r["schedule_id"]
                for r in await env.repo.list_reminder_candidates(
                    G1, w["remind_from"], w["remind_to"]
                )
            }
            open_ = {r["schedule_id"] for r in await env.repo.list_open_schedules(G1)}
            assert due == {healthy_id}, "自動締切の対象がおかしい"
            assert remind == {healthy_id}, "自動催促の対象がおかしい"
            assert open_ == {healthy_id}, "開催中一覧がおかしい"

    run(_main())


def test_partial_board_post_removes_the_board_already_sent():
    """ボタン式: 候補26件（2ページ）で2通目だけ失敗 → 1通目のボードを消す。"""

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel(fail_on_send=2)
            interaction = await _create(env, channel, options=26)

            first = channel.messages[7001]
            assert first.deleted, "投稿済みのボードが残っている"
            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)
            assert "削除できませんでした" not in interaction.last_text

    run(_main())


def test_partial_reaction_post_removes_the_message_already_sent():
    """リアクション式: 候補2件で2通目だけ失敗 → 1通目の候補メッセージを消す。"""

    async def _main():
        async with _env("reaction") as env:
            channel = _Channel(fail_on_send=2)
            await _create(env, channel, options=2)

            assert channel.messages[7001].deleted, "投稿済みの候補メッセージが残っている"
            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)

    run(_main())


def test_cleanup_failure_still_folds_and_is_reported():
    """投稿済みメッセージを消せなくても予定は畳む。消せなかった件数は伝える。"""

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel(fail_on_send=2, fail_delete=True)
            interaction = await _create(env, channel, options=26)

            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)
            assert "1 件を削除できませんでした" in interaction.last_text

    run(_main())


def _connection_reset() -> OSError:
    return OSError("Connection reset by peer")


@pytest.mark.parametrize(("style", "options"), [("buttons", 26), ("reaction", 2)])
def test_transport_error_on_send_still_folds(style, options):
    """通信層の例外（HTTPException に包まれない OSError 等）でもゾンビを残さない。

    案内は全体のエラーハンドラに任せるので例外はそのまま上がるが、予定は畳まれ、
    2通目で落ちたときは投稿済みの1通目も消えている。
    """

    async def _main():
        async with _env(style) as env:
            channel = _Channel(fail_on_send=2, send_error=_connection_reset)
            with pytest.raises(OSError):
                await _create(env, channel, options=options)

            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)
            assert channel.messages[7001].deleted, "投稿済みのメッセージが残っている"

    run(_main())


def test_folding_happens_before_cleanup():
    """**畳むのが先。** 後始末（投稿済みメッセージの削除）で想定外の例外が出ても、
    予定は既に畳まれている。

    後始末の失敗が HTTPException の系統だと握られてしまい順序が見えないので、
    通信層の例外（OSError）で確かめる。
    """

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel(fail_on_send=2, fetch_error=_connection_reset)
            with pytest.raises(OSError):
                await _create(env, channel, options=26)

            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)

    run(_main())


def _cleanup_boom() -> RuntimeError:
    return RuntimeError("cleanup failed")


def test_cleanup_failure_does_not_mask_the_transport_error():
    """通信層の例外で取り消したとき、後始末の失敗で**元の例外を隠さない**。

    全体のエラーハンドラが見るべきは「送信が落ちた」ほう。後始末の例外が
    上がってしまうと原因を取り違える。予定は畳まれている。
    """

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel(
                fail_on_send=2, send_error=_connection_reset, fetch_error=_cleanup_boom
            )
            with pytest.raises(OSError):
                await _create(env, channel, options=26)

            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)

    run(_main())


def test_messages_already_gone_are_not_reported_as_left_behind():
    """投稿済みのメッセージが既に消えていた（NotFound）なら「削除できなかった」に数えない。"""

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel(fail_on_send=2)
            # 1通目は投稿できたが、後始末の時点では誰かが既に消している
            original_send = channel.send

            async def _send_then_vanish(*args, **kwargs):
                msg = await original_send(*args, **kwargs)
                channel.messages.pop(msg.id, None)
                return msg

            channel.send = _send_then_vanish
            interaction = await _create(env, channel, options=26)

            folded = await _only_schedule(env.db)
            assert (folded["deleted_flag"], folded["closed_flag"]) == (1, 1)
            assert "削除できませんでした" not in interaction.last_text

    run(_main())


def test_success_notice_failure_does_not_fold_a_posted_vote():
    """**成功の通知は try の外。** 投稿が済んだあとで通知の送信だけが落ちても、
    正常に投稿された予定を畳まない・メッセージも消さない。
    """

    async def _main():
        async with _env("buttons") as env:
            channel = _Channel()
            with pytest.raises(discord.HTTPException):
                await _create(env, channel, followup_error=_http_error)

            schedule = await _only_schedule(env.db)
            assert (schedule["deleted_flag"], schedule["closed_flag"]) == (0, 0)
            assert not channel.messages[7001].deleted, "投稿済みの投票を消している"

    run(_main())


# =====================================================================
# 4. add_reaction の失敗は投票を無効にしない
# =====================================================================
@pytest.mark.parametrize("fail_reaction", [_forbidden, _http_error, _connection_reset])
def test_reaction_failure_does_not_void_the_vote(fail_reaction):
    async def _main():
        async with _env("reaction") as env:
            channel = _Channel(fail_reaction=fail_reaction)
            interaction = await _create(env, channel, options=2)

            schedule = await _only_schedule(env.db)
            assert (schedule["deleted_flag"], schedule["closed_flag"]) == (0, 0)
            assert "作成しました" in interaction.last_text
            assert "リアクションを追加" in interaction.last_text, "付けられなかったことを黙っている"
            options = await env.repo.list_options(G1, schedule["schedule_id"])
            assert all(o["message_id"] for o in options)
            # 1回失敗したら以降は付けない（同じ理由で全部失敗するので叩き続けない）
            assert channel.reaction_calls == 1

    run(_main())


# =====================================================================
# 5. 成功と返したときは全候補に message_id がある
# =====================================================================
@pytest.mark.parametrize(("style", "count"), [("buttons", 2), ("buttons", 26), ("reaction", 3)])
def test_success_sets_message_id_on_every_option(style, count):
    async def _main():
        async with _env(style) as env:
            channel = _Channel()
            interaction = await _create(env, channel, options=count)

            assert "作成しました" in interaction.last_text
            schedule = await _only_schedule(env.db)
            assert (schedule["deleted_flag"], schedule["closed_flag"]) == (0, 0)
            options = await env.repo.list_options(G1, schedule["schedule_id"])
            assert len(options) == count
            assert all(o["message_id"] for o in options), "message_id の無い候補がある"
            assert "リアクションを追加" not in interaction.last_text

    run(_main())
