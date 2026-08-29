"""
Reports モジュール（仕様 11.6）。

週次サマリー、CSV エクスポート、通知ログ（reminders_log）と
操作ログ（audit_log）の閲覧。
出力例: 期限超過タスク一覧、月次出欠率、支援依頼頻度（11.6.2）。
マルチテナント版: 全集計を interaction.guild.id でスコープする。
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from repositories.audit_log_repository import AuditLogRepository
from repositories.layer_session_repository import LayerSessionRepository
from repositories.member_repository import MemberRepository
from repositories.name_cache_repository import ENTITY_USER, NameCacheRepository
from repositories.progress_repository import ProgressRepository
from repositories.reminders_log_repository import RemindersLogRepository
from repositories.schedule_repository import ScheduleRepository
from repositories.task_repository import TaskRepository
from services import team_service
from services.attendance_service import (
    MemberAttendance,
    ScheduleAnswers,
    aggregate_member_attendance,
    format_rate,
)
from services.layer_stats_service import aggregate_layer_stats
from services.milestone_service import days_until_competition, evaluate_all
from services.progress_tree import load_tree
from services.schedule_service import select_unanswered_targets
from services.weekly_digest_service import (
    count_completed_between,
    last_week_range,
    week_label,
)
from utils.embeds import (
    MAX_EMBED_FIELDS,
    add_truncation_note,
    empty_state_embed,
    info_embed,
    success_embed,
)
from utils.logger import get_logger
from utils.parser import fmt_jp, from_iso, now
from utils.permissions import Level, ensure_guild, require

log = get_logger("reports")


class Reports(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.task_repo = TaskRepository(bot.db)
        self.schedule_repo = ScheduleRepository(bot.db)
        self.log_repo = RemindersLogRepository(bot.db)
        self.audit_repo = AuditLogRepository(bot.db)
        self.name_repo = NameCacheRepository(bot.db)
        self.session_repo = LayerSessionRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)

    group = app_commands.Group(name="report", description="集計・エクスポート・監査")

    # ---------- weekly ----------
    async def build_weekly_embed(self, guild_id: int, now_dt=None) -> discord.Embed | None:
        """週次サマリーの Embed を作る。データが1件も無ければ None。

        **`/report weekly` と月曜朝の自動投稿（G4-5）が同じものを使う。**
        別々に組み立てると、同じ「今週」の数字が画面ごとに食い違う。

        `None` は「まだ何も始まっていない」を意味する。呼び出し側は、
        コマンドなら空状態を出し、自動投稿なら**何も送らない**
        （ADR 0023 と同じ考え方: 言うことが無い週は黙る）。
        """
        current = now_dt or now()
        today = current.date().isoformat()
        overdue = await self.task_repo.list_overdue(guild_id, today)
        open_tasks = await self.task_repo.list_tasks(guild_id, status="open")
        schedules = await self.schedule_repo.list_open_schedules(guild_id)
        start, end = last_week_range(current)
        completed = count_completed_between(
            await self.task_repo.list_completed(guild_id), start, end
        )
        layer = aggregate_layer_stats(
            await self.session_repo.list_records(guild_id), {}, since=start, until=end
        )

        gconf = await config.for_guild(guild_id, db=self.bot.db)

        if not open_tasks and not overdue and not schedules and not completed and not layer.records:
            # 全部0件は「健全に運用できている」ではなく「まだ始まっていない」。
            # 0/0/0 のサマリーは両者を見分けられない
            return None

        embed = info_embed(f"{gconf.club_name_or_default} 週次サマリー")
        embed.add_field(name="未完了タスク", value=str(len(open_tasks)), inline=True)
        embed.add_field(name="期限超過", value=str(len(overdue)), inline=True)
        embed.add_field(name="開催中の投票", value=str(len(schedules)), inline=True)

        # 先週の実績（G4-5）。**「遅延はありません」の類は書かない**
        # ——それを入れると ADR 0023 が却下した「毎週届く定型文」になる
        embed.add_field(
            name=f"先週の実績（{week_label(start, end)}）",
            value=(
                f"完了タスク {completed} 件\n"
                f"積層 {layer.records} 件 / {layer.total_minutes} 分 / "
                f"参加 {len(layer.members)} 人"
            ),
            inline=False,
        )

        # 班別タスク集計
        team_names = await team_service.team_name_map(self.bot.db, guild_id)
        by_team: dict[str, int] = {}
        for t in open_tasks:
            key = t.get("team_key") or "未分類"
            by_team[key] = by_team.get(key, 0) + 1
        if by_team:
            lines = [f"{team_names.get(k, k)}: {v}" for k, v in sorted(by_team.items())]
            embed.add_field(name="班別未完了タスク", value="\n".join(lines), inline=False)

        countdown = await self.countdown_summary(guild_id, gconf)
        if countdown:
            embed.add_field(name="大会まで", value=countdown, inline=False)
        return embed

    @group.command(name="weekly", description="週次サマリーを表示します。")
    @app_commands.describe(public="チャンネルへ公開で投稿する（既定は自分にだけ表示）")
    @require(Level.L2)
    async def weekly(self, interaction: discord.Interaction, public: bool = False):
        # 公開指定のときだけ ephemeral を外す。**既定は今までどおり自分にだけ**
        # （既存の使い方を勝手に公開へ変えない）
        await interaction.response.defer(ephemeral=not public)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        embed = await self.build_weekly_embed(guild_id)
        if embed is None:
            gconf = await config.for_guild(guild_id, db=self.bot.db)
            await interaction.followup.send(
                embed=empty_state_embed(
                    f"{gconf.club_name_or_default} 週次サマリー",
                    "まだデータがありません。`/task add` でタスクを、"
                    "`/schedule create` で日程調整を作成できます。",
                    "/task add",
                ),
                # 空状態は公開しない（部員に見せる意味が無く、誤解も招く）
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=embed, ephemeral=not public)

    async def countdown_summary(self, guild_id: int, gconf) -> str:
        """「大会まで N 日 / 遅延 M 件」の1行。

        大会日が未設定でマイルストーンも無いサーバーでは空文字を返し、
        週次サマリーの表示を変えない。
        """
        today = now().date()
        left = days_until_competition(gconf.competition_date, today)
        repo = ProgressRepository(self.bot.db)
        try:
            rows = await repo.list_milestones(guild_id)
            statuses = (
                evaluate_all(await load_tree(repo, guild_id), rows, today=today) if rows else []
            )
        except Exception as e:  # noqa: BLE001  (週次サマリー全体は止めない)
            log.warning("マイルストーンの集計に失敗 (guild=%s): %s", guild_id, type(e).__name__)
            return ""

        if left is None and not statuses:
            return ""
        parts = []
        if left is None:
            parts.append("大会日: 未設定")
        elif left > 0:
            parts.append(f"残り {left} 日")
        elif left == 0:
            parts.append("本日が大会日")
        else:
            parts.append(f"{-left} 日経過")
        if statuses:
            behind = sum(1 for s in statuses if s.is_behind)
            parts.append(f"遅延 {behind} 件 / 全 {len(statuses)} 件")
        return " / ".join(parts)

    # ---------- export tasks (CSV) ----------
    @group.command(name="export-tasks", description="タスク一覧を CSV で出力します。")
    @require(Level.L2)
    async def export_tasks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        tasks = await self.task_repo.list_all_for_export(guild_id)
        team_names = await team_service.team_name_map(self.bot.db, guild_id)
        guild = interaction.guild
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "local_task_id",
                "todoist_task_id",
                "title",
                "assignee",
                "team",
                "due_date",
                "priority",
                "status",
                "created_by",
                "created_at",
                "completed_at",
            ]
        )
        for t in tasks:
            assignee = ""
            if t.get("assignee_id") and guild:
                m = guild.get_member(int(t["assignee_id"]))
                assignee = m.display_name if m else t["assignee_id"]
            writer.writerow(
                [
                    t["local_task_id"],
                    t.get("todoist_task_id") or "",
                    t["title"],
                    assignee,
                    team_names.get(t.get("team_key"), t.get("team_key") or ""),
                    t.get("due_date") or "",
                    t.get("priority") or "",
                    t["status"],
                    t["created_by"],
                    t["created_at"],
                    t.get("completed_at") or "",
                ]
            )
        data = buf.getvalue().encode("utf-8-sig")
        file = discord.File(io.BytesIO(data), filename=f"tasks_{now().strftime('%Y%m%d')}.csv")
        await interaction.followup.send(
            embed=success_embed("タスク CSV を出力しました", f"{len(tasks)} 件"),
            file=file,
            ephemeral=True,
        )

    # ---------- notifications (通知ログ) ----------
    #
    # **旧 `/report audit` はこれ。** 読んでいるのは reminders_log
    # （bot が送った通知の記録）で、管理者操作の証跡である audit_log とは
    # 別物だった。証跡側は `/report changes` が読む（G4-3）。
    @group.command(name="notifications", description="直近の通知ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）")
    @require(Level.L3)
    async def notifications(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.log_repo.list_recent(guild_id, limit)
        if not rows:
            # ログは通知（リマインダー等）が動いて初めて溜まる。
            # 何も無い＝まだ通知が発生する運用が始まっていない
            await interaction.followup.send(
                embed=empty_state_embed(
                    "通知ログ",
                    "通知はまだ記録されていません。日程調整やタスクを作ると、"
                    "締切前のリマインドがここに記録されます。",
                    "/schedule create",
                ),
                ephemeral=True,
            )
            return
        embed = info_embed("通知ログ")
        for r in rows:
            d = dict(r)
            embed.add_field(
                name=f"{d['reminder_type']} [{d['status']}]",
                value=f"対象: {d['target_id']} / {fmt_jp(from_iso(d['sent_at']))}"
                + (f"\nエラー: {d['error_message']}" if d.get("error_message") else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- changes (操作ログ / audit_log) ----------
    async def _actor_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """操作ログに実際に出てくる実行者だけを候補に出す。

        ギルドの全メンバーを並べても、そのほとんどは1度も操作していない。
        """
        if interaction.guild is None:
            return []
        actors = await self.audit_repo.list_actors(interaction.guild.id, limit=25)
        names = await self.name_repo.names(interaction.guild.id, ENTITY_USER)
        out: list[app_commands.Choice[str]] = []
        for actor_id in actors:
            label = self._resolve_actor(interaction.guild, names, actor_id)
            if current and current.lower() not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=actor_id))
        return out[:25]

    @staticmethod
    def _resolve_actor(guild, names: dict[str, str], actor_id: str | None) -> str:
        """ID を表示名へ。ギルドキャッシュ → discord_name_cache → ID の順。

        退部した人・bot 再起動直後でも生の ID が並ばないようにする。
        解決できないものは ID のまま出す（伏せると追跡できなくなる）。
        """
        raw = str(actor_id or "")
        if not raw:
            return "（不明）"
        if guild is not None and raw.isdigit():
            member = guild.get_member(int(raw))
            if member is not None:
                return member.display_name
        return names.get(raw, raw)

    @group.command(name="changes", description="設定・マスタ変更の操作ログを表示します。")
    @app_commands.describe(limit="表示件数（最大25）", actor="実行者で絞り込む")
    @app_commands.autocomplete(actor=_actor_autocomplete)
    @require(Level.L3)
    async def changes(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
        actor: str | None = None,
    ):
        """audit_log を読む。/report notifications が読む reminders_log とは別物。"""
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        rows = await self.audit_repo.list_recent(guild_id, limit, actor_id=actor)
        names = await self.name_repo.names(guild_id, ENTITY_USER)
        guild = interaction.guild
        if not rows:
            situation = (
                "この実行者の操作ログはありません。"
                if actor
                else "設定やマスタの変更はまだ記録されていません。"
            )
            await interaction.followup.send(
                embed=empty_state_embed("操作ログ", situation, "/setup"),
                ephemeral=True,
            )
            return
        title = "操作ログ"
        if actor:
            title += f" — {self._resolve_actor(guild, names, actor)}"
        embed = info_embed(title)
        for r in rows:
            d = dict(r)
            parts = [f"実行者: {self._resolve_actor(guild, names, d.get('actor_id'))}"]
            if d.get("target"):
                # target は ID が入ることがある（ロール ID・表#行 ID など）
                parts.append(f"対象: {self._resolve_actor(guild, names, d['target'])}")
            if d.get("detail"):
                parts.append(str(d["detail"])[:300])
            try:
                parts.append(fmt_jp(from_iso(str(d["created_at"]))))
            except (TypeError, ValueError):
                parts.append(str(d.get("created_at")))
            embed.add_field(
                name=str(d["action"])[:250], value="\n".join(parts)[:1024], inline=False
            )
        add_truncation_note(embed, len(rows), MAX_EMBED_FIELDS)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- member attendance（メンバー軸の出欠） ----------
    async def collect_member_attendance(
        self, guild_id: int, guild, months: int, now_dt=None
    ) -> tuple[list[MemberAttendance], int]:
        """締切済み投票からメンバー別の出欠実績を作る。

        戻り値は (集計結果, 対象にした投票の件数)。

        **母集団は `/schedule remind` と同じ**（G3-2 / ADR 0025 の更新版）。
        対象ロールがある予定はロール保持者から名簿の退部者を引いたもの、
        無い予定は名簿の現役。ロールを解決できない予定は
        **0名として数えず、集計から丸ごと外す**（誰が対象か分からない
        予定を「全員未回答」と数えると、実在しない連続未回答が出る）。

        ロールは**現在**の保持者しか分からない。過去の予定について
        当時の保持者を復元する手段は無いので、そこは近似になる。
        """
        current = now_dt or now()
        # ざっくり30日/月。境界を厳密にしても「最近来ていない人」の判断は変わらない
        since = current - timedelta(days=30 * months)
        # 現役の条件は cogs/schedule.py の _roster_ids と同じにする
        # （active_flag=1 かつ status='active'。ADR 0025）。
        # list_members は既定で status='active' に絞るので、
        # 「全員」は include_alumni=True で取り、その差を退部・休止とする
        active_ids = {str(m["user_id"]) for m in await self.member_repo.list_members(guild_id)}
        everyone = {
            str(m["user_id"])
            for m in await self.member_repo.list_members(
                guild_id, active_only=False, include_alumni=True
            )
        }
        retired_ids = everyone - active_ids

        entries: list[ScheduleAnswers] = []
        for row in await self.schedule_repo.list_closed_schedules(guild_id):
            try:
                if from_iso(str(row["deadline"])) < since:
                    continue
            except (TypeError, ValueError):
                continue

            role_member_ids = None
            if row.get("target_role_id"):
                role = guild.get_role(int(row["target_role_id"])) if guild else None
                if role is None:
                    continue  # 誰が対象か分からない。数えない
                role_member_ids = {str(m.id) for m in role.members if not m.bot}
                if not role_member_ids:
                    continue

            targets = select_unanswered_targets(
                role_member_ids=role_member_ids,
                roster_active_ids=active_ids,
                roster_retired_ids=retired_ids,
                answered_ids=set(),  # 対象そのものが欲しいので回答は差し引かない
            )
            if not targets:
                continue

            votes = await self.schedule_repo.list_schedule_votes(guild_id, row["schedule_id"])
            answered = {str(v["user_id"]) for v in votes}
            ok = {str(v["user_id"]) for v in votes if str(v["status"]) == "ok"}
            entries.append(
                ScheduleAnswers(
                    schedule_id=str(row["schedule_id"]),
                    targets=targets,
                    answered=answered,
                    ok=ok,
                )
            )

        # list_closed_schedules は deadline の降順（新しい順）。
        # aggregate_member_attendance は連続未回答を先頭から数えるので
        # この順序のまま渡す
        return aggregate_member_attendance(entries), len(entries)

    @group.command(
        name="member-attendance", description="メンバー別の回答率・参加率を表示します。"
    )
    @app_commands.describe(months="対象期間（何ヶ月ぶんの締切済み投票を見るか）")
    @require(Level.L2)
    async def member_attendance(
        self, interaction: discord.Interaction, months: app_commands.Range[int, 1, 24] = 3
    ):
        # **ephemeral 固定。** 公開オプションを付けない（晒しにしない）
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        members, schedule_count = await self.collect_member_attendance(
            guild_id, interaction.guild, months
        )
        if not members:
            await interaction.followup.send(
                embed=empty_state_embed(
                    "メンバー別の出欠",
                    f"直近 {months} ヶ月に、集計できる締切済みの投票がありません。",
                    "/schedule create",
                ),
                ephemeral=True,
            )
            return

        names = await self.name_repo.names(guild_id, ENTITY_USER)
        embed = info_embed(
            "メンバー別の出欠",
            f"直近 {months} ヶ月 / 締切済み {schedule_count} 件。回答率の低い順。\n"
            "回答率 = 回答した回数 ÷ 対象になった回数、"
            "ok率 = 参加と答えた回数 ÷ 回答した回数。",
        )
        for member in members[:MAX_EMBED_FIELDS]:
            label = self._resolve_actor(interaction.guild, names, member.user_id)
            streak = (
                f" / **{member.streak_unanswered}回連続で未回答**"
                if member.streak_unanswered
                else ""
            )
            embed.add_field(
                name=label,
                value=(
                    f"回答 {format_rate(member.answer_rate)}"
                    f"（{member.answered}/{member.targeted}）"
                    f" / ok {format_rate(member.ok_rate)}（{member.ok}/{member.answered}）"
                    f"{streak}"
                ),
                inline=False,
            )
        add_truncation_note(
            embed, len(members), MAX_EMBED_FIELDS, "回答率の低い順に表示しています"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- attendance rate ----------
    @group.command(name="attendance-rate", description="出欠率一覧を表示します。")
    @require(Level.L2)
    async def attendance_rate(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        all_sched = await self.schedule_repo.list_all(guild_id)
        embed = info_embed("出欠率一覧")
        if not all_sched:
            await interaction.followup.send(
                embed=empty_state_embed(
                    "出欠率一覧", "集計対象の投票がありません。", "/schedule create"
                ),
                ephemeral=True,
            )
            return
        # 集計は全件で行い、表示だけ Embed の上限に合わせて絞る。
        # 26 件目以降を集計から落とすと、表示されないだけでなく
        # 全体の参加率そのものが誤った数字になる。
        grand_ok = 0
        grand_votes = 0
        for i, s in enumerate(all_sched):
            options = await self.schedule_repo.list_options(guild_id, s["schedule_id"])
            total_ok = 0
            total_votes = 0
            for opt in options:
                votes = await self.schedule_repo.list_votes(guild_id, opt["option_id"])
                total_votes += len(votes)
                total_ok += sum(1 for v in votes if v["status"] == "ok")
            grand_ok += total_ok
            grand_votes += total_votes
            if i >= MAX_EMBED_FIELDS:
                continue
            rate = f"{(total_ok / total_votes * 100):.0f}%" if total_votes else "—"
            embed.add_field(
                name=s["title"],
                value=f"参加率(ok/総票): {rate}（ok {total_ok} / 票 {total_votes}）",
                inline=False,
            )
        overall = f"{(grand_ok / grand_votes * 100):.0f}%" if grand_votes else "—"
        embed.description = (
            f"全 {len(all_sched)} 件の通算参加率: **{overall}**（ok {grand_ok} / 票 {grand_votes}）"
        )
        add_truncation_note(embed, len(all_sched), MAX_EMBED_FIELDS, "通算は全件で計算しています")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reports(bot))
