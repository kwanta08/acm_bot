"""サーバー設定の参照・更新 API（P2-6）。

更新は管理者（サーバー管理権限）のみ。編集できるキーは**ホワイトリスト**で
限定する（Todoist トークンや暗号鍵など、設定テーブルに入りうる機密値を
ダッシュボードから触れないようにするため）。

更新すると utils/db.py の set_setting が PostgreSQL の NOTIFY を送り、
bot プロセスの config キャッシュが無効化される。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from dashboard.db import get_database
from dashboard.security import AdminGuild, ScopedGuild
from repositories.audit_log_repository import AuditLogRepository
from repositories.settings_repository import SettingsRepository
from utils.permissions import Level

router = APIRouter(prefix="/api/guilds/{guild_id}")


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    # "channel" | "role" | "text"
    type: str = "text"
    description: str = ""


# 編集を許可する設定キー（これ以外は参照も更新もしない）
EDITABLE_SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec("GUILD_NAME", "サークル名", "text", "週次サマリー等の表示に使う名称"),
    SettingSpec("BOT_LOG_CHANNEL_ID", "Bot ログチャンネル", "channel"),
    SettingSpec("DEFAULT_ANNOUNCE_CHANNEL_ID", "共通アナウンス先", "channel"),
    SettingSpec("DEFAULT_TASK_CHANNEL_ID", "タスク通知先", "channel"),
    SettingSpec("TODAY_LABEL_CHANNEL_ID", "「今日やること」通知先", "channel"),
    SettingSpec(
        "PROGRESS_DEFAULT_CHANNEL_ID",
        "進捗通知の共通送信先",
        "channel",
        "プロジェクト個別の指定が無い場合の送信先",
    ),
    SettingSpec("EXEC_ROLE_ID", "幹部ロール", "role"),
    SettingSpec("ADMIN_ROLE_ID", "Bot 管理者ロール", "role"),
)

_BY_KEY = {spec.key: spec for spec in EDITABLE_SETTINGS}


def _validate(spec: SettingSpec, value: str) -> str:
    value = str(value).strip()
    if spec.type in ("channel", "role") and value and not value.isdigit():
        raise HTTPException(
            status_code=400, detail=f"{spec.label} には ID（数字）を入力してください。"
        )
    return value


@router.get("/settings")
async def read_settings(scope: ScopedGuild):
    """編集対象の設定を現在値つきで返す（閲覧は参加者なら可）。"""
    repo = scope.bind(SettingsRepository(get_database()))
    stored = await repo.get_all()
    return {
        "settings": [
            {
                "key": spec.key,
                "label": spec.label,
                "type": spec.type,
                "description": spec.description,
                "value": stored.get(spec.key, ""),
            }
            for spec in EDITABLE_SETTINGS
        ],
        "can_edit": scope.level >= Level.L4,
    }


@router.patch("/settings")
async def update_settings(
    scope: AdminGuild,
    values: Annotated[dict[str, Any], Body(...)],
):
    """設定を更新する（管理者のみ）。

    保存後、PostgreSQL 構成では NOTIFY により bot プロセスの
    config キャッシュが無効化される。
    """
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="変更内容がありません。")
    unknown = [key for key in values if key not in _BY_KEY]
    if unknown:
        raise HTTPException(
            status_code=400, detail="この設定は変更できません: " + ", ".join(sorted(unknown))
        )

    repo = scope.bind(SettingsRepository(get_database()))
    before = await repo.get_all()
    changes: list[str] = []
    for key, raw in values.items():
        value = _validate(_BY_KEY[key], raw)
        if value:
            await repo.set(key, value)
        else:
            await repo.delete(key)
        changes.append(f"{key}: {before.get(key) or '（空）'} → {value or '（空）'}")

    await scope.bind(AuditLogRepository(get_database())).record(
        actor_id=scope.user_id,
        action="dashboard.settings",
        target="settings",
        detail=", ".join(changes),
    )
    return {"changed": list(values)}
