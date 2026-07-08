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
from utils.parser import TZ, fmt_jp, from_iso, now, parse_datetime, to_iso

log = get_logger("reminders")

PRIORITY_LABELS = {1: "低", 2: "中", 3: "高", 4: "最優先"}


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

    # ---------- 毎朝 08:00: タスク通知 ----------
    @tasks.loop(time=time(hour=8, minute=0, tzinfo=TZ))
    async def daily_morning(self):
        await self._notify_due_within_7days()
        await self._notify_today_label()
        # Todoist セクション別の期限7日以内/超過タスクを各班チャンネルへ
        try:
            await self.push_section_tasks()
        except Exception as e:  # noqa: BLE001
            log.warning("セクション別通知失敗: %s", e)

    @daily_morning.before_loop
    async def _before_morning(self):
        await self.bot.wait_until_ready()

    async def _notify_due_within_7days(self):
        today = now().date()
        until = (today + timedelta(days=7)).isoformat()
        try:
            tasks_ = await self.task_repo.list_due_within(today.isoformat(), until)
        except Exception as e:  # noqa: BLE001
            log.warning("7日以内タスク取得失敗: %s", e)
            return
        if not tasks_:
            return
        await self._dispatch_by_team(
            tasks_,
            title="【今週の期限タスク】今日から7日以内",
            reminder_type="task_due_7days",
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
        """Todoist セクションごとに、期限7日以内または超過のタスクを
        対応する班チャンネルへ通知する。送信したセクション数を返す。

        班チャンネル未設定の場合は共通チャンネルにフォールバックする。
        """
        if not self.bot.todoist.enabled:
            return 0
        links = await self.section_repo.list_links()
        if not links:
            return 0

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
            except Exception as e:  # noqa: BLE001
                log.warning("セクション %s のタスク取得失敗: %s", section_id, e)
                continue

            # 期限7日以内 or 超過のものに絞る（期限なしは除外）。
            filtered = []
            for t in sec_tasks:
                due_date = self._todoist_due_date(t)
                if due_date is None:
                    continue
                if due_date <= until:  # 超過（today未満）も today〜until も含む
                    filtered.append((t, due_date))
            if not filtered:
                continue

            # 送信先チャンネルを決定（班チャンネル → なければ共通）。
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
            embed = task_embed(
                f"【Todoist・{team_disp}班】{section_name} の期限タスク",
                "期限が7日以内または超過しているタスクです。")
            for t, due_date in sorted(filtered, key=lambda x: x[1])[:25]:
                overdue = "（超過）" if due_date < today else ""
                embed.add_field(
                    name=t.content,
                    value=f"期限: {due_date.isoformat()}{overdue}",
                    inline=False)
            await self._safe_send(channel, embed=embed)
            await self._log_reminder(
                "todoist_section", f"section:{section_id}", None,
                str(channel.id), "success")
            sent += 1

        return sent

    @staticmethod
    def _todoist_due_date(task):
        """Todoist タスクの due から date を取り出す。期限なしは None。"""
        due = getattr(task, "due", None)
        if not due:
            return None
        date_str = getattr(due, "date", None)
        if not date_str:
            return None
        try:
            return parse_datetime(str(date_str)[:10]).date()
        except Exception:  # noqa: BLE001
            try:
                return from_iso(str(date_str)).date()
            except Exception:  # noqa: BLE001
                return None

    # ---------- 毎晚 21:00: 超過通知 ----------
    @tasks.loop(time=time(hour=21, minute=0, tzinfo=TZ))
    async def daily_night(self):
        today = now().date().isoformat()
        try:
            tasks_ = await self.task_repo.list_overdue(today)
        except Exception as e:  # noqa: BLE001
            log.warning("超過タスク取得失敗: %s", e)
            return
        if not tasks_:
            return
        await self._dispatch_by_team(
            tasks_,
            title="⚠️【期限超過タスク】対応をお願いします",
            reminder_type="task_overdue",
        )

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

    async def _dispatch_by_team(self, tasks_: list[dict], *, title: str,
                                reminder_type: str) -> None:
        """タスクを班ごとに集約し、各班チャンネルへ送信する。

        班チャンネル未設定・班未割当（team_key が空）のタスクは
        共通チャンネル（DEFAULT_TASK_CHANNEL）にまとめて送る。
        """
        team_map = await self._team_map()
        default_channel = self._task_channel()

        # 班キーごとにタスクを分ける。None キーは共通チャンネル行き。
        buckets: dict[str | None, list[dict]] = {}
        for t in tasks_:
            team_key = t.get("team_key") or None
            info = team_map.get(team_key) if team_key else None
            # 班にチャンネルがあればその班に、なければ共通（None）に振る。
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
                # チャンネルが見つからない（削除等）場合は共通に退避。
                if channel is None:
                    channel = default_channel
                heading = f"{title}｜{info.get('name', bucket_key)}班"

            if channel is None:
                # 共通チャンネルも未設定なら送れない。
                await self.bot.log_to_channel(
                    f"[Reminder] 送信先チャンネルが見つかりません（{reminder_type}）")
                continue

            guild = channel.guild if hasattr(channel, "guild") else None
            embed = task_embed(heading)
            for t in bucket_tasks[:25]:
                assignee = self._assignee_name(t, guild)
                embed.add_field(
                    name=f"`{t['local_task_id']}` {t['title']}",
                    value=f"担当: {assignee} / 期限: {fmt_jp(from_iso(t['due_date']))}",
                    inline=False)
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
