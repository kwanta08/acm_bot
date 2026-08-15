"""表グリッド用の汎用テーブル読み書き（ダッシュボード P2-4 / P2-5）。

**ホワイトリスト方式**: 参照できるテーブル・列・並び順・編集可能な列を
本モジュールの TABLES に定義したものだけに限定する。リクエスト由来の
文字列を SQL へ埋め込まない（テーブル名・列名は必ず定義済みの値を使う）。

他リポジトリと同じく全メソッドが guild_id を第1引数に取り、
すべての SQL に guild_id 条件が付く。ダッシュボードからは
`scope.bind(TableRepository(db))` 経由でのみ呼ばれる。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from repositories.base import BaseRepository
from utils.db import Database

MAX_LIMIT = 500
DEFAULT_LIMIT = 200

# Excel が UTF-8 と認識するための BOM
CSV_BOM = "﻿"

# 表計算ソフトが数式として解釈してしまう先頭文字（CSV インジェクション）
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class Column:
    """表グリッドの1列。

    type は表示・入力の扱いを決める:
      "text" | "number" | "bool" | "datetime" | "progress"
      | "user"（Discord ユーザー ID。表示層で表示名へ解決）
      | "channel"（チャンネル ID。表示層で #名前 へ解決）
      | "option"（日程調整の候補 ID。表示層で候補ラベルへ解決）
    ID 系の列も DB には生の ID のまま保持する（解決は dashboard/display.py）。
    """

    name: str
    label: str
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


def _c(name: str, label: str, type_: str = "text", editable: bool = False) -> Column:
    return Column(name=name, label=label, type=type_, editable=editable)


# ---------------------------------------------------------------------
# 参照を許可するテーブル（設計方針 2.2 の対象テーブル）
# ---------------------------------------------------------------------
TABLES: dict[str, TableSpec] = {
    "tasks": TableSpec(
        key="tasks",
        label="タスク",
        table="tasks",
        pk="local_task_id",
        description="Todoist 連携タスクとローカルタスク",
        order_by="(due_date IS NULL), due_date, priority DESC",
        columns=(
            _c("local_task_id", "ID", "number"),
            _c("title", "タイトル", "text", editable=True),
            _c("assignee_id", "担当者", "user", editable=True),
            _c("team_key", "班", "text", editable=True),
            _c("due_date", "期限", "text", editable=True),
            _c("priority", "優先度", "number", editable=True),
            _c("status", "状態", "text", editable=True),
            _c("todoist_task_id", "TodoistタスクID"),
            _c("created_by", "作成者", "user"),
            _c("created_at", "作成日時", "datetime"),
            _c("completed_at", "完了日時", "datetime"),
        ),
    ),
    "members": TableSpec(
        key="members",
        label="メンバー",
        table="members",
        pk="member_id",
        description="班所属・技能タグ",
        order_by="display_name",
        columns=(
            _c("member_id", "ID", "number"),
            _c("user_id", "Discordユーザー", "user"),
            _c("display_name", "表示名", "text", editable=True),
            _c("primary_team", "主所属班", "text", editable=True),
            _c("secondary_teams", "副所属班", "text", editable=True),
            _c("is_leader", "班長", "bool", editable=True),
            _c("skills", "技能タグ", "text", editable=True),
            _c("notes", "メモ", "text", editable=True),
            _c("joined_at", "登録日時", "datetime"),
            _c("active_flag", "有効", "bool", editable=True),
        ),
    ),
    "teams": TableSpec(
        key="teams",
        label="班",
        table="teams",
        pk="team_id",
        description="班のマスタとロール紐付け",
        order_by="team_name",
        columns=(
            _c("team_id", "ID", "number"),
            _c("team_key", "班キー"),
            _c("team_name", "班名", "text", editable=True),
            _c("leader_role_id", "班長ロールID", "text", editable=True),
            _c("member_role_id", "班員ロールID", "text", editable=True),
            _c("secondary_role_id", "副所属ロールID", "text", editable=True),
            _c("channel_id", "通知チャンネル", "channel", editable=True),
            _c("active_flag", "有効", "bool", editable=True),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
    "schedules": TableSpec(
        key="schedules",
        label="日程調整",
        table="schedules",
        pk="schedule_id",
        description="出欠投票の親レコード",
        order_by="deadline DESC",
        columns=(
            _c("schedule_id", "ID"),
            _c("title", "タイトル", "text", editable=True),
            _c("description", "説明", "text", editable=True),
            _c("place", "場所", "text", editable=True),
            _c("target_role_id", "対象ロールID"),
            _c("deadline", "締切", "datetime", editable=True),
            _c("created_by", "作成者", "user"),
            _c("channel_id", "投稿チャンネル", "channel"),
            _c("closed_flag", "締切済み", "bool", editable=True),
        ),
    ),
    "schedule_votes": TableSpec(
        key="schedule_votes",
        label="出欠回答",
        table="schedule_votes",
        pk="vote_id",
        description="候補日ごとの回答（○/△/×）",
        order_by="updated_at DESC",
        columns=(
            _c("vote_id", "ID", "number"),
            _c("option_id", "候補", "option"),
            _c("user_id", "回答者", "user"),
            _c("status", "回答", "text", editable=True),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
    "layer_records": TableSpec(
        key="layer_records",
        label="桁巻き積層記録",
        table="layer_records",
        pk="record_id",
        description="/layer start〜end の作業記録",
        order_by="ended_at DESC",
        columns=(
            _c("record_id", "ID", "number"),
            _c("user_id", "作業者", "user"),
            _c("keta", "桁名", "text", editable=True),
            _c("layer_num", "層番号", "text", editable=True),
            _c("started_at", "開始", "datetime"),
            _c("ended_at", "終了", "datetime"),
            _c("minutes", "作業時間(分)", "number", editable=True),
        ),
    ),
    "progress": TableSpec(
        key="progress",
        label="機体進捗",
        table="progress_nodes",
        pk="progress_node_id",
        description="機体→パーツ→部品の進捗ツリー",
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
            # 重量はグラム固定（列名の _g で明示。単位設定は作らない）
            _c("target_weight_g", "目標重量(g)", "number", editable=True),
            _c("actual_weight_g", "実測重量(g)", "number", editable=True),
            _c("source", "ソース"),
            _c("todoist_task_id", "TodoistタスクID"),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
}


# ---------------------------------------------------------------------
# シート切替（Google スプレッドシートのタブに相当する絞り込み）
#
# 表キー → タブ1つが指すものの呼び名。schedule_votes は「予定（日程調整）」
# 単位、layer_records は「桁」単位で切り替える。絞り込み条件は
# TableRepository 側でホワイトリスト的に固定する（リクエスト由来の
# 文字列はバインド値としてのみ使う）。
# ---------------------------------------------------------------------
SHEET_TABLES: dict[str, str] = {
    "schedule_votes": "予定",
    "layer_records": "桁",
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


def csv_safe(value: Any) -> str:
    """CSV インジェクション対策として先頭の数式記号を無害化する。

    タスク名やメモは Discord の利用者が自由に入力できるため、
    `=cmd|...` のような値をそのまま出すと表計算ソフトが数式として実行する。
    """
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def rows_to_csv(spec: TableSpec, rows: list[dict[str, Any]]) -> str:
    """行を CSV 文字列にする（BOM 付き UTF-8。Excel でそのまま開ける）。

    出力するのは spec.columns のホワイトリスト列だけ。guild_id も
    Todoist トークンのような機密列も TABLES に無いため、
    ここを通る限り構造的に出力されない。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([column.label for column in spec.columns])
    for row in rows:
        writer.writerow([csv_safe(row.get(column.name)) for column in spec.columns])
    return CSV_BOM + buf.getvalue()


