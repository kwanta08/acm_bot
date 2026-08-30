"""
日程調整ロジック（仕様 11.2）。

リアクション集計、Embed 生成、締切処理を担う。
リアクション絵文字と投票状態の対応:
  ok = 参加 / ng = 不参加 / maybe = 未定
"""

from __future__ import annotations

import uuid
from typing import Any

import discord

from repositories.schedule_repository import ScheduleRepository
from utils.embeds import MAX_EMBED_FIELDS, add_truncation_note, schedule_embed
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso

log = get_logger("schedule_service")

DEFAULT_STATUS_TO_EMOJI = {
    "ok": "✅",
    "maybe": "❓",
    "ng": "❌",
}


def new_schedule_id() -> str:
    return uuid.uuid4().hex[:12]


def new_option_id() -> str:
    return uuid.uuid4().hex[:12]


def parse_options(options_str: str) -> list[str]:
    """`;` 区切りの候補日時文字列を分割する（仕様 11.2.2）。"""
    return [p.strip() for p in options_str.split(";") if p.strip()]


def select_unanswered_targets(
    *,
    role_member_ids: set[str] | None,
    roster_active_ids: set[str],
    roster_retired_ids: set[str],
    answered_ids: set[str],
) -> set[str] | None:
    """催促の対象になるユーザー ID を返す（純関数）。

    - ``role_member_ids`` は **「対象ロール指定なし」を None** で表す。
      ロールを解決できなかった場合（ギルド不可視・ロール削除済み）と、
      **ロールは解決できたが保持者が0名の場合**は、呼び出し側がこの関数を
      呼ぶ前に「特定できない」を返すこと。空集合を渡すとこの関数は
      空集合（＝未回答0名）を返すので、偽の 0 になる
    - 戻り値 ``None`` は「対象を特定できない」。空集合は「対象は特定でき、
      未回答が0名」。**0 と None を混ぜない**（0 は「全員回答済み」という
      主張になる。ADR 0021 / 0022）

    対象ロールがあるときは、ロール保持者から **名簿で退部・休止と分かって
    いる人だけ** を差し引く。名簿に無い人は「退部か未登録か区別できない」
    ので残す。積集合にすると、``/member register`` がまだ進んでいない
    ギルドで今日届いている催促が止まる（ADR 0024）。

    ID は TEXT 列（名簿）と int（discord.Member.id）が混ざるため、
    ここで文字列へ正規化する。
    """

    def _norm(ids) -> set[str]:
        return {str(i) for i in ids}

    answered = _norm(answered_ids)
    if role_member_ids is None:
        candidates = _norm(roster_active_ids)
        if not candidates:
            # 対象ロールも名簿も無い。誰が回答すべきかを知る手段がない
            return None
    else:
        candidates = _norm(role_member_ids) - _norm(roster_retired_ids)
    return candidates - answered


def get_schedule_emojis(gconf, guild: discord.Guild | None = None) -> dict[str, Any]:
    """スケジュール用絵文字を返す（ステータス → 絵文字）。

    ギルド別設定（gconf.schedule_emoji_*_id。DB > 環境変数の順で解決済み）の
    カスタム絵文字 ID を guild.get_emoji() で実在検証して使う。
    未設定・検証不能（設定後にサーバーから削除された等）の場合は
    既定絵文字（✅❓❌）へフォールバックし、警告ログを残す。
    guild.get_emoji() は animated フラグ込みの discord.Emoji を返すため、
    アニメーション絵文字でもそのままリアクション付与できる。
    """
    resolved: dict[str, Any] = {}
    mapping = {
        "ok": getattr(gconf, "schedule_emoji_ok_id", None),
        "maybe": getattr(gconf, "schedule_emoji_maybe_id", None),
        "ng": getattr(gconf, "schedule_emoji_ng_id", None),
    }
    for status, emoji_id in mapping.items():
        emoji = None
        if emoji_id and guild is not None:
            emoji = guild.get_emoji(emoji_id)
            if emoji is None:
                log.warning(
                    "設定された絵文字が見つかりません"
                    " (status=%s, emoji_id=%s, guild=%s)。"
                    "既定絵文字へフォールバックします",
                    status,
                    emoji_id,
                    getattr(guild, "id", "?"),
                )
        resolved[status] = emoji or DEFAULT_STATUS_TO_EMOJI[status]
    return resolved


def emoji_key(emoji: Any) -> str:
    """集計用のキー（カスタム絵文字は ID、Unicode 絵文字はそのもの）。"""
    emoji_id = getattr(emoji, "id", None)
    return str(emoji_id) if emoji_id else str(emoji)


