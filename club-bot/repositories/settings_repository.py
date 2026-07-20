"""
<<<<<<< HEAD
設定リポジトリ（改訂版）

データベースから設定を読み書きするためのリポジトリ
=======
設定リポジトリ（マルチテナント版）

データベースから設定を読み書きするためのリポジトリ。
設定は settings テーブルに (guild_id, setting_key) 単位で保存される。
>>>>>>> 803617a (v4.0)
"""
from __future__ import annotations

from typing import Any

<<<<<<< HEAD
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
=======
from repositories.base import BaseRepository
from utils.db import Database


class SettingsRepository(BaseRepository):
    """設定をデータベースで管理するリポジトリ"""

    def __init__(self, db: Database):
        super().__init__(db)

    async def get(self, guild_id: int, key: str, default: str | None = None) -> str | None:
        """設定値を取得する"""
        value = await self.db.get_setting(guild_id, key)
        return value if value is not None else default

    async def get_int(self, guild_id: int, key: str, default: int | None = None) -> int | None:
        """設定値を整数で取得する"""
        value = await self.get(guild_id, key)
>>>>>>> 803617a (v4.0)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

<<<<<<< HEAD
    async def get_int_list(self, key: str) -> list[int]:
        """設定値をカンマ区切りの整数リストで取得する"""
        value = await self.get(key, "")
=======
    async def get_int_list(self, guild_id: int, key: str) -> list[int]:
        """設定値をカンマ区切りの整数リストで取得する"""
        value = await self.get(guild_id, key, "")
>>>>>>> 803617a (v4.0)
        if not value:
            return []
        result = []
        for part in value.split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
        return result

<<<<<<< HEAD
    async def get_str_list(self, key: str) -> list[str]:
        """設定値をカンマ区切りの文字列リストで取得する"""
        value = await self.get(key, "")
=======
    async def get_str_list(self, guild_id: int, key: str) -> list[str]:
        """設定値をカンマ区切りの文字列リストで取得する"""
        value = await self.get(guild_id, key, "")
>>>>>>> 803617a (v4.0)
        if not value:
            return []
        return [part.strip() for part in value.split(",") if part.strip()]

<<<<<<< HEAD
    async def get_dict(self, key: str) -> dict[str, str]:
        """設定値をカンマ区切りの key:value 形式の辞書で取得する"""
        value = await self.get(key, "")
=======
    async def get_dict(self, guild_id: int, key: str) -> dict[str, str]:
        """設定値をカンマ区切りの key:value 形式の辞書で取得する"""
        value = await self.get(guild_id, key, "")
>>>>>>> 803617a (v4.0)
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

<<<<<<< HEAD
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
=======
    async def set(self, guild_id: int, key: str, value: Any) -> None:
        """設定値を保存する"""
        await self.db.set_setting(guild_id, key, str(value))

    async def set_if_absent(self, guild_id: int, key: str, value: Any) -> bool:
        """設定値が未存在の場合のみ保存する。保存した場合は True。"""
        current = await self.get(guild_id, key)
        if current is not None:
            return False
        await self.set(guild_id, key, value)
        return True

    async def delete(self, guild_id: int, key: str) -> bool:
        """設定値を削除する"""
        return await self.db.delete_setting(guild_id, key)

    async def get_all(self, guild_id: int) -> dict[str, str]:
        """全ての設定を取得する"""
        return await self.db.get_all_settings(guild_id)

    async def set_from_env(self, guild_id: int, key: str, env_value: str | None,
                           default: str | None = None) -> None:
>>>>>>> 803617a (v4.0)
        """
        環境変数から設定を読み込む（環境変数があれば優先）
        """
        if env_value:
<<<<<<< HEAD
            await self.set(key, env_value)
        elif default is not None:
            current = await self.get(key)
            if current is None:
                await self.set(key, default)
=======
            await self.set(guild_id, key, env_value)
        elif default is not None:
            current = await self.get(guild_id, key)
            if current is None:
                await self.set(guild_id, key, default)
>>>>>>> 803617a (v4.0)
