"""
鳥人間サークル統合運用 Discord Bot エントリーポイント（マルチテナント版）

- .env 読み込み・必須設定検証（DISCORD_TOKEN のみ必須。GUILD_ID は後方互換用の任意指定）
- SQLite 初期化・ギルドごとの初期設定投入
- 各 Cog 読み込み
- スラッシュコマンド同期（グローバル登録。新規サーバーへの参加時に追加作業は不要）
- on_guild_join による新規ギルド自動セットアップ
- グローバルエラーハンドラ

班（teams）・技能タグ（skill_tags）は固定の初期値を投入せず、
新規ギルドは空の状態で開始する。管理者が /team-add /skill-add で登録する。
"""

from __future__ import annotations

import asyncio
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import GuildConfig, config
from repositories.guild_repository import GuildRepository
from repositories.settings_repository import SettingsRepository
from services.todoist_service import TodoistServiceManager
from utils import crypto
from utils.db import Database
from utils.embeds import error_embed
from utils.logger import get_logger, setup_logging
from utils.parser import InvalidDatetimeError, now, to_iso
from utils.permissions import PermissionDenied

log = get_logger("bot")

COGS = [
    "cogs.core",
    "cogs.name_cache",  # 表示名キャッシュ（ダッシュボードの名前解決用）
    "cogs.help",  # /help コマンドカタログ
    "cogs.data",  # /data エクスポート・削除
    "cogs.season",  # /season 年度替わり
    "cogs.schedule",
    "cogs.tasks",
    "cogs.members",
    "cogs.reminders",
    "cogs.reports",
    "cogs.layer_tracking",
    "cogs.settings",  # 設定管理コグを追加
    "cogs.setup_wizard",  # /setup 設定ウィザードコグ
    "cogs.teams",  # 班・技能タグ管理コグ
    "cogs.todoist_admin",  # Todoist トークン管理コグ
    "cogs.progress",  # 機体進捗管理コグ（DB 正本）
    "cogs.welcome",  # 新入生オンボーディング（既定 OFF）
    "cogs.me",  # /me 個人サマリー（既存クエリの合成のみ）
]

# on_guild_join / 起動時の自動セットアップで投入するギルド別デフォルト設定
# （ID 系は自動作成に成功した場合のみ保存される）
#
# 旧マーカー。**読まない**（旧実装が「権限不足で何も作れなかったギルド」にも
# 立ててしまっていたため、これを分岐に使うと docs/GUIDE.md の
# 「権限を付けて再招待」という復旧手順が永久に効かない）。
AUTO_SETUP_DONE_KEY = "AUTO_SETUP_DONE"
# ロールもログチャンネルも揃った日時。**成功したときだけ**立て、
# 立っていたら自動作成をやり直さない。
# 管理者が意図的に BOT_LOG_CHANNEL_ID を消した場合に復活させないための
# マーカーでもある（ADR 0024「明示的な操作でだけ変える」）。
# 旧 AUTO_SETUP_DONE と別キーにしてあるのは、旧実装が誤って立てた値を
# 引き継がないため。
AUTO_SETUP_COMPLETED_KEY = "AUTO_SETUP_COMPLETED_AT"
# 旧ギルド限定コマンドの除去マーカー（グローバル登録への移行措置。
# 一度クリアしたギルドでは再実行しない）
GUILD_COMMANDS_CLEARED_KEY = "GUILD_COMMANDS_CLEARED_AT"
BOT_LOG_CHANNEL_NAME = "bot-log"
EXEC_ROLE_NAME = "幹部"
ADMIN_ROLE_NAME = "Bot管理者"

# 招待直後の案内を送るときに試すチャンネル数の上限。
# 送信できないチャンネルが並ぶギルドで 403 を連発しないための歯止め
# （Discord の invalid request 制限に触れないようにする）。
MAX_NOTICE_CHANNEL_ATTEMPTS = 5