def build_emoji_maps(gconf, guild: discord.Guild | None = None) -> dict:
    status_to_emoji = get_schedule_emojis(gconf, guild)
    emoji_to_status = {}
    all_emojis = []

    for status, emoji in status_to_emoji.items():
        all_emojis.append(emoji)
        emoji_to_status[emoji_key(emoji)] = status

    return {
        "status_to_emoji": status_to_emoji,
        "emoji_to_status": emoji_to_status,
        "all_emojis": all_emojis,
    }


async def count_unanswered(
    repo: ScheduleRepository,
    guild_id: int,
    schedule: dict[str, Any],
    guild: discord.Guild | None,
    roster_active_ids: set[str],
    roster_retired_ids: set[str],
) -> int | None:
    """予定単位の未回答者数。特定できないときは None（G4-12）。

    **`cogs/schedule.py` の `notify_unanswered` と同じ母集団・同じ単位**にする。
    部員が最初に見る数字は投票メッセージのこれなので、
    実際に DM が飛ぶ相手と食い違うと「催促されたのに未回答は0人」になる。

    None は「対象を特定できない」（ロール削除済み・保持者0名・名簿も空）。
    0 は「対象は特定でき、未回答が0名」。**この2つを混ぜない**（ADR 0021 / 0022）。
    """
    role_member_ids: set[str] | None = None
    if schedule.get("target_role_id"):
        if guild is None:
            return None
        try:
            role = guild.get_role(int(schedule["target_role_id"]))
        except (TypeError, ValueError):
            return None
        if role is None:
            return None
        role_member_ids = {str(m.id) for m in role.members if not m.bot}
        if not role_member_ids:
            # ロールは生きているのに保持者が見えない。0 とは主張しない
            # （notify_unanswered と同じ判断）
            return None
    answered = await repo.list_voters_for_schedule(guild_id, schedule["schedule_id"])
    targets = select_unanswered_targets(
        role_member_ids=role_member_ids,
        roster_active_ids=roster_active_ids,
        roster_retired_ids=roster_retired_ids,
        answered_ids=answered,
    )
    return None if targets is None else len(targets)


async def build_option_embed(
    repo: ScheduleRepository,
    guild_id: int,
    bot: discord.Client,
    schedule: dict[str, Any],
    option: dict[str, Any],
    guild: discord.Guild | None,
    *,
    roster_active_ids: set[str] | None = None,
    roster_retired_ids: set[str] | None = None,
) -> discord.Embed:
    """候補日程1件分の投票状況 Embed を生成する（仕様 11.2.4）。

    `guild_id` を**明示引数で受ける**（ADR 0009 の完了条件2）。
    以前は `repo.for_guild(guild_id)` のプロキシを渡していた。

    未回答者数は**予定単位**で、`notify_unanswered` と同じ母集団を使う（G4-12）。
    名簿（`roster_*_ids`）を渡さないと母集団を決められないので、
    渡されなかった場合は「特定できない」＝ `-` を表示する。
    """
    votes = await repo.list_votes(guild_id, option["option_id"])
    ok_users, ng_users, maybe_users = [], [], []
    for v in votes:
        name = await _resolve_name(bot, guild, v["user_id"])
        if v["status"] == "ok":
            ok_users.append(name)
        elif v["status"] == "ng":
            ng_users.append(name)
        elif v["status"] == "maybe":
            maybe_users.append(name)

    target_role_name = "名簿の現役"
    if schedule.get("target_role_id") and guild:
        role = guild.get_role(int(schedule["target_role_id"]))
        if role:
            target_role_name = role.name

    # **未回答者数は候補単位ではなく予定単位。** 候補ごとに数えると
    # 「3候補のうち1つに答えた人」が未回答として出る（G4-12）
    unanswered_count = "-"
    if roster_active_ids is not None and roster_retired_ids is not None:
        count = await count_unanswered(
            repo, guild_id, schedule, guild, roster_active_ids, roster_retired_ids
        )
        if count is not None:
            unanswered_count = str(count)

    embed = schedule_embed(f"【日程調整】{schedule['title']}")
    embed.add_field(name="候補日時", value=option["label"], inline=False)
    if schedule.get("place"):
        embed.add_field(name="場所", value=schedule["place"], inline=True)
    embed.add_field(name="締切", value=fmt_jp(from_iso(schedule["deadline"])), inline=True)
    embed.add_field(name="対象", value=target_role_name, inline=True)
    embed.add_field(name=f"参加 ({len(ok_users)})", value="\n".join(ok_users) or "—", inline=True)
    embed.add_field(name=f"不参加 ({len(ng_users)})", value="\n".join(ng_users) or "—", inline=True)
    embed.add_field(
        name=f"未定 ({len(maybe_users)})", value="\n".join(maybe_users) or "—", inline=True
    )
    embed.add_field(name="未回答者数（この予定）", value=unanswered_count, inline=True)
    if schedule.get("description"):
        embed.add_field(name="説明", value=schedule["description"], inline=False)
    return embed


