"""表グリッド用の汎用テーブル読み書き（ダッシュボード P2-4 / P2-5）。

**ホワイトリスト方式**: 参照できるテーブル・列・並び順・編集可能な列を
本モジュールの TABLES に定義したものだけに限定する。リクエスト由来の
文字列を SQL へ埋め込まない（テーブル名・列名は必ず定義済みの値を使う）。

他リポジトリと同じく全メソッドが guild_id を第1引数に取り、
すべての SQL に guild_id 条件が付く。ダッシュボードからは
`scope.bind(TableRepository(db))` 経由でのみ呼ばれる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repositories.base import BaseRepository
from utils.db import Database

MAX_LIMIT = 500
DEFAULT_LIMIT = 200


@dataclass(frozen=True)
class Column:
    """表グリッドの1列。"""
    name: str
    label: str
    # "text" | "number" | "bool" | "datetime" | "progress"
    type: str = "text"
    editable: bool = False


@dataclass(frozen=True)
class TableSpec:
    """参照を許可するテーブルの定義。"""
    key: str
    label: str
    table: str
    pk: str
    columns: tuple[Column, ...]
    order_by: str
    description: str = ""
    # guild_id 以外に必ず付ける絞り込み（例: 論理削除の除外）
    extra_where: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def editable_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.editable)


def _c(name: str, label: str, type_: str = "text",
       editable: bool = False) -> Column:
    return Column(name=name, label=label, type=type_, editable=editable)


# ---------------------------------------------------------------------
# 参照を許可するテーブル（設計方針 2.2 の対象テーブル）
# ---------------------------------------------------------------------
TABLES: dict[str, TableSpec] = {
    "tasks": TableSpec(
        key="tasks", label="タスク", table="tasks", pk="local_task_id",
        description="Todoist 連携タスクとローカルタスク",
        order_by="(due_date IS NULL), due_date, priority DESC",
        columns=(
            _c("local_task_id", "ID", "number"),
            _c("title", "タイトル", "text", editable=True),
            _c("assignee_id", "担当者ID", "text", editable=True),
            _c("team_key", "班", "text", editable=True),
            _c("due_date", "期限", "text", editable=True),
            _c("priority", "優先度", "number", editable=True),
            _c("status", "状態", "text", editable=True),
            _c("todoist_task_id", "TodoistタスクID"),
            _c("created_by", "作成者ID"),
            _c("created_at", "作成日時", "datetime"),
            _c("completed_at", "完了日時", "datetime"),
        )),
    "members": TableSpec(
        key="members", label="メンバー", table="members", pk="member_id",
        description="班所属・技能タグ",
        order_by="display_name",
        columns=(
            _c("member_id", "ID", "number"),
            _c("user_id", "DiscordユーザーID"),
            _c("display_name", "表示名", "text", editable=True),
            _c("primary_team", "主所属班", "text", editable=True),
            _c("secondary_teams", "副所属班", "text", editable=True),
            _c("is_leader", "班長", "bool", editable=True),
            _c("skills", "技能タグ", "text", editable=True),
            _c("notes", "メモ", "text", editable=True),
            _c("joined_at", "登録日時", "datetime"),
            _c("active_flag", "有効", "bool", editable=True),
        )),
    "teams": TableSpec(
        key="teams", label="班", table="teams", pk="team_id",
        description="班のマスタとロール紐付け",
        order_by="team_name",
        columns=(
            _c("team_id", "ID", "number"),
            _c("team_key", "班キー"),
            _c("team_name", "班名", "text", editable=True),
            _c("leader_role_id", "班長ロールID", "text", editable=True),
            _c("member_role_id", "班員ロールID", "text", editable=True),
            _c("secondary_role_id", "副所属ロールID", "text", editable=True),
            _c("channel_id", "通知チャンネルID", "text", editable=True),
            _c("active_flag", "有効", "bool", editable=True),
            _c("updated_at", "更新日時", "datetime"),
        )),
    "schedules": TableSpec(
        key="schedules", label="日程調整", table="schedules", pk="schedule_id",
        description="出欠投票の親レコード",
        order_by="deadline DESC",
        columns=(
            _c("schedule_id", "ID"),
            _c("title", "タイトル", "text", editable=True),
            _c("description", "説明", "text", editable=True),
            _c("place", "場所", "text", editable=True),
            _c("target_role_id", "対象ロールID"),
            _c("deadline", "締切", "text", editable=True),
            _c("created_by", "作成者ID"),
            _c("channel_id", "投稿チャンネルID"),
            _c("closed_flag", "締切済み", "bool", editable=True),
        )),
    "schedule_votes": TableSpec(
        key="schedule_votes", label="出欠回答", table="schedule_votes",
        pk="vote_id", description="候補日ごとの回答（○/△/×）",
        order_by="updated_at DESC",
        columns=(
            _c("vote_id", "ID", "number"),
            _c("option_id", "候補ID"),
            _c("user_id", "DiscordユーザーID"),
            _c("status", "回答", "text", editable=True),
            _c("updated_at", "更新日時", "datetime"),
        )),
    "layer_records": TableSpec(
        key="layer_records", label="桁巻き積層記録", table="layer_records",
        pk="record_id", description="/layer start〜end の作業記録",
        order_by="ended_at DESC",
        columns=(
            _c("record_id", "ID", "number"),
            _c("user_id", "作業者ID"),
            _c("keta", "桁名", "text", editable=True),
            _c("layer_num", "層番号", "text", editable=True),
            _c("started_at", "開始", "datetime"),
            _c("ended_at", "終了", "datetime"),
            _c("minutes", "作業時間(分)", "number", editable=True),
        )),
    "progress": TableSpec(
        key="progress", label="機体進捗", table="progress_nodes",
        pk="progress_node_id", description="機体→パーツ→部品の進捗ツリー",
        order_by="sort_order, node_id",
        columns=(
            _c("progress_node_id", "ID", "number"),
            _c("node_id", "ノードID"),
            _c("parent_id", "親ノードID", "text", editable=True),
            _c("sort_order", "表示順", "number", editable=True),
            _c("name", "名前", "text", editable=True),
            _c("assignee", "担当者", "text", editable=True),
            _c("status", "状態", "text", editable=True),
            _c("manual_progress", "進捗率", "progress", editable=True),
            _c("source", "ソース"),
            _c("todoist_task_id", "TodoistタスクID"),
            _c("updated_at", "更新日時", "datetime"),
        )),
}


class UnknownTableError(KeyError):
    """ホワイトリストに無いテーブルが指定された。"""


class UnknownColumnError(KeyError):
    """ホワイトリストに無い（または編集不可の）列が指定された。"""


def get_spec(table_key: str) -> TableSpec:
    spec = TABLES.get(table_key)
    if spec is None:
        raise UnknownTableError(table_key)
    return spec


class TableRepository(BaseRepository):
    """ホワイトリストされたテーブルの汎用参照・更新。"""

    def __init__(self, db: Database):
        super().__init__(db)

    def _where(self, spec: TableSpec) -> str:
        where = "guild_id = ?"
        if spec.extra_where:
            where += f" AND {spec.extra_where}"
        return where

    async def count_rows(self, guild_id: int, table_key: str) -> int:
        spec = get_spec(table_key)
        row = await self.db.fetchone(
            f"SELECT COUNT(*) AS n FROM {spec.table}"
            f" WHERE {self._where(spec)}",
            (guild_id,))
        return int(row["n"]) if row else 0

    async def list_rows(self, guild_id: int, table_key: str, *,
                        limit: int = DEFAULT_LIMIT,
                        offset: int = 0) -> list[dict[str, Any]]:
        """指定テーブルの行を返す（列はホワイトリストのものだけ）。"""
        spec = get_spec(table_key)
        limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        rows = await self.db.fetchall(
            f"SELECT {', '.join(spec.column_names)} FROM {spec.table}"
            f" WHERE {self._where(spec)}"
            f" ORDER BY {spec.order_by} LIMIT ? OFFSET ?",
            (guild_id, limit, offset))
        return [dict(r) for r in rows]

    async def get_row(self, guild_id: int, table_key: str,
                      row_id: Any) -> dict[str, Any] | None:
        spec = get_spec(table_key)
        row = await self.db.fetchone(
            f"SELECT {', '.join(spec.column_names)} FROM {spec.table}"
            f" WHERE guild_id = ? AND {spec.pk} = ?",
            (guild_id, row_id))
        return dict(row) if row else None

    async def update_row(self, guild_id: int, table_key: str, row_id: Any,
                         values: dict[str, Any]) -> bool:
        """編集可能な列だけを更新する（P2-5）。

        編集不可・未知の列が含まれていれば UnknownColumnError。
        更新した行があれば True。
        """
        spec = get_spec(table_key)
        editable = set(spec.editable_columns)
        unknown = [name for name in values if name not in editable]
        if unknown:
            raise UnknownColumnError(", ".join(sorted(unknown)))
        if not values:
            return False
        assignments = ", ".join(f"{name} = ?" for name in values)
        cur = await self.db.execute(
            f"UPDATE {spec.table} SET {assignments}"
            f" WHERE guild_id = ? AND {spec.pk} = ?",
            (*values.values(), guild_id, row_id))
        return cur.rowcount > 0
