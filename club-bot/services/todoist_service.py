"""
Todoist 連携サービス（ギルド別・暗号化トークン版）

- トークンは todoist_configs テーブルに Fernet 暗号文で保存される。
  API 呼び出しの都度復号し、平文トークンはキャッシュしない。
- ギルドごとに TodoistServiceManager.for_guild(guild_id) で
  TodoistService を取得する。未登録・復号不可のギルドでは
  enabled=False の無効サービスが返り、全メソッドは no-op となる。
- 例外・ログにトークン（平文・暗号文）を含めない。
"""
from __future__ import annotations

import asyncio
from typing import Any

from repositories.todoist_config_repository import TodoistConfigRepository
from utils import crypto
from utils.db import Database
from utils.logger import get_logger

log = get_logger("todoist")

try:
    from todoist_api_python.api import TodoistAPI
except Exception:  # SDK 未インストール環境でも import 失敗しないように
    TodoistAPI = None  # type: ignore


class TodoistError(Exception):
    """TODOIST_API_FAILED に対応する例外。"""


def _to_list(result: Any) -> list:
    """SDK の戻り値を平坦なリストに正規化する。

    todoist-api-python v2 は list を直接返し、v3 はページ（list）を
    順に返す paginator を返すため、両方に対応する。
    """
    if result is None:
        return []
    if isinstance(result, list):
        return result
    out: list = []
    for item in result:
        if isinstance(item, list):
            out.extend(item)
        else:
            out.append(item)
    return out


async def validate_token(token: str) -> bool:
    """トークンの有効性を Todoist API で検証する（bool のみ返す）。

    例外・ログにトークンを含めない。検証に使ったクライアントは
    この関数スコープでのみ生存する。
    """
    if TodoistAPI is None:
        return False
    api = TodoistAPI(token)
    try:
        await asyncio.to_thread(lambda: _to_list(api.get_projects()))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Todoist トークン検証に失敗しました: %s", type(e).__name__)
        return False


