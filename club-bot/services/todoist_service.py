"""
Todoist 連携サービス（改訂版）

todoist-api-python SDK を用いる。Todoist 無効時（トークン未設定）は
enabled=False となり、各メソッドは戻り値に no-op / None を返す。
改訂版: 設定再読み込みメソッドを追加
"""
from __future__ import annotations

import asyncio
from typing import Any

from config import config
from utils.logger import get_logger

log = get_logger("todoist")

try:
    from todoist_api_python.api import TodoistAPI
except Exception:  # SDK 未インストール環境でも import 失敗しないように
    TodoistAPI = None  # type: ignore


class TodoistError(Exception):
    """TODOIST_API_FAILED に対応する例外。"""


class TodoistService:
    def __init__(self):
        self.reload_config()

    def reload_config(self) -> None:
        """config から設定を再読み込みする"""
        self.enabled = bool(config.todoist_api_token) and TodoistAPI is not None
        self._api = TodoistAPI(config.todoist_api_token) if self.enabled else None
        self.project_id = config.todoist_project_id or None
        self.label_name = config.today_label_name

    async def _run(self, fn, *args, **kwargs):
        """
同期 SDK 呼び出しをスレッドへ逃がす。
"""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.error("Todoist API 失敗: %s", e)
            raise TodoistError(str(e)) from e

    # ---------- タスク CRUD ----------
    async def add_task(self, content: str, due_string: str | None = None,
                       priority: int | None = None,
                       description: str | None = None,
                       labels: list[str] | None = None) -> dict[str, Any] | None:
        """
        タスクを追加する
        """
        if not self.enabled or self._api is None:
            return None
        try:
            result = await self._run(
                self._api.add_task,
                content=content,
                due_string=due_string,
                priority=priority,
                description=description,
                labels=labels,
            )
            return result
        except TodoistError:
            return None

    async def update_task(self, task_id: str, **kwargs) -> dict[str, Any] | None:
        """
        タスクを更新する
        """
        if not self.enabled or self._api is None:
            return None
        try:
            result = await self._run(self._api.update_task, task_id, **kwargs)
            return result
        except TodoistError:
            return None

    async def close_task(self, task_id: str) -> bool:
        """
        タスクを完了する
        """
        if not self.enabled or self._api is None:
            return False
        try:
            await self._run(self._api.close_task, task_id)
            return True
        except TodoistError:
            return False

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """
        タスクを取得する
        """
        if not self.enabled or self._api is None:
            return None
        try:
            result = await self._run(self._api.get_task, task_id)
            return result
        except TodoistError:
            return None

    async def get_tasks(self, **kwargs) -> list[dict[str, Any]]:
        """
        タスク一覧を取得する
        """
        if not self.enabled or self._api is None:
            return []
        try:
            result = await self._run(self._api.get_tasks, **kwargs)
            return result
        except TodoistError:
            return []

    async def get_all_tasks(self) -> list[dict[str, Any]]:
        """
        全てのタスクを取得する
        """
        if not self.enabled or self._api is None:
            return []
        try:
            result = await self._run(self._api.get_all_tasks)
            return result
        except TodoistError:
            return []

    async def get_labels(self) -> list[dict[str, Any]]:
        """
        ラベル一覧を取得する
        """
        if not self.enabled or self._api is None:
            return []
        try:
            result = await self._run(self._api.get_labels)
            return result
        except TodoistError:
            return []

    async def add_label(self, name: str) -> dict[str, Any] | None:
        """
        ラベルを追加する
        """
        if not self.enabled or self._api is None:
            return None
        try:
            result = await self._run(self._api.add_label, name=name)
            return result
        except TodoistError:
            return None

    async def get_projects(self) -> list[dict[str, Any]]:
        """
        プロジェクト一覧を取得する
        """
        if not self.enabled or self._api is None:
            return []
        try:
            result = await self._run(self._api.get_projects)
            return result
        except TodoistError:
            return []

    # ---------- ----------
    async def sync_tasks(self) -> list[dict[str, Any]]:
        """
        Todoist からタスクを同期する
        """
        if not self.enabled or self._api is None or not self.project_id:
            return []
        try:
            result = await self.get_tasks(project_id=self.project_id)
            return result
        except TodoistError:
            return []

    async def get_today_tasks(self) -> list[dict[str, Any]]:
        """
        今日やることラベルのタスクを取得する
        """
        if not self.enabled or self._api is None:
            return []
        try:
            result = await self.get_tasks(label_id=self.label_name)
            return result
        except TodoistError:
            return []
