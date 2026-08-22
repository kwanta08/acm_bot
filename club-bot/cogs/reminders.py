"""
Reminders モジュール（仕様 11.5）。

全自動通知を統括する。discord.ext.tasks で定期実行する。
ジョブ一覧（仕様 11.5.1）:
  - Schedule 締切前催促: 締切1時間前 → 未回答者へ通知
  - Schedule 自動締切: 5分ごと → 締切済み投票を終了
  - Task 7日以内期限通知: 毎日08:00
  - Task 今日やること通知: 毎日08:00
  - Task 超過通知: 毎日21:00
通知失敗の扱い（11.5.2）: DM 失敗→チャンネル、API 障害→#bot-log、多重送信防止。

マルチテナント版: 各ループは「参加中の全ギルド」を対象にギルドごと処理する。
送信先チャンネル・班マップはギルド別設定から解決する。
"""

from __future__ import annotations

from datetime import time, timedelta
from itertools import groupby

import discord
from discord.ext import commands, tasks

from config import config
from repositories.guild_repository import GuildRepository
from repositories.member_repository import MemberRepository
from repositories.progress_repository import ProgressRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.section_repository import SectionRepository
from repositories.task_repository import TaskRepository
from services.milestone_service import days_until_competition, evaluate_all
from services.progress_sync_service import resolve_default_channel_id
from services.progress_tree import load_tree
from utils.embeds import task_embed
from utils.logger import get_logger
from utils.parser import TZ, from_iso, now, to_iso

log = get_logger("reminders")

# 遅延マイルストーンの週次通知（0 = 月曜）。reminders_log の種別名も兼ねる
MILESTONE_ALERT_WEEKDAY = 0
MILESTONE_ALERT_TYPE = "milestone_alert"

PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}
PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🔵", 1: "⚪"}
PRIORITY_P_LABEL = {4: "P1", 3: "P2", 2: "P3", 1: "P4"}


def _relative_day_label(due_date, today) -> str:
    diff = (due_date - today).days
    if diff < 0:
        return f"{-diff}日超過"
    if diff == 0:
        return "今日"
    if diff == 1:
        return "明日"
    return f"{diff}日後"


def _todoist_task_url(task_id: str) -> str:
    return f"https://app.todoist.com/app/task/{task_id}"


def _channel_id_of(info: dict) -> int | None:
    """班情報のチャンネル ID を int で返す。未設定・不正値は None。

    teams.channel_id は TEXT 列で、旧データや手入力で数字以外が入りうる。
    int() を直に呼ぶと通知ループが例外で停止するため、ここで吸収する。
    """
    raw = str(info.get("channel_id") or "").strip()
    return int(raw) if raw.isdigit() else None


