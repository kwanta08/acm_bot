"""
Todoist 連携サービス（仕様 11.3, 7.1）。

todoist-api-python SDK を用いる。Todoist 無効時（トークン未設定）は
enabled=False となり、各メソッドは安全に no-op / None を返す。
仕様 17.3: タスク基盤を差し替え可能な抽象層として実装する。
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
        self.enabled = bool(config.todoist_api_token) and TodoistAPI is not None
        self._api = TodoistAPI(config.todoist_api_token) if self.enabled else None
        self.project_id = config.todoist_project_id or None
        self.label_name = config.today_label_name

    async def _run(self, fn, *args, **kwargs):
        """同期 SDK 呼び出しをスレッドへ逃がす。"""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.error("Todoist API 失敗: %s", e)
            raise TodoistError(str(e)) from e

    # ---------- タスク CRUD ----------
    async def add_task(self, content: str, due_string: str | None = None,
                       priority: int | None = None,
                       description: str | None = None,
                       section_id: str | None = None) -> str | None:
        """タスクを作成し Todoist タスク ID を返す。無効時は None。

        section_id を渡すと、その班セクションにタスクを配置する。
        """
        if not self.enabled:
            return None
        kwargs: dict[str, Any] = {"content": content}
        if self.project_id:
            kwargs["project_id"] = self.project_id
        if section_id:
            kwargs["section_id"] = str(section_id)
        if due_string:
            kwargs["due_string"] = due_string
        if priority:  # Todoist は 1(低)〜4(高)。仕様も 1〜4
            kwargs["priority"] = max(1, min(4, priority))
        if description:
            kwargs["description"] = description
        task = await self._run(self._api.add_task, **kwargs)
        return str(task.id)

    async def close_task(self, todoist_task_id: str) -> bool:
        if not self.enabled or not todoist_task_id:
            return False
        return await self._run(self._api.close_task, task_id=todoist_task_id)

    async def delete_task(self, todoist_task_id: str) -> bool:
        if not self.enabled or not todoist_task_id:
            return False
        return await self._run(self._api.delete_task, task_id=todoist_task_id)

    def _get_tasks_sync(self):
        kwargs = {"project_id": self.project_id} if self.project_id else {}
        pages = self._api.get_tasks(**kwargs)
        return [t for page in pages for t in page]

    async def get_tasks(self) -> list[Any]:
        if not self.enabled:
            return []
        return await self._run(self._get_tasks_sync)

    # ---------- セクション ----------
    def _get_sections_sync(self):
        kwargs = {"project_id": self.project_id} if self.project_id else {}
        pages = self._api.get_sections(**kwargs)
        return [s for page in pages for s in page]

    async def get_sections(self) -> list[Any]:
        """プロジェクトのセクション一覧を取得（無効時は空）。"""
        if not self.enabled:
            return []
        return await self._run(self._get_sections_sync)

    async def get_tasks_by_section(self, section_id: str) -> list[Any]:
        """指定セクション内の未完了タスクを取得する。"""
        if not self.enabled:
            return []
        tasks = await self.get_tasks()
        return [t for t in tasks if str(getattr(t, "section_id", "") or "") == str(section_id)]

    def _get_tasks_by_section_sync(self, section_id: str):
        kwargs = {"section_id": str(section_id)}
        if self.project_id:
            kwargs["project_id"] = self.project_id
        result = self._api.get_tasks(**kwargs)
        # ページネーターの場合と生リストの場合の両方に対応
        tasks = []
        for item in result:
            if hasattr(item, "id"):          # Task オブジェクト
                tasks.append(item)
            elif hasattr(item, "__iter__"):  # ページ（リスト）
                tasks.extend(item)
        return tasks

    async def get_tasks_by_section(self, section_id: str) -> list[Any]:
        if not self.enabled:
            return []
        return await self._run(self._get_tasks_by_section_sync, section_id)

    # ---------- ラベル ----------
    def _get_labels_sync(self):
        pages = self._api.get_labels()
        return [l for page in pages for l in page]

    async def ensure_label(self) -> None:
        """「今日やること」ラベルが無ければ作成する。"""
        if not self.enabled:
            return
        labels = await self._run(self._get_labels_sync)
        if not any(l.name == self.label_name for l in labels):
            await self._run(self._api.add_label, name=self.label_name)
            log.info("Todoist ラベルを作成: %s", self.label_name)

    async def find_open_tasks_by_name(self, name: str) -> list[Any]:
        """未完了タスクを完全一致で検索（仕様 11.3.3 /today）。"""
        if not self.enabled:
            return []
        tasks = await self.get_tasks()
        return [t for t in tasks if t.content == name]

    async def add_today_label(self, todoist_task_id: str) -> bool:
        """対象タスクに「今日やること」ラベルを追加（既存ラベルは残す）。"""
        if not self.enabled:
            return False
        await self.ensure_label()
        tasks = await self.get_tasks()
        target = next((t for t in tasks if str(t.id) == str(todoist_task_id)), None)
        if not target:
            return False
        labels = list(getattr(target, "labels", []) or [])
        if self.label_name not in labels:
            labels.append(self.label_name)
        await self._run(self._api.update_task, task_id=str(todoist_task_id), labels=labels)
        return True

    async def get_today_labeled_tasks(self) -> list[Any]:
        if not self.enabled:
            return []
        tasks = await self.get_tasks()
        return [t for t in tasks if self.label_name in (getattr(t, "labels", []) or [])]