async def _resolve_name(bot: discord.Client, guild: discord.Guild | None, user_id: str) -> str:
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            return member.display_name
    user = bot.get_user(int(user_id))
    if user:
        return user.display_name
    return f"<@{user_id}>"


async def build_summary_embed(
    repo: ScheduleRepository,
    guild_id: int,
    bot: discord.Client,
    schedule: dict[str, Any],
    guild: discord.Guild | None,
) -> discord.Embed:
    """締切後の結果要約 Embed（仕様 11.2.5）。

    `guild_id` を明示引数で受ける（ADR 0009 の完了条件2）。

    **候補は inline field で横に並べる**（Discord は最大3列/行。
    縦積みだと候補が多い予定で「どの日が良いか」の比較ができない）。
    場所・締切を field にすると先頭の候補が同じ行に混ざって列がずれるため、
    description 側に置く。value は3列時の狭い列幅でも読めるよう
    状態ごとに1行（`参加 N: 名前…`）にする。
    """
    options = await repo.list_options(guild_id, schedule["schedule_id"])
    embed = schedule_embed(f"【締切】{schedule['title']} 集計結果")

    best_label = None
    best_ok = -1
    for index, opt in enumerate(options):
        votes = await repo.list_votes(guild_id, opt["option_id"])

        ok_users, ng_users, maybe_users = [], [], []
        for v in votes:
            name = await _resolve_name(bot, guild, v["user_id"])
            if v["status"] == "ok":
                ok_users.append(name)
            elif v["status"] == "ng":
                ng_users.append(name)
            elif v["status"] == "maybe":
                maybe_users.append(name)

        # 最多参加の判定は**全候補**で行う。表示は 25 field で打ち切っても、
        # 人に見せる集計値を打ち切った分だけで出さない
        if len(ok_users) > best_ok:
            best_ok = len(ok_users)
            best_label = opt["label"]

        # 26 件目以降を add_field すると送信時に HTTPException(400) になり、
        # finalize_schedule が握り潰すため集計サマリーごと無言で消える
        if index >= MAX_EMBED_FIELDS:
            continue

        detail_lines = []
        for status_label, users in (("参加", ok_users), ("不参加", ng_users), ("未定", maybe_users)):
            line = f"{status_label} {len(users)}"
            if users:
                line += f": {', '.join(users)}"
            detail_lines.append(line)

        embed.add_field(name=opt["label"], value="\n".join(detail_lines), inline=True)

    # 「結局いつに決まったのか」を残す（G3-4）。
    #
    # **field ではなく description に足す。** 候補数に上限が無いので、
    # field を1つ増やすと上限25に当たる閾値が下がり、候補の多い予定で
    # 表示できる候補が減る。
    #
    # このサマリーは公開チャンネルへ出るので、L1 の部員に実行できない
    # コマンドを命令しない（主語を書く）。
    lines: list[str] = []
    if schedule.get("place"):
        lines.append(f"場所: {schedule['place']}")
    lines.append(f"締切: {fmt_jp(from_iso(schedule['deadline']))}")
    if best_label:
        lines.append(f"最多参加候補: **{best_label}**（{best_ok}名）")

    confirmed_id = schedule.get("confirmed_option_id")
    if confirmed_id:
        confirmed = next((o for o in options if str(o["option_id"]) == str(confirmed_id)), None)
        if confirmed is not None:
            try:
                when = fmt_jp(from_iso(str(confirmed["start_at"])))
            except (TypeError, ValueError, KeyError):
                when = str(confirmed.get("label") or "?")
            lines.append(f"確定した日程: **{when}**")
    elif options:
        lines.append("班長以上が `/schedule confirm` で確定した日程を登録します。")

    if lines:
        embed.description = "\n".join(lines)
    add_truncation_note(
        embed, len(options), MAX_EMBED_FIELDS, "最多参加候補は全候補から算出しています"
    )
    return embed
