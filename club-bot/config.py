"""
設定読み込みモジュール（改訂版）

.env を読み込み、必要項目の欠落を検証する。
改訂版: 設定をデータベースからも読み込むようにし、ボットコマンドでカスタマイズ可能にする
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
class Config:
    """
    設定クラス
    環境変数 > データベースの優先順で設定を読み込む
    """
    # Discord - 環境変数のみ（必須）
    discord_token: str = _get_str("DISCORD_TOKEN")
    guild_id: int | None = _get_int("GUILD_ID")

    # チャンネルID - 環境変数 or データベース
    bot_log_channel_id: int | None = _get_int("BOT_LOG_CHANNEL_ID")
    default_announce_channel_id: int | None = _get_int("DEFAULT_ANNOUNCE_CHANNEL_ID")
    default_schedule_channel_id: int | None = _get_int("DEFAULT_SCHEDULE_CHANNEL_ID")
    default_progress_channel_id: int | None = _get_int("DEFAULT_PROGRESS_CHANNEL_ID")
    default_task_channel_id: int | None = _get_int("DEFAULT_TASK_CHANNEL_ID")

    # ロールID - 環境変数 or データベース
    exec_role_id: int | None = _get_int("EXEC_ROLE_ID")
    admin_role_id: int | None = _get_int("ADMIN_ROLE_ID")
    leader_role_ids: List[int] = field(default_factory=lambda: _get_int_list("LEADER_ROLE_IDS"))
    primary_team_role_ids: dict[str, int] = field(
        default_factory=lambda: _get_team_role_map("PRIMARY_TEAM_ROLE_IDS"))
    secondary_team_role_ids: dict[str, int] = field(
        default_factory=lambda: _get_team_role_map("SECONDARY_TEAM_ROLE_IDS"))

    # Todoist - 環境変数 or データベース
    todoist_api_token: str = _get_str("TODOIST_API_TOKEN")
    todoist_project_id: str = _get_str("TODOIST_PROJECT_ID")
    today_label_name: str = _get_str("TODAY_LABEL_NAME", "今日やること")
    today_label_channel_id: int | None = _get_int("TODAY_LABEL_CHANNEL_ID")

    # Google Sheets - 環境変数 or データベース
    google_credentials_path: str = _get_str("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
    spreadsheet_id: str = _get_str("SPREADSHEET_ID")
    sheet_tasks: str = _get_str("SHEET_TASKS", "tasks")
    sheet_attendance: str = _get_str("SHEET_ATTENDANCE", "attendance")
    sheet_members: str = _get_str("SHEET_MEMBERS", "members")
    sheet_team_summary: str = _get_str("SHEET_TEAM_SUMMARY", "team_summary")
    sheet_audit_log: str = _get_str("SHEET_AUDIT_LOG", "audit_log")
    layer_spreadsheet_id: str = _get_str("LAYER_SPREADSHEET_ID")

    # 共通 - 環境変数 or デフォルト
    tz: str = _get_str("TZ", "Asia/Tokyo")
    db_path: str = _get_str("DB_PATH", "./data/club.db")

    # データベース接続（設定読み込み用）
    _db: "Database | None" = None

    @property
    def effective_layer_spreadsheet_id(self) -> str:
        """
        層塗り記録用ブック ID 未指定なら運用台帳ブックを流用する
        """
        return self.layer_spreadsheet_id or self.spreadsheet_id

    @property
    def schedule_spreadsheet_id(self) -> str | None:
        return os.getenv("SCHEDULE_SPREADSHEET_ID") or None

    def schedule_sheets_enabled(self) -> bool:
        return bool(self.google_credentials_path and self.schedule_spreadsheet_id)

    @property
    def today_channel_id(self) -> int | None:
        return self.today_label_channel_id or self.default_task_channel_id

    def validate(self) -> list[str]:
        """
        必須設定の検証。欠落項目のリストを返す（空なら正常）
        """
        missing: list[str] = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.guild_id:
            missing.append("GUILD_ID")
        return missing

    def loaded_env_path(self) -> str:
        """
        実際に読み込んだ .env の絶対パス。OS 環境変数のみの場合は空。
        """
        return _loaded_env_path or ""

    def sheets_enabled(self) -> bool:
        return bool(self.spreadsheet_id) and os.path.exists(self.google_credentials_path)

    def todoist_enabled(self) -> bool:
        return bool(self.todoist_api_token)

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

    async def load_from_db(self, db: "Database") -> None:
        """
        データベースから設定を読み込む（環境変数が優先）
        """
        from repositories.settings_repository import SettingsRepository
        
        repo = SettingsRepository(db)
        
        # 環境変数が設定されていない場合のみ、データベースから読み込む
        if self.bot_log_channel_id is None:
            val = await repo.get_int("BOT_LOG_CHANNEL_ID")
            if val is not None:
                self.bot_log_channel_id = val
        
        if self.default_announce_channel_id is None:
            val = await repo.get_int("DEFAULT_ANNOUNCE_CHANNEL_ID")
            if val is not None:
                self.default_announce_channel_id = val
        
        if self.default_schedule_channel_id is None:
            val = await repo.get_int("DEFAULT_SCHEDULE_CHANNEL_ID")
            if val is not None:
                self.default_schedule_channel_id = val
        
        if self.default_progress_channel_id is None:
            val = await repo.get_int("DEFAULT_PROGRESS_CHANNEL_ID")
            if val is not None:
                self.default_progress_channel_id = val
        
        if self.default_task_channel_id is None:
            val = await repo.get_int("DEFAULT_TASK_CHANNEL_ID")
            if val is not None:
                self.default_task_channel_id = val
        
        if self.exec_role_id is None:
            val = await repo.get_int("EXEC_ROLE_ID")
            if val is not None:
                self.exec_role_id = val
        
        if self.admin_role_id is None:
            val = await repo.get_int("ADMIN_ROLE_ID")
            if val is not None:
                self.admin_role_id = val
        
        if not self.leader_role_ids:
            val = await repo.get_int_list("LEADER_ROLE_IDS")
            if val:
                self.leader_role_ids = val
        
        if not self.todoist_api_token:
            val = await repo.get("TODOIST_API_TOKEN")
            if val:
                self.todoist_api_token = val
        
        if not self.todoist_project_id:
            val = await repo.get("TODOIST_PROJECT_ID")
            if val:
                self.todoist_project_id = val
        
        if self.today_label_name == "今日やること":  # デフォルト値の場合
            val = await repo.get("TODAY_LABEL_NAME")
            if val:
                self.today_label_name = val
        
        if self.today_label_channel_id is None:
            val = await repo.get_int("TODAY_LABEL_CHANNEL_ID")
            if val is not None:
                self.today_label_channel_id = val
        
        if self.google_credentials_path == "./credentials.json":  # デフォルト値の場合
            val = await repo.get("GOOGLE_CREDENTIALS_PATH")
            if val:
                self.google_credentials_path = val
        
        if not self.spreadsheet_id:
            val = await repo.get("SPREADSHEET_ID")
            if val:
                self.spreadsheet_id = val
        
        if self.sheet_tasks == "tasks":  # デフォルト値の場合
            val = await repo.get("SHEET_TASKS")
            if val:
                self.sheet_tasks = val
        
        if self.sheet_attendance == "attendance":  # デフォルト値の場合
            val = await repo.get("SHEET_ATTENDANCE")
            if val:
                self.sheet_attendance = val
        
        if self.sheet_members == "members":  # デフォルト値の場合
            val = await repo.get("SHEET_MEMBERS")
            if val:
                self.sheet_members = val
        
        if self.sheet_team_summary == "team_summary":  # デフォルト値の場合
            val = await repo.get("SHEET_TEAM_SUMMARY")
            if val:
                self.sheet_team_summary = val
        
        if self.sheet_audit_log == "audit_log":  # デフォルト値の場合
            val = await repo.get("SHEET_AUDIT_LOG")
            if val:
                self.sheet_audit_log = val
        
        if not self.layer_spreadsheet_id:
            val = await repo.get("LAYER_SPREADSHEET_ID")
            if val:
                self.layer_spreadsheet_id = val
        
        if self.tz == "Asia/Tokyo":  # デフォルト値の場合
            val = await repo.get("TZ")
            if val:
                self.tz = val
        
        if self.db_path == "./data/club.db":  # デフォルト値の場合
            val = await repo.get("DB_PATH")
            if val:
                self.db_path = val


config = Config()

# 機能ごとの Embed カラー（改訂版 13.2）
COLOR_SCHEDULE = 0x3498DB  # 青
COLOR_TASKS = 0xE67E22     # 橙
COLOR_MEMBERS = 0x9B59B6   # 紫
COLOR_ERROR = 0xE74C3C     # 赤
COLOR_INFO = 0x95A5A6      # 情報（灰）
COLOR_SUCCESS = 0x2ECC71   # 成功（緑）

# 初期チーム（改訂版 10.1）
INITIAL_TEAMS = [
    ("design", "設計"),
    ("wing", "翼"),
    ("cfrp", "CFRP"),
    ("drive", "駆動"),
    ("propeller", "プロペラ"),
    ("electronics", "電装"),
    ("fairing", "フェアリング"),
    ("pilot", "パイロット"),
]

# 技能タグ（改訂版 11.4.3）
SKILL_TAGS = [
    "CAD", "解析", "木工", "CFRP積層", "はんだ",
    "回路設計", "加工", "写真記録", "試験整備",
]