# 招待リンクに含める最小権限（Administrator・Manage 系は要求しない）。
# ロール・ログチャンネルの自動作成を使う場合のみ、招待後に手動で
# Manage Roles / Manage Channels を付与する（README 参照）。
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    read_message_history=True,
)


def build_invite_url(client_id: int) -> str:
    """最小権限の OAuth2 招待 URL を返す（bot + applications.commands）。"""
    return discord.utils.oauth_url(
        client_id,
        permissions=INVITE_PERMISSIONS,
        scopes=("bot", "applications.commands"),
    )


def build_setup_guidance(auto_setup_ok: bool) -> str:
    """招待直後にギルドへ送る案内文を組み立てる。

    `auto_setup_ok` が False のときは、ロールとログチャンネルが自動では
    用意されていないことを添える。ADR 0017 の最小権限招待では Manage 系の
    権限を要求しないため、**新規ギルドではこちらが既定の経路**になる。
    失敗の告知ではなく手順として書き、原因（権限不足 / API 失敗 / 同名あり）の
    切り分けはログにだけ残す。
    """
    lines = [
        "**club-bot を導入いただきありがとうございます。**",
        "使い始めるには、管理者が次の順に実行してください。",
        "",
        (
            "1. `/setup` — 通知チャンネル・ロール・サークル名・班を設定します"
            "（班は自動作成されません）"
        ),
        "2. `/setup-status` — 設定の不足を確認できます",
        "3. `/help` — 使えるコマンドの一覧を表示します",
    ]
    if not auto_setup_ok:
        lines += [
            "",
            (
                f"`{EXEC_ROLE_NAME}` / `{ADMIN_ROLE_NAME}` ロールと "
                f"`#{BOT_LOG_CHANNEL_NAME}` チャンネルは、まだ自動では用意されていません。"
                "次のどちらかで設定してください。"
            ),
            "- **自分で作成し、`/setup` で指定する**（すぐ反映されます）",
            (
                "- Bot に `ロールの管理` と `チャンネルの管理` を付ける"
                "（同じ名前のロール・チャンネルが無ければ、Bot の次回起動時に作成されます）"
            ),
        ]
    return "\n".join(lines)


def build_intents() -> discord.Intents:
    """Bot が要求する Gateway Intents を返す。

    特権インテントは `members`（班・メンバー管理で実使用）のみ。
    `message_content` は要求しない: 本 Bot はスラッシュコマンドのみで動作し、
    `on_message` ハンドラ・`message.content` 参照・prefix コマンドを一切持たない。
    公開 Bot として不要な特権インテントを持たないことは、
    Bot Verification（100サーバー超）での審査負担も下げる。
    再混入は tests/test_intents.py の回帰テストで検出する。
    """
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True  # Guild Members（特権。班・メンバー管理で使用）
    intents.messages = True  # Guild Messages（本文は含まない）
    intents.reactions = True  # Guild Message Reactions（日程調整の投票）
    intents.dm_messages = True  # Direct Messages
    return intents


