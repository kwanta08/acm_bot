"""
設定リポジトリ（改訂版）

データベースから設定を読み書きするためのリポジトリ
"""
from __future__ import annotations

from typing import Any

from utils.db import Database


class SettingsRepository:
    """設定をデータベースで管理するリポジトリ"""

    def __init__(self, db: Database):
        self.db = db

    async def get(self, key: str, default: str | None = None) -> str | None:
        """設定値を取得する"""
        value = await self.db.get_setting(key)
        return value if value is not None else default

    async def get_int(self, key: str, default: int | None = None) -> int | None:
        """設定値を整数で取得する"""
        value = await self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    async def get_int_list(self, key: str) -> list[int]:
        """設定値をカンマ区切りの整数リストで取得する"""
        value = await self.get(key, "")
        if not value:
            return []
        result = []
        for part in value.split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
        return result

    async def get_str_list(self, key: str) -> list[str]:
        """設定値をカンマ区切りの文字列リストで取得する"""
        value = await self.get(key, "")
        if not value:
            return []
        return [part.strip() for part in value.split(",") if part.strip()]

    async def get_dict(self, key: str) -> dict[str, str]:
        """設定値をカンマ区切りの key:value 形式の辞書で取得する"""
        value = await self.get(key, "")
        if not value:
            return {}
        result: dict[str, str] = {}
        for part in value.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            result[k.strip()] = v.strip()
        return result

    async def set(self, key: str, value: Any) -> None:
        """設定値を保存する"""
        await self.db.set_setting(key, str(value))

    async def delete(self, key: str) -> bool:
        """設定値を削除する"""
        return await self.db.delete_setting(key)

    async def get_all(self) -> dict[str, str]:
        """全ての設定を取得する"""
        return await self.db.get_all_settings()

    async def set_from_env(self, key: str, env_value: str | None, default: str | None = None) -> None:
        """
        環境変数から設定を読み込む（環境変数があれば優先）
        """
        if env_value:
            await self.set(key, env_value)
        elif default is not None:
            current = await self.get(key)
            if current is None:
                await self.set(key, default)
