"""機体進捗ツリー（progress_nodes ほか）の CRUD。

マルチテナント版: 全メソッドが第1引数に guild_id を取り、全 SQL に
guild_id 条件を付ける。ノードは (guild_id, node_id) で一意。

扱うテーブル（migrations/009 / スキーマ v10）:
- progress_nodes         : 機体 → パーツ → 部品 → … の隣接リスト
- progress_todoist_links : Todoist プロジェクト → ノードの紐付け
- progress_spar_links    : 桁 → ノードの紐付けと目標層数

進捗率（manual_progress）は 0.0〜1.0 の小数で保持する。表示・集計は
services/progress_tree.py が行い、本リポジトリは永続化だけを担当する。
"""

from __future__ import annotations

from typing import Any, ClassVar

from repositories.base import BaseRepository
from utils.db import Database

# 進捗ノードの由来（services/progress_tree.py と共有する語彙）
SOURCE_MANUAL = "manual"
SOURCE_TODOIST = "todoist"
SOURCE_SPAR_WINDING = "spar_winding"

# 取得系で使う列（SELECT * に依存しないことで、列追加時の挙動を安定させる）
NODE_COLUMNS = (
    "node_id, parent_id, sort_order, name, assignee, status,"
    " manual_progress, source, todoist_task_id, weight,"
    " target_weight_g, actual_weight_g,"
    " created_at, updated_at"
)