def _build_grouped_description(
    period_start, period_end, period_desc: str, items: list[dict]
) -> str:
    """
    items: [{"due_date": date, "title": str, "priority": int,
            "url": str | None, "category": str}]
    """
    today = now().date()
    lines = [
        f"対象期間: {period_start.isoformat()} 〜 {period_end.isoformat()}（{period_desc}）",
        "",
    ]
    items_sorted = sorted(items, key=lambda x: x["due_date"])
    for due_date, group in groupby(items_sorted, key=lambda x: x["due_date"]):
        lines.append(f"📅{due_date.isoformat()}（{_relative_day_label(due_date, today)}）")
        for it in group:
            emoji = PRIORITY_EMOJI.get(it.get("priority") or 1, "⚪")
            p_label = PRIORITY_P_LABEL.get(it.get("priority") or 1, "P4")
            line = f"　{emoji}[{p_label}] {it['title']}"
            if it.get("url"):
                line += f" （[開く]({it['url']})）"
            lines.append(line)
            lines.append(f"　　📂 {it['category']}")
        lines.append("")
    return "\n".join(lines).rstrip()


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.schedule_repo = ScheduleRepository(bot.db)
        self.task_repo = TaskRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)
        self.section_repo = SectionRepository(bot.db)
        self.log_repo = RemindersLogRepository(bot.db)

    async def cog_load(self):
        # 起動時にループを開始
        self.schedule_tick.start()
        self.daily_morning.start()
        self.daily_night.start()
        self.daily_purge.start()
        self.weekly_milestone_alert.start()

    async def cog_unload(self):
        self.schedule_tick.cancel()
        self.daily_morning.cancel()
        self.daily_night.cancel()
        self.daily_purge.cancel()
        self.weekly_milestone_alert.cancel()

    # ---------- 5分ごと: 締切前催促 + 自動締切 ----------
    @tasks.loop(minutes=5)
    async def schedule_tick(self):
        for guild in list(self.bot.guilds):
            # ギルド単位で握る。discord.ext.tasks は未処理例外でループ自体を
            # 停止するため、1ギルドの失敗が全ギルドの自動通知を永久に止める
            try:
                await self._process_schedule_reminders(guild.id)
                await self._process_schedule_close(guild.id)
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("日程調整の定期処理に失敗 (guild=%s): %s", guild.id, type(e).__name__)

    @schedule_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    async def _process_schedule_reminders(self, guild_id: int):
        """締切1時間前の未回答者催促（多重送信防止フラグ付き）。"""
        current = now()
        window_end = current + timedelta(hours=1)
        try:
            candidates = await self.schedule_repo.list_reminder_candidates(
                guild_id, to_iso(current), to_iso(window_end)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("催促候補取得失敗 (guild=%s): %s", guild_id, e)
            return
        schedule_cog = self.bot.get_cog("Schedule")
        if not schedule_cog:
            return
        for s in candidates:
            try:
                count = await schedule_cog.notify_unanswered(s)
                if count is None:
                    # 対象ロール未設定などで催促できない。送信済みフラグを
                    # 立てない（立てると後からロールを付けても永久に再送
                    # されない。G2-3）。ウィンドウ内は毎tick ここへ来るが、
                    # skipped の記録だけで送信は発生しない
                    await self._log_reminder(
                        guild_id,
                        "schedule_unanswered",
                        s["schedule_id"],
                        None,
                        None,
                        "skipped",
                        "対象ロール未設定のため未回答者を特定できません",
                    )
                    log.info(
                        "締切前催促をスキップ: %s（対象ロール未設定, guild=%s）",
                        s["title"],
                        guild_id,
                    )
                    continue
                await self.schedule_repo.mark_reminder_sent(guild_id, s["schedule_id"])
                await self._log_reminder(
                    guild_id, "schedule_unanswered", s["schedule_id"], None, None, "success"
                )
                log.info("締切前催促: %s（%d名, guild=%s）", s["title"], count, guild_id)
            except Exception as e:  # noqa: BLE001
                await self._log_reminder(
                    guild_id, "schedule_unanswered", s["schedule_id"], None, None, "failed", str(e)
                )
                await self.bot.log_to_channel(
                    f"[Reminder] 催促失敗 {s['schedule_id']}: {e}", guild_id=guild_id
                )

    async def _process_schedule_close(self, guild_id: int):
        """締切を過ぎた投票を自動クローズ。"""
        try:
            due = await self.schedule_repo.list_due_schedules(guild_id, to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("締切候補取得失敗 (guild=%s): %s", guild_id, e)
            return
        schedule_cog = self.bot.get_cog("Schedule")
        if not schedule_cog:
            return
        for s in due:
            try:
                await schedule_cog.finalize_schedule(s)
                log.info("自動締切: %s (guild=%s)", s["title"], guild_id)
            except Exception as e:  # noqa: BLE001
                await self.bot.log_to_channel(
                    f"[Reminder] 自動締切失敗 {s['schedule_id']}: {e}", guild_id=guild_id
                )

    # ---------- 毎朝 08:30: タスク通知 ----------
    @tasks.loop(time=time(hour=8, minute=30, tzinfo=TZ))
    async def daily_morning(self):
        for guild in list(self.bot.guilds):
            # 各ジョブを個別に握る。1つの失敗で同じギルドの残りのジョブや
            # 他ギルドの通知、ループ自体を止めない
            for label, job in (
                ("7日以内タスク通知", self._notify_due_within_7days),
                ("今日やること通知", self._notify_today_label),
                # Todoist セクション別の期限7日以内/超過タスクを各班チャンネルへ
                ("セクション別通知", self.push_section_tasks),
            ):
                try:
                    await job(guild.id)
                except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                    log.warning("%s に失敗 (guild=%s): %s", label, guild.id, type(e).__name__)

    @daily_morning.before_loop
    async def _before_morning(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=21, minute=0, tzinfo=TZ))
    async def daily_night(self):
        today = now().date()
        for guild in list(self.bot.guilds):
            try:
                tasks_ = await self.task_repo.list_overdue(guild.id, today.isoformat())
            except Exception as e:  # noqa: BLE001
                log.warning("超過タスク取得失敗 (guild=%s): %s", guild.id, e)
                continue
            if not tasks_:
                continue
            try:
                await self._dispatch_by_team(
                    guild.id,
                    tasks_,
                    title="⚠️【期限超過タスク】対応をお願いします",
                    reminder_type="task_overdue",
                    period_desc="期限超過",
                    period_start=today,
                    period_end=today,
                )
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("超過タスク通知失敗 (guild=%s): %s", guild.id, type(e).__name__)

    @daily_night.before_loop
    async def _before_night(self):
        await self.bot.wait_until_ready()

    # ---------- 毎週月曜 08:30: 遅延マイルストーンの通知 ----------
    @tasks.loop(time=time(hour=8, minute=30, tzinfo=TZ))
    async def weekly_milestone_alert(self):
        # tasks.loop(time=...) は毎日発火するので曜日で絞る
        if now().weekday() != MILESTONE_ALERT_WEEKDAY:
            return
        await self.run_milestone_alerts()

    @weekly_milestone_alert.before_loop
    async def _before_milestone_alert(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def week_key(current) -> str:
        """ISO 週のキー（例 2026-W33）。同じ週の二重送信を防ぐのに使う。"""
        iso = current.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    async def run_milestone_alerts(self, now_dt=None) -> dict[int, int]:
        """遅延しているマイルストーンがあるギルドにだけ通知する。

        **遅れが無いときは沈黙する**（毎週「問題ありません」を送ると
        通知が読まれなくなるため）。1ギルドの失敗は他ギルドを止めない。
        """
        current = now_dt or now()
        key = self.week_key(current)
        sent: dict[int, int] = {}
        for guild in list(self.bot.guilds):
            try:
                count = await self._alert_milestones(guild.id, current, key)
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("マイルストーン通知に失敗 (guild=%s): %s", guild.id, type(e).__name__)
                continue
            if count:
                sent[guild.id] = count
        return sent

    async def _alert_milestones(self, guild_id: int, current, week_key: str) -> int:
        target_id = f"milestone:{week_key}"
        if await self.log_repo.exists(guild_id, MILESTONE_ALERT_TYPE, target_id):
            return 0

        repo = ProgressRepository(self.bot.db)
        rows = await repo.list_milestones(guild_id)
        if not rows:
            return 0
        tree = await load_tree(repo, guild_id)
        statuses = evaluate_all(tree, rows, today=current.date())
        behind = [s for s in statuses if s.is_behind]
        if not behind:
            return 0

        gconf = await config.for_guild(guild_id)
        channel_id = await resolve_default_channel_id(self.bot.db, guild_id)
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is None:
            # 部員への通知は送らない（ADR 0023: 送り先が無いギルドは沈黙する）が、
            # 「遅延はあるのに届いていない」ことは運用者に見える形で残す。
            log.info("マイルストーン通知の送信先が無い (guild=%s)", guild_id)
            await self.bot.log_to_channel(
                "[マイルストーン] 遅れている節目がありますが、通知先チャンネルが"
                "設定されていないため送信できませんでした。"
                "`/setup` の進捗チャンネル、または `/set_channel` で設定してください。",
                guild_id=guild_id,
            )
            return 0

        left = days_until_competition(gconf.competition_date, current.date())
        head = f"大会まで残り {left} 日。" if left is not None and left >= 0 else ""
        lines = [
            f"・**{s.node_name}: {s.name}** — "
            + (
                f"期限まで {s.days_left} 日"
                if s.days_left > 0
                else "本日が期限"
                if s.days_left == 0
                else f"{-s.days_left} 日超過"
            )
            + f" / 進捗 {s.progress * 100:.0f}%"
            for s in behind[:20]
        ]
        if len(behind) > 20:
            lines.append(f"…ほか {len(behind) - 20} 件")
        embed = task_embed(
            "⚠️ 遅れているマイルストーン", f"{head}遅延 **{len(behind)} 件**\n\n" + "\n".join(lines)
        )

        await self._safe_send(guild_id, channel, embed=embed)
        await self._log_reminder(
            guild_id, MILESTONE_ALERT_TYPE, target_id, None, str(channel_id), "sent"
        )
        return len(behind)

    # ---------- 毎日 04:00: 期限切れギルドのデータ削除 ----------
    @tasks.loop(time=time(hour=4, minute=0, tzinfo=TZ))
    async def daily_purge(self):
        await self.run_purge()

    @daily_purge.before_loop
    async def _before_purge(self):
        await self.bot.wait_until_ready()

    async def run_purge(self, now_dt=None) -> dict[int, dict[str, int]]:
        """削除予定日時を過ぎたギルドのデータを全テーブルから削除する。

        対象は「退出して猶予を過ぎたサーバー」と「/data delete で
        自分から削除を申告したサーバー」。**唯一の破壊的な定期処理**なので、
        1つのギルドの失敗が他ギルドの削除を止めないよう個別に握る。
        """
        repo = GuildRepository(self.bot.db)
        try:
            due = await repo.list_purge_due(now_dt)
        except Exception as e:  # noqa: BLE001
            log.warning("削除対象ギルドの取得に失敗: %s", type(e).__name__)
            return {}

        results: dict[int, dict[str, int]] = {}
        for row in due:
            guild_id = int(row["guild_id"])
            try:
                results[guild_id] = await self._purge_one(guild_id, row)
            except Exception as e:  # noqa: BLE001  (ギルド間の影響を遮断)
                log.warning("データ削除に失敗 (guild=%s): %s", guild_id, type(e).__name__)
        return results

    async def _purge_one(self, guild_id: int, row) -> dict[str, int]:
        repo = GuildRepository(self.bot.db)
        # 設定ごと消えるため、通知先は削除する前に解決しておく。
        # 退出済みサーバーではチャンネルを取得できないので通知は出ない。
        channel = None
        try:
            gconf = await config.for_guild(guild_id)
            if gconf.bot_log_channel_id:
                channel = self.bot.get_channel(gconf.bot_log_channel_id)
        except Exception:  # noqa: BLE001
            channel = None

        deleted = await repo.purge_guild(guild_id)
        total = sum(deleted.values())
        # ギルド別設定のキャッシュに消したはずの値が残らないようにする
        config.invalidate_guild(guild_id)

        reason = "退出後の保持期間満了" if row["left_at"] else "管理者による削除要求"
        summary = (
            f"[Data] サーバーのデータを削除しました（{reason}）: "
            f"{total} 行 / {len(deleted)} テーブル"
        )
        log.info("%s (guild=%s, 内訳=%s)", summary, guild_id, deleted)
        if channel is not None:
            await self._safe_send(guild_id, channel, content=f"```\n{summary}\n```")
        return deleted

    async def _notify_due_within_7days(self, guild_id: int):
        today = now().date()
        until_date = today + timedelta(days=7)
        until = until_date.isoformat()
        try:
            tasks_ = await self.task_repo.list_due_within(guild_id, today.isoformat(), until)
        except Exception as e:  # noqa: BLE001
            log.warning("7日以内タスク取得失敗 (guild=%s): %s", guild_id, e)
            return
        if not tasks_:
            return
        await self._dispatch_by_team(
            guild_id,
            tasks_,
            title="【今週の期限タスク】今日から7日以内",
            reminder_type="task_due_7days",
            period_desc="今日から7日以内",
            period_start=today,
            period_end=until_date,
        )

    async def _notify_today_label(self, guild_id: int):
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            return
        try:
            tasks_ = await svc.get_today_labeled_tasks()
        except Exception as e:  # noqa: BLE001
            log.warning("今日やること取得失敗 (guild=%s): %s", guild_id, e)
            return
        channel = await self._today_channel(guild_id)
        if not channel or not tasks_:
            return
        embed = task_embed(f"【{svc.label_name}】本日のタスク")
        for t in tasks_[:25]:
            due = getattr(getattr(t, "due", None), "string", None) or "期限なし"
            embed.add_field(name=t.content, value=f"期限: {due}", inline=False)
        await self._safe_send(guild_id, channel, embed=embed)
        await self._log_reminder(
            guild_id, "task_today_label", "batch", None, str(channel.id), "success"
        )

    # ---------- Todoist セクション別通知 ----------
    async def push_section_tasks(self, guild_id: int) -> int:
        svc = await self.bot.todoist_manager.for_guild(guild_id)
        if not svc.enabled:
            return 0
        links = await self.section_repo.list_links(guild_id)
        linked_section_ids: set[str] = {link["section_id"] for link in links}

        team_map = await self._team_map(guild_id)
        default_channel = await self._task_channel(guild_id)

        today = now().date()
        until = today + timedelta(days=7)
        sent = 0

        for link in links:
            section_id = link["section_id"]
            team_key = link["team_key"]
            section_name = link.get("section_name") or section_id

            try:
                sec_tasks = await svc.get_tasks_by_section(section_id)
            except Exception as e:  # noqa: BLE001
                log.warning("セクション %s のタスク取得失敗: %s", section_id, e)
                continue

            items = []
            for t in sec_tasks:
                due_date = self._todoist_due_date(t)
                if due_date is None or due_date > until:
                    continue
                raw_pr = getattr(t, "priority", None)
                pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
                items.append(
                    {
                        "due_date": due_date,
                        "title": t.content,
                        "priority": pr_int,
                        "url": _todoist_task_url(t.id),
                        "category": section_name,
                    }
                )
            if not items:
                continue

            info = team_map.get(team_key, {})
            channel = None
            channel_id = _channel_id_of(info)
            if channel_id is not None:
                channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = default_channel
            if channel is None:
                await self.bot.log_to_channel(
                    f"[Reminder] セクション通知の送信先がありません（{section_name}）",
                    guild_id=guild_id,
                )
                continue

            team_disp = info.get("name", team_key)
            desc = _build_grouped_description(today, until, "今日から7日以内", items)
            embed = task_embed(f"【Todoist・{team_disp}班】{section_name} の期限タスク")
            embed.description = desc[:4096]
            await self._safe_send(guild_id, channel, embed=embed)
            await self._log_reminder(
                guild_id,
                "todoist_section",
                f"section:{section_id}",
                None,
                str(channel.id),
                "success",
            )
            sent += 1

        if default_channel is None:
            return sent

        unlinked_items = []
        try:
            all_sections = await svc.get_sections()
        except Exception as e:  # noqa: BLE001
            log.warning("全セクション取得失敗: %s", e)
            all_sections = []

        for section in all_sections:
            sid = str(section.id)
            if sid in linked_section_ids:
                continue
            try:
                sec_tasks = await svc.get_tasks_by_section(sid)
            except Exception as e:  # noqa: BLE001
                log.warning("未紐付けセクション %s のタスク取得失敗: %s", sid, e)
                continue
            for t in sec_tasks:
                due_date = self._todoist_due_date(t)
                if due_date is None or due_date > until:
                    continue
                raw_pr = getattr(t, "priority", None)
                pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
                unlinked_items.append(
                    {
                        "due_date": due_date,
                        "title": t.content,
                        "priority": pr_int,
                        "url": _todoist_task_url(t.id),
                        "category": section.name,
                    }
                )

        try:
            no_section_tasks = await svc.get_tasks_without_section()
        except Exception as e:  # noqa: BLE001
            log.warning("セクションなしタスク取得失敗: %s", e)
            no_section_tasks = []

        for t in no_section_tasks:
            due_date = self._todoist_due_date(t)
            if due_date is None or due_date > until:
                continue
            raw_pr = getattr(t, "priority", None)
            pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
            unlinked_items.append(
                {
                    "due_date": due_date,
                    "title": t.content,
                    "priority": pr_int,
                    "url": _todoist_task_url(t.id),
                    "category": "セクションなし",
                }
            )

        if unlinked_items:
            desc = _build_grouped_description(today, until, "今日から7日以内", unlinked_items)
            embed = task_embed("【Todoist】全体タスク")
            embed.description = desc[:4096]
            await self._safe_send(guild_id, default_channel, embed=embed)
            await self._log_reminder(
                guild_id, "todoist_unlinked", "unlinked", None, str(default_channel.id), "success"
            )
            sent += 1

        return sent

    @staticmethod
    def _todoist_due_date(t):
        """Todoist タスクの期限日（date）。未設定なら None。"""
        due = getattr(t, "due", None)
        if due is None:
            return None
        raw = getattr(due, "date", None)
        if raw is None:
            return None
        if hasattr(raw, "date"):  # datetime の場合
            return raw.date()
        return raw

    # ====================================================================
    # ヘルパー
    # ====================================================================
    async def _task_channel(self, guild_id: int):
        gconf = await config.for_guild(guild_id)
        if gconf.default_task_channel_id:
            return self.bot.get_channel(gconf.default_task_channel_id)
        return None

    async def _today_channel(self, guild_id: int):
        gconf = await config.for_guild(guild_id)
        if gconf.today_channel_id:
            return self.bot.get_channel(gconf.today_channel_id)
        return await self._task_channel(guild_id)

    async def _team_map(self, guild_id: int) -> dict[str, dict]:
        """班キー → {name, channel_id} のマップを返す。取得失敗時は空 dict。"""
        try:
            teams = await self.member_repo.list_teams(guild_id)
        except Exception as e:  # noqa: BLE001
            log.warning("班一覧取得失敗 (guild=%s): %s", guild_id, e)
            return {}
        return {
            t["team_key"]: {
                "name": t.get("team_name") or t["team_key"],
                "channel_id": t.get("channel_id"),
            }
            for t in teams
        }

    async def _dispatch_by_team(
        self,
        guild_id: int,
        tasks_: list[dict],
        *,
        title: str,
        reminder_type: str,
        period_desc: str,
        period_start,
        period_end,
    ) -> None:
        team_map = await self._team_map(guild_id)
        default_channel = await self._task_channel(guild_id)

        buckets: dict[str | None, list[dict]] = {}
        for t in tasks_:
            team_key = t.get("team_key") or None
            info = team_map.get(team_key) if team_key else None
            # 班チャンネルは TEXT 列で、手入力・旧データに数字以外が入りうる。
            # 数字として読めないものは既定チャンネル行きにまとめる
            if team_key and info and _channel_id_of(info) is not None:
                buckets.setdefault(team_key, []).append(t)
            else:
                buckets.setdefault(None, []).append(t)

        for bucket_key, bucket_tasks in buckets.items():
            if bucket_key is None:
                channel = default_channel
                heading = title
            else:
                info = team_map.get(bucket_key, {})
                channel_id = _channel_id_of(info)
                channel = self.bot.get_channel(channel_id) if channel_id is not None else None
                if channel is None:
                    channel = default_channel
                heading = f"{title}｜{info.get('name', bucket_key)}班"

            if channel is None:
                await self.bot.log_to_channel(
                    f"[Reminder] 送信先チャンネルが見つかりません（{reminder_type}）",
                    guild_id=guild_id,
                )
                continue

            items = []
            for t in bucket_tasks:
                # due_date は TEXT 列。壊れた値の1件で通知全体を落とさない
                try:
                    due_date = from_iso(t["due_date"]).date()
                except (TypeError, ValueError):
                    log.warning(
                        "期限を解釈できないタスクをスキップ (guild=%s): %r",
                        guild_id,
                        t.get("due_date"),
                    )
                    continue
                url = _todoist_task_url(t["todoist_task_id"]) if t.get("todoist_task_id") else None
                items.append(
                    {
                        "due_date": due_date,
                        "title": t["title"],
                        "priority": t.get("priority") or 1,
                        "url": url,
                        "category": "班別タスク",
                    }
                )
            if not items:
                continue

            desc = _build_grouped_description(period_start, period_end, period_desc, items)
            embed = task_embed(heading)
            embed.description = desc[:4096]
            await self._safe_send(guild_id, channel, embed=embed)
            await self._log_reminder(
                guild_id,
                reminder_type,
                f"team:{bucket_key}" if bucket_key else "batch",
                None,
                str(channel.id),
                "success",
            )

    def _assignee_name(self, task: dict, guild) -> str:
        if task.get("assignee_id") and guild:
            m = guild.get_member(int(task["assignee_id"]))
            return m.display_name if m else "不明"
        return "未割当"

    async def _safe_send(self, guild_id: int, channel, **kwargs):
        try:
            await channel.send(**kwargs)
        except discord.HTTPException as e:
            await self.bot.log_to_channel(f"[Reminder] 通知送信失敗: {e}", guild_id=guild_id)

    async def _log_reminder(
        self,
        guild_id: int,
        rtype: str,
        target_id: str,
        target_user_id: str | None,
        channel_id: str | None,
        status: str,
        error: str | None = None,
    ):
        try:
            await self.log_repo.add(
                guild_id, rtype, target_id, target_user_id, channel_id, status, error
            )
        except Exception as e:  # noqa: BLE001
            log.warning("reminders_log 記録失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
