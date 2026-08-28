"""
/help — コマンドカタログ（F1-1）。

コマンド一覧はハードコードせず `bot.tree.walk_commands()` から動的に生成する。
手書きの一覧はコマンドを足すたびに腐るため。

カテゴリは Cog 名から導出する。班名・サークル名・チャンネル名といった
サークルごとに異なるものは含めない（鳥人間ドメイン語である「桁巻き」
「機体進捗」はそのまま使う。AGENTS.md のコーディング規約に従う）。

権限が足りないコマンドも一覧からは消さず、「L3 以上」のバッジを付けて見せる。
何ができる Bot なのかは全員に分かる必要があるため。
"""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from config import GuildConfig, config
from repositories.layer_keta_repository import LayerKetaRepository
from repositories.member_repository import MemberRepository
from utils.embeds import info_embed, success_embed
from utils.logger import get_logger
from utils.permissions import (
    Level,
    command_required_level,
    ensure_guild,
    get_level,
    level_label,
)

log = get_logger("help")

# Cog 名 → カテゴリ名。ここに無い Cog は UNCATEGORIZED に落ち、
# tests/test_help.py が検出する（新しい Cog の登録漏れを防ぐ）。
CATEGORY_BY_COG: dict[str, str] = {
    "Core": "基本",
    "Help": "基本",
    "Schedule": "日程調整",
    "Tasks": "タスク",
    "Members": "班・メンバー",
    "Teams": "班・メンバー",
    "Season": "班・メンバー",
    "LayerTracking": "桁巻き",
    "Progress": "機体進捗",
    "Reports": "レポート",
    "Settings": "設定",
    "SetupWizard": "設定",
    "TodoistAdmin": "設定",
    "Data": "データ",
}

UNCATEGORIZED = "その他"

# 選択メニューと一覧の表示順（ここに無いカテゴリは末尾に回る）
CATEGORY_ORDER: list[str] = [
    "基本",
    "日程調整",
    "タスク",
    "班・メンバー",
    "桁巻き",
    "機体進捗",
    "レポート",
    "設定",
    "データ",
]

# Discord の制限
MAX_DESCRIPTION = 4096
MAX_FIELD_VALUE = 1024
MAX_SELECT_OPTIONS = 25

_VIEW_TIMEOUT = 180.0


# ---------------------------------------------------------------------
# カタログ生成
# ---------------------------------------------------------------------
def category_for(command: app_commands.Command) -> str:
    """コマンドが属するカテゴリ名を返す。"""
    binding = getattr(command, "binding", None)
    if binding is None:
        return UNCATEGORIZED
    return CATEGORY_BY_COG.get(type(binding).__name__, UNCATEGORIZED)


def build_catalog(tree: app_commands.CommandTree) -> dict[str, list[app_commands.Command]]:
    """コマンドツリーからカテゴリ別のコマンド一覧を作る。

    グループ自体は含めず、実行可能なコマンドのみを列挙する。
    """
    catalog: dict[str, list[app_commands.Command]] = {}
    for cmd in tree.walk_commands():
        if not isinstance(cmd, app_commands.Command):
            continue
        catalog.setdefault(category_for(cmd), []).append(cmd)

    for cmds in catalog.values():
        cmds.sort(key=lambda c: c.qualified_name)

    ordered: dict[str, list[app_commands.Command]] = {}
    for name in CATEGORY_ORDER:
        if name in catalog:
            ordered[name] = catalog[name]
    for name, cmds in catalog.items():
        if name not in ordered:
            ordered[name] = cmds
    return ordered


def level_badge(command: app_commands.Command, viewer_level: Level | None) -> str:
    """実行者の権限では足りないコマンドに付けるバッジ文字列。

    足りている場合と制限が無い場合は空文字（バッジ無し）。
    実行者のレベルが不明なときは最低レベル (L1) とみなす。
    """
    required = command_required_level(command)
    if required is None:
        return ""
    viewer = viewer_level or Level.L1
    if viewer >= required:
        return ""
    return f"{level_label(required)}以上"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _join_within(lines: list[str], limit: int) -> str:
    """制限内に収まるまで行を連結し、溢れた分は件数で示す。"""
    out: list[str] = []
    total = 0
    for i, line in enumerate(lines):
        remaining = len(lines) - i
        note = f"\n…ほか {remaining} 件（`/help command:` で個別に参照できます）"
        if total + len(line) + 1 + len(note) > limit:
            out.append(note.lstrip("\n"))
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def category_embed(
    category: str, commands_: list[app_commands.Command], viewer_level: Level | None
) -> discord.Embed:
    """カテゴリ内のコマンド一覧の Embed。

    field ではなく description に列挙する（コマンドが増えても
    25 field 制限に当たらないようにするため）。
    """
    lines: list[str] = []
    for cmd in commands_:
        badge = level_badge(cmd, viewer_level)
        head = f"`/{cmd.qualified_name}`"
        if badge:
            head += f"　🔒 {badge}"
        desc = (cmd.description or "").strip()
        lines.append(f"{head}\n　{desc}" if desc else head)

    header = f"{len(commands_)} 件　🔒 は実行に必要な権限が足りないものです。\n\n"
    body = _join_within(lines, MAX_DESCRIPTION - len(header) - 200)
    return info_embed(f"コマンド一覧 — {category}", header + (body or "コマンドがありません。"))


