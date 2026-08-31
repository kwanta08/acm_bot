"""
Tasks モジュール（仕様 11.3）。

Discord を操作面、**Todoist をタスクの唯一の正本**とする。ローカル DB に
タスクの複製は持たない（スキーマ v22 で `tasks` テーブルを廃止）。
`/task` の各コマンドは Todoist API を直接叩き、タスク ID も Todoist の ID を
そのまま使う。Todoist 未設定のギルドでは `/task` は実行できない。

ローカル DB に残るのは「班 ↔ Todoist セクション」の紐付け
（`todoist_sections`）だけで、これはギルド別設定であってタスクではない。

/today で「今日やること」ラベルを完全一致検索で付与する。

マルチテナント版: Todoist 接続設定・セクション紐付けとも
interaction.guild.id でスコープする。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from repositories.member_repository import MemberRepository
from repositories.section_repository import SectionRepository
from services import team_service, todoist_task_service
from services.todoist_service import TodoistError
from services.todoist_task_service import PRIORITY_LABELS, TodoistTask
from utils.embeds import empty_state_embed, error_embed, info_embed, success_embed, task_embed
from utils.logger import get_logger
from utils.parser import now, parse_datetime
from utils.permissions import Level, ensure_guild, require
from utils.views import TimeoutAwareView

log = get_logger("tasks")


def task_choices(
    tasks: list[TodoistTask], current: str, limit: int = 25
) -> list[tuple[str, str]]:
    """オートコンプリート用の (表示名, Todoist タスク ID) 一覧を返す。

    表示名はタスク名。ID は Todoist の文字列 ID なので、利用者に
    手で写させない（G2-2）。current による絞り込みはタスク名・ID の部分一致。
    """
    needle = (current or "").strip().lower()
    out: list[tuple[str, str]] = []
    for t in todoist_task_service.sort_for_display(tasks):
        label = t.content or t.id
        if needle and needle not in f"{label} {t.id}".lower():
            continue
        out.append((label[:100], t.id))
        if len(out) >= limit:
            break
    return out


class SectionSelectView(TimeoutAwareView):
    def __init__(self, cog: Tasks, candidates: list[dict], owner_id: int, **task_kwargs):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.task_kwargs = task_kwargs

        options = [
            discord.SelectOption(
                label=(c.get("section_name") or c["section_id"])[:100],
                value=c["section_id"],
            )
            for c in candidates[:25]
        ]
        select = discord.ui.Select(
            placeholder="タスクを配置するセクションを選択してください",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        # コマンド実行者以外の操作を拒否（他人の interaction で
        # タスク作成が確定しないようにする）
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "このメニューはコマンドを実行した本人のみ操作できます。", ephemeral=True
            )
            return
        section_id = interaction.data["values"][0]
        await interaction.response.defer(ephemeral=True)
        await self.cog._finalize_add_task(interaction, section_id=section_id, **self.task_kwargs)
        self.stop()


class Tasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.section_repo = SectionRepository(bot.db)
        self.member_repo = MemberRepository(bot.db)

    group = app_commands.Group(name="task", description="タスク管理（Todoist）")

    # ---------- autocomplete / 班検証 ----------
    async def _team_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        return await team_service.team_choices(self.bot.db, interaction.guild.id, current)

    async def _task_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """未完了タスクの候補（done / delete / priority 用）。

        値は Todoist のタスク ID（文字列）。
        """
        if interaction.guild is None:
            return []
        try:
            svc = await self._todoist_svc(interaction.guild.id)
            if not svc.enabled:
                return []
            tasks = await todoist_task_service.list_open_tasks(svc)
        except Exception:  # noqa: BLE001  (補完は失敗しても致命的でない)
            return []
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in task_choices(tasks, current)
        ]

    async def _resolve_team(
        self, interaction: discord.Interaction, guild_id: int, team_key: str
    ) -> dict | None:
        """有効な班を返す。無効ならエラーを返信して None。"""
        t = await self.member_repo.get_team(guild_id, team_key)
        if not t or not t["active_flag"]:
            await interaction.followup.send(
                embed=error_embed(
                    f"班 `{team_key}` は登録されていません。"
                    "管理者に `/team-add` での登録を依頼してください。"
                ),
                ephemeral=True,
            )
            return None
        return t

    async def _todoist_svc(self, guild_id: int):
        """ギルド別の TodoistService を返す（未登録なら enabled=False）。"""
        return await self.bot.todoist_manager.for_guild(guild_id)

    async def _require_todoist(self, interaction: discord.Interaction, guild_id: int):
        """有効な TodoistService を返す。未設定なら案内を返信して None。

        タスクの正本は Todoist だけなので、未設定ギルドでは
        **タスク系コマンドは成立しない**。ここで打ち切る。
        """
        svc = await self._todoist_svc(guild_id)
        if not svc.enabled:
            await interaction.followup.send(
                embed=self._todoist_unconfigured_embed(), ephemeral=True
            )
            return None
        return svc

    @staticmethod
    def _todoist_unconfigured_embed() -> discord.Embed:
        return info_embed(
            "Todoist 未設定",
            "このサーバーでは Todoist が未設定です。\n"
            "タスクの追加・削除・管理は Todoist で行うため、\n"
            "管理者が `/todoist-setup` で登録するまで `/task` は使えません。",
        )

    @staticmethod
    async def _api_failed(interaction: discord.Interaction, what: str) -> None:
        await interaction.followup.send(
            embed=error_embed(
                f"Todoist の{what}に失敗しました。時間をおいて再試行してください。",
                code="TODOIST_API_FAILED",
            ),
            ephemeral=True,
        )

    async def _fetch_open_tasks(
        self, interaction: discord.Interaction, svc
    ) -> list[TodoistTask] | None:
        """未完了タスクを取得する。失敗時は案内を返信して None。"""
        try:
            return await todoist_task_service.list_open_tasks(svc)
        except TodoistError:
            await self._api_failed(interaction, "取得")
            return None

    # ---------- add ----------
    @group.command(name="add", description="新規タスクを作成します（Todoist に登録）。")
    @app_commands.describe(
        title="タスク名",
        due="期限（例: 2026-07-05 18:00）",
        team="関連班（紐付いた Todoist セクションへ配置）",
        priority="優先度 1〜4",
        note="補足（Todoist の説明欄）",
    )
    @app_commands.autocomplete(team=_team_ac)
    @require(Level.L1)
    async def add(
        self,
        interaction: discord.Interaction,
        title: str,
        due: str | None = None,
        team: str | None = None,
        priority: app_commands.Range[int, 1, 4] | None = None,
        note: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        if await self._require_todoist(interaction, guild_id) is None:
            return

        due_string = None
        if due:
            due_dt = parse_datetime(due)  # 失敗時 INVALID_DATETIME
            due_string = due_dt.strftime("%Y-%m-%d %H:%M")

        team_key = None
        team_name = None
        if team:
            t = await self._resolve_team(interaction, guild_id, team)
            if t is None:
                return
            team_key = team
            team_name = t["team_name"]

        # 班に紐付いた Todoist セクション候補を取得する。
        candidates: list[dict] = []
        if team_key:
            links = await self.section_repo.list_links(guild_id)
            candidates = [link for link in links if link["team_key"] == team_key]

        task_kwargs = {
            "guild_id": guild_id,
            "title": title,
            "due_string": due_string,
            "team_name": team_name,
            "priority": priority,
            "note": note,
        }

        if len(candidates) <= 1:
            section_id = candidates[0]["section_id"] if candidates else None
            await self._finalize_add_task(interaction, section_id=section_id, **task_kwargs)
            return

        # 2件以上あれば選択メニューを表示（B案）
        view = SectionSelectView(self, candidates, interaction.user.id, **task_kwargs)
        view.message = await interaction.followup.send(
            embed=info_embed(
                "配置先セクションを選択してください",
                f"{team_name}班には複数のセクションが紐付いています。",
            ),
            view=view,
            ephemeral=True,
        )

    async def _finalize_add_task(
        self,
        interaction: discord.Interaction,
        *,
        guild_id: int,
        section_id: str | None,
        title: str,
        due_string: str | None,
        team_name: str | None,
        priority: int | None,
        note: str | None,
    ):
        svc = await self._todoist_svc(guild_id)
        if not svc.enabled:
            await interaction.followup.send(
                embed=self._todoist_unconfigured_embed(), ephemeral=True
            )
            return
        content = f"[{team_name}] {title}" if team_name else title
        try:
            created = await svc.add_task(
                content=content,
                due_string=due_string,
                priority=priority,
                description=note,
                section_id=section_id,
            )
        except TodoistError:
            await self._api_failed(interaction, "登録")
            return

        task_id = str(getattr(created, "id", "")) or None
        desc_lines = []
        if task_id:
            desc_lines.append(f"Todoist ID: `{task_id}`")
        if due_string:
            desc_lines.append(f"期限: {due_string}")
        if priority:
            desc_lines.append(f"優先度: {PRIORITY_LABELS.get(priority, priority)}")
        embed = success_embed(
            f"タスクを作成しました: {content}",
            "\n".join(desc_lines),
            executor=interaction.user.display_name,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- list ----------
    @group.command(name="list", description="未完了タスクの一覧を表示します。")
    @require(Level.L1)
    async def list_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        tasks = await self._fetch_open_tasks(interaction, svc)
        if tasks is None:
            return
        embed = self._build_task_list_embed("タスク一覧", tasks)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- done ----------
    @group.command(name="done", description="タスクを完了にします。")
    @app_commands.describe(task_id="Todoist のタスク ID（候補から選べます）")
    @require(Level.L1)
    async def done(self, interaction: discord.Interaction, task_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        task = await self._get_task(interaction, svc, task_id)
        if task is None:
            return
        try:
            await svc.close_task(task.id)
        except TodoistError:
            await self._api_failed(interaction, "完了")
            return
        await interaction.followup.send(
            embed=success_embed(
                "完了にしました",
                f"`{task.id}` {task.content}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- delete ----------
    @group.command(name="delete", description="タスクを削除します。")
    @app_commands.describe(task_id="Todoist のタスク ID（候補から選べます）")
    @require(Level.L2)
    async def delete(self, interaction: discord.Interaction, task_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        task = await self._get_task(interaction, svc, task_id)
        if task is None:
            return
        try:
            await svc.delete_task(task.id)
        except TodoistError:
            await self._api_failed(interaction, "削除")
            return
        await interaction.followup.send(
            embed=success_embed(
                "削除しました",
                f"`{task.id}` {task.content}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- priority ----------
    @group.command(name="priority", description="優先度を変更します。")
    @app_commands.describe(task_id="Todoist のタスク ID（候補から選べます）", priority="優先度 1〜4")
    @require(Level.L1)
    async def priority(
        self,
        interaction: discord.Interaction,
        task_id: str,
        priority: app_commands.Range[int, 1, 4],
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        task = await self._get_task(interaction, svc, task_id)
        if task is None:
            return
        try:
            await svc.update_task(task.id, priority=priority)
        except TodoistError:
            await self._api_failed(interaction, "更新")
            return
        await interaction.followup.send(
            embed=success_embed(
                "優先度を変更しました",
                f"`{task.id}` {task.content} → {PRIORITY_LABELS.get(priority, priority)}",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ---------- overdue ----------
    @group.command(name="overdue", description="期限超過タスク一覧を表示します。")
    @require(Level.L1)
    async def overdue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        tasks = await self._fetch_open_tasks(interaction, svc)
        if tasks is None:
            return
        embed = self._build_task_list_embed(
            "期限超過タスク", todoist_task_service.overdue(tasks, now().date())
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- team ----------
    @group.command(name="team", description="班別のタスク一覧を表示します。")
    @app_commands.describe(team="班")
    @app_commands.autocomplete(team=_team_ac)
    @require(Level.L1)
    async def team(self, interaction: discord.Interaction, team: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        t = await self._resolve_team(interaction, guild_id, team)
        if t is None:
            return
        team_name = t["team_name"]
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return

        links = await self.section_repo.list_links(guild_id)
        section_ids = [link["section_id"] for link in links if link["team_key"] == team]
        if not section_ids:
            await interaction.followup.send(
                embed=info_embed(
                    f"{team_name}班のセクション未紐付け",
                    "`/task link-section` でセクションを紐付けてください。",
                ),
                ephemeral=True,
            )
            return

        try:
            tasks: list[TodoistTask] = []
            for sid in section_ids:
                tasks.extend(
                    todoist_task_service.normalize(await svc.get_tasks_by_section(sid))
                )
        except TodoistError:
            await self._api_failed(interaction, "取得")
            return

        embed = self._build_task_list_embed(f"班別タスク: {team_name}", tasks)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- sync（L4）----------
    @group.command(name="sync", description="Todoist 同期を再実行します（管理者）。")
    @require(Level.L4)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        try:
            await svc.ensure_label()
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("同期に失敗しました。", code="TODOIST_API_FAILED"), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=success_embed("同期を実行しました", executor=interaction.user.display_name),
            ephemeral=True,
        )

    # ====================================================================
    # Todoist セクション ↔ 班 の連携
    # ====================================================================
    @group.command(
        name="sections", description="Todoist のセクション一覧と、班との紐付け状況を表示します。"
    )
    @require(Level.L2)
    async def sections(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        try:
            sections = await svc.get_sections()
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("セクション取得に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True,
            )
            return

        links = await self.section_repo.list_links(guild_id)
        link_map = {link["section_id"]: link["team_key"] for link in links}
        team_names = await team_service.team_name_map(self.bot.db, guild_id)

        embed = task_embed(
            "Todoist セクション一覧", "`/task link-section` で班と紐付けてください。"
        )
        if not sections:
            embed.add_field(
                name="（セクションなし）",
                value="Todoist プロジェクトにセクションがありません。",
                inline=False,
            )
        for s in sections[:25]:
            sid = str(s.id)
            team_key = link_map.get(sid)
            team_disp = team_names.get(team_key, team_key) if team_key else "未紐付け"
            embed.add_field(
                name=f"{s.name}", value=f"section_id: `{sid}`\n紐付け: {team_disp}", inline=False
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="link-section", description="Todoist のセクションIDと班を紐付けます。")
    @app_commands.describe(
        team="対象の班", section_id="Todoist の section_id（/task sections で確認）"
    )
    @app_commands.autocomplete(team=_team_ac)
    @require(Level.L3)
    async def link_section(self, interaction: discord.Interaction, team: str, section_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        t = await self._resolve_team(interaction, guild_id, team)
        if t is None:
            return
        svc = await self._todoist_svc(guild_id)
        section_name = None
        if svc.enabled:
            try:
                sections = await svc.get_sections()
                match = next((s for s in sections if str(s.id) == str(section_id)), None)
                if match is None:
                    await interaction.followup.send(
                        embed=error_embed(
                            f"section_id `{section_id}` が Todoist に見つかりません。"
                            "`/task sections` で正しいIDを確認してください。"
                        ),
                        ephemeral=True,
                    )
                    return
                section_name = match.name
            except TodoistError as e:
                # 名前解決だけの失敗なので紐付けは続行するが、黙らない（G2-7）
                log.warning(
                    "Todoist セクション名の解決に失敗 (guild=%s, section=%s): %s",
                    guild_id,
                    section_id,
                    e,
                )
        await self.section_repo.link(guild_id, str(section_id), team, section_name)
        await interaction.followup.send(
            embed=success_embed(
                "セクションと班を紐付けました",
                f"{t['team_name']}班 ↔ セクション「{section_name or section_id}」\n"
                f"今後この班のタスク作成時は自動でこのセクションに配置され、"
                f"通知もこの班のチャンネルに届きます。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @group.command(name="unlink-section", description="セクションと班の紐付けを解除します。")
    @app_commands.describe(section_id="解除する section_id")
    @require(Level.L3)
    async def unlink_section(self, interaction: discord.Interaction, section_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        link = await self.section_repo.get_by_section(guild_id, str(section_id))
        if not link:
            await interaction.followup.send(
                embed=error_embed(f"section_id `{section_id}` の紐付けが見つかりません。"),
                ephemeral=True,
            )
            return
        await self.section_repo.unlink(guild_id, str(section_id))
        await interaction.followup.send(
            embed=success_embed(
                "紐付けを解除しました",
                f"section_id `{section_id}`",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @group.command(
        name="unlink-team-sections", description="指定した班のセクション紐付けをすべて解除します。"
    )
    @app_commands.describe(team="対象の班")
    @app_commands.autocomplete(team=_team_ac)
    @require(Level.L3)
    async def unlink_team_sections(self, interaction: discord.Interaction, team: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        team_names = await team_service.team_name_map(self.bot.db, guild_id)
        team_disp = team_names.get(team, team)
        links = await self.section_repo.list_links(guild_id)
        targets = [link for link in links if link["team_key"] == team]
        if not targets:
            await interaction.followup.send(
                embed=error_embed(f"{team_disp}班に紐付けられたセクションはありません。"),
                ephemeral=True,
            )
            return
        for link in targets:
            await self.section_repo.unlink(guild_id, link["section_id"])
        await interaction.followup.send(
            embed=success_embed(
                "紐付けを一括解除しました",
                f"{team_disp}班: {len(targets)} 件のセクション紐付けを解除",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    @group.command(
        name="push",
        description="各セクションの期限が近い/超過タスクを、対応する班チャンネルへ通知します。",
    )
    @require(Level.L2)
    async def push(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        reminders_cog = self.bot.get_cog("Reminders")
        if reminders_cog is None:
            await interaction.followup.send(
                embed=error_embed("通知モジュールが読み込まれていません。"), ephemeral=True
            )
            return
        if await self._require_todoist(interaction, guild_id) is None:
            return
        try:
            sent = await reminders_cog.push_section_tasks(guild_id)
        except Exception as e:  # noqa: BLE001
            log.warning("セクション通知失敗: %s", e)
            await interaction.followup.send(
                embed=error_embed("通知の送信中にエラーが発生しました。"), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "セクション別通知を送信しました",
                f"{sent} 件のセクションに通知しました。",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ====================================================================
    # /today コマンド群（仕様 11.3.1, 11.3.3）
    # ====================================================================
    today_group = app_commands.Group(name="today", description="「今日やること」ラベル付与")

    @today_group.command(name="task", description="タスク名で「今日やること」ラベルを付与します。")
    @app_commands.describe(name="完全一致するタスク名")
    @require(Level.L1)
    async def today_task(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        try:
            matches = await svc.find_open_tasks_by_name(name)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("Todoist 検索に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True,
            )
            return

        if not matches:
            await interaction.followup.send(
                embed=error_embed(f"未完了タスク「{name}」が見つかりません。"), ephemeral=True
            )
            return

        if len(matches) == 1:
            try:
                ok = await svc.add_today_label(str(matches[0].id))
            except TodoistError:
                await interaction.followup.send(
                    embed=error_embed("ラベル付与に失敗しました。", code="TODOIST_API_FAILED"),
                    ephemeral=True,
                )
                return
            if not ok:
                await interaction.followup.send(
                    embed=error_embed(f"タスク「{name}」へのラベル付与に失敗しました。"),
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                embed=success_embed(
                    "ラベルを付与しました",
                    f"「{name}」に「{svc.label_name}」を付与",
                    executor=interaction.user.display_name,
                ),
                ephemeral=True,
            )
            return

        # 複数候補 → 候補一覧を実行者にのみ表示（仕様 11.3.3）
        embed = task_embed(
            f"同名タスクが {len(matches)} 件あります", "`/today id:<ID>` で対象を確定してください。"
        )
        for t in matches:
            section = getattr(t, "section_id", None) or "—"
            due = getattr(getattr(t, "due", None), "string", None) or "期限なし"
            embed.add_field(
                name=f"ID: {t.id}",
                value=f"プロジェクト: {getattr(t, 'project_id', '—')}\n"
                f"セクション: {section}\n期限: {due}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @today_group.command(name="id", description="重複候補からID指定でラベルを付与します。")
    @app_commands.describe(task_id="Todoist タスク ID")
    @require(Level.L1)
    async def today_id(self, interaction: discord.Interaction, task_id: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        svc = await self._require_todoist(interaction, guild_id)
        if svc is None:
            return
        try:
            ok = await svc.add_today_label(task_id)
        except TodoistError:
            await interaction.followup.send(
                embed=error_embed("ラベル付与に失敗しました。", code="TODOIST_API_FAILED"),
                ephemeral=True,
            )
            return
        if not ok:
            await interaction.followup.send(
                embed=error_embed(f"タスク ID `{task_id}` が見つかりません。"), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "ラベルを付与しました",
                f"ID `{task_id}` に「{svc.label_name}」を付与",
                executor=interaction.user.display_name,
            ),
            ephemeral=True,
        )

    # ====================================================================
    # 内部ヘルパー
    # ====================================================================
    async def _get_task(
        self, interaction: discord.Interaction, svc, task_id: str
    ) -> TodoistTask | None:
        """Todoist から1件取得する。見つからなければ案内を返信して None。"""
        try:
            raw = await svc.get_task(str(task_id))
        except TodoistError:
            await self._api_failed(interaction, "取得")
            return None
        if raw is None:
            await interaction.followup.send(
                embed=error_embed(
                    f"タスク ID `{task_id}` が Todoist に見つかりません。"
                    "`/task list` で確認してください。"
                ),
                ephemeral=True,
            )
            return None
        return TodoistTask.from_raw(raw)

    def _build_task_list_embed(self, title: str, tasks: list[TodoistTask]) -> discord.Embed:
        if not tasks:
            return empty_state_embed(title, "該当するタスクはありません。", "/task add")
        embed = task_embed(title)
        for t in todoist_task_service.sort_for_display(tasks)[:25]:
            due = t.due_string or (t.due_date.isoformat() if t.due_date else "期限なし")
            embed.add_field(
                name=f"`{t.id}` {t.content}",
                value=f"期限: {due} / 優先: {t.priority_label}",
                inline=False,
            )
        if len(tasks) > 25:
            embed.set_footer(text=f"他 {len(tasks) - 25} 件")
        return embed


# task_id のオートコンプリートを一括登録する（cogs/progress.py と同じ作法）
Tasks.done.autocomplete("task_id")(Tasks._task_autocomplete)
Tasks.delete.autocomplete("task_id")(Tasks._task_autocomplete)
Tasks.priority.autocomplete("task_id")(Tasks._task_autocomplete)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tasks(bot))
