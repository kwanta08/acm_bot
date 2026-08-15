"""discord_name_cache テーブルの CRUD（Discord 表示名のギルド別キャッシュ）。

bot がギルドキャッシュから名前を書き込み、ダッシュボード
（Bot トークンを持たない別プロセス）が ID → 表示名の解決に読む。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
これは**キャッシュでありデータの正本ではない**（消えても bot の
起動時同期で復元される）。各テーブルは従来どおり ID を保持し、
表示名への変換は表示層（dashboard/display.py）が行う。
"""

from __future__ import annotations

from collections.abc import Iterable

from repositories.base import BaseRepository
from utils.db import Database

# entity_type の値（スキーマの CHECK 制約と一致させる）
ENTITY_USER = "user"
ENTITY_CHANNEL = "channel"


class NameCacheRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def upsert(
        self, guild_id: int, entity_type: str, entity_id: str, name: str, updated_at: str
    ) -> None:
        """1件の名前を登録・更新する。"""
        await self.db.execute(
            """
            INSERT INTO discord_name_cache
                (guild_id, entity_type, entity_id, name, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, entity_type, entity_id) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (guild_id, entity_type, str(entity_id), name, updated_at),
        )

    async def upsert_many(
        self, guild_id: int, entity_type: str, pairs: Iterable[tuple[str, str]], updated_at: str
    ) -> int:
        """(entity_id, name) の一括登録・更新。戻り値は処理件数。

        既存の行は上書きし、pairs に無い既存行は**消さない**
        （ユーザー名の同期で使う。退会者の最後の名前を残すため）。
        """
        count = 0
        for entity_id, name in pairs:
            await self.upsert(guild_id, entity_type, entity_id, name, updated_at)
            count += 1
        return count

    async def replace_all(
        self, guild_id: int, entity_type: str, pairs: Iterable[tuple[str, str]], updated_at: str
    ) -> int:
        """種別ごとの全入れ替え。戻り値は登録件数。

        既存行を消してから入れ直す（チャンネルの同期で使う。
        bot 停止中に削除されたチャンネルの行を残さないため）。
        """
        await self.db.execute(
            "DELETE FROM discord_name_cache WHERE guild_id = ? AND entity_type = ?",
            (guild_id, entity_type),
        )
        return await self.upsert_many(guild_id, entity_type, pairs, updated_at)

    async def delete(self, guild_id: int, entity_type: str, entity_id: str) -> bool:
        """1件を削除する（チャンネル削除イベント用）。対象が無ければ False。"""
        cur = await self.db.execute(
            "DELETE FROM discord_name_cache"
            " WHERE guild_id = ? AND entity_type = ? AND entity_id = ?",
            (guild_id, entity_type, str(entity_id)),
        )
        return cur.rowcount > 0

    async def names(self, guild_id: int, entity_type: str) -> dict[str, str]:
        """entity_id → name の辞書を返す（1クエリ。表示時の一括解決用）。"""
        rows = await self.db.fetchall(
            "SELECT entity_id, name FROM discord_name_cache WHERE guild_id = ? AND entity_type = ?",
            (guild_id, entity_type),
        )
        return {str(r["entity_id"]): str(r["name"]) for r in rows}