def command_embed(command: app_commands.Command, viewer_level: Level | None) -> discord.Embed:
    """個別コマンドの説明・引数・必要権限の Embed。"""
    desc = (command.description or "").strip() or "（説明なし）"
    embed = info_embed(f"/{command.qualified_name}", _clip(desc, MAX_DESCRIPTION))

    params = list(command.parameters)
    if params:
        lines = []
        for p in params:
            kind = "必須" if p.required else "任意"
            pdesc = (p.description or "").strip()
            lines.append(f"`{p.name}`（{kind}）{pdesc}".rstrip())
        embed.add_field(name="引数", value=_clip("\n".join(lines), MAX_FIELD_VALUE), inline=False)

    required = command_required_level(command)
    if required is None:
        level_text = "制限なし（全員が実行できます）"
    else:
        level_text = f"{level_label(required)}以上"
        if level_badge(command, viewer_level):
            level_text += "\n⚠️ あなたの権限では実行できません。"
    embed.add_field(name="必要な権限", value=level_text, inline=False)
    embed.add_field(name="カテゴリ", value=category_for(command), inline=False)
    return embed


# ---------------------------------------------------------------------
# 初期設定の未完了チェック（F1-2）
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class SetupItem:
    """初期設定の1項目。hint は未設定のときに案内するコマンド。"""

    name: str
    done: bool
    hint: str


async def collect_setup_status(db, gconf: GuildConfig) -> list[SetupItem]:
    """このサーバーの初期設定の状況を洗い出す。

    判定はギルド別設定の有無と各リポジトリの件数だけで行う。
    「あるべき初期値」はコードに持たない（班の構成も桁の本数も
    サークルごとに違うため、件数が 0 かどうかだけを見る）。

    **実際に Bot の挙動を左右する設定だけを見る。** 設定はできるが
    どこからも参照されない項目を並べると、「すべて設定済み」と表示された
    サーバーに通知が届かない、という最も追いにくい形で表面化する。

    Bot と連携サービスの死活は /health の責務なのでここでは扱わない。
    """
    guild_id = gconf.guild_id
    teams = await MemberRepository(db).list_teams(guild_id)
    ketas = await LayerKetaRepository(db).list_active(guild_id)
    return [
        # 毎朝・毎晩のタスク通知と進捗通知の落とし先。ここが無いと
        # 通知は送信先を解決できず捨てられる（利用者からは Bot が
        # 止まっているようにしか見えない）。
        SetupItem(
            "タスク通知チャンネル",
            gconf.default_task_channel_id is not None,
            "`/setup` で設定してください",
        ),
        SetupItem(
            "ログチャンネル", gconf.bot_log_channel_id is not None, "`/setup` で設定してください"
        ),
        SetupItem("管理者ロール", gconf.admin_role_id is not None, "`/setup` で設定してください"),
        # 幹部（L3）の判定は EXEC_ROLE_ID だけを見ている（utils/permissions.py）。
        # 招待直後の案内が /setup-status を入口に指すので、ここが抜けていると
        # 「すべて設定済み」と出たサーバーで幹部が L3 コマンドを使えない。
        SetupItem(
            "幹部ロール",
            gconf.exec_role_id is not None,
            "`/setup` の「実行役ロール」で設定してください",
        ),
        # 班長（L2）の判定は LEADER_ROLE_IDS だけを見ている。
        # members.is_leader は Web ダッシュボードの認可にしか使われないため、
        # ここが空だと班長は Discord 上で何もできない。
        SetupItem(
            "班長ロール",
            bool(gconf.leader_role_ids),
            "`/set_role role_type:リーダー` で設定してください",
        ),
        SetupItem("班", len(teams) > 0, "`/team-add` で登録してください"),
        SetupItem("桁", len(ketas) > 0, "`/layer keta-add` で登録してください"),
        # 大会日は /countdown と週次のマイルストーン警告の起点。
        # 未設定でも他機能は動くので、任意項目として最後に置く。
        SetupItem(
            "大会日（任意）",
            bool(gconf.competition_date),
            "`/settings_set setting_key:COMPETITION_DATE value:2026-07-25` の形式で設定してください",
        ),
    ]


def setup_status_embed(items: list[SetupItem]) -> discord.Embed:
    """初期設定の状況の Embed。未設定があれば案内コマンドを添える。"""
    lines = [f"✅ {i.name}" if i.done else f"❌ {i.name} — {i.hint}" for i in items]
    body = "\n".join(lines)
    pending = [i for i in items if not i.done]
    if pending:
        body += f"\n\n未設定が **{len(pending)} 件** あります。"
        return info_embed("初期設定の状況", _clip(body, MAX_DESCRIPTION))
    body += "\n\nすべて設定済みです。"
    return success_embed("初期設定の状況", _clip(body, MAX_DESCRIPTION))


