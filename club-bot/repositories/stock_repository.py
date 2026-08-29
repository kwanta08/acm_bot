"""stock_items / stock_movements テーブルの CRUD（G4-8）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
品目は (guild_id, item_name) で一意。

**品目の初期値は持たない。** 何を在庫管理するかはサークルごとに違う
（AGENTS.md「組織構造は可変」）。
"""

from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database


class StockRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    # ---------- 品目 ----------
    async def get_item(self, guild_id: int, item_name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM stock_items WHERE guild_id = ? AND item_name = ?",
            (guild_id, item_name),
        )
        return dict(row) if row else None

    async def create_item(
        self,
        guild_id: int,
        item_name: str,
        unit: str,
        quantity: float,
        created_by: str,
        now_text: str,
        threshold: float | None = None,
        note: str | None = None,
    ) -> int:
        """品目を新規登録する。戻り値は stock_item_id。

        既に無効化されている同名品目があれば有効化して数量を足す
        （layer_keta の add と同じ扱い。名前で一意なので作り直せない）。
        """
        existing = await self.get_item(guild_id, item_name)
        if existing is not None:
            await self.db.execute(
                "UPDATE stock_items SET active_flag = 1, unit = ?,"
                " quantity = quantity + ?, updated_at = ?"
                " WHERE guild_id = ? AND stock_item_id = ?",
                (unit, quantity, now_text, guild_id, existing["stock_item_id"]),
            )
            return int(existing["stock_item_id"])
        cur = await self.db.execute(
            """
            INSERT INTO stock_items
                (guild_id, item_name, unit, quantity, threshold, note,
                 active_flag, low_notified_flag, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
            """,
            (
                guild_id,
                item_name,
                unit,
                quantity,
                threshold,
                note,
                created_by,
                now_text,
                now_text,
            ),
        )
        return cur.lastrowid

    async def deactivate_item(self, guild_id: int, item_name: str) -> bool:
        """品目を無効化する（履歴は消さない）。対象が無ければ False。"""
        cur = await self.db.execute(
            "UPDATE stock_items SET active_flag = 0 WHERE guild_id = ? AND item_name = ?"
            " AND active_flag = 1",
            (guild_id, item_name),
        )
        return cur.rowcount > 0

    async def list_items(self, guild_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM stock_items WHERE guild_id = ?"
        if active_only:
            sql += " AND active_flag = 1"
        sql += " ORDER BY item_name"
        rows = await self.db.fetchall(sql, (guild_id,))
        return [dict(r) for r in rows]

    async def list_item_names(self, guild_id: int) -> list[str]:
        """有効な品目名（オートコンプリート用）。"""
        rows = await self.db.fetchall(
            "SELECT item_name FROM stock_items WHERE guild_id = ? AND active_flag = 1"
            " ORDER BY item_name",
            (guild_id,),
        )
        return [str(r["item_name"]) for r in rows]

    async def set_threshold(
        self, guild_id: int, item_name: str, threshold: float | None, now_text: str
    ) -> bool:
        """発注アラートの閾値を設定する。None で解除。"""
        cur = await self.db.execute(
            "UPDATE stock_items SET threshold = ?, updated_at = ?"
            " WHERE guild_id = ? AND item_name = ?",
            (threshold, now_text, guild_id, item_name),
        )
        return cur.rowcount > 0

    # ---------- 増減 ----------
    async def apply_movement(
        self,
        guild_id: int,
        stock_item_id: int,
        delta: float,
        user_id: str,
        now_text: str,
        reason: str | None = None,
    ) -> None:
        """数量を増減し、履歴を1行残す。

        数量は**負にしない**（在庫が -3 本になることは物理的に無い）。
        使いすぎの申告は 0 で止め、呼び出し側が実際の増減量を利用者へ伝える。
        """
        await self.db.execute(
            "UPDATE stock_items SET quantity = MAX(quantity + ?, 0), updated_at = ?"
            " WHERE guild_id = ? AND stock_item_id = ?",
            (delta, now_text, guild_id, stock_item_id),
        )
        await self.db.execute(
            """
            INSERT INTO stock_movements
                (guild_id, stock_item_id, delta, reason, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, stock_item_id, delta, reason, user_id, now_text),
        )

    async def list_movements(
        self, guild_id: int, stock_item_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM stock_movements WHERE guild_id = ? AND stock_item_id = ?"
            " ORDER BY movement_id DESC LIMIT ?",
            (guild_id, stock_item_id, limit),
        )
        return [dict(r) for r in rows]

    # ---------- 閾値割れの通知状態 ----------
    async def set_low_notified(self, guild_id: int, stock_item_id: int, notified: bool) -> None:
        """即時通知を送ったかどうかを記録する。

        閾値以上へ戻ったときに False へ戻すので、割り込むたびに1回だけ飛ぶ。
        `reminders_log` を使わないのは、**同じ品目が何度も割りうる**ため
        （送信済みキーを作ると2回目以降が永久に飛ばない）。
        """
        await self.db.execute(
            "UPDATE stock_items SET low_notified_flag = ? WHERE guild_id = ? AND stock_item_id = ?",
            (1 if notified else 0, guild_id, stock_item_id),
        )
