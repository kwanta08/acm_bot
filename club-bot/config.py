"""
設定読み込みモジュール（マルチテナント版）

.env を読み込み、必要項目の欠落を検証する。

マルチテナント化:
- GUILD_ID は任意（後方互換用の「レガシーギルド」指定）。validate() は
  DISCORD_TOKEN のみを必須とする。
- チャンネル ID・ロール ID などのギルド固有情報は settings テーブルから
  guild_id キーで解決する。config.for_guild(guild_id) がキャッシュ付きの
  GuildConfig を返す。解決順は「ギルド別 DB 設定 > 環境変数 > デフォルト」。
- Google Sheets 連携は廃止（NocoDB 移行）。記録の正本は SQLite。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

from dotenv import load_dotenv

if TYPE_CHECKING:
    from utils.db import Database

# .env の場所に依存しないように、config.py の場所を基準に探索する
# 探索順: 1) プロジェクト直下（config.py の1つ上 = app/ の親） 2) app/ 内
#         3) 見つからなければカレントディレクトリから .env を探す
_HERE = Path(__file__).resolve().parent
_ENV_CANDIDATES = [_HERE.parent / ".env", _HERE / ".env"]

# encoding="utf-8-sig" で読むことで、Windows メモ帳等で付けられる先頭 BOM を
# 除去する（BOM が残ると最後の改行が曇って読み込めなくなるため）
_loaded_env_path = None
for _candidate in _ENV_CANDIDATES:
    if _candidate.is_file():
        load_dotenv(_candidate, override=False, encoding="utf-8-sig")
        _loaded_env_path = str(_candidate)
        break
else:
    # フォールバック: カレントディレクトリ基準
    load_dotenv(override=False, encoding="utf-8-sig")

# systemd の EnvironmentFile 等で OS 環境変数が既に注入されている場合
# override=False によりそれらを優先する（.env で上書きしない）


def _clean(value: str | None) -> str:
    """CR・BOM・前後空白・囲い引用符を除去する（手書き集 .env への防衛）"""
    if value is None:
        return ""
    # BOM・CR・NBSP・全角スペースなどの不可視文字を除去
    value = value.replace("\ufeff", "").replace("\r", "").replace("\u00a0", " ").replace("\u3000", " ")
    value = value.strip()
    # 値全体を囲む引用符を除去（"xxx" / 'xxx'）
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        value = value[1:-1].strip()
    return value


def _get_str(name: str, default: str = "") -> str:
    return _clean(os.getenv(name, default))


def _get_int(name: str, default: int | None = None) -> int | None:
    raw = _clean(os.getenv(name, ""))
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_int_list(name: str) -> List[int]:
    raw = _clean(os.getenv(name, ""))
    if not raw:
        return []
    out: List[int] = []
    for part in raw.split(","):
        part = _clean(part)
        if part.isdigit():
            out.append(int(part))
    return out


def _get_team_role_map(name: str) -> dict[str, int]:
    raw = _clean(os.getenv(name, ""))
    if not raw:
        return {}
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = _clean(part)
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        key, val = _clean(key), _clean(val)
        if key and val.isdigit():
            result[key] = int(val)
    return result


@dataclass
class GuildConfig:
    """
    ギルドごとに解決された設定（チャンネル ID・ロール ID など）。

    config.for_guild(guild_id) で取得する。解決順は
    「ギルド別 DB 設定 > 環境変数（グローバル）> デフォルト」。
    """
    guild_id: int

    # チャンネル ID
    bot_log_channel_id: int | None = None
    default_announce_channel_id: int | None = None
    default_schedule_channel_id: int | None = None
    default_progress_channel_id: int | None = None
    default_task_channel_id: int | None = None
    today_label_channel_id: int | None = None

    # ロール ID
    exec_role_id: int | None = None
    admin_role_id: int | None = None
    leader_role_ids: List[int] = field(default_factory=list)
    primary_team_role_ids: dict[str, int] = field(default_factory=dict)
    secondary_team_role_ids: dict[str, int] = field(default_factory=dict)

    @property
    def today_channel_id(self) -> int | None:
        return self.today_label_channel_id or self.default_task_channel_id


@dataclass
class Config:
    """
    設定クラス
    環境変数 > データベース（レガシーギルド）の優先順でグローバル設定を読み込む。
    ギルド固有設定は for_guild() で解決する。
    """
    # Discord - 環境変数のみ（DISCORD_TOKEN は必須）
    discord_token: str = _get_str("DISCORD_TOKEN")
    # GUILD_ID は任意（後方互換のためのレガシーギルド ID。
    # 指定時はそのギルドの DB 設定をグローバル設定としても読み込み、
    # コマンド同期をこのギルドへ即時反映する）
    guild_id: int | None = _get_int("GUILD_ID")

    # チャンネルID - 環境変数 or データベース（ギルド別解決のフォールバック）
    bot_log_channel_id: int | None = _get_int("BOT_LOG_CHANNEL_ID")
    default_announce_channel_id: int | None = _get_int("DEFAULT_ANNOUNCE_CHANNEL_ID")
    default_schedule_channel_id: int | None = _get_int("DEFAULT_SCHEDULE_CHANNEL_ID")
    default_progress_channel_id: int | None = _get_int("DEFAULT_PROGRESS_CHANNEL_ID")
    default_task_channel_id: int | None = _get_int("DEFAULT_TASK_CHANNEL_ID")

    # ロールID - 環境変数 or データベース（ギルド別解決のフォールバック）
    exec_role_id: int | None = _get_int("EXEC_ROLE_ID")
    admin_role_id: int | None = _get_int("ADMIN_ROLE_ID")
    leader_role_ids: List[int] = field(default_factory=lambda: _get_int_list("LEADER_ROLE_IDS"))
    primary_team_role_ids: dict[str, int] = field(
        default_factory=lambda: _get_team_role_map("PRIMARY_TEAM_ROLE_IDS"))
    secondary_team_role_ids: dict[str, int] = field(
        default_factory=lambda: _get_team_role_map("SECONDARY_TEAM_ROLE_IDS"))

    # Todoist - ギルド別管理（todoist_configs テーブル + /todoist-setup）。
    # トークン・プロジェクトIDは環境変数・settings からは読まない
    # （平文保存を廃止。/todoist-setup で暗号化して DB 登録する）。
    # today_label_channel_id は通知先チャンネルの設定（ギルド別解決のフォールバック）
    today_label_channel_id: int | None = _get_int("TODAY_LABEL_CHANNEL_ID")

    # Google Sheets 連携は廃止（NocoDB 移行）。記録の正本は SQLite で、
    # 外部参照は NocoDB が同じ DB に接続して行う。
    # 旧 Sheets 関連の環境変数・設定項目（SPREADSHEET_ID, SHEET_*,
    # GOOGLE_CREDENTIALS_PATH 等）は移行スクリプト
    # （scripts/migrate_sheets_to_db.py）でのみ使用する。

    # 共通 - 環境変数 or デフォルト
    tz: str = _get_str("TZ", "Asia/Tokyo")
    # SQLite のパス（ローカル開発・テスト用。デフォルト: ./data/club.db）
    db_path: str = _get_str("DB_PATH", "./data/club.db")
    # PostgreSQL 接続 URL（本番の NocoDB 構成用。
    # 設定時は PostgreSQL に接続し、db_path の SQLite は使わない）
    database_url: str = _get_str("DATABASE_URL")

    # データベース接続（設定読み込み用。setup_hook で接続後に保持される）
    _db: "Database | None" = None

    # ギルド別設定キャッシュ（guild_id -> GuildConfig）
    _guild_cache: dict[int, GuildConfig] = field(default_factory=dict)

    @property
    def today_channel_id(self) -> int | None:
        return self.today_label_channel_id or self.default_task_channel_id

    def validate(self) -> list[str]:
        """
        必須設定の検証。欠落項目のリストを返す（空なら正常）
        マルチテナント化により GUILD_ID は必須ではない。
        """
        missing: list[str] = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        return missing

    def loaded_env_path(self) -> str:
        """
        実際に読み込んだ .env の絶対パス。OS 環境変数のみの場合は空。
        """
        return _loaded_env_path or ""

    # リアクション絵文字ID
    @property
    def schedule_emoji_ok_id(self) -> int | None:
        v = os.getenv("SCHEDULE_EMOJI_OK_ID")
        return int(v) if v else None

    @property
    def schedule_emoji_maybe_id(self) -> int | None:
        v = os.getenv("SCHEDULE_EMOJI_MAYBE_ID")
        return int(v) if v else None

    @property
    def schedule_emoji_ng_id(self) -> int | None:
        v = os.getenv("SCHEDULE_EMOJI_NG_ID")
        return int(v) if v else None

    # ------------------------------------------------------------------
    # ギルド別設定解決
    # ------------------------------------------------------------------
    async def for_guild(self, guild_id: int,
                        db: "Database | None" = None,
                        force_reload: bool = False) -> GuildConfig:
        """
        ギルド固有設定をキャッシュ付きで返す。

        解決順: ギルド別 DB 設定 > 環境変数（グローバルフォールバック）。
        db 未指定時は setup_hook で保持した接続を使う（未接続なら env のみ）。
        """
        if not force_reload and guild_id in self._guild_cache:
            return self._guild_cache[guild_id]

        db = db or self._db
        gc = GuildConfig(
            guild_id=guild_id,
            bot_log_channel_id=self.bot_log_channel_id,
            default_announce_channel_id=self.default_announce_channel_id,
            default_schedule_channel_id=self.default_schedule_channel_id,
            default_progress_channel_id=self.default_progress_channel_id,
            default_task_channel_id=self.default_task_channel_id,
            today_label_channel_id=self.today_label_channel_id,
            exec_role_id=self.exec_role_id,
            admin_role_id=self.admin_role_id,
            leader_role_ids=list(self.leader_role_ids),
            primary_team_role_ids=dict(self.primary_team_role_ids),
            secondary_team_role_ids=dict(self.secondary_team_role_ids),
        )

        if db is not None:
            from repositories.settings_repository import SettingsRepository
            repo = SettingsRepository(db)

            for key, attr in (
                ("BOT_LOG_CHANNEL_ID", "bot_log_channel_id"),
                ("DEFAULT_ANNOUNCE_CHANNEL_ID", "default_announce_channel_id"),
                ("DEFAULT_SCHEDULE_CHANNEL_ID", "default_schedule_channel_id"),
                ("DEFAULT_PROGRESS_CHANNEL_ID", "default_progress_channel_id"),
                ("DEFAULT_TASK_CHANNEL_ID", "default_task_channel_id"),
                ("TODAY_LABEL_CHANNEL_ID", "today_label_channel_id"),
                ("EXEC_ROLE_ID", "exec_role_id"),
                ("ADMIN_ROLE_ID", "admin_role_id"),
            ):
                val = await repo.get_int(guild_id, key)
                if val is not None:
                    setattr(gc, attr, val)

            leader_ids = await repo.get_int_list(guild_id, "LEADER_ROLE_IDS")
            if leader_ids:
                gc.leader_role_ids = leader_ids

            for key, attr in (
                ("PRIMARY_TEAM_ROLE_IDS", "primary_team_role_ids"),
                ("SECONDARY_TEAM_ROLE_IDS", "secondary_team_role_ids"),
            ):
                raw = await repo.get_dict(guild_id, key)
                parsed = {k: int(v) for k, v in raw.items() if v.isdigit()}
                if parsed:
                    setattr(gc, attr, parsed)

        self._guild_cache[guild_id] = gc
        return gc

    def invalidate_guild(self, guild_id: int) -> None:
        """ギルド別設定キャッシュを破棄する（設定変更後に呼ぶ）。"""
        self._guild_cache.pop(guild_id, None)

    def clear_guild_cache(self) -> None:
        self._guild_cache.clear()

    async def load_from_db(self, db: "Database") -> None:
        """
        データベースから設定を読み込む（環境変数が優先）。

        DB 接続を保持し、レガシーギルド（GUILD_ID 指定時）の settings 行を
        グローバル設定として読み込む（単一サーバー運用との後方互換）。
        ギルド別設定は for_guild() で解決する。
        """
        self._db = db

        if not self.guild_id:
            # レガシーギルド未指定: グローバル読み込みは行わない
            return

        from repositories.settings_repository import SettingsRepository

        gid = self.guild_id
        repo = SettingsRepository(db)

        # 環境変数が設定されていない場合のみ、データベースから読み込む
        if self.bot_log_channel_id is None:
            val = await repo.get_int(gid, "BOT_LOG_CHANNEL_ID")
            if val is not None:
                self.bot_log_channel_id = val

        if self.default_announce_channel_id is None:
            val = await repo.get_int(gid, "DEFAULT_ANNOUNCE_CHANNEL_ID")
            if val is not None:
                self.default_announce_channel_id = val

        if self.default_schedule_channel_id is None:
            val = await repo.get_int(gid, "DEFAULT_SCHEDULE_CHANNEL_ID")
            if val is not None:
                self.default_schedule_channel_id = val

        if self.default_progress_channel_id is None:
            val = await repo.get_int(gid, "DEFAULT_PROGRESS_CHANNEL_ID")
            if val is not None:
                self.default_progress_channel_id = val

        if self.default_task_channel_id is None:
            val = await repo.get_int(gid, "DEFAULT_TASK_CHANNEL_ID")
            if val is not None:
                self.default_task_channel_id = val

        if self.exec_role_id is None:
            val = await repo.get_int(gid, "EXEC_ROLE_ID")
            if val is not None:
                self.exec_role_id = val

        if self.admin_role_id is None:
            val = await repo.get_int(gid, "ADMIN_ROLE_ID")
            if val is not None:
                self.admin_role_id = val

        if not self.leader_role_ids:
            val = await repo.get_int_list(gid, "LEADER_ROLE_IDS")
            if val:
                self.leader_role_ids = val

        if self.today_label_channel_id is None:
            val = await repo.get_int(gid, "TODAY_LABEL_CHANNEL_ID")
            if val is not None:
                self.today_label_channel_id = val

        if self.tz == "Asia/Tokyo":  # デフォルト値の場合
            val = await repo.get(gid, "TZ")
            if val:
                self.tz = val

        if self.db_path == "./data/club.db":  # デフォルト値の場合
            val = await repo.get(gid, "DB_PATH")
            if val:
                self.db_path = val

        # レガシーギルドのグローバル値が変わった可能性があるためキャッシュを破棄
        self.clear_guild_cache()


config = Config()

# 機能ごとの Embed カラー（改訂版 13.2）
COLOR_SCHEDULE = 0x3498DB  # 青
COLOR_TASKS = 0xE67E22     # 橙
COLOR_MEMBERS = 0x9B59B6   # 紫
COLOR_ERROR = 0xE74C3C     # 赤
COLOR_INFO = 0x95A5A6      # 情報（灰）
COLOR_SUCCESS = 0x2ECC71   # 成功（緑）

# 班（teams）と技能タグ（skill_tags）は config.py の固定配列ではなく、
# DB（teams / skill_tags テーブル）でギルド単位に管理する。
# 参照は services/team_service.py、登録・変更は /team-add /skill-add 等の
# 管理コマンド（cogs/teams.py）で行う。新規ギルドは空の状態で開始する。