class TodoistService:
    """ギルド別 Todoist クライアント。enabled=False の場合は全メソッド no-op。"""

    def __init__(self, token: str | None, project_id: str | None,
                 label_name: str):
        self.enabled = bool(token) and TodoistAPI is not None
        self._api = TodoistAPI(token) if self.enabled else None
        self.project_id = project_id or None
        self.label_name = label_name

    @classmethod
    def disabled(cls) -> "TodoistService":
        return cls(None, None, "今日やること")

    async def _run(self, fn, *args, **kwargs):
        """同期 SDK 呼び出しをスレッドへ逃がす。"""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.error("Todoist API 失敗: %s", type(e).__name__)
            raise TodoistError(type(e).__name__) from e

    # ---------- タスク CRUD ----------
    async def add_task(self, content: str, due_string: str | None = None,
                       priority: int | None = None,
                       description: str | None = None,
                       labels: list[str] | None = None,
                       section_id: str | None = None) -> Any | None:
        if not self.enabled:
            return None
        kwargs: dict[str, Any] = dict(
            content=content, due_string=due_string, priority=priority,
            description=description, labels=labels)
        if section_id:
            kwargs["section_id"] = section_id
        if self.project_id:
            kwargs["project_id"] = self.project_id
        try:
            return await self._run(self._api.add_task, **kwargs)
        except TodoistError:
            return None

    async def update_task(self, task_id: str, **kwargs) -> Any | None:
        if not self.enabled:
            return None
        try:
            return await self._run(self._api.update_task, task_id, **kwargs)
        except TodoistError:
            return None

    async def close_task(self, task_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            await self._run(self._api.close_task, task_id)
            return True
        except TodoistError:
            return False

    async def delete_task(self, task_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            await self._run(self._api.delete_task, task_id)
            return True
        except TodoistError:
            return False

    async def get_task(self, task_id: str) -> Any | None:
        if not self.enabled:
            return None
        try:
            return await self._run(self._api.get_task, task_id)
        except TodoistError:
            return None

    async def get_tasks(self, **kwargs) -> list[Any]:
        if not self.enabled:
            return []
        try:
            result = await self._run(self._api.get_tasks, **kwargs)
            return _to_list(result)
        except TodoistError:
            return []

    # ---------- プロジェクト / セクション ----------
    async def get_projects(self) -> list[Any]:
        if not self.enabled:
            return []
        try:
            result = await self._run(self._api.get_projects)
            return _to_list(result)
        except TodoistError:
            return []

    async def get_sections(self) -> list[Any]:
        """プロジェクトのセクション一覧（project_id 設定時はそのプロジェクト）。"""
        if not self.enabled:
            return []
        kwargs: dict[str, Any] = {}
        if self.project_id:
            kwargs["project_id"] = self.project_id
        try:
            result = await self._run(self._api.get_sections, **kwargs)
            return _to_list(result)
        except TodoistError:
            return []

    async def get_tasks_by_section(self, section_id: str) -> list[Any]:
        if not self.enabled:
            return []
        return await self.get_tasks(section_id=section_id)

    async def get_tasks_without_section(self) -> list[Any]:
        """セクション未設定のタスク（project_id 設定時はそのプロジェクト内）。"""
        if not self.enabled:
            return []
        kwargs: dict[str, Any] = {}
        if self.project_id:
            kwargs["project_id"] = self.project_id
        tasks = await self.get_tasks(**kwargs)
        return [t for t in tasks if not getattr(t, "section_id", None)]

    # ---------- ラベル ----------
    async def get_labels(self) -> list[Any]:
        if not self.enabled:
            return []
        try:
            result = await self._run(self._api.get_labels)
            return _to_list(result)
        except TodoistError:
            return []

    async def add_label(self, name: str) -> Any | None:
        if not self.enabled:
            return None
        try:
            return await self._run(self._api.add_label, name=name)
        except TodoistError:
            return None

    async def ensure_label(self) -> bool:
        """「今日やること」ラベルが存在しなければ作成する。"""
        if not self.enabled:
            return False
        labels = await self.get_labels()
        if any(getattr(l, "name", None) == self.label_name for l in labels):
            return True
        return await self.add_label(self.label_name) is not None

    async def get_today_labeled_tasks(self) -> list[Any]:
        """「今日やること」ラベル付きのタスクを取得する。"""
        if not self.enabled:
            return []
        return await self.get_tasks(label=self.label_name)

    # ---------- 検索 / ラベル付与 ----------
    async def find_open_tasks_by_name(self, name: str) -> list[Any]:
        """未完了タスクをタスク名の完全一致で検索する。"""
        if not self.enabled:
            return []
        kwargs: dict[str, Any] = {}
        if self.project_id:
            kwargs["project_id"] = self.project_id
        tasks = await self.get_tasks(**kwargs)
        return [t for t in tasks if getattr(t, "content", None) == name]

    async def add_today_label(self, task_id: str) -> bool:
        """タスクに「今日やること」ラベルを付与する（既存ラベルは保持）。"""
        if not self.enabled:
            return False
        task = await self.get_task(task_id)
        if task is None:
            return False
        labels = list(getattr(task, "labels", None) or [])
        if self.label_name not in labels:
            labels.append(self.label_name)
        result = await self.update_task(task_id, labels=labels)
        return result is not None


class TodoistServiceManager:
    """ギルド別 TodoistService のファクトリ。

    for_guild() の都度、DB の暗号文を復号して新しいサービスを構築する。
    平文トークンをキャッシュしない（メモリ上の生存期間を最小化する）。
    """

    def __init__(self, db: Database):
        self._repo = TodoistConfigRepository(db)

    async def for_guild(self, guild_id: int) -> TodoistService:
        """ギルド別の TodoistService を返す（未登録・復号不可なら無効）。"""
        try:
            cfg = await self._repo.get(guild_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Todoist 設定の取得に失敗 (guild=%s): %s",
                        guild_id, type(e).__name__)
            return TodoistService.disabled()
        if not cfg or not cfg["enabled_flag"]:
            return TodoistService.disabled()
        try:
            token = crypto.decrypt_token(cfg["api_token_encrypted"])
        except crypto.EncryptionKeyMissingError:
            log.error("ENCRYPTION_KEY 未設定/不正のため Todoist を利用できません"
                      " (guild=%s)", guild_id)
            return TodoistService.disabled()
        except crypto.TokenDecryptError:
            log.warning("Todoist トークンの復号に失敗しました。"
                        "/todoist-setup で再登録してください (guild=%s)", guild_id)
            return TodoistService.disabled()
        return TodoistService(token, cfg.get("project_id"),
                              cfg.get("today_label_name") or "今日やること")

    async def is_configured(self, guild_id: int) -> bool:
        """設定行の存在のみ確認する（復号はしない）。"""
        try:
            return await self._repo.get(guild_id) is not None
        except Exception:  # noqa: BLE001
            return False
