"""チャンネル解決のギルドスコープ（H1-1）。

`utils/notify.resolve_notice_channel_id` の docstring が明文化している約束——

> **チャンネルの解決はしない。** 呼び出し側が `guild_channel(guild, id)` か
> `guild_channel_by_id(bot, guild_id, id)` で**同じギルド内に限定して**引くこと
> （bot 全体のキャッシュから引くと他テナントへ流れる。`guild.get_channel` は
> スレッドを解決しない）。

——を、通知の主要経路すべてで守らせる（H1-1 以前はこの約束が
`guild.get_channel` を指していたが、スレッドを解決しないので改めた）。

`config.for_guild()` は環境変数の値を**全ギルドの GuildConfig へ配る**ため、
レガシー運用（`GUILD_ID` 指定 + 旧 `.env` に `DEFAULT_TASK_CHANNEL_ID` 等が
残っている）のまま2サーバー目を迎えたインスタンスでは、送信先を
`bot.get_channel()`（bot 全体のキャッシュ）で引くと
**B サーバーの通知が A サーバーのチャンネルに出る**。

このファイルは2種類の守りを持つ。

1. **静的走査** — `cogs/` `services/` `utils/` の実行コードに、bot 全体から
   チャンネルを引く書き方（`bot.get_channel` / `bot.fetch_channel` /
   `bot.get_partial_messageable`。受け手は `bot` / `_bot` / `client`）が
   現れたら落ちる（再混入の検出）。除外は許可リストで明示する
2. **実挙動** — 2ギルドが同じ `channel_id` を指している状況で、A のチャンネルへ
   B の通知が出ないことを、**実装を本物のまま呼んで**確かめる

2 のフェイク bot は `get_channel` / `fetch_channel` を **「他ギルドのチャンネルを
返す罠」** にしてある。これがあるので、実装が bot 全体の解決へ戻ると赤くなる。
（`cogs/reminders.py` はギルド間の影響を遮断するために広い `except Exception` を
持つ経路があり、フェイクから `get_channel` を消すだけでは握られて緑のまま通る。
だから「属性を消す」ではなく「罠を仕込む」にしている。）

さらに各テストは、**同じ設定で自ギルドからなら届く**ことも見る。
「そもそも送る条件が揃っていない」理由で「送られない」が成り立つ空振りを防ぐため
（開発ノートの gotcha `test-is-green-but-the-guard-it-checks-is-gone`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from test_intents import _code_only, _iter_source_files

from utils.notify import guild_channel, guild_channel_by_id

BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

G1 = 100000000000000001
G2 = 200000000000000002
SHARED_CHANNEL_ID = 777  # 両ギルドが同じ ID を設定している状況を作る
G2_OWN_CHANNEL_ID = 888  # G2 に実在する別のチャンネル
THREAD_ID = 999


def run(coro):
    return asyncio.run(coro)


# =====================================================================
# 1. 静的走査: bot 全体のキャッシュからチャンネルを引かない
# =====================================================================

#: 走査対象（`tests/test_intents.py` の `_iter_source_files()` の出力を絞る）。
#: 受入基準は `cogs/` `services/` だが、ヘルパ自体（`utils/notify.py`）への
#: 混入も拾うため `utils/` を含める。
#:
#: **`test_intents` のモジュール変数（SCAN_DIRS / SCAN_FILES）は書き換えない。**
#: 書き換えると `test_intents.test_scan_actually_covers_sources` が実行順で壊れる。
SCAN_PREFIXES = ("cogs/", "services/", "utils/")

#: bot 全体から書いてよい場所と、その出現数。
#: **(ファイル, 囲っている関数名, API) で持つ**（行番号だと差分のたびに腐る）。
#:
#: どちらも raw リアクションの処理で、呼び出し元が `payload.guild_id` を検査済み。
#: `payload.channel_id` は Discord がそのギルドのイベントとして渡す値で、
#: 未キャッシュのチャンネルに対する `fetch_channel` フォールバックが要るため、
#: `guild.get_channel_or_thread` には寄せていない（H1-1 の「除外」）。
ALLOWED_BOT_WIDE_LOOKUPS = {
    ("cogs/schedule.py", "_remove_other_reactions", "get_channel"): 1,
    ("cogs/schedule.py", "_remove_other_reactions", "fetch_channel"): 1,
    ("cogs/schedule.py", "_refresh_option_message", "get_channel"): 1,
    ("cogs/schedule.py", "_refresh_option_message", "fetch_channel"): 1,
}

# 括弧を要求しない。`getter = self.bot.get_channel` のような参照も拾い、
# `bot.get_channel(int(schedule["channel_id"]))` のように引数に括弧を含む形も
# 取り逃がさない。`\b` で終えるので `get_channel_or_thread` には一致しない。
_BOT_WIDE_LOOKUP = re.compile(
    r"\b(?:bot|_bot|client)\.(get_channel|fetch_channel|get_partial_messageable)\b"
)
_DEF_LINE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)", re.MULTILINE)


def _rel(path: str) -> str:
    """走査対象の相対パス。Windows でも `/` 区切りに揃える。"""
    return os.path.relpath(path, BOT_ROOT).replace(os.sep, "/")


def _enclosing_def(source: str, pos: int) -> str:
    """一致位置を囲っている関数名（見つからなければ空文字）。"""
    name = ""
    for match in _DEF_LINE.finditer(source, 0, pos):
        name = match.group(1)
    return name


def _scan_bot_wide_lookups() -> list[tuple[str, str, str, int]]:
    """`(相対パス, 囲っている関数名, API, 行番号)` の一覧を返す。"""
    found: list[tuple[str, str, str, int]] = []
    for path in _iter_source_files():
        rel = _rel(path)
        if not rel.startswith(SCAN_PREFIXES):
            continue
        with open(path, encoding="utf-8") as f:
            source = _code_only(f.read())
        for match in _BOT_WIDE_LOOKUP.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            found.append((rel, _enclosing_def(source, match.start()), match.group(1), line_no))
    return found


def test_scan_covers_the_intended_sources():
    """走査対象が空でないこと（テストが空振りしていないことの確認）。"""
    rels = [_rel(p) for p in _iter_source_files()]
    scanned = [r for r in rels if r.startswith(SCAN_PREFIXES)]
    assert len(scanned) > 10
    assert "cogs/schedule.py" in scanned
    assert "cogs/reminders.py" in scanned
    assert "utils/notify.py" in scanned


@pytest.mark.parametrize(
    "line",
    [
        "channel = self.bot.get_channel(int(schedule['channel_id']))",
        "channel = await self.bot.fetch_channel(cid)",
        "channel = interaction.client.get_channel(cid)",
        "channel = self._bot.get_channel(cid)",
        "getter = self.bot.get_channel",
        "channel = bot.get_partial_messageable(cid)",
    ],
)
def test_scanner_catches_bot_wide_lookups(line):
    """走査の語彙そのものを固定する（正規表現を弱める変更を検出する）。"""
    assert _BOT_WIDE_LOOKUP.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "channel = guild.get_channel_or_thread(cid)",
        "channel = guild.get_channel(cid)",
        "channel = guild_channel_by_id(self.bot, guild_id, cid)",
        "channel = robot.get_channel(cid)",
    ],
)
def test_scanner_ignores_guild_scoped_lookups(line):
    assert not _BOT_WIDE_LOOKUP.search(line)


def test_no_bot_wide_channel_lookup_in_cogs_services_utils():
    """チャンネル解決が bot 全体のキャッシュへ戻っていないこと。

    落ちたら `utils.notify.guild_channel` / `guild_channel_by_id` を使うこと。
    ギルドが解決できないときは**送らない**（誤送信よりログが出ないほうがましである）。
    """
    violations = [
        f"{rel}:{line} ({func or '<module>'}: {api})"
        for rel, func, api, line in _scan_bot_wide_lookups()
        if (rel, func, api) not in ALLOWED_BOT_WIDE_LOOKUPS
    ]
    assert not violations, (
        "bot 全体のキャッシュからチャンネルを引いています"
        "（他テナントへ流れます）:\n" + "\n".join(violations)
    )


def test_allowlisted_call_sites_still_exist():
    """許可リストが実態と一致していること（腐りの検出）。

    除外が消えた／増えたのに許可リストだけ残っていると、
    次の混入を素通りさせる穴になる。
    """
    allowed = Counter(
        (rel, func, api)
        for rel, func, api, _ in _scan_bot_wide_lookups()
        if (rel, func, api) in ALLOWED_BOT_WIDE_LOOKUPS
    )
    assert dict(allowed) == ALLOWED_BOT_WIDE_LOOKUPS


# =====================================================================
# 2. ヘルパ単体
# =====================================================================
class _Channel:
    def __init__(self, channel_id: int, guild=None):
        self.id = channel_id
        self.guild = guild
        self.mention = f"<#{channel_id}>"
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return SimpleNamespace(id=5000 + len(self.sent))


class _Guild:
    """`discord.Guild` の最小フェイク。**MagicMock を使わない。**

    MagicMock だと `get_channel_or_thread` が任意の ID に対して真を返し、
    クロスギルドのテストが素通りする。

    `get_channel` はスレッドを返さない（本物と同じ）。スレッドは
    `get_channel_or_thread` でだけ引ける。
    """

    def __init__(
        self,
        guild_id: int,
        channels: dict[int, _Channel] | None = None,
        threads: dict[int, _Channel] | None = None,
        members=None,
        roles=None,
    ):
        self.id = guild_id
        self.name = str(guild_id)
        self.emojis = []
        self._channels = channels or {}
        self._threads = threads or {}
        self._members = {m.id: m for m in members or []}
        self._roles = {r.id: r for r in roles or []}
        for channel in (*self._channels.values(), *self._threads.values()):
            channel.guild = self

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    def get_channel_or_thread(self, channel_id: int):
        return self._channels.get(channel_id) or self._threads.get(channel_id)

    def get_member(self, user_id: int):
        return self._members.get(user_id)

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_emoji(self, _emoji_id: int):
        return None


class _Bot:
    """`get_channel` / `fetch_channel` が**他ギルドのチャンネルを返す罠**の bot。

    実装が bot 全体の解決へ戻ると、他ギルドのチャンネルへ届いてしまい
    このファイルのテストが赤くなる。
    """

    def __init__(self, db, guilds, trap: _Channel | None = None):
        self.db = db
        self.guilds = list(guilds)
        self._trap = trap
        self.logged: list[tuple] = []

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == guild_id), None)

    def get_channel(self, _channel_id):
        return self._trap

    async def fetch_channel(self, _channel_id):
        return self._trap

    def get_cog(self, _name):
        return None

    async def log_to_channel(self, message, guild_id=None):
        self.logged.append((guild_id, message))


def _two_guilds(db=None):
    """G1 だけが SHARED_CHANNEL_ID を持ち、G2 は持たない状況。"""
    channel = _Channel(SHARED_CHANNEL_ID)
    g1 = _Guild(G1, {SHARED_CHANNEL_ID: channel})
    g2 = _Guild(G2, {})
    return channel, g1, g2, _Bot(db, [g1, g2], trap=channel)


def test_guild_channel_by_id_never_crosses_guilds():
    """G1 のチャンネル ID を G2 で引くと None（G1 で引けば取れる）。"""
    channel, _g1, _g2, bot = _two_guilds()
    assert guild_channel_by_id(bot, G1, SHARED_CHANNEL_ID) is channel
    assert guild_channel_by_id(bot, G2, SHARED_CHANNEL_ID) is None


def test_guild_channel_by_id_returns_none_when_guild_is_not_cached():
    """ギルドが見えないときは送らない（`bot.py` `_log_channel_for` と同じ判断）。

    チャンネルを持つ G1 はキャッシュにある。「引けなければ他のギルドを探す」
    変異がここで赤くなる。
    """
    channel = _Channel(SHARED_CHANNEL_ID)
    g1 = _Guild(G1, {SHARED_CHANNEL_ID: channel})
    bot = _Bot(None, [g1], trap=channel)
    assert guild_channel_by_id(bot, G2, SHARED_CHANNEL_ID) is None
    assert guild_channel_by_id(bot, G1, SHARED_CHANNEL_ID) is channel


def test_guild_channel_by_id_survives_broken_ids():
    """壊れた1行でループを止めない（DB の channel_id は TEXT 列）。"""
    g1 = _Guild(G1, {})
    bot = _Bot(None, [g1])
    for guild_id, channel_id in ((G1, "#general"), (G1, None), ("abc", 1), (None, 1)):
        assert guild_channel_by_id(bot, guild_id, channel_id) is None


def test_guild_channel_does_not_fall_back_to_get_channel():
    """`get_channel_or_thread` を持たないギルドで `get_channel` へ落ちない。

    本番の `discord.Guild` は `get_channel_or_thread` を必ず持つ。
    フェイク互換のために `get_channel` へ落ちる枝や「無ければ None」の枝を足すと、
    `get_channel` しか持たないフェイクが「チャンネルが引けない」理由で
    緑になる穴ができる。**AttributeError で赤くなること**を固定する。
    """
    calls: list[int] = []

    class _OldGuild:
        id = G1

        def get_channel(self, cid):
            calls.append(cid)
            return _Channel(SHARED_CHANNEL_ID)

    with pytest.raises(AttributeError):
        guild_channel(_OldGuild(), SHARED_CHANNEL_ID)
    assert calls == []


def test_guild_channel_resolves_threads():
    """スレッド（`get_channel_or_thread` でだけ引けるもの）を解決する。

    置換前の `bot.get_channel` はスレッドも解決していた。`guild.get_channel`
    へ寄せると、スレッド内で作った予定の通知が黙って消える。
    """
    thread = _Channel(THREAD_ID)
    guild = _Guild(G1, {}, threads={THREAD_ID: thread})
    assert guild_channel(guild, THREAD_ID) is thread
    assert guild_channel(guild, str(THREAD_ID)) is thread  # DB の TEXT 列


def test_guild_channel_rejects_unsendable_channels():
    """カテゴリ等（`send` を持たないもの）は送信先にしない。"""

    class _Category:
        id = SHARED_CHANNEL_ID

    guild = _Guild(G1, {})
    guild._channels[SHARED_CHANNEL_ID] = _Category()  # type: ignore[assignment]
    assert guild_channel(guild, SHARED_CHANNEL_ID) is None


# =====================================================================
# 3. 実挙動: 2ギルドが同じ channel_id を指している状況
# =====================================================================
def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


async def _connected_db():
    from utils.db import Database

    db = Database(_tmp_db_path())
    await db.connect()
    return db


@contextlib.contextmanager
def _legacy_env(db, **values):
    """env フォールバック相当の値を global config に立てる。

    DB（ギルド別 settings）には何も入れず、`.env` に残ったチャンネル ID が
    `config.for_guild()` で**全ギルドへ配られる**状況を作る。
    元の値へ必ず戻す（フルセットで他のテストへ漏らさない）。
    """
    from config import config as global_config

    names = ("_db", *values)
    saved = {name: getattr(global_config, name) for name in names}
    try:
        global_config._db = db
        for name, value in values.items():
            setattr(global_config, name, value)
        global_config.clear_guild_cache()
        yield global_config
    finally:
        for name, value in saved.items():
            setattr(global_config, name, value)
        global_config.clear_guild_cache()


def _reminders(bot):
    """リポジトリを作らずに Reminders を組み立てる（実装は本物を使う）。"""
    from cogs.reminders import Reminders

    cog = Reminders.__new__(Reminders)
    cog.bot = bot
    return cog


# ---------- reminders: _task_channel / _today_channel（危険度「高」） ----------
def test_task_channel_does_not_cross_guilds():
    """env フォールバックで両ギルドに同じ ID が配られても、他ギルドへ出さない。"""

    async def _main():
        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            with _legacy_env(db, default_task_channel_id=SHARED_CHANNEL_ID):
                cog = _reminders(bot)
                assert await cog._task_channel(G1) is channel, "自ギルドへは解決できること"
                assert await cog._task_channel(G2) is None, "他ギルドのチャンネルへ流れている"
        finally:
            await db.close()

    run(_main())


@pytest.mark.parametrize(
    "env",
    [
        # 今日のラベル通知先だけが他ギルドを指している
        {"today_label_channel_id": SHARED_CHANNEL_ID, "default_task_channel_id": None},
        # ラベル通知先は未設定で、タスク通知先（`today_channel_id` の既定）が指している
        {"today_label_channel_id": None, "default_task_channel_id": SHARED_CHANNEL_ID},
    ],
)
def test_today_channel_does_not_cross_guilds(env):
    """`today_channel_id` はラベル通知先 → タスク通知先の順に落ちる（config 側）。

    どちらから来た ID でも、他ギルドのチャンネルへは解決しない。
    """

    async def _main():
        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            with _legacy_env(db, **env):
                cog = _reminders(bot)
                assert await cog._today_channel(G1) is channel
                assert await cog._today_channel(G2) is None, "他ギルドのチャンネルへ流れている"
        finally:
            await db.close()

    run(_main())


# ---------- reminders: _purge_one の完了通知（危険度「中」） ----------
def test_purge_notice_does_not_cross_guilds():
    """データ削除の完了通知が他ギルドの bot-log へ出ないこと。

    `_purge_one` はチャンネル解決を `except Exception` で囲っている。
    **罠の send が呼ばれていないことを直接確かめる**（例外が握られて
    「何も起きない」のと区別がつく形にする）。
    """

    async def _main():
        from repositories.guild_repository import GuildRepository

        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            with _legacy_env(db, bot_log_channel_id=SHARED_CHANNEL_ID):
                cog = _reminders(bot)
                await cog._purge_one(G2, {"guild_id": G2, "left_at": "2026-01-01T00:00:00+09:00"})
                assert channel.sent == [], "他ギルドの bot-log へ削除通知が出ている"

                # 自ギルドへは届く（テストが「何も送られない」を見ていないことの確認）
                await cog._purge_one(G1, {"guild_id": G1, "left_at": None})
                assert len(channel.sent) == 1, "自ギルドへは届くこと"

            # 削除自体は通知の可否と無関係に行われる
            assert await GuildRepository(db).list_purge_due() == []
        finally:
            await db.close()

    run(_main())


# ---------- reminders: マイルストーン警告（危険度「低」） ----------
async def _seed_behind_milestone(db, guild_id: int) -> None:
    """そのギルドに「遅れているマイルストーン」を1件作り、通知先を SHARED にする。"""
    from repositories.progress_repository import ProgressRepository
    from repositories.settings_repository import SettingsRepository

    await SettingsRepository(db).set(
        guild_id, "PROGRESS_DEFAULT_CHANNEL_ID", str(SHARED_CHANNEL_ID)
    )
    prog = ProgressRepository(db)
    await prog.upsert_node(
        guild_id, "wing", name="主翼", manual_progress=0.1, now_text="2026-07-13 10:00"
    )
    await db.execute(
        "UPDATE progress_nodes SET created_at = '2026-07-13 10:00',"
        " updated_at = '2026-08-12 10:00' WHERE guild_id = ?",
        (guild_id,),
    )
    await prog.add_milestone(guild_id, "wing", "接着完了", "2026-08-22", "2026-08-12 10:00")


def test_milestone_alert_does_not_cross_guilds():
    """危険度「低」（ギルド別 settings 由来）も同じ形に揃っていること。

    G2 の settings が「G1 のチャンネル」を指してしまっている状態。
    `run_milestone_alerts` は per-guild で `except Exception` を握るので、
    **罠の send が呼ばれていないこと**を直接見る。
    """

    async def _main():
        from cogs.reminders import MILESTONE_ALERT_TYPE
        from repositories.reminders_log_repository import RemindersLogRepository
        from utils.parser import TZ

        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            await _seed_behind_milestone(db, G1)
            await _seed_behind_milestone(db, G2)
            with _legacy_env(db):
                cog = _reminders(bot)
                cog.log_repo = RemindersLogRepository(db)
                current = datetime(2026, 8, 12, 8, 30, tzinfo=TZ)

                sent = await cog._alert_milestones(G2, current, "2026-W33")
                assert channel.sent == [], "他ギルドのチャンネルへ遅延警告が出ている"
                assert sent == 0
                # 部員には沈黙するが、運用者には見える形で残す（ADR 0023）
                assert any(gid == G2 for gid, _ in bot.logged)
                assert not await cog.log_repo.exists(G2, MILESTONE_ALERT_TYPE, "milestone:2026-W33")

                # 同じ条件で自ギルドからなら届く（空振りしていないことの確認）
                assert await cog._alert_milestones(G1, current, "2026-W33") == 1
                assert len(channel.sent) == 1, "自ギルドへは届くこと"
        finally:
            await db.close()

    run(_main())


# ---------- progress: push_project_tasks（危険度「高」） ----------
class _Todoist:
    enabled = True

    async def get_projects(self):
        return [SimpleNamespace(id="p1", name="主翼")]

    async def get_tasks(self, project_id=None):
        from utils.parser import now

        due = SimpleNamespace(date=now().date() + timedelta(days=1))
        return [SimpleNamespace(id="t1", content="リブ切り出し", priority=1, due=due)]


def test_project_task_notice_does_not_cross_guilds():
    """紐付けに通知先が無く、env のタスク通知先へ落ちる経路。"""

    async def _main():
        from cogs.progress import Progress
        from repositories.progress_repository import ProgressRepository

        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            todoist = _Todoist()

            async def _for_guild(_guild_id):
                return todoist

            bot.todoist_manager = SimpleNamespace(for_guild=_for_guild)
            repo = ProgressRepository(db)
            for gid in (G1, G2):
                await repo.upsert_node(gid, "wing", name="主翼", now_text="2026-08-11 10:00")
                # notify_channel_id を持たない紐付け → 既定 → タスク通知先（env）
                await repo.upsert_todoist_link(gid, "主翼", "wing", "2026-08-11 10:00")

            with _legacy_env(db, default_task_channel_id=SHARED_CHANNEL_ID):
                cog = Progress(bot)
                assert await cog.push_project_tasks(G2) == 0
                assert channel.sent == [], "他ギルドのチャンネルへ期限タスクが出ている"
                assert any(gid == G2 for gid, _ in bot.logged), "運用者向けに残すこと"

                assert await cog.push_project_tasks(G1) == 1
                assert len(channel.sent) == 1, "自ギルドへは届くこと"
        finally:
            await db.close()

    run(_main())


# ---------- schedule: create の投稿先（危険度「高」） ----------
class _Interaction:
    def __init__(self, guild, channel):
        self.guild = guild
        self.channel = channel
        self.user = SimpleNamespace(id=501, display_name="tester")
        self.sent: list[dict] = []
        self.response = SimpleNamespace(defer=self._defer, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self, *args, **kwargs):
        return None

    async def _send(self, *args, **kwargs):
        self.sent.append(kwargs)


async def _create(cog, interaction):
    from cogs.schedule import Schedule

    # 候補は締切より後（H1-5 の「候補 < 締切」拒否に巻き込まれないため）
    await Schedule.create.callback(
        cog,
        interaction,
        title="定例会",
        options="2099-01-02 10:00",
        deadline="2099-01-01 00:00",
    )


def test_schedule_create_does_not_post_to_another_guild():
    """env の既定投稿先が他ギルドのチャンネルなら、作らずにエラーで返す。

    **実行したチャンネル（`interaction.channel`）へも落とさない。**
    設定されているのに引けないとき「たぶんこれだろう」で投稿しない。
    """

    async def _main():
        from cogs.schedule import Schedule
        from repositories.schedule_repository import ScheduleRepository

        db = await _connected_db()
        try:
            channel = _Channel(SHARED_CHANNEL_ID)
            g1_here = _Channel(G2_OWN_CHANNEL_ID + 1)
            g2_here = _Channel(G2_OWN_CHANNEL_ID)
            g1 = _Guild(G1, {SHARED_CHANNEL_ID: channel, g1_here.id: g1_here})
            g2 = _Guild(G2, {G2_OWN_CHANNEL_ID: g2_here})
            bot = _Bot(db, [g1, g2], trap=channel)
            cog = Schedule(bot)
            repo = ScheduleRepository(db)

            with _legacy_env(db, default_schedule_channel_id=SHARED_CHANNEL_ID):
                interaction = _Interaction(g2, g2_here)
                await _create(cog, interaction)

                assert channel.sent == [], "他ギルドのチャンネルへ投票が投稿されている"
                assert g2_here.sent == [], "設定を無視して実行チャンネルへ投稿している"
                assert await repo.list_all(G2) == [], "投稿できないのに予定を作っている"
                embed = interaction.sent[-1]["embed"]
                assert "投稿先チャンネルが特定できません" in (embed.description or "")

                # 同じ設定で自ギルドから実行すれば、既定の投稿先へ投稿される
                interaction = _Interaction(g1, g1_here)
                await _create(cog, interaction)
                assert channel.sent, "自ギルドの既定チャンネルへは投稿されること"
                assert g1_here.sent == []
                assert len(await repo.list_all(G1)) == 1
        finally:
            await db.close()

    run(_main())


# ---------- schedule: finalize_schedule（危険度「低」） ----------
async def _seed_schedule(repo, guild_id: int, schedule_id: str, target_role_id=None):
    await repo.create_schedule(
        guild_id,
        schedule_id,
        "定例会",
        None,
        None,
        target_role_id,
        "2026-01-01T00:00:00+09:00",
        "u1",
        str(SHARED_CHANNEL_ID),
    )
    return dict(await repo.get_schedule(guild_id, schedule_id))


def test_schedule_finalize_does_not_post_to_another_guild():
    """締切サマリーが他ギルドのチャンネルへ出ないこと。"""

    async def _main():
        from cogs.schedule import Schedule
        from repositories.schedule_repository import ScheduleRepository

        db = await _connected_db()
        try:
            channel, _g1, _g2, bot = _two_guilds(db)
            repo = ScheduleRepository(db)
            with _legacy_env(db):
                cog = Schedule(bot)
                await cog.finalize_schedule(await _seed_schedule(repo, G2, "s-g2"))
                assert channel.sent == [], "他ギルドのチャンネルへ締切サマリーが出ている"
                # 予定自体は閉じる（通知の可否と無関係）
                assert (await repo.get_schedule(G2, "s-g2"))["closed_flag"] == 1

                await cog.finalize_schedule(await _seed_schedule(repo, G1, "s-g1"))
                assert len(channel.sent) == 1, "自ギルドへは届くこと"
        finally:
            await db.close()

    run(_main())


# ---------- schedule: notify_unanswered の DM 不可フォールバック（危険度「低」） ----------
class _Member:
    def __init__(self, user_id: int):
        self.id = user_id
        self.bot = False
        self.mention = f"<@{user_id}>"

    async def send(self, _text):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="dm closed"), "denied")


def test_unanswered_fallback_does_not_mention_in_another_guild():
    """DM を拒否した部員へのメンションが、他ギルドのチャンネルへ出ないこと。

    他サーバーの人に、見知らぬ部員のメンションと予定名が流れる経路。
    """

    async def _main():
        from cogs.schedule import Schedule
        from repositories.schedule_repository import ScheduleRepository

        db = await _connected_db()
        try:
            role_id = 4242
            channel = _Channel(SHARED_CHANNEL_ID)
            m1, m2 = _Member(11), _Member(22)
            g1 = _Guild(
                G1,
                {SHARED_CHANNEL_ID: channel},
                members=[m1],
                roles=[SimpleNamespace(id=role_id, members=[m1])],
            )
            g2 = _Guild(G2, {}, members=[m2], roles=[SimpleNamespace(id=role_id, members=[m2])])
            bot = _Bot(db, [g1, g2], trap=channel)
            repo = ScheduleRepository(db)
            with _legacy_env(db):
                cog = Schedule(bot)
                assert (
                    await cog.notify_unanswered(
                        await _seed_schedule(repo, G2, "s-g2", str(role_id))
                    )
                    == 1
                )
                assert channel.sent == [], "他ギルドのチャンネルで部員をメンションしている"

                assert (
                    await cog.notify_unanswered(
                        await _seed_schedule(repo, G1, "s-g1", str(role_id))
                    )
                    == 1
                )
                assert len(channel.sent) == 1, "自ギルドへはフォールバックで届くこと"
                assert m1.mention in channel.sent[0]["content"]
        finally:
            await db.close()

    run(_main())