class TableRepository(BaseRepository):
    """ホワイトリストされたテーブルの汎用参照・更新。"""

    def __init__(self, db: Database):
        super().__init__(db)

    def _where(self, spec: TableSpec) -> str:
        where = "guild_id = ?"
        if spec.extra_where:
            where += f" AND {spec.extra_where}"
        return where

    def _sheet_where(self, table_key: str, guild_id: int, sheet_id: str) -> tuple[str, tuple]:
        """シート絞り込みの WHERE 断片とバインド値を返す。

        条件式は表キーごとにここで固定する（sheet_id はバインド値として
        のみ使い、SQL へ文字列連結しない）。
        """
        if table_key == "schedule_votes":
            # 回答は候補（option）にぶら下がるため、予定 ID では
            # 副問い合わせで絞る。副問い合わせにも guild_id を付ける
            return (
                (
                    "option_id IN (SELECT option_id FROM schedule_options"
                    " WHERE guild_id = ? AND schedule_id = ?)"
                ),
                (guild_id, sheet_id),
            )
        if table_key == "layer_records":
            return ("keta = ?", (sheet_id,))
        raise UnknownTableError(table_key)

    async def list_sheets(self, guild_id: int, table_key: str) -> list[dict[str, Any]]:
        """シート（タブ）の一覧を返す。

        - schedule_votes: 予定1件 = シート1枚。表示用の開催日時として
          最初の候補日（無ければ締切）を添える。並び替えは表示層が行う
        - layer_records: 桁1つ = シート1枚。有効な桁マスタに加え、
          記録にだけ残っている桁（無効化済み等）も漏らさず含める
        """
        if table_key == "schedule_votes":
            rows = await self.db.fetchall(
                """
                SELECT s.schedule_id AS id, s.title AS label,
                       COALESCE(MIN(o.start_at), s.deadline) AS at
                FROM schedules s
                LEFT JOIN schedule_options o
                  ON o.guild_id = s.guild_id AND o.schedule_id = s.schedule_id
                WHERE s.guild_id = ?
                GROUP BY s.schedule_id, s.title, s.deadline
                """,
                (guild_id,),
            )
            return [dict(r) for r in rows]
        if table_key == "layer_records":
            rows = await self.db.fetchall(
                """
                SELECT keta_name FROM layer_keta
                WHERE guild_id = ? AND active_flag = 1
                UNION
                SELECT DISTINCT keta FROM layer_records WHERE guild_id = ?
                ORDER BY keta_name
                """,
                (guild_id, guild_id),
            )
            return [{"id": r["keta_name"], "label": r["keta_name"], "at": None} for r in rows]
        raise UnknownTableError(table_key)

    async def count_rows(
        self, guild_id: int, table_key: str, *, sheet_id: str | None = None
    ) -> int:
        spec = get_spec(table_key)
        where = self._where(spec)
        params: tuple = (guild_id,)
        if sheet_id is not None:
            sheet_sql, sheet_params = self._sheet_where(table_key, guild_id, sheet_id)
            where += f" AND {sheet_sql}"
            params += sheet_params
        row = await self.db.fetchone(
            f"SELECT COUNT(*) AS n FROM {spec.table} WHERE {where}", params
        )
        return int(row["n"]) if row else 0

    async def list_rows(
        self,
        guild_id: int,
        table_key: str,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        sheet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """指定テーブルの行を返す（列はホワイトリストのものだけ）。

        sheet_id を指定すると、そのシート（予定・桁）の行だけに絞る。
        """
        spec = get_spec(table_key)
        limit = max(1, min(int(limit), MAX_LIMIT))
        offset = max(0, int(offset))
        where = self._where(spec)
        params: tuple = (guild_id,)
        if sheet_id is not None:
            sheet_sql, sheet_params = self._sheet_where(table_key, guild_id, sheet_id)
            where += f" AND {sheet_sql}"
            params += sheet_params
        rows = await self.db.fetchall(
            f"SELECT {', '.join(spec.column_names)} FROM {spec.table}"
            f" WHERE {where}"
            f" ORDER BY {spec.order_by} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [dict(r) for r in rows]

    async def list_all_rows(self, guild_id: int, table_key: str) -> list[dict[str, Any]]:
        """指定テーブルの全行を返す（エクスポート用）。

        list_rows は画面表示用に MAX_LIMIT の上限を持つため、
        全件が必要なエクスポートではページングで読み切る。
        """
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            chunk = await self.list_rows(guild_id, table_key, limit=MAX_LIMIT, offset=offset)
            out.extend(chunk)
            if len(chunk) < MAX_LIMIT:
                return out
            offset += MAX_LIMIT

    async def get_row(self, guild_id: int, table_key: str, row_id: Any) -> dict[str, Any] | None:
        spec = get_spec(table_key)
        row = await self.db.fetchone(
            f"SELECT {', '.join(spec.column_names)} FROM {spec.table}"
            f" WHERE guild_id = ? AND {spec.pk} = ?",
            (guild_id, row_id),
        )
        return dict(row) if row else None

    async def update_row(
        self, guild_id: int, table_key: str, row_id: Any, values: dict[str, Any]
    ) -> bool:
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
            f"UPDATE {spec.table} SET {assignments} WHERE guild_id = ? AND {spec.pk} = ?",
            (*values.values(), guild_id, row_id),
        )
        return cur.rowcount > 0
