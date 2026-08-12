"""guilds テーブル（ギルド台帳）の CRUD。

参加中ギルドの登録簿。新規ギルド参加時・起動時の自動セットアップで
冪等に登録・名称更新される。guild_id がそのまま PK。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from repositories.base import BaseRepository
from utils.db import TABLE_DDL, Database
from utils.parser import from_iso, now, to_iso


def purge_target_tables() -> tuple[str, ...]:
    """データ削除の対象テーブルを返す。

    **ホワイトリストを手で持たず TABLE_DDL の全テーブルから導出する。**
    テーブルを追加したときに消し漏れが出ないようにするため
    （tests/test_data_purge.py が網羅を検証する）。

    順序は TABLE_DDL の逆順（guilds だけ最後）。schedule_options →
    schedules のように後から定義したテーブルが先のテーブルを参照するため、
    逆順にすると子から先に消える。ON DELETE CASCADE があるのでどちらの順でも
    最終的には消えるが、親を先に消すと子が連鎖削除されて DELETE の
    rowcount に現れず、削除件数のログが実際より少なくなる。
    """
    others = tuple(name for name in reversed(list(TABLE_DDL))
                   if name != "guilds")
    return (*others, "guilds")


class GuildRepository(BaseRepository):
    def __init__(self, db: Database):
        super().__init__(db)

    async def ensure(self, guild_id: int, guild_name: str) -> None:
        """ギルドを台帳へ冪等登録する（既存なら名称のみ更新）。"""
        await self.db.execute(
            """
            INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version)
            VALUES (?, ?, ?, 2)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name = excluded.guild_name
            """,
            (guild_id, guild_name, to_iso(now())),
        )

    async def get(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        return dict(row) if row else None

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM guilds ORDER BY joined_at")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # ライフサイクル（退出 → 猶予 → 自動削除）
    # ------------------------------------------------------------------
    async def mark_left(self, guild_id: int, retention_days: int,
                        left_at: datetime | None = None) -> tuple[str, str]:
        """退出を記録し、(left_at, purge_after) を ISO 文字列で返す。

        **この時点ではデータを消さない。** 誤ってキックされた場合や
        一時的に外した場合に再招待でそのまま復帰できるよう、
        purge_after を過ぎたギルドだけを日次ジョブが削除する。
        """
        left = left_at or now()
        purge = left + timedelta(days=max(0, retention_days))
        left_iso, purge_iso = to_iso(left), to_iso(purge)
        await self.db.execute(
            "UPDATE guilds SET left_at = ?, purge_after = ? WHERE guild_id = ?",
            (left_iso, purge_iso, guild_id),
        )
        return left_iso, purge_iso

    async def clear_left(self, guild_id: int) -> None:
        """再参加したギルドの削除予定を取り消す（データはそのまま復活する）。"""
        await self.db.execute(
            "UPDATE guilds SET left_at = NULL, purge_after = NULL"
            " WHERE guild_id = ?",
            (guild_id,),
        )

    async def request_purge(self, guild_id: int,
                            at: datetime | None = None) -> str:
        """サーバー管理者の申告による削除を予約し、purge_after を返す。

        退出（mark_left）と違い left_at は立てない。参加したまま
        「このサーバーのデータを消す」と宣言した状態を表す。
        実削除は日次ジョブが行うため、それまでは cancel_purge で取り消せる。
        """
        when = at or now()
        iso = to_iso(when)
        await self.db.execute(
            "UPDATE guilds SET purge_after = ? WHERE guild_id = ?",
            (iso, guild_id),
        )
        return iso

    async def cancel_purge(self, guild_id: int) -> bool:
        """削除予約を取り消す。取り消す対象があれば True。"""
        cur = await self.db.execute(
            "UPDATE guilds SET purge_after = NULL"
            " WHERE guild_id = ? AND purge_after IS NOT NULL",
            (guild_id,),
        )
        return cur.rowcount > 0

    async def list_purge_due(self,
                             now_dt: datetime | None = None) -> list[dict[str, Any]]:
        """削除予定日時を過ぎたギルドを返す。

        ISO 文字列の辞書順比較はタイムゾーン表記が混ざると誤るため、
        候補だけを SQL で絞り、日時の比較は Python 側で行う。
        解釈できない値は対象から外す（消さない側に倒す）。
        """
        current = now_dt or now()
        rows = await self.db.fetchall(
            "SELECT * FROM guilds WHERE purge_after IS NOT NULL")
        due: list[dict[str, Any]] = []
        for row in rows:
            try:
                when = from_iso(row["purge_after"])
            except (TypeError, ValueError):
                continue
            if when <= current:
                due.append(dict(row))
        return due

    async def purge_guild(self, guild_id: int) -> dict[str, int]:
        """1つのギルドの行を全テーブルから削除し、テーブル別の件数を返す。

        **破壊的操作。** 呼び出す前に purge_after を過ぎていることを
        確認すること（list_purge_due が判定する）。
        """
        deleted: dict[str, int] = {}
        for table in purge_target_tables():
            cur = await self.db.execute(
                f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            if cur.rowcount:
                deleted[table] = cur.rowcount
        return deleted