class ClubBot(commands.Bot):
    def __init__(self):
        # command_prefix は commands.Bot の必須引数だが prefix コマンドは
        # 一切定義していない（message_content を持たないため機能もしない）。
        super().__init__(command_prefix="!club ", intents=build_intents(), help_command=None)

        # プールサイズは環境変数 DB_POOL_MIN_SIZE / DB_POOL_MAX_SIZE で調整できる
        self.db = Database(config.db_path, database_url=config.database_url)
        self.todoist_manager = TodoistServiceManager(self.db)
        self._initial_guild_setup_done = False

    async def setup_hook(self) -> None:
        # DB 接続・スキーマ初期化（旧 DB は guild_id 自動マイグレーション）
        await self.db.connect()

        # データベースから設定を読み込む（環境変数が優先。
        # GUILD_ID 指定時はそのギルドの設定をグローバル設定としても読み込む）
        await config.load_from_db(self.db)

        # ダッシュボード（別プロセス）からの settings 更新を購読し、
        # ギルド別設定のキャッシュを無効化する（PostgreSQL 構成のみ）。
        # SQLite 構成では何もしない（単一プロセス運用が前提）。
        if await self.db.start_settings_listener(config.invalidate_guild):
            log.info("ダッシュボードからの設定変更を反映します（LISTEN/NOTIFY 有効）")

        # 暗号鍵チェック（Todoist トークン管理の前提）。
        # 未設定/不正でも Bot 自体は動作を継続するが、トークンの登録・利用は
        # 安全に拒否される（復号不可のため）。
        if crypto.is_encryption_ready():
            log.info("ENCRYPTION_KEY を検証しました（Todoist トークン管理: 有効）")
        else:
            log.error(
                "ENCRYPTION_KEY が未設定または不正です。"
                "Todoist トークンの登録・利用はできません。"
                ".env に Fernet 鍵を設定してください（生成: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"）'
            )

        # Cog 読み込み
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Cog 読み込み: %s", cog)
            except Exception:
                log.exception("Cog 読み込み失敗: %s", cog)

        # 再起動後も押せるボタン（新入生オンボーディングの「班を選ぶ」）。
        # 登録を忘れると、再起動を挟んだ瞬間にボタンが無反応になる。
        # **Cog 読み込みと同じく失敗を握る。** ここで例外を上げると
        # setup_hook ごと落ちて、全ギルドの bot が起動しなくなる
        try:
            from cogs.welcome import TeamPickButton

            self.add_dynamic_items(TeamPickButton)
        except Exception:
            log.exception("DynamicItem の登録に失敗: cogs.welcome")

        # スラッシュコマンドはグローバル登録に統一する。
        # 新規サーバーへ参加してもコマンド登録の追加作業は不要
        # （グローバル反映には最大1時間程度かかることがある。README 参照）。
        # 過去のギルド限定登録が残っていると二重表示になるため、
        # 各ギルドのコマンドは on_ready / on_guild_join で1回だけ除去する。
        synced = await self.tree.sync()
        log.info("スラッシュコマンドをグローバル同期: %d 件", len(synced))

        # グローバルエラーハンドラ
        self.tree.error(self.on_app_command_error)

    # ------------------------------------------------------------------
    # ギルド自動セットアップ
    # ------------------------------------------------------------------
    async def _ensure_guild_setup(self, guild: discord.Guild) -> bool:
        """
        ギルドの初期セットアップを冪等に行う。

        (a) guilds 台帳へ登録し、settings にギルド用デフォルト設定を INSERT（未存在時のみ）
        (b) ロール（幹部/Bot管理者）と bot-log チャンネルを用意し、
            ID を settings に保存（権限不足・API 失敗時はログに残して続行）

        冪等性は二段構え。
        (1) 完了マーカー `AUTO_SETUP_COMPLETED_AT`（**揃ったときだけ立てる**）が
            あれば (b) をやり直さない。管理者が後から消した設定を復活させないため
        (2) マーカーが無くても、**実効設定**（環境変数フォールバックを含む）に
            ID があるものは作らない
        旧 `AUTO_SETUP_DONE` は読まない（旧実装が権限不足のギルドにも立てており、
        これで早期 return すると「権限を付けて再招待」の復旧手順が効かない）。
        そのため権限が後から付与されたギルドでは、次の起動時に (b) が
        再試行される。

        班・技能タグの初期値は投入しない（新規ギルドは空で開始。
        管理者が /team-add /skill-add で登録する）。

        戻り値は「ロールもログチャンネルも settings に揃ったか」。
        """
        repo = SettingsRepository(self.db)

        # (a) ギルド台帳への登録（冪等。既存なら名称のみ更新）。
        #     参加中である以上、過去の退出で入った削除予定は取り消す
        #     （Bot 停止中に退出→再参加した場合も起動時にここで復旧する）。
        try:
            repo_g = GuildRepository(self.db)
            await repo_g.ensure(guild.id, guild.name)
            await repo_g.clear_left(guild.id)
        except Exception as e:  # noqa: BLE001
            log.warning("ギルド台帳への登録に失敗 (guild=%s): %s", guild.id, e)

        # デフォルト設定（存在しないキーのみ。ID 系は env フォールバックを
        #     活かすため空値は入れない）
        try:
            await repo.set_if_absent(guild.id, "GUILD_NAME", guild.name)
            await repo.set_if_absent(guild.id, "SETUP_VERSION", "1")
            await repo.set_if_absent(guild.id, "SETUP_AT", to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("ギルド初期設定の保存に失敗 (guild=%s): %s", guild.id, e)

        # (b) ロール・ログチャンネルの用意
        # 完了マーカーがあればやり直さない（管理者が後から設定を消した場合に
        # 復活させないため）。**旧 AUTO_SETUP_DONE は読まない**
        try:
            completed = await repo.get(guild.id, AUTO_SETUP_COMPLETED_KEY)
        except Exception as e:  # noqa: BLE001
            log.warning("自動セットアップ状態の取得に失敗 (guild=%s): %s", guild.id, e)
            completed = None
        if completed:
            return True

        try:
            gconf = await config.for_guild(guild.id)
            roles_ok = await self._auto_create_roles(guild, repo, gconf)
            channel_ok = await self._auto_create_log_channel(guild, repo, gconf)
        except Exception:
            log.exception("ロール・ログチャンネルの自動セットアップに失敗 (guild=%s)", guild.id)
            roles_ok = channel_ok = False
        ok = roles_ok and channel_ok

        if ok:
            try:
                await repo.set_if_absent(guild.id, AUTO_SETUP_COMPLETED_KEY, to_iso(now()))
                # 旧キーも残す（過去の運用ログ・ダッシュボードとの互換のため。
                # 読まないが、成功したときだけ立てる点は同じ）
                await repo.set_if_absent(guild.id, AUTO_SETUP_DONE_KEY, to_iso(now()))
            except Exception as e:  # noqa: BLE001
                log.warning("自動セットアップ完了マーカーの保存に失敗 (guild=%s): %s", guild.id, e)
        else:
            log.info(
                "自動セットアップは未完了です（次回の起動時に再試行します, guild=%s）", guild.id
            )
        # 作成できた ID を後続の処理（案内の送信先解決など）へ反映するため、
        # 成否によらずキャッシュを捨てる
        config.invalidate_guild(guild.id)
        log.info(
            "ギルド自動セットアップを実行しました: %s (id=%s, 完了=%s)", guild.name, guild.id, ok
        )
        return ok

    @staticmethod
    def _resolve_role(guild: discord.Guild, raw: str):
        """settings に入っているロール ID 文字列を、このギルドのロールへ解決する。"""
        value = raw.strip()
        return guild.get_role(int(value)) if value.isdigit() else None

    @staticmethod
    def _resolve_channel(guild: discord.Guild, raw: str):
        """settings に入っているチャンネル ID 文字列を、このギルドのチャンネルへ解決する。"""
        value = raw.strip()
        return guild.get_channel(int(value)) if value.isdigit() else None

    async def _auto_create_roles(
        self, guild: discord.Guild, repo: SettingsRepository, gconf: GuildConfig
    ) -> bool:
        """幹部/Bot管理者ロールを用意し ID を settings に保存する。

        班ロールは自動作成しない（班は管理者が /team-add で登録し、
        既存ロールとの紐付けは /team-role で行う）。

        戻り値は「2つとも設定済みの状態にできたか」。判定は settings 行だけでなく
        **実効設定**（環境変数フォールバックを含む）で行う。settings 行だけを見ると、
        env で運用しているギルドに空のロールを作って実効設定を奪ってしまう。

        **同名ロールが既にある場合は作成せず、ID の紐付けもしない。**
        名前が一致するだけのロールを EXEC_ROLE_ID にすると、そのロールを
        持っている人へ黙って権限を配ることになるため、管理者が `/setup` で
        明示的に指定する（未設定のままなら `/setup-status` が拾う）。
        """
        ok = True
        me = guild.me
        for key, name, env_value in (
            ("EXEC_ROLE_ID", EXEC_ROLE_NAME, gconf.exec_role_id),
            ("ADMIN_ROLE_ID", ADMIN_ROLE_NAME, gconf.admin_role_id),
        ):
            raw = await repo.get(guild.id, key)
            if raw is not None:
                # このギルドの settings で設定済み。解決できなくても作り直さない
                # （set_if_absent では古い行を直せず、毎起動ロールを作り続ける）
                if self._resolve_role(guild, raw) is None:
                    log.info(
                        "設定済みのロールが見つかりません: %s (%s) [guild=%s]。"
                        "/setup で指定し直してください",
                        name,
                        raw,
                        guild.id,
                    )
                    ok = False
                continue
            if env_value is not None and guild.get_role(env_value) is not None:
                # 環境変数で設定されており、このギルドに実在する（レガシー運用）
                continue
            if discord.utils.get(guild.roles, name=name) is not None:
                log.info(
                    "同名ロールがあるため自動作成しません: %s [guild=%s]。"
                    "/setup で指定してください",
                    name,
                    guild.id,
                )
                ok = False
                continue
            if me is None or not me.guild_permissions.manage_roles:
                log.info(
                    "ロール自動作成をスキップ（manage_roles 権限なし）: %s [guild=%s]",
                    name,
                    guild.id,
                )
                ok = False
                continue
            try:
                role = await guild.create_role(
                    name=name, mentionable=True, reason="club-bot 自動セットアップ"
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("ロール作成失敗: %s [guild=%s]: %s", name, guild.id, e)
                ok = False
                continue
            log.info("ロール作成: %s (%s) [guild=%s]", role.name, role.id, guild.id)
            try:
                await repo.set_if_absent(guild.id, key, str(role.id))
            except Exception as e:  # noqa: BLE001
                # 作成できたのに保存できない場合、復旧先が人間に見えないと詰む
                log.error(
                    "作成したロールの ID 保存に失敗: %s (%s) [guild=%s]: %s。"
                    "/setup で指定してください",
                    role.name,
                    role.id,
                    guild.id,
                    e,
                )
                ok = False
        return ok

    async def _auto_create_log_channel(
        self, guild: discord.Guild, repo: SettingsRepository, gconf: GuildConfig
    ) -> bool:
        """bot-log チャンネルを用意し ID を settings に保存する。

        戻り値は「BOT_LOG_CHANNEL_ID が設定済みの状態にできたか」。判定は
        ロール側と同じく**実効設定**で行い、このギルドに実在するかまで見る
        （環境変数の値は全ギルドの GuildConfig に配られるため、
        他ギルドのチャンネル ID を「設定済み」と誤認しない）。

        同名チャンネルが既にある場合は、そこへ送信できることを確認したうえで
        採用する（ロールと違い、権限を配ることにはならないため）。
        """
        key = "BOT_LOG_CHANNEL_ID"
        raw = await repo.get(guild.id, key)
        if raw is not None:
            if self._resolve_channel(guild, raw) is None:
                log.info(
                    "設定済みのログチャンネルが見つかりません (%s) [guild=%s]。"
                    "/setup で指定し直してください",
                    raw,
                    guild.id,
                )
                return False
            return True
        if gconf.bot_log_channel_id is not None and guild.get_channel(gconf.bot_log_channel_id):
            # 環境変数で設定されており、このギルドに実在する（レガシー運用）
            return True

        me = guild.me
        existing = discord.utils.get(
            getattr(guild, "text_channels", []), name=BOT_LOG_CHANNEL_NAME
        )
        if existing is not None:
            if me is None:
                log.info(
                    "同名チャンネルへの送信可否を確認できないため採用しません: #%s [guild=%s]",
                    BOT_LOG_CHANNEL_NAME,
                    guild.id,
                )
                return False
            if not existing.permissions_for(me).send_messages:
                log.info(
                    "同名チャンネルへ送信できないため採用しません: #%s (%s) [guild=%s]",
                    existing.name,
                    existing.id,
                    guild.id,
                )
                return False
            log.info(
                "既存の #%s (%s) をログチャンネルとして採用します [guild=%s]",
                existing.name,
                existing.id,
                guild.id,
            )
            return await self._save_log_channel_id(guild, repo, existing)

        if me is None or not me.guild_permissions.manage_channels:
            log.info(
                "ログチャンネル自動作成をスキップ（manage_channels 権限なし, guild=%s）", guild.id
            )
            return False
        try:
            channel = await guild.create_text_channel(
                BOT_LOG_CHANNEL_NAME, reason="club-bot 自動セットアップ"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("ログチャンネル作成失敗 [guild=%s]: %s", guild.id, e)
            return False
        log.info("ログチャンネル作成: #%s (%s) [guild=%s]", channel.name, channel.id, guild.id)
        return await self._save_log_channel_id(guild, repo, channel)

    async def _save_log_channel_id(
        self, guild: discord.Guild, repo: SettingsRepository, channel
    ) -> bool:
        """ログチャンネルの ID を settings に保存する（失敗は log.error に残す）。"""
        try:
            await repo.set_if_absent(guild.id, "BOT_LOG_CHANNEL_ID", str(channel.id))
        except Exception as e:  # noqa: BLE001
            log.error(
                "ログチャンネルの ID 保存に失敗: #%s (%s) [guild=%s]: %s。"
                "/setup で指定してください",
                channel.name,
                channel.id,
                guild.id,
                e,
            )
            return False
        return True

    async def _clear_legacy_guild_commands(self, guild: discord.Guild) -> None:
        """旧ギルド限定登録のコマンドを除去する（グローバル登録への移行措置）。

        グローバル登録と旧ギルド登録が併存すると同じコマンドが二重表示に
        なるため、ギルド側を空で同期して除去する。settings のマーカーで
        ギルドごとに1回だけ実行する（失敗時はマーカーを残さず次回再試行）。
        """
        repo = SettingsRepository(self.db)
        try:
            done = await repo.get(guild.id, GUILD_COMMANDS_CLEARED_KEY)
        except Exception as e:  # noqa: BLE001
            log.warning("コマンド除去マーカーの取得に失敗 (guild=%s): %s", guild.id, e)
            done = None
        if done:
            return
        try:
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("旧ギルド限定コマンドを除去しました（guild=%s）", guild.id)
        except Exception as e:  # noqa: BLE001
            log.warning("旧ギルド限定コマンドの除去に失敗（guild=%s）: %s", guild.id, e)
            return
        try:
            await repo.set(guild.id, GUILD_COMMANDS_CLEARED_KEY, to_iso(now()))
        except Exception as e:  # noqa: BLE001
            log.warning("コマンド除去マーカーの保存に失敗 (guild=%s): %s", guild.id, e)

    # ------------------------------------------------------------------
    # イベント
    # ------------------------------------------------------------------
    async def on_ready(self) -> None:
        log.info("ログイン完了: %s (id=%s)", self.user, self.user.id if self.user else "?")
        app_id = self.application_id or (self.user.id if self.user else None)
        if app_id:
            log.info("招待リンク（最小権限）: %s", build_invite_url(app_id))
        await self.change_presence(activity=discord.Game(name="鳥人間サークル運営"))

        # 参加中の全ギルドをセットアップ（ギルド登録とデフォルト設定の投入）し、
        # 旧ギルド限定コマンドを除去する（コマンド本体はグローバル登録済み）。
        # 初回の on_ready のみ実行し、それ以降の新規参加は on_guild_join で処理する。
        if not self._initial_guild_setup_done:
            self._initial_guild_setup_done = True
            for guild in list(self.guilds):
                try:
                    await self._ensure_guild_setup(guild)
                except Exception:
                    log.exception("ギルドセットアップ失敗 %s (id=%s)", guild.name, guild.id)
                await self._clear_legacy_guild_commands(guild)

        # 起動ログをチャンネルへ
        await self.log_to_channel(f"Bot を起動しました: {self.user}")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """新規ギルド参加時の自動セットアップ（招待するだけで利用開始できる）。"""
        log.info("新規ギルドに参加しました: %s (id=%s)", guild.name, guild.id)
        try:
            ok = await self._ensure_guild_setup(guild)
        except Exception:
            log.exception("on_guild_join セットアップ失敗 (guild=%s)", guild.id)
            ok = False
        await self._clear_legacy_guild_commands(guild)
        # 案内は log_to_channel（bot-log 限定）では送らない。
        # BOT_LOG_CHANNEL_ID が無いギルドでは無言で捨てられてしまうため
        await self.send_guild_notice(guild, build_setup_guidance(ok))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """サーバーから外れたとき。

        **データはここでは消さない。** 退出日時と削除予定日時だけを記録し、
        猶予期間（既定30日 / ギルド別設定 DATA_RETENTION_DAYS）を過ぎたものを
        日次ジョブが削除する。誤キックや一時的な離脱から再招待で復帰できる。
        """
        log.info("ギルドから退出しました: %s (id=%s)", guild.name, guild.id)
        try:
            gconf = await config.for_guild(guild.id)
            _, purge_after = await GuildRepository(self.db).mark_left(
                guild.id, gconf.data_retention_days
            )
        except Exception:
            log.exception("退出の記録に失敗しました (guild=%s)", guild.id)
            return
        log.info("データの削除予定を記録しました (guild=%s, purge_after=%s)", guild.id, purge_after)

    async def _notice_channels(self, guild: discord.Guild) -> list:
        """案内メッセージの送信先候補を優先順に返す。

        bot-log →（無ければ）guild.system_channel →（無ければ）送信可能な
        最初のテキストチャンネル。bot-log は **guild.get_channel で解決する**
        （環境変数の BOT_LOG_CHANNEL_ID は全ギルドの GuildConfig に配られるため、
        bot 全体から引くと他ギルドのチャンネルへ案内が飛びうる）。

        送信を試す数は MAX_NOTICE_CHANNEL_ATTEMPTS 件までに抑える。
        """
        me = guild.me
        candidates: list = []
        seen: set[int] = set()

        def add(channel) -> None:
            if channel is None or channel.id in seen:
                return
            # 設定値がカテゴリ・フォーラムを指していることがある。
            # send を持たないチャンネルは AttributeError になり、
            # 下のフォールバックまで巻き添えで止まる
            if not hasattr(channel, "send"):
                return
            if me is not None:
                perms = channel.permissions_for(me)
                if not (perms.view_channel and perms.send_messages):
                    return
            seen.add(channel.id)
            candidates.append(channel)

        try:
            gconf = await config.for_guild(guild.id)
        except Exception:  # noqa: BLE001
            gconf = None
        if gconf is not None and gconf.bot_log_channel_id:
            add(guild.get_channel(gconf.bot_log_channel_id))
        add(guild.system_channel)
        for channel in getattr(guild, "text_channels", []):
            add(channel)
        return candidates[:MAX_NOTICE_CHANNEL_ATTEMPTS]

    async def send_guild_notice(self, guild: discord.Guild, message: str) -> bool:
        """ギルドの人が読めるチャンネルへ案内を送る（最初に成功した1箇所だけ）。

        運用ログ用の log_to_channel と違い、bot-log が無いギルドでも届く。
        逆に運用ログをここへ流すと一般チャンネルへ漏れるので、
        用途は「招待直後の案内」のように必ず人に届ける必要があるものに限る。
        """
        for channel in await self._notice_channels(guild):
            try:
                await channel.send(message)
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning(
                    "案内の送信に失敗 (guild=%s, channel=%s): %s", guild.id, channel.id, e
                )
                continue
            log.info("案内を送信しました (guild=%s, channel=%s)", guild.id, channel.id)
            return True
        log.warning("案内を送信できるチャンネルがありません (guild=%s)", guild.id)
        return False

    async def log_to_channel(self, message: str, guild_id: int | None = None) -> None:
        """
        #bot-log チャンネルへログを投稿する（改訂版 11.1.2）。

        guild_id 指定時はそのギルドのログチャンネルのみ。
        未指定時は参加中の全ギルドのログチャンネルへブロードキャストする。
        """
        channel_ids: list[int] = []
        if guild_id is not None:
            try:
                gconf = await config.for_guild(guild_id)
            except Exception:  # noqa: BLE001
                return
            if gconf.bot_log_channel_id:
                channel_ids.append(gconf.bot_log_channel_id)
        else:
            for guild in list(self.guilds):
                try:
                    gconf = await config.for_guild(guild.id)
                # 設定取得に失敗したギルドはスキップ（他ギルドへの送信を止めない）
                except Exception:  # noqa: BLE001, S112
                    continue
                if gconf.bot_log_channel_id and gconf.bot_log_channel_id not in channel_ids:
                    channel_ids.append(gconf.bot_log_channel_id)
            # 起動直後など guilds キャッシュが空の場合はレガシー設定へフォールバック
            if not channel_ids and config.bot_log_channel_id:
                channel_ids.append(config.bot_log_channel_id)

        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                # 取得不能なチャンネルはスキップ（他の送信先への投稿を止めない）
                except Exception:  # noqa: BLE001, S112
                    continue
            try:
                await channel.send(f"```\n{message[:1900]}\n```")
            except Exception as e:  # noqa: BLE001
                log.warning("bot-log への投稿失敗: %s", e)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """
        全スラッシュコマンドのエラーを集約（改訂版 14）
        """
        # ラップされた元例外を取り出す
        # （app_commands.check 内で発生した例外は CommandInvokeError に
        #   ラップされて届くため、error だけでなく original 側も判定する）
        original = getattr(error, "original", error)

        if isinstance(original, PermissionDenied):
            embed = error_embed(str(original), code="PERMISSION_DENIED")
        elif isinstance(original, InvalidDatetimeError):
            embed = error_embed(str(original), code="INVALID_DATETIME")
        elif isinstance(error, app_commands.CommandOnCooldown):
            embed = error_embed("実行間隔が短すぎます。少々待って再試行してください。")
        else:
            embed = error_embed("予期せぬエラーが発生しました。時間をおいて再試行してください。")
            # ハンドラ内は except 節の外なので log.exception ではなく
            # 元例外を exc_info として明示的に渡す
            log.error("未処理のコマンドエラー: %s", original, exc_info=original)
            await self.log_to_channel(
                f"[ERROR] {interaction.command}: {original!r}",
                guild_id=interaction.guild.id if interaction.guild else None,
            )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        # エラー応答の送信自体に失敗した場合はこれ以上できることがないため握りつぶす
        except Exception:  # noqa: BLE001, S110
            pass

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def main() -> None:
    setup_logging()

    # .env の読み込み元を確認（デバッグ用）
    _env_src = config.loaded_env_path()
    if _env_src:
        log.info(".env を読み込みました: %s", _env_src)
    else:
        log.info(".env ファイルは見つかりませんでした（OS 環境変数のみで動作します）")

    missing = config.validate()
    if missing:
        log.error("必須設定が不足しています: %s", ", ".join(missing))
        if _env_src:
            log.error("読み込んだ .env: %s（この中の記載を確認してください）", _env_src)
        else:
            log.error(
                ".env が見つかりませんでした。config.py と同じ階層、"
                "またはその1つ上（プロジェクト直下）に .env を置いてください。"
            )
        log.error(".env を確認してください。起動を中止します。")
        sys.exit(1)

    if not config.guild_id:
        log.info(
            "GUILD_ID 未指定: マルチテナントモードで起動します"
            "（参加中の全ギルドで独立して動作します）"
        )

    bot = ClubBot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("停止シグナルを受信しました。")
