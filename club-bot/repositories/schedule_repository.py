"""schedules / schedule_options / schedule_votes の CRUD（仕様 10.3〜10.5）。

マルチテナント版: 全メソッドが guild_id を必須引数に取る。
子テーブル（options/votes）も親の guild_id を冗長保持してスコープを固定する。
"""

from __future__ import annotations

from typing import Any

from repositories.base import BaseRepository
from utils.db import Database
from utils.parser import now, to_iso


class ScheduleRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    # ---------- schedules ----------
    async def create_schedule(
        self,
        guild_id: int,
        schedule_id: str,
        title: str,
        description: str | None,
        place: str | None,
        target_role_id: str | None,
        deadline_iso: str,
        created_by: str,
        channel_id: str,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO schedules
                (schedule_id, guild_id, title, description, place, target_role_id, deadline,
                 created_by, channel_id, closed_flag, reminder_sent_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                schedule_id,
                guild_id,
                title,
                description,
                place,
                target_role_id,
                deadline_iso,
                created_by,
                channel_id,
            ),
        )

    async def get_schedule(
        self, guild_id: int, schedule_id: str, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        """予定を1件返す。

        **既定では削除済みを返さない。** これにより /schedule status や
        /schedule close などが個別の条件を書かずに削除済みを除外できる
        （安全側を既定にする）。削除済みを見たい呼び出し
        （/schedule restore・「削除されています」の案内）だけが
        include_deleted=True を渡す。
        """
        sql = "SELECT * FROM schedules WHERE guild_id = ? AND schedule_id = ?"
        if not include_deleted:
            sql += " AND deleted_flag = 0"
        row = await self.db.fetchone(sql, (guild_id, schedule_id))
        return dict(row) if row else None

    async def list_open_schedules(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ? AND closed_flag = 0 AND deleted_flag = 0"
            " ORDER BY deadline",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def list_due_schedules(self, guild_id: int, deadline_iso: str) -> list[dict[str, Any]]:
        """締切を過ぎた未クローズの投票を返す。"""
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ? AND closed_flag = 0 AND deleted_flag = 0"
            " AND deadline <= ?",
            (guild_id, deadline_iso),
        )
        return [dict(r) for r in rows]

    async def list_reminder_candidates(
        self, guild_id: int, from_iso: str, to_iso_: str
    ) -> list[dict[str, Any]]:
        """締切が [from, to] にあり、まだ催促未送信の投票を返す。"""
        rows = await self.db.fetchall(
            """
            SELECT * FROM schedules
            WHERE guild_id = ? AND closed_flag = 0 AND deleted_flag = 0
              AND reminder_sent_flag = 0
              AND deadline >= ? AND deadline <= ?
            """,
            (guild_id, from_iso, to_iso_),
        )
        return [dict(r) for r in rows]

    async def close_schedule(self, guild_id: int, schedule_id: str) -> None:
        await self.db.execute(
            "UPDATE schedules SET closed_flag = 1 WHERE guild_id = ? AND schedule_id = ?",
            (guild_id, schedule_id),
        )

    async def mark_reminder_sent(self, guild_id: int, schedule_id: str) -> None:
        await self.db.execute(
            "UPDATE schedules SET reminder_sent_flag = 1 WHERE guild_id = ? AND schedule_id = ?",
            (guild_id, schedule_id),
        )

    async def soft_delete_schedule(self, guild_id: int, schedule_id: str) -> None:
        """予定を論理削除する（票は消さない）。

        **同時に closed_flag も立てる。** 投票メッセージを消した時点で
        投票は現実に終わっているので嘘ではなく、こうしておくと自動催促
        （list_reminder_candidates）・自動締切（list_due_schedules）・
        開催中の候補（list_open_schedules）が**既存の条件式だけで**止まる。
        復元用の抑止フラグを別に足す必要がなく、restore は deleted_flag を
        戻すだけで済む。
        """
        await self.db.execute(
            "UPDATE schedules SET deleted_flag = 1, closed_flag = 1"
            " WHERE guild_id = ? AND schedule_id = ?",
            (guild_id, schedule_id),
        )

    async def restore_schedule(self, guild_id: int, schedule_id: str) -> bool:
        """論理削除を取り消す。**deleted_flag しか書き換えない。**

        締切済みとして戻る（closed_flag は削除時に立っている）。投票
        メッセージは戻らないので、投票を再開しないほうが実態に合う。
        """
        cur = await self.db.execute(
            "UPDATE schedules SET deleted_flag = 0"
            " WHERE guild_id = ? AND schedule_id = ? AND deleted_flag = 1",
            (guild_id, schedule_id),
        )
        return cur.rowcount > 0

    async def list_deleted_schedules(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ? AND deleted_flag = 1"
            " ORDER BY deadline DESC",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    # ---------- options ----------
    async def add_option(
        self,
        guild_id: int,
        option_id: str,
        schedule_id: str,
        label: str,
        start_at: str,
        end_at: str | None,
        message_id: str | None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO schedule_options
                (option_id, guild_id, schedule_id, label, start_at, end_at, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (option_id, guild_id, schedule_id, label, start_at, end_at, message_id),
        )

    async def set_option_message(self, guild_id: int, option_id: str, message_id: str) -> None:
        await self.db.execute(
            "UPDATE schedule_options SET message_id = ? WHERE guild_id = ? AND option_id = ?",
            (message_id, guild_id, option_id),
        )

    async def list_options(self, guild_id: int, schedule_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedule_options WHERE guild_id = ? AND schedule_id = ? ORDER BY start_at",
            (guild_id, schedule_id),
        )
        return [dict(r) for r in rows]

    async def get_option_by_message(self, guild_id: int, message_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM schedule_options WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )
        return dict(row) if row else None

    async def list_schedule_votes(self, guild_id: int, schedule_id: str) -> list[dict[str, Any]]:
        """予定配下の全候補への回答を返す（ダッシュボードの出欠ピボット用）。

        候補ごとではなく予定単位で一括取得する（1クエリ。N+1 を作らない）。
        """
        rows = await self.db.fetchall(
            """
            SELECT v.option_id, v.user_id, v.status
            FROM schedule_votes v
            JOIN schedule_options o
              ON o.option_id = v.option_id AND o.guild_id = v.guild_id
            WHERE v.guild_id = ? AND o.schedule_id = ?
            """,
            (guild_id, schedule_id),
        )
        return [dict(r) for r in rows]

    async def list_option_labels(self, guild_id: int) -> dict[str, str]:
        """option_id → 候補ラベルの辞書を返す（1クエリ）。

        ダッシュボードが出欠回答の候補 ID を表示用ラベルへ解決するために使う
        （行ごとに引かず一括で読む。N+1 を作らない）。
        """
        rows = await self.db.fetchall(
            "SELECT option_id, label FROM schedule_options WHERE guild_id = ?", (guild_id,)
        )
        return {str(r["option_id"]): str(r["label"]) for r in rows}

    # ---------- votes ----------
    async def set_vote(self, guild_id: int, option_id: str, user_id: str, status: str) -> None:
        """1候補1ユーザー1状態（仕様 11.2.3）。upsert。"""
        await self.db.execute(
            """
            INSERT INTO schedule_votes (guild_id, option_id, user_id, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, option_id, user_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (guild_id, option_id, user_id, status, to_iso(now())),
        )

    async def remove_vote(self, guild_id: int, option_id: str, user_id: str) -> None:
        await self.db.execute(
            "DELETE FROM schedule_votes WHERE guild_id = ? AND option_id = ? AND user_id = ?",
            (guild_id, option_id, user_id),
        )

    async def list_votes(self, guild_id: int, option_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedule_votes WHERE guild_id = ? AND option_id = ?",
            (guild_id, option_id),
        )
        return [dict(r) for r in rows]

    async def list_voters_for_schedule(self, guild_id: int, schedule_id: str) -> set[str]:
        """投票内のいずれかの候補に1票でも入れたユーザー ID 集合。"""
        rows = await self.db.fetchall(
            """
            SELECT DISTINCT v.user_id
            FROM schedule_votes v
            JOIN schedule_options o ON v.option_id = o.option_id
            WHERE v.guild_id = ? AND o.guild_id = ? AND o.schedule_id = ?
            """,
            (guild_id, guild_id, schedule_id),
        )
        return {r["user_id"] for r in rows}

    async def list_closed_schedules(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ? AND closed_flag = 1 AND deleted_flag = 0"
            " ORDER BY deadline DESC",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def list_all(self, guild_id: int) -> list[dict[str, Any]]:
        """クローズ済みも含む全スケジュールを返す（集計用）。削除済みは除く。"""
        rows = await self.db.fetchall(
            "SELECT * FROM schedules WHERE guild_id = ? AND deleted_flag = 0"
            " ORDER BY deadline DESC",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def update_deadline(self, guild_id: int, schedule_id: str, deadline_iso: str) -> None:
        """締切を変更する。再通知フラグもリセットし、変更後の期間で再度リマインドできるようにする。"""
        await self.db.execute(
            "UPDATE schedules SET deadline = ?, reminder_sent_flag = 0"
            " WHERE guild_id = ? AND schedule_id = ?",
            (deadline_iso, guild_id, schedule_id),
        )
