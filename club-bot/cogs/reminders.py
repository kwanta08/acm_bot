"""
Reminders モジュール（仕様 11.5）。

全自動通知を統括する。discord.ext.tasks で定期実行する。
ジョブ一覧（仕様 11.5.1）:
  - Schedule 締切前催促: 締切1時間前 → 未回答者へ通知
  - Schedule 自動締切: 5分ごと → 締切済み投票を終了
  - Task 7日以内期限通知: 毎日08:00
  - Task 今日やること通知: 毎日08:00
  - Task 超過通知: 毎日21:00
  - Sheets 定期同期: 毎日04:00
通知失敗の扱い（11.5.2）: DM 失敗→チャンネル、API 障害→#bot-log、多重送信防止。
"""
from __future__ import annotations

from datetime import time, timedelta

import discord
from discord.ext import commands, tasks

from config import config
from repositories.member_repository import MemberRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.section_repository import SectionRepository
from repositories.task_repository import TaskRepository
from utils.embeds import task_embed
from utils.logger import get_logger
from itertools import groupby
from utils.parser import TZ, fmt_jp, from_iso, now, parse_datetime, to_iso

log = get_logger("reminders")

PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}
PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🔵", 1: "⚪"}
PRIORITY_P_LABEL = {4: "P1", 3: "P2", 2: "P3", 1: "P4"}


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.schedule_repo = ScheduleRepository(bot.db)
        self.task_repo = TaskRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)
        self.section_repo = SectionRepository(bot.db)

    async def cog_load(self):
        # 起動時にループを開始
        self.schedule_tick.start()
        self.daily_morning.start()
        self.daily_night.start()
        self.daily_sheets_sync.start()

    async def cog_unload(self):
        self.schedule_tick.cancel()
        self.daily_morning.cancel()
        self.daily_night.cancel()
        self.daily_sheets_sync.cancel()

    # ---------- 5分ごと: 締切前催促 + 自動締切 ----------
    @tasks.loop(minutes=5)
    async def schedule_tick(self):
        await self._process_schedule_reminders()
        await self._process_schedule_close()

    @schedule_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    async def _process_schedule_reminders(self):
        """締切1時間前の未回答者催促（多重送信防止フラグ付き）。"""
        current = now()
        window_end = current + timedelta(hours=1)
        try:
            candidates = await self.schedule_repo.list_reminder_candidates(
                to_iso(current), to_iso(window_end))
        except Exception as e:  # noqa: BLE001
            log.warning("催促候補取得失敗: %s", e)
            return
        schedule_cog = self.bot.get_cog("Schedule")
        if not schedule_cog:
            return
        for s in candidates:
            try:
                count = await schedule_cog.notify_unanswered(s)
                await self.schedule_repo.mark_reminder_sent(s["schedule_id"])
                await self._log_reminder("schedule_unanswered", s["schedule_id"], None,
                                         None, "success")
                log.info("締切前催促: %s（%d名）", s["title"], count)
            except Exception as e:  # noqa: BLE001
                await self._log_reminder("schedule_unanswered", s["schedule_id"], None,
                                         None, "failed", str(e))
                await self.bot.log_to_channel(f"[Reminder] 催促失敗 {s['schedule_id']}: {e}")

    async def _process_schedule_close(self):
        """締切を過ぎた投票を自動クローズ。"""
        try:
            due = await self.schedule_repo.list_due_schedules(to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("締切候補取得失敗: %s", e)
            return
        schedule_cog = self.bot.get_cog("Schedule")
        if not schedule_cog:
            return
        for s in due:
            try:
                await schedule_cog.finalize_schedule(s)
                log.info("自動締切: %s", s["title"])
            except Exception as e:  # noqa: BLE001
                await self.bot.log_to_channel(f"[Reminder] 自動締切失敗 {s['schedule_id']}: {e}")

    # ---------- 毎朝 08:30: タスク通知 ----------
    @tasks.loop(time=time(hour=8, minute=30, tzinfo=TZ))
    async def daily_morning(self):
        await self._notify_due_within_7days()
        await self._notify_today_label()
        # Todoist セクション別の期限7日以内/超過タスクを各班チャンネルへ
        try:
            await self.push_section_tasks()
        except Exception as e:  # noqa: BLE001
            log.warning("セクション別通知失敗: %s", e)

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


    def _build_grouped_description(period_start, period_end, period_desc: str,
                                    items: list[dict]) -> str:
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

    @tasks.loop(time=time(hour=21, minute=0, tzinfo=TZ))
    async def daily_night(self):
        today = now().date()
        try:
            tasks_ = await self.task_repo.list_overdue(today.isoformat())
        except Exception as e:
            log.warning("超過タスク取得失敗: %s", e)
            return
        if not tasks_:
            return
        await self._dispatch_by_team(
            tasks_,
            title="⚠️【期限超過タスク】対応をお願いします",
            reminder_type="task_overdue",
            period_desc="期限超過",
            period_start=today,
            period_end=today,
        )

    @daily_morning.before_loop
    async def _before_morning(self):
        await self.bot.wait_until_ready()

    async def _notify_due_within_7days(self):
        today = now().date()
        until_date = today + timedelta(days=7)
        until = until_date.isoformat()
        try:
            tasks_ = await self.task_repo.list_due_within(today.isoformat(), until)
        except Exception as e:
            log.warning("7日以内タスク取得失敗: %s", e)
            return
        if not tasks_:
            return
        await self._dispatch_by_team(
            tasks_,
            title="【今週の期限タスク】今日から7日以内",
            reminder_type="task_due_7days",
            period_desc="今日から7日以内",
            period_start=today,
            period_end=until_date,
        )

    async def _notify_today_label(self):
        if not self.bot.todoist.enabled:
            return
        try:
            tasks_ = await self.bot.todoist.get_today_labeled_tasks()
        except Exception as e:  # noqa: BLE001
            log.warning("今日やること取得失敗: %s", e)
            return
        channel = self._today_channel()
        if not channel or not tasks_:
            return
        embed = task_embed(f"【{config.today_label_name}】本日のタスク")
        for t in tasks_[:25]:
            due = getattr(getattr(t, "due", None), "string", None) or "期限なし"
            embed.add_field(name=t.content, value=f"期限: {due}", inline=False)
        await self._safe_send(channel, embed=embed)
        await self._log_reminder("task_today_label", "batch", None,
                                 str(channel.id), "success")

    # ---------- Todoist セクション別通知 ----------
    async def push_section_tasks(self) -> int:
        if not self.bot.todoist.enabled:
            return 0
        links = await self.section_repo.list_links()
        linked_section_ids: set[str] = {l["section_id"] for l in links}

        team_map = await self._team_map()
        default_channel = self._task_channel()

        today = now().date()
        until = today + timedelta(days=7)
        sent = 0

        for link in links:
            section_id = link["section_id"]
            team_key = link["team_key"]
            section_name = link.get("section_name") or section_id

            try:
                sec_tasks = await self.bot.todoist.get_tasks_by_section(section_id)
            except Exception as e:
                log.warning("セクション %s のタスク取得失敗: %s", section_id, e)
                continue

            items = []
            for t in sec_tasks:
                due_date = self._todoist_due_date(t)
                if due_date is None or due_date > until:
                    continue
                raw_pr = getattr(t, "priority", None)
                pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
                items.append({
                    "due_date": due_date,
                    "title": t.content,
                    "priority": pr_int,
                    "url": _todoist_task_url(t.id),
                    "category": section_name,
                })
            if not items:
                continue

            info = team_map.get(team_key, {})
            channel = None
            if info.get("channel_id"):
                channel = self.bot.get_channel(int(info["channel_id"]))
            if channel is None:
                channel = default_channel
            if channel is None:
                await self.bot.log_to_channel(
                    f"[Reminder] セクション通知の送信先がありません（{section_name}）")
                continue

            team_disp = info.get("name", team_key)
            desc = _build_grouped_description(today, until, "今日から7日以内", items)
            embed = task_embed(f"【Todoist・{team_disp}班】{section_name} の期限タスク")
            embed.description = desc[:4096]
            await self._safe_send(channel, embed=embed)
            await self._log_reminder(
                "todoist_section", f"section:{section_id}", None,
                str(channel.id), "success")
            sent += 1

        if default_channel is None:
            return sent

        unlinked_items = []
        try:
            all_sections = await self.bot.todoist.get_sections()
        except Exception as e:
            log.warning("全セクション取得失敗: %s", e)
            all_sections = []

        for section in all_sections:
            sid = str(section.id)
            if sid in linked_section_ids:
                continue
            try:
                sec_tasks = await self.bot.todoist.get_tasks_by_section(sid)
            except Exception as e:
                log.warning("未紐付けセクション %s のタスク取得失敗: %s", sid, e)
                continue
            for t in sec_tasks:
                due_date = self._todoist_due_date(t)
                if due_date is None or due_date > until:
                    continue
                raw_pr = getattr(t, "priority", None)
                pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
                unlinked_items.append({
                    "due_date": due_date,
                    "title": t.content,
                    "priority": pr_int,
                    "url": _todoist_task_url(t.id),
                    "category": section.name,
                })

        try:
            no_section_tasks = await self.bot.todoist.get_tasks_without_section()
        except Exception as e:
            log.warning("セクションなしタスク取得失敗: %s", e)
            no_section_tasks = []

        for t in no_section_tasks:
            due_date = self._todoist_due_date(t)
            if due_date is None or due_date > until:
                continue
            raw_pr = getattr(t, "priority", None)
            pr_int = raw_pr.value if hasattr(raw_pr, "value") else (raw_pr or 1)
            unlinked_items.append({
                "due_date": due_date,
                "title": t.content,
                "priority": pr_int,
                "url": _todoist_task_url(t.id),
                "category": "セクションなし",
            })

        if unlinked_items:
            desc = _build_grouped_description(today, until, "今日から7日以内", unlinked_items)
            embed = task_embed("【Todoist】全体タスク")
            embed.description = desc[:4096]
            await self._safe_send(default_channel, embed=embed)
            await self._log_reminder(
                "todoist_unlinked", "unlinked", None, str(default_channel.id), "success")
            sent += 1

        return sent

        @daily_night.before_loop
        async def _before_night(self):
            await self.bot.wait_until_ready()

        # ---------- 毎日 04:00: Sheets 定期同期 ----------
        @tasks.loop(time=time(hour=4, minute=0, tzinfo=TZ))
        async def daily_sheets_sync(self):
            if not self.bot.sheets.enabled:
                return
            sheets_cog = self.bot.get_cog("Sheets")
            if not sheets_cog:
                return
            if not self.bot.sheets.begin_sync():
                return
            try:
                await sheets_cog.sync_tasks()
                await sheets_cog.sync_members()
                await sheets_cog.sync_all_attendance()
                log.info("定期 Sheets 同期 完了")
            except Exception as e:  # noqa: BLE001
                await self.bot.log_to_channel(f"[Reminder] Sheets 定期同期失敗: {e}")
            finally:
                self.bot.sheets.end_sync()

    @daily_sheets_sync.before_loop
    async def _before_sheets(self):
        await self.bot.wait_until_ready()

    # ====================================================================
    # ヘルパー
    # ====================================================================
    def _task_channel(self):
        if config.default_task_channel_id:
            return self.bot.get_channel(config.default_task_channel_id)
        return None

    def _today_channel(self):
        if config.today_channel_id:
            return self.bot.get_channel(config.today_channel_id)
        return self._task_channel()

    async def _team_map(self) -> dict[str, dict]:
        """班キー → {name, channel_id} のマップを返す。取得失敗時は空 dict。"""
        try:
            teams = await self.member_repo.list_teams()
        except Exception as e:  # noqa: BLE001
            log.warning("班一覧取得失敗: %s", e)
            return {}
        return {
            t["team_key"]: {"name": t.get("team_name") or t["team_key"],
                            "channel_id": t.get("channel_id")}
            for t in teams
        }

    async def _dispatch_by_team(self, tasks_: list[dict], *, title: str,reminder_type: str, period_desc: str,period_start, period_end) -> None:
        team_map = await self._team_map()
        default_channel = self._task_channel()

        buckets: dict[str | None, list[dict]] = {}
        for t in tasks_:
            team_key = t.get("team_key") or None
            info = team_map.get(team_key) if team_key else None
            if team_key and info and info.get("channel_id"):
                buckets.setdefault(team_key, []).append(t)
            else:
                buckets.setdefault(None, []).append(t)

        for bucket_key, bucket_tasks in buckets.items():
            if bucket_key is None:
                channel = default_channel
                heading = title
            else:
                info = team_map.get(bucket_key, {})
                channel = self.bot.get_channel(int(info["channel_id"]))
                if channel is None:
                    channel = default_channel
                heading = f"{title}｜{info.get('name', bucket_key)}班"

            if channel is None:
                await self.bot.log_to_channel(
                    f"[Reminder] 送信先チャンネルが見つかりません（{reminder_type}）")
                continue

            items = []
            for t in bucket_tasks:
                due_date = from_iso(t["due_date"]).date()
                url = _todoist_task_url(t["todoist_task_id"]) if t.get("todoist_task_id") else None
                items.append({
                    "due_date": due_date,
                    "title": t["title"],
                    "priority": t.get("priority") or 1,
                    "url": url,
                    "category": "班別タスク",
                })

            desc = _build_grouped_description(period_start, period_end, period_desc, items)
            embed = task_embed(heading)
            embed.description = desc[:4096]
            await self._safe_send(channel, embed=embed)
            await self._log_reminder(
                reminder_type,
                f"team:{bucket_key}" if bucket_key else "batch",
                None, str(channel.id), "success")

    def _assignee_name(self, task: dict, guild) -> str:
        if task.get("assignee_id") and guild:
            m = guild.get_member(int(task["assignee_id"]))
            return m.display_name if m else "不明"
        return "未割当"

    async def _safe_send(self, channel, **kwargs):
        try:
            await channel.send(**kwargs)
        except discord.HTTPException as e:
            await self.bot.log_to_channel(f"[Reminder] 通知送信失敗: {e}")

    async def _log_reminder(self, rtype: str, target_id: str, target_user_id: str | None,
                            channel_id: str | None, status: str, error: str | None = None):
        try:
            await self.bot.db.execute(
                """
                INSERT INTO reminders_log
                    (reminder_type, target_id, target_user_id, sent_channel_id, sent_at,
                     status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rtype, target_id, target_user_id, channel_id, to_iso(now()), status, error),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("reminders_log 記録失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