class ProgressRepository(BaseRepository):
    """機体進捗ツリーのリポジトリ。"""

    def __init__(self, db: Database):
        super().__init__(db)

    # ------------------------------------------------------------------
    # ノード: 取得
    # ------------------------------------------------------------------
    async def list_nodes(self, guild_id: int) -> list[dict[str, Any]]:
        """ギルドの全ノードを表示順で返す（ツリー構築用）。"""
        rows = await self.db.fetchall(
            f"SELECT {NODE_COLUMNS} FROM progress_nodes"
            " WHERE guild_id = ? ORDER BY sort_order, node_id",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def get_node(self, guild_id: int, node_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            f"SELECT {NODE_COLUMNS} FROM progress_nodes WHERE guild_id = ? AND node_id = ?",
            (guild_id, node_id),
        )
        return dict(row) if row else None

    async def exists(self, guild_id: int, node_id: str) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM progress_nodes WHERE guild_id = ? AND node_id = ?", (guild_id, node_id)
        )
        return row is not None

    async def count_nodes(self, guild_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM progress_nodes WHERE guild_id = ?", (guild_id,)
        )
        return int(row["n"]) if row else 0

    async def list_children(self, guild_id: int, parent_id: str | None) -> list[dict[str, Any]]:
        """直下の子ノードを表示順で返す（parent_id=None はルート＝機体）。"""
        if parent_id is None:
            rows = await self.db.fetchall(
                f"SELECT {NODE_COLUMNS} FROM progress_nodes"
                " WHERE guild_id = ? AND (parent_id IS NULL OR parent_id = '')"
                " ORDER BY sort_order, node_id",
                (guild_id,),
            )
        else:
            rows = await self.db.fetchall(
                f"SELECT {NODE_COLUMNS} FROM progress_nodes"
                " WHERE guild_id = ? AND parent_id = ?"
                " ORDER BY sort_order, node_id",
                (guild_id, parent_id),
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # ノード: 追加・更新
    # ------------------------------------------------------------------
    async def upsert_node(
        self,
        guild_id: int,
        node_id: str,
        *,
        parent_id: str | None = None,
        sort_order: float = 0.0,
        name: str = "",
        assignee: str | None = None,
        status: str | None = None,
        manual_progress: float | None = None,
        source: str = SOURCE_MANUAL,
        todoist_task_id: str | None = None,
        weight: float = 1.0,
        now_text: str = "",
    ) -> None:
        """ノードを追加、既存なら全項目を更新する（同期・移行用）。

        部分更新は update_node() を使う。created_at は初回のみ設定され、
        更新時は既存値を保持する。
        """
        await self.db.execute(
            """
            INSERT INTO progress_nodes
                (guild_id, node_id, parent_id, sort_order, name, assignee,
                 status, manual_progress, source, todoist_task_id, weight,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, node_id) DO UPDATE SET
                parent_id = excluded.parent_id,
                sort_order = excluded.sort_order,
                name = excluded.name,
                assignee = excluded.assignee,
                status = excluded.status,
                manual_progress = excluded.manual_progress,
                source = excluded.source,
                todoist_task_id = excluded.todoist_task_id,
                weight = excluded.weight,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                node_id,
                parent_id,
                sort_order,
                name,
                assignee,
                status,
                manual_progress,
                source,
                todoist_task_id,
                weight,
                now_text,
                now_text,
            ),
        )

    # 部分更新を許可する列（呼び出し側の文字列をそのまま SQL に入れない）。
    # guild_id / node_id は update_node() の位置引数と同名のため、
    # そもそもキーワードとして渡せない（テナント越境の更新を構造的に防ぐ）。
    _UPDATABLE: ClassVar[set[str]] = {
        "parent_id",
        "sort_order",
        "name",
        "assignee",
        "status",
        "manual_progress",
        "source",
        "todoist_task_id",
        "weight",
        "target_weight_g",
        "actual_weight_g",
    }

    async def update_node(self, guild_id: int, node_id: str, now_text: str, **fields: Any) -> bool:
        """指定した列だけを更新する。更新した行があれば True。

        未知の列名は ValueError（呼び出し側の打ち間違いを早期に検出する）。
        """
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"更新できない列です: {', '.join(sorted(unknown))}")
        if not fields:
            return False
        assignments = ", ".join(f"{col} = ?" for col in fields)
        params = (*fields.values(), now_text, guild_id, node_id)
        cur = await self.db.execute(
            f"UPDATE progress_nodes SET {assignments}, updated_at = ?"
            " WHERE guild_id = ? AND node_id = ?",
            params,
        )
        return cur.rowcount > 0

    async def set_progress(
        self,
        guild_id: int,
        node_id: str,
        progress: float | None,
        now_text: str,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> bool:
        """進捗率（と任意で状態・ソース）を更新する。"""
        fields: dict[str, Any] = {"manual_progress": progress}
        if status is not None:
            fields["status"] = status
        if source is not None:
            fields["source"] = source
        return await self.update_node(guild_id, node_id, now_text, **fields)

    # ------------------------------------------------------------------
    # ノード: 削除
    # ------------------------------------------------------------------
    async def delete_node(self, guild_id: int, node_id: str) -> bool:
        """1ノードを削除する（子は孤児になるため呼び出し側で扱う）。"""
        cur = await self.db.execute(
            "DELETE FROM progress_nodes WHERE guild_id = ? AND node_id = ?", (guild_id, node_id)
        )
        return cur.rowcount > 0

    async def delete_subtree(self, guild_id: int, node_id: str) -> int:
        """ノードとその子孫をまとめて削除し、削除件数を返す。

        親子関係はアプリ側で辿る（再帰 CTE はドライバ差が大きく、
        循環データがあると停止しないため）。訪問済み集合で循環を防ぐ。
        """
        children_by_parent: dict[str, list[str]] = {}
        for row in await self.list_nodes(guild_id):
            parent = row["parent_id"] or None
            if parent:
                children_by_parent.setdefault(parent, []).append(row["node_id"])

        targets: list[str] = []
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            targets.append(current)
            stack.extend(children_by_parent.get(current, []))

        deleted = 0
        for target in targets:
            if await self.delete_node(guild_id, target):
                deleted += 1
        return deleted

    async def delete_all_nodes(self, guild_id: int) -> int:
        """ギルドの全ノードを削除する（移行スクリプトの洗い替え用）。"""
        cur = await self.db.execute("DELETE FROM progress_nodes WHERE guild_id = ?", (guild_id,))
        return cur.rowcount

    # ------------------------------------------------------------------
    # Todoist 紐付け（旧「Todoist対応表」シート）
    # ------------------------------------------------------------------
    async def list_todoist_links(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT project_name, node_id, notify_channel_id, created_by,"
            " created_at, updated_at FROM progress_todoist_links"
            " WHERE guild_id = ? ORDER BY project_name",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def upsert_todoist_link(
        self,
        guild_id: int,
        project_name: str,
        node_id: str,
        now_text: str,
        *,
        notify_channel_id: str = "",
        created_by: str = "",
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO progress_todoist_links
                (guild_id, project_name, node_id, notify_channel_id,
                 created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, project_name) DO UPDATE SET
                node_id = excluded.node_id,
                notify_channel_id = excluded.notify_channel_id,
                updated_at = excluded.updated_at
            """,
            (guild_id, project_name, node_id, notify_channel_id, created_by, now_text, now_text),
        )

    async def delete_todoist_link(self, guild_id: int, project_name: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM progress_todoist_links WHERE guild_id = ? AND project_name = ?",
            (guild_id, project_name),
        )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 桁巻き紐付け（旧「桁巻き対応表」＋「桁マスタ」）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # マイルストーン（大会からの逆算アラート）
    # ------------------------------------------------------------------
    async def add_milestone(
        self, guild_id: int, node_id: str, name: str, due_date: str, now_text: str
    ) -> None:
        """マイルストーンを登録する（同じ node_id + 名前なら期限を更新）。"""
        await self.db.execute(
            """
            INSERT INTO progress_milestones
                (guild_id, node_id, name, due_date, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, node_id, name) DO UPDATE SET
                due_date = excluded.due_date,
                updated_at = excluded.updated_at
            """,
            (guild_id, node_id, name, due_date, now_text, now_text),
        )

    async def remove_milestone(self, guild_id: int, node_id: str, name: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM progress_milestones WHERE guild_id = ? AND node_id = ? AND name = ?",
            (guild_id, node_id, name),
        )
        return cur.rowcount > 0

    async def list_milestones(self, guild_id: int) -> list[dict[str, Any]]:
        """期限の早い順にマイルストーンを返す。"""
        rows = await self.db.fetchall(
            "SELECT milestone_id, node_id, name, due_date, created_at,"
            " updated_at FROM progress_milestones"
            " WHERE guild_id = ? ORDER BY due_date, name",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def list_spar_links(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT keta_name, node_id, target_layers, created_at, updated_at"
            " FROM progress_spar_links WHERE guild_id = ? ORDER BY keta_name",
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def upsert_spar_link(
        self, guild_id: int, keta_name: str, node_id: str, target_layers: int, now_text: str
    ) -> None:
        if target_layers <= 0:
            raise ValueError("目標層数は 1 以上を指定してください。")
        await self.db.execute(
            """
            INSERT INTO progress_spar_links
                (guild_id, keta_name, node_id, target_layers,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, keta_name) DO UPDATE SET
                node_id = excluded.node_id,
                target_layers = excluded.target_layers,
                updated_at = excluded.updated_at
            """,
            (guild_id, keta_name, node_id, target_layers, now_text, now_text),
        )

    async def delete_spar_link(self, guild_id: int, keta_name: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM progress_spar_links WHERE guild_id = ? AND keta_name = ?",
            (guild_id, keta_name),
        )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 桁巻きの完了層数（layer_records から集計）
    # ------------------------------------------------------------------
    async def count_completed_layers(self, guild_id: int) -> dict[str, int]:
        """桁名ごとの完了層数を返す（/layer end で記録された層番号の種類数）。

        同じ層を巻き直しても1層として数える（旧・桁巻きシートの
        count_completed_layers と同じ数え方）。
        """
        rows = await self.db.fetchall(
            "SELECT keta, COUNT(DISTINCT layer_num) AS layers"
            " FROM layer_records WHERE guild_id = ? GROUP BY keta",
            (guild_id,),
        )
        return {r["keta"]: int(r["layers"]) for r in rows}

    async def list_layer_dates(self, guild_id: int) -> dict[str, list[str]]:
        """桁名ごとの「各層を最初に巻き終えた日時」を返す。

        大会逆算の実績ペース算出に使う。巻き直しを二重に数えないよう、
        層番号ごとに最初の完了日時だけを採る（count_completed_layers と
        同じ数え方）。
        """
        rows = await self.db.fetchall(
            "SELECT keta, layer_num, MIN(ended_at) AS ended_at"
            " FROM layer_records WHERE guild_id = ?"
            " GROUP BY keta, layer_num ORDER BY keta, ended_at",
            (guild_id,),
        )
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["keta"], []).append(row["ended_at"])
        return out