# ---------------------------------------------------------------------
# カテゴリ選択メニュー
# ---------------------------------------------------------------------
class _CategorySelect(discord.ui.Select):
    def __init__(self, catalog: dict[str, list[app_commands.Command]], viewer_level: Level | None):
        self._catalog = catalog
        self._viewer_level = viewer_level
        options = [
            discord.SelectOption(label=name, description=f"{len(cmds)} コマンド")
            for name, cmds in list(catalog.items())[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="カテゴリを選ぶ", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        category = self.values[0]
        embed = category_embed(category, self._catalog.get(category, []), self._viewer_level)
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.HTTPException as e:
            log.warning("/help のカテゴリ表示に失敗: %s", e)


class HelpView(discord.ui.View):
    """カテゴリ選択メニュー付きのビュー（ephemeral 応答に付ける）。"""

    def __init__(self, catalog: dict[str, list[app_commands.Command]], viewer_level: Level | None):
        super().__init__(timeout=_VIEW_TIMEOUT)
        self.add_item(_CategorySelect(catalog, viewer_level))


def overview_embed(
    catalog: dict[str, list[app_commands.Command]], viewer_level: Level | None
) -> discord.Embed:
    """最初に出す全体像の Embed。"""
    total = sum(len(c) for c in catalog.values())
    lines = [f"**{name}** — {len(cmds)} コマンド" for name, cmds in catalog.items()]
    body = (
        f"このサーバーで使えるコマンドは **{total} 件** です。\n"
        "下のメニューからカテゴリを選ぶと一覧が表示されます。\n"
        "個別の説明は `/help command:<コマンド名>`、\n"
        "初期設定の進み具合は `/setup-status` で確認できます。\n"
        "🔒 が付くコマンドは、あなたの権限では実行できません。\n\n" + "\n".join(lines)
    )
    return info_embed("コマンドヘルプ", _clip(body, MAX_DESCRIPTION))


# ---------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------
class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def viewer_level(self, interaction: discord.Interaction) -> Level | None:
        """実行者の権限レベル。判定できない場合は None。"""
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            return None
        try:
            gconf = await config.for_guild(interaction.guild.id)
        except Exception as e:  # noqa: BLE001
            log.warning("権限レベルの判定に失敗 (guild=%s): %s", interaction.guild.id, e)
            return None
        return get_level(member, gconf)

    def _all_commands(self) -> list[app_commands.Command]:
        return [c for c in self.bot.tree.walk_commands() if isinstance(c, app_commands.Command)]

    @app_commands.command(name="help", description="使えるコマンドの一覧と説明を表示します。")
    @app_commands.describe(command="個別に説明を見たいコマンド名（省略時は一覧）")
    async def help_command(self, interaction: discord.Interaction, command: str | None = None):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return

        viewer_level = await self.viewer_level(interaction)

        if command:
            target = self._find_command(command)
            if target is None:
                embed = info_embed(
                    "コマンドが見つかりません",
                    f"`{_clip(command, 100)}` に一致するコマンドはありません。\n"
                    "`/help` でカテゴリから探せます。",
                )
                await self._send(interaction, embed)
                return
            await self._send(interaction, command_embed(target, viewer_level))
            return

        catalog = build_catalog(self.bot.tree)
        await self._send(
            interaction, overview_embed(catalog, viewer_level), view=HelpView(catalog, viewer_level)
        )

    @app_commands.command(
        name="setup-status", description="このサーバーの初期設定で未完了の項目を表示します。"
    )
    async def setup_status(self, interaction: discord.Interaction):
        guild_id = await ensure_guild(interaction)
        if guild_id is None:
            return
        gconf = await config.for_guild(guild_id)
        items = await collect_setup_status(self.bot.db, gconf)
        await self._send(interaction, setup_status_embed(items))

    def _find_command(self, name: str) -> app_commands.Command | None:
        wanted = name.strip().lstrip("/").lower()
        for cmd in self._all_commands():
            if cmd.qualified_name.lower() == wanted:
                return cmd
        return None

    @help_command.autocomplete("command")
    async def _command_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = (current or "").strip().lstrip("/").lower()
        names = sorted(c.qualified_name for c in self._all_commands())
        hits = [n for n in names if current in n.lower()] if current else names
        return [app_commands.Choice(name=f"/{n}", value=n) for n in hits[:MAX_SELECT_OPTIONS]]

    async def _send(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        view: discord.ui.View | None = None,
    ) -> None:
        """ephemeral で応答する（チャンネルを汚さない）。"""
        kwargs = {"embed": embed, "ephemeral": True}
        if view is not None:
            kwargs["view"] = view
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
        except discord.HTTPException as e:
            log.warning("/help の応答送信に失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
