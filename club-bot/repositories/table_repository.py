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
import math
from dataclasses import dataclass, field
from typing import Any

from repositories.base import BaseRepository

# 進捗率の解釈は bot 側（/progress edit）と同じ規則を使う。
# progress_tree は DB・Discord に依存しない純粋関数モジュールなので
# ここから参照しても循環参照にはならない。
from services.progress_tree import parse_progress
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
      | "team"（班キー slug。表示層で teams.team_name へ解決）
      | "team_list"（班キー slug の JSON 配列。表示層で班名の「、」区切りへ解決）
    ID 系・slug 系の列も DB には生の値のまま保持し、編集入力も生値で行う
    （解決は dashboard/display.py）。
    """

    name: str
    label: str
    type: str = "text"
    editable: bool = False
    # type == "number" のときの下位型: "int" | "real"（DDL の INTEGER / REAL）。
    #
    # **安全な既定値が無い。** int を既定にすると重量・並び順（REAL）が壊れ、
    # real を既定にすると priority / minutes（INTEGER）へ小数が入り、
    # asyncpg が int8 引数に float を受け付けず本番だけ落ちる。
    # そこで既定値を置かず、number 列は列ごとに宣言させる（__post_init__ で強制）。
    number_type: str | None = None

    def __post_init__(self):
        if self.type == "number":
            if self.number_type not in ("int", "real"):
                raise ValueError(
                    f"{self.name}: number 列には number_type（'int' か 'real'）が必要です"
                )
        elif self.number_type is not None:
            raise ValueError(f"{self.name}: number 以外の列に number_type は指定できません")


@dataclass(frozen=True)
class TableSpec:
    """参照を許可するテーブルの定義。"""

    key: str
    label: str
    table: str
    pk: str
    # 主キー列の型: "int" | "text"。既定値を置かない（新しい表を足すときに
    # 決め忘れると、PostgreSQL でだけ落ちる不具合が再発するため）。
    # Web から来る row_id は必ず str なので、ここを見て正規化する。
    pk_type: str
    columns: tuple[Column, ...]
    order_by: str
    description: str = ""
    # guild_id 以外に必ず付ける絞り込み（例: 論理削除の除外）
    extra_where: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    # ダッシュボードでこの表を**閲覧**するのに必要な権限レベル（G4-3）。
    #
    # 既定の 1 は「サーバー参加者なら誰でも」。運用の証跡（audit_log）や
    # ロール ID を含む設定（settings）はここを上げる。
    # `GET /settings` がロール ID の実値を L4 にだけ返している（G1-6）のに、
    # 同じ値が表グリッド経由で L1 に見えるのでは意味がないため、
    # **表ごとに必要レベルを定義側が持つ**（ADR 0016 と同じ考え方。
    # ルータ側の if で守ると、表を足すときに書き忘れる）。
    # `/data export` は元から L4 なので、この値の影響を受けない。
    min_level: int = 1

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def editable_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.editable)


def _c(
    name: str,
    label: str,
    type_: str = "text",
    editable: bool = False,
    number_type: str | None = None,
) -> Column:
    return Column(
        name=name, label=label, type=type_, editable=editable, number_type=number_type
    )


# ---------------------------------------------------------------------
# 参照を許可するテーブル（設計方針 2.2 の対象テーブル）
# ---------------------------------------------------------------------
TABLES: dict[str, TableSpec] = {
    "members": TableSpec(
        key="members",
        label="メンバー",
        table="members",
        pk="member_id",
        pk_type="int",
        description="班所属",
        order_by="display_name",
        columns=(
            _c("member_id", "ID", "number", number_type="int"),
            _c("user_id", "Discordユーザー", "user"),
            _c("display_name", "表示名", "text", editable=True),
            # 班は slug で保持し、表示だけ班名へ解決する（編集は slug / JSON 配列のまま）
            _c("primary_team", "主所属班", "team", editable=True),
            _c("secondary_teams", "副所属班", "team_list", editable=True),
            # is_leader は Web ダッシュボードの認可（L2 判定）そのもの。
            # 編集可にすると L2 が任意の相手を L2 へ昇格させられる。
            # 変更は Discord の /member set-leader（L3 以上）から行う。
            _c("is_leader", "班長", "bool"),
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
        pk_type="int",
        description="班のマスタとロール紐付け",
        order_by="team_name",
        columns=(
            _c("team_id", "ID", "number", number_type="int"),
            _c("team_key", "班キー"),
            _c("team_name", "班名", "text", editable=True),
            # ロール ID は Web から編集させない。
            #
            # cogs/members._sync_roles() は member_role_id をそのまま
            # add_roles() に渡す。ここを Bot 管理者ロールの ID に書き換えてから
            # /member assign-team（L2）を実行すると、bot の権限で L4 相当の
            # ロールが付いてしまう（権限昇格の経路）。
            # Discord 側の /team-role は元から管理者限定なので、そちらへ一本化する。
            _c("leader_role_id", "班長ロールID", "text"),
            _c("member_role_id", "班員ロールID", "text"),
            _c("secondary_role_id", "副所属ロールID", "text"),
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
        pk_type="text",
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
            # 論理削除は編集不可。ダッシュボードの編集認可は L2 だが
            # /schedule delete と /schedule restore は L3 なので、
            # editable にすると L2 が L3 の操作を取り消せる
            # （members.is_leader / teams.leader_role_id と同じ理由）
            _c("deleted_flag", "削除済み", "bool"),
        ),
    ),
    "schedule_votes": TableSpec(
        key="schedule_votes",
        label="出欠回答",
        table="schedule_votes",
        pk="vote_id",
        pk_type="int",
        description="候補日ごとの回答（○/△/×）",
        order_by="updated_at DESC",
        columns=(
            _c("vote_id", "ID", "number", number_type="int"),
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
        pk_type="int",
        description="/layer start〜end の作業記録",
        order_by="ended_at DESC",
        columns=(
            _c("record_id", "ID", "number", number_type="int"),
            _c("user_id", "作業者", "user"),
            _c("keta", "桁名", "text", editable=True),
            _c("layer_num", "層番号", "text", editable=True),
            _c("started_at", "開始", "datetime"),
            _c("ended_at", "終了", "datetime"),
            _c("minutes", "作業時間(分)", "number", editable=True, number_type="int"),
        ),
    ),
    "progress": TableSpec(
        key="progress",
        label="機体進捗",
        table="progress_nodes",
        pk="progress_node_id",
        pk_type="int",
        description="機体→パーツ→部品の進捗ツリー",
        order_by="sort_order, node_id",
        columns=(
            _c("progress_node_id", "ID", "number", number_type="int"),
            _c("node_id", "ノードID"),
            _c("parent_id", "親ノードID", "text", editable=True),
            _c("sort_order", "表示順", "number", editable=True, number_type="real"),
            _c("name", "名前", "text", editable=True),
            _c("assignee", "担当者", "text", editable=True),
            _c("status", "状態", "text", editable=True),
            _c("manual_progress", "進捗率", "progress", editable=True),
            # 重量はグラム固定（列名の _g で明示。単位設定は作らない）
            _c("target_weight_g", "目標重量(g)", "number", editable=True, number_type="real"),
            _c("actual_weight_g", "実測重量(g)", "number", editable=True, number_type="real"),
            _c("source", "ソース"),
            _c("todoist_task_id", "TodoistタスクID"),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
    # ------------------------------------------------------------------
    # 読み取り専用の表（G4-3）。
    #
    # ここまでの7表は「Discord から入れたデータを表で直す」ためのもので、
    # 以下は**溜まっているのに持ち出せなかった**もの。編集可能な列は
    # 1つも置かない（正本の入口は Discord コマンド側にある）。
    # ------------------------------------------------------------------
    "audit_log": TableSpec(
        key="audit_log",
        label="操作ログ",
        table="audit_log",
        pk="audit_id",
        pk_type="int",
        description="/setup・班マスタ変更・年度替わり・ダッシュボード編集の証跡",
        order_by="audit_id DESC",
        # 誰がいつ何を変えたかの記録。/report changes と同じ L3 に揃える
        min_level=3,
        columns=(
            _c("audit_id", "ID", "number", number_type="int"),
            _c("actor_id", "実行者", "user"),
            _c("action", "操作"),
            _c("target", "対象"),
            _c("detail", "詳細"),
            _c("created_at", "日時", "datetime"),
        ),
    ),
    "seasons": TableSpec(
        key="seasons",
        label="年度",
        table="seasons",
        pk="season_id",
        pk_type="int",
        description="/season new・/season rollover で区切った年度",
        order_by="started_at DESC",
        columns=(
            _c("season_id", "ID", "number", number_type="int"),
            _c("name", "年度名"),
            _c("started_at", "開始", "datetime"),
            _c("ended_at", "終了", "datetime"),
            _c("created_at", "作成日時", "datetime"),
        ),
    ),
    "progress_milestones": TableSpec(
        key="progress_milestones",
        label="節目（マイルストーン）",
        table="progress_milestones",
        pk="milestone_id",
        pk_type="int",
        description="大会から逆算した節目の期限",
        order_by="due_date, name",
        columns=(
            _c("milestone_id", "ID", "number", number_type="int"),
            _c("node_id", "ノードID"),
            _c("name", "節目名"),
            _c("due_date", "期限"),
            _c("created_at", "作成日時", "datetime"),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
    "layer_keta": TableSpec(
        key="layer_keta",
        label="桁マスタ",
        table="layer_keta",
        pk="keta_id",
        pk_type="int",
        description="/layer keta-add で登録した桁名",
        order_by="active_flag DESC, keta_name",
        columns=(
            _c("keta_id", "ID", "number", number_type="int"),
            _c("keta_name", "桁名"),
            _c("active_flag", "有効", "bool"),
            _c("created_by", "登録者", "user"),
            _c("created_at", "登録日時", "datetime"),
        ),
    ),
    "progress_snapshots": TableSpec(
        key="progress_snapshots",
        label="進捗の履歴",
        table="progress_snapshots",
        pk="snapshot_id",
        pk_type="int",
        description="1日1件の進捗スナップショット（/progress history が読む）",
        order_by="snapshot_date DESC, node_id",
        columns=(
            _c("snapshot_id", "ID", "number", number_type="int"),
            _c("node_id", "ノードID"),
            _c("snapshot_date", "日付"),
            # 未集計・未計測は NULL のまま出す（0.0 に丸めない。ADR 0021）
            _c("aggregated", "集計進捗率", "progress"),
            _c("actual_weight_g", "実測重量(g)", "number", number_type="real"),
        ),
    ),
    "stock_items": TableSpec(
        key="stock_items",
        label="在庫（品目）",
        table="stock_items",
        pk="stock_item_id",
        pk_type="int",
        description="/stock で管理する資材・消耗品",
        order_by="active_flag DESC, item_name",
        columns=(
            _c("stock_item_id", "ID", "number", number_type="int"),
            _c("item_name", "品目名"),
            _c("unit", "単位"),
            _c("quantity", "数量", "number", number_type="real"),
            # 閾値未設定は NULL のまま出す（0 に丸めない。ADR 0021）
            _c("threshold", "発注閾値", "number", number_type="real"),
            _c("note", "メモ"),
            _c("active_flag", "有効", "bool"),
            _c("created_by", "登録者", "user"),
            _c("created_at", "登録日時", "datetime"),
            _c("updated_at", "更新日時", "datetime"),
        ),
    ),
    "stock_movements": TableSpec(
        key="stock_movements",
        label="在庫の増減",
        table="stock_movements",
        pk="movement_id",
        pk_type="int",
        description="/stock add・/stock use の履歴",
        order_by="movement_id DESC",
        columns=(
            _c("movement_id", "ID", "number", number_type="int"),
            _c("stock_item_id", "品目ID", "number", number_type="int"),
            _c("delta", "増減", "number", number_type="real"),
            _c("reason", "用途"),
            _c("user_id", "記録者", "user"),
            _c("created_at", "日時", "datetime"),
        ),
    ),
    "settings": TableSpec(
        key="settings",
        label="サーバー設定",
        table="settings",
        # (guild_id, setting_key) が主キー。guild_id は scope 側が付けるので
        # 表としての行 ID は setting_key
        pk="setting_key",
        pk_type="text",
        description="/setup・/settings_set で保存したこのサーバーの設定",
        order_by="setting_key",
        # 値にロール ID・チャンネル ID が入る。GET /settings が
        # ロール ID の実値を L4 にだけ返している（G1-6）ので、同じ扱いにする
        min_level=4,
        columns=(
            _c("setting_key", "設定キー"),
            _c("setting_value", "値"),
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


class InvalidValueError(ValueError):
    """列の型に合わない値が指定された（例: 進捗率に数値でない文字列）。"""


class UnknownRowError(KeyError):
    """行 ID が主キーの型に変換できない（＝そんな行は存在しえない）。

    「存在しない行」と同じ扱い（HTTP 404）にする。異常系ではなく
    URL の打ち間違いなので、500 にはしない。
    """


def coerce_row_id(spec: TableSpec, row_id: Any) -> Any:
    """行 ID を主キー列の型へ正規化する。変換できなければ UnknownRowError。

    Web から来る row_id は URL 由来なので必ず str になる。SQLite は
    型親和性で `'5'` を 5 として扱うため素通しでも動くが、**PostgreSQL の
    asyncpg は bigint 引数に str を渡すと DataError を投げる**
    （本番は PostgreSQL。ADR 0006）。

        asyncpg.exceptions.DataError: invalid input for query argument $2: '5'

    型はルータではなく TableSpec が持つ。列の定義とその型が同じ場所に
    あれば、表を足すときに片方だけ直し忘れることがない（ADR 0016）。
    """
    if spec.pk_type == "int":
        # bool は int の派生。True が「1行目」として通らないように弾く
        if isinstance(row_id, bool):
            raise UnknownRowError(f"{spec.key}: 行 ID が不正です")
        if isinstance(row_id, int):
            return row_id
        text = str(row_id).strip()
        # ASCII の数字だけを受ける。int() は全角「５」やアラビア数字「٥」、
        # 桁区切りの "5_000" も 5 / 5000 として通してしまい、**同じ行を指す
        # URL の綴りが複数できる**。監査ログには URL の生値が残るため、
        # 「tasks#٥ を編集」と記録されて実際は 5 行目、というずれが起きる。
        if not (text.isascii() and text.isdigit()):
            raise UnknownRowError(f"{spec.key}: 行 ID が不正です: {row_id!r}")
        return int(text)
    if row_id is None:
        raise UnknownRowError(f"{spec.key}: 行 ID が指定されていません")
    return str(row_id)


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


# ---------------------------------------------------------------------
# 値の型変換
#
# SQLite は動的型付けなので、REAL 列にも文字列がそのまま保存できてしまう。
# 進捗率に数値でない値が入ると bot 側の float() 変換が落ち、
# そのサーバーの /progress view と定期同期がまとめて動かなくなる。
# 書き込み口である本リポジトリで列の型に正規化しておく。
# ---------------------------------------------------------------------
_TRUE_VALUES = {"1", "true", "yes", "on", "はい"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "いいえ"}


# 編集できる INTEGER 列（tasks.priority / layer_records.minutes）は
# PostgreSQL では **int4**。BIGINT になるのは主キーだけで、to_pg_ddl() は
# 一般の INTEGER 列を INTEGER のまま出す（guild_id だけ BIGINT へ寄せる）。
# int8 の範囲で通すと 3000000000 のような値が素通りして本番だけ
# OverflowError: value out of int32 range になるため、int4 で判定する。
# SQLite の INTEGER は 64bit なので開発環境では通ってしまう＝ここで揃える。
# priority / minutes に CHECK 制約は無く、DB 側では止まらない。
# （編集できる int 列が本当に int4 かは
#   tests/test_number_column_types.py が DDL と突き合わせて固定している）
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1

def _coerce_number(column: Column, value: Any) -> int | float | None:
    """number 列の値を DDL の型（INTEGER / REAL）へ正規化する。

    以前は `int()` → 失敗したら `float()` の順に試し、さらに数値型は
    そのまま返していたため、**INTEGER 列に float が入りえた**。
    asyncpg は int8 の引数に float を渡しても DataError になるので、
    本番（PostgreSQL）だけが 500 になる（SQLite は保存できてしまう）。

    小数は丸めない。`priority` に 2.7 が来たら「2 にしておく」ではなく
    拒否して入れ直してもらう（勝手に値を変えない）。

    **変換が通っても DB の型に収まるとは限らない。** Python の int は
    任意精度なので `"9" * 30` はそのまま通り、`"1e20"` は float 経由で
    整数になる。どちらも int4 を超えて asyncpg が投げる（本番だけ 500）。
    範囲外は丸めず・切り詰めず 400 で返す。
    """
    if isinstance(value, bool):
        # 従来どおり ON/OFF を 1/0 として受ける
        return int(value)
    if isinstance(value, (int, float)):
        number: int | float = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                raise InvalidValueError(
                    f"{column.label} には数値を入力してください。"
                ) from None

    # inf / nan は int にも float8 にも「値」として入れてはいけない。
    # PostgreSQL の float8 は Infinity / NaN を**格納できてしまう**ので、
    # int 側と違って 500 にすらならず静かに入る。target_weight_g /
    # actual_weight_g に NaN が入ると services/progress_tree.py の
    # _resolve_weight() が子の合計を取る際に伝播し、重量ツリーが静かに壊れる。
    # ADR 0021 は未計測を 0.0 に丸めず None のまま扱うと決めているので、
    # 「値でない値」を第3の状態として通さない。
    if isinstance(number, float) and not math.isfinite(number):
        raise InvalidValueError(
            f"{column.label} が扱える範囲を超えています"
            "（inf / nan は保存できません）。"
        )

    if column.number_type == "int":
        # 2.0 のような「小数点付きの整数」は受ける（丸めではなく等価変換）
        if isinstance(number, float) and not number.is_integer():
            raise InvalidValueError(
                f"{column.label} には整数を入力してください（小数は使えません）。"
            )
        number = int(number)
        # 丸めない・切り詰めない。範囲外は 400 にして入れ直してもらう
        if not _INT32_MIN <= number <= _INT32_MAX:
            raise InvalidValueError(
                f"{column.label} が扱える範囲を超えています"
                f"（{_INT32_MIN} 〜 {_INT32_MAX}）。"
            )
        return number

    try:
        return float(number)
    except OverflowError:
        # 任意精度の int（"9" * 400 など）は float8 にできない
        raise InvalidValueError(
            f"{column.label} が扱える範囲を超えています。"
        ) from None


def _coerce(column: Column, value: Any) -> Any:
    """列の型に合わせて値を正規化する。合わない値は InvalidValueError。"""
    if value is None:
        return None
    if column.type == "number":
        return _coerce_number(column, value)
    if column.type == "bool":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return 1 if value else 0
        text = str(value).strip().lower()
        if text in _TRUE_VALUES:
            return 1
        if text in _FALSE_VALUES:
            return 0
        raise InvalidValueError(f"{column.label} には ON / OFF（1 または 0）を指定してください。")
    if column.type == "progress":
        text = str(value).strip()
        if not text:
            return None
        parsed = parse_progress(value)
        if parsed is None:
            raise InvalidValueError(
                f"{column.label} には 0.5 または 50% の形式で入力してください。"
            )
        return parsed
    return value


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
                WHERE s.guild_id = ? AND s.deleted_flag = 0
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

    async def list_all_rows(
        self, guild_id: int, table_key: str, *, sheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """指定テーブルの全行を返す（エクスポート用）。

        list_rows は画面表示用に MAX_LIMIT の上限を持つため、
        全件が必要なエクスポートではページングで読み切る。
        """
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            chunk = await self.list_rows(
                guild_id, table_key, limit=MAX_LIMIT, offset=offset, sheet_id=sheet_id
            )
            out.extend(chunk)
            if len(chunk) < MAX_LIMIT:
                return out
            offset += MAX_LIMIT

    async def get_row(self, guild_id: int, table_key: str, row_id: Any) -> dict[str, Any] | None:
        """1行を返す。行 ID が主キーの型に変換できなければ UnknownRowError。"""
        spec = get_spec(table_key)
        row = await self.db.fetchone(
            f"SELECT {', '.join(spec.column_names)} FROM {spec.table}"
            f" WHERE guild_id = ? AND {spec.pk} = ?",
            (guild_id, coerce_row_id(spec, row_id)),
        )
        return dict(row) if row else None

    async def update_row(
        self, guild_id: int, table_key: str, row_id: Any, values: dict[str, Any]
    ) -> bool:
        """編集可能な列だけを更新する（P2-5）。

        編集不可・未知の列が含まれていれば UnknownColumnError。
        列の型に合わない値は InvalidValueError。
        行 ID が主キーの型に変換できなければ UnknownRowError。
        更新した行があれば True。
        """
        spec = get_spec(table_key)
        # 変換は列の検査より前に行う（UPDATE へ到達させない）
        row_id = coerce_row_id(spec, row_id)
        by_name = {c.name: c for c in spec.columns if c.editable}
        unknown = [name for name in values if name not in by_name]
        if unknown:
            raise UnknownColumnError(", ".join(sorted(unknown)))
        if not values:
            return False
        coerced = {name: _coerce(by_name[name], value) for name, value in values.items()}
        assignments = ", ".join(f"{name} = ?" for name in coerced)
        cur = await self.db.execute(
            f"UPDATE {spec.table} SET {assignments} WHERE guild_id = ? AND {spec.pk} = ?",
            (*coerced.values(), guild_id, row_id),
        )
        return cur.rowcount > 0
