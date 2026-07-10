"""
設定読み込みモジュール。

.env を読み込み、必須項目の欠如を検証する。
仕様 11.1.2: 必須設定が欠ける場合は起動停止する。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# .env の場所に依存しないよう、config.py の位置を基準に明示的に探索する。
# 探索順: 1) プロジェクト直下（config.py の1つ上 = app/ の親） 2) app/ 内
#         3) 見つからなければ従来どおりカレントディレクトリから探す
# python-dotenv は既定で CRLF・引用符・前後空白を正規化して読み込む。
_HERE = Path(__file__).resolve().parent
_ENV_CANDIDATES = [_HERE.parent / ".env", _HERE / ".env"]

# encoding="utf-8-sig" で読むことで、Windows メモ帳等が付ける先頭 BOM を
# 除去する（BOM が残ると最初のキー名が壊れて読めなくなるため）。
_loaded_env_path = None
for _candidate in _ENV_CANDIDATES:
    if _candidate.is_file():
        load_dotenv(_candidate, override=False, encoding="utf-8-sig")
        _loaded_env_path = str(_candidate)
        break
else:
    # フォールバック: カレントディレクトリ基準（従来動作）
    load_dotenv(override=False, encoding="utf-8-sig")

# systemd の EnvironmentFile 等で OS 環境変数が既に注入されている場合も
# override=False によりそれを尊重する（.env で上書きしない）。


def _clean(value: str) -> str:
    """CR・BOM・前後空白・囲い引用符を除去する（手編集 .env への防御）。"""
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
    # Discord
    discord_token: str = _get_str("DISCORD_TOKEN")
    guild_id: int | None = _get_int("GUILD_ID")

    # チャンネル
    bot_log_channel_id: int | None = _get_int("BOT_LOG_CHANNEL_ID")
    default_announce_channel_id: int | None = _get_int("DEFAULT_ANNOUNCE_CHANNEL_ID")
    default_schedule_channel_id: int | None = _get_int("DEFAULT_SCHEDULE_CHANNEL_ID")
    default_progress_channel_id: int | None = _get_int("DEFAULT_PROGRESS_CHANNEL_ID")
    default_task_channel_id: int | None = _get_int("DEFAULT_TASK_CHANNEL_ID")

    # ロール（権限）
    exec_role_id: int | None = _get_int("EXEC_ROLE_ID")
    admin_role_id: int | None = _get_int("ADMIN_ROLE_ID")
    leader_role_ids: List[int] = field(default_factory=lambda: _get_int_list("LEADER_ROLE_IDS"))
    primary_team_role_ids: dict[str, int] = field(
    default_factory=lambda: _get_team_role_map("PRIMARY_TEAM_ROLE_IDS"))
    secondary_team_role_ids: dict[str, int] = field(
    default_factory=lambda: _get_team_role_map("SECONDARY_TEAM_ROLE_IDS"))

    # Todoist
    todoist_api_token: str = _get_str("TODOIST_API_TOKEN")
    todoist_project_id: str = _get_str("TODOIST_PROJECT_ID")
    today_label_name: str = _get_str("TODAY_LABEL_NAME", "今日やること")
    today_label_channel_id: int | None = _get_int("TODAY_LABEL_CHANNEL_ID")

    # Google Sheets
    google_credentials_path: str = _get_str("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
    spreadsheet_id: str = _get_str("SPREADSHEET_ID")
    sheet_tasks: str = _get_str("SHEET_TASKS", "tasks")
    sheet_attendance: str = _get_str("SHEET_ATTENDANCE", "attendance")
    sheet_members: str = _get_str("SHEET_MEMBERS", "members")
    sheet_team_summary: str = _get_str("SHEET_TEAM_SUMMARY", "team_summary")
    sheet_audit_log: str = _get_str("SHEET_AUDIT_LOG", "audit_log")
    layer_spreadsheet_id: str = _get_str("LAYER_SPREADSHEET_ID")

    # 共通
    tz: str = _get_str("TZ", "Asia/Tokyo")
    db_path: str = _get_str("DB_PATH", "./data/club.db")

    @property
    def effective_layer_spreadsheet_id(self) -> str:
        """桁巻き用ブック ID 未指定なら運営台帳ブックを流用する。"""
        return self.layer_spreadsheet_id or self.spreadsheet_id
    
    # config.py の sheets 関連プロパティに追加
    @property
    def schedule_spreadsheet_id(self) -> str | None:
        return os.getenv("SCHEDULE_SPREADSHEET_ID") or None

    def schedule_sheets_enabled(self) -> bool:
        return bool(self.google_credentials_path and self.schedule_spreadsheet_id)

    @property
    def today_channel_id(self) -> int | None:
        return self.today_label_channel_id or self.default_task_channel_id

    def validate(self) -> list[str]:
        """必須設定の検証。欠落項目のリストを返す（空なら正常）。"""
        missing: list[str] = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.guild_id:
            missing.append("GUILD_ID")
        return missing

    def loaded_env_path(self) -> str:
        """実際に読み込んだ .env の絶対パス。OS 環境変数のみの場合は空。"""
        return _loaded_env_path or ""

    def sheets_enabled(self) -> bool:
        return bool(self.spreadsheet_id) and os.path.exists(self.google_credentials_path)

    def todoist_enabled(self) -> bool:
        return bool(self.todoist_api_token)
    
    # リアクション絵文字管理
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


config = Config()

# 機能ごとの Embed カラー（仕様 13.2）
COLOR_SCHEDULE = 0x3498DB  # 青
COLOR_TASKS = 0xE67E22     # 橙
COLOR_MEMBERS = 0x9B59B6   # 紫
COLOR_ERROR = 0xE74C3C     # 赤
COLOR_INFO = 0x95A5A6      # 情報（灰）
COLOR_SUCCESS = 0x2ECC71   # 成功（緑）

# 初期班マスタ（仕様 10.1）
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

# 桁名候補（仕様 11.8.3）。サークルの機体設計に合わせて変更する。
LAYER_KETA_CHOICES = [
    "0",
    "1L",
    "1R",
    "2L",
    "2R",
    "3L",
    "3R",
    "H",
    "V",
    "MB",
    "TB",
    "シャフト",
    "手すり",
    
]

# 技能タグ候補（仕様 11.4.3）
SKILL_TAGS = [
    "CAD", "解析", "木工", "CFRP積層", "はんだ",
    "回路設計", "加工", "写真記録", "試験準備",
]
