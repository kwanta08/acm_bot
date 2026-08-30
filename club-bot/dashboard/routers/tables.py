"""表グリッド API（P2-4: 参照 / P2-5: 編集 / P3-2: CSV 出力）。

すべてのハンドラは検証済みの `GuildScope` だけを受け取り、
`scope.bind(TableRepository(db))` 経由でしかデータへ触れない。
テーブル名・列名はリポジトリ側のホワイトリストで解決する。

表示の整形（ID → 表示名・チャンネル名、日時の JST 秒表示）は
dashboard/display.py に集約し、各行へ `_display` として添える。
DB の値そのもの（ID・ISO 文字列）は変えない。

シート切替（予定・桁ごとのタブ）は `?sheet=` で行い、絞り込み条件は
リポジトリ側で固定する。

編集（PATCH）は班長以上（EditorGuild）に限定し、
**成功・失敗にかかわらず audit_log へ必ず記録する**。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Response

from dashboard import display
from dashboard.db import get_database
from dashboard.display import NameMaps
from dashboard.security import EditorGuild, GuildScope, ScopedGuild
from repositories.audit_log_repository import AuditLogRepository
from repositories.member_repository import MemberRepository
from repositories.name_cache_repository import (
    ENTITY_CHANNEL,
    ENTITY_USER,
    NameCacheRepository,
)
from repositories.schedule_repository import ScheduleRepository
from repositories.table_repository import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SHEET_TABLES,
    TABLES,
    InvalidValueError,
    TableRepository,
    TableSpec,
    UnknownColumnError,
    UnknownRowError,
    UnknownTableError,
    get_spec,
    rows_to_csv,
)
from utils.logger import get_logger
from utils.permissions import Level

log = get_logger("dashboard.tables")

router = APIRouter(prefix="/api/guilds/{guild_id}")

# 監査ログに残す値の最大長（1セルに長文が入っていてもログを膨らませない）
AUDIT_VALUE_MAX = 200

# ?sheet= の長さ上限（schedule_id / 桁名。異常な長文をバインドしない）
SHEET_ID_MAX = 200

# ?q= の長さ上限（検索語。異常な長文をバインドしない）
SEARCH_MAX = 200


def _columns_payload(spec) -> list[dict]:
    return [
        {"name": c.name, "label": c.label, "type": c.type, "editable": c.editable}
        for c in spec.columns
    ]


async def _name_maps(scope: GuildScope, spec: TableSpec) -> NameMaps:
    """表示解決に必要な辞書だけを一括で読む（列型ごとに1クエリ。N+1 なし）。

    人物は members 台帳（サークルの名簿名）を土台に、bot が同期した
    Discord のギルド表示名キャッシュ（ニックネーム → グローバル表示名 →
    ユーザー名）で上書きする。どちらにも無い ID は表示層が
    ID 付きフォールバックにする。

    班は teams テーブル（無効化済みも含む）から班キー → 班名を引く。
    teams に無いキーは表示層が slug のまま出す。
    """
    db = get_database()
    types = {c.type for c in spec.columns}
    users: dict[str, str] = {}
    channels: dict[str, str] = {}
    options: dict[str, str] = {}
    teams: dict[str, str] = {}
    if "user" in types:
        register = {
            str(m["user_id"]): str(m["display_name"])
            for m in await scope.bind(MemberRepository(db)).list_members(
                active_only=False, include_alumni=True
            )
        }
        cached = await scope.bind(NameCacheRepository(db)).names(ENTITY_USER)
        users = {**register, **cached}
    if "channel" in types:
        channels = await scope.bind(NameCacheRepository(db)).names(ENTITY_CHANNEL)
    if "option" in types:
        options = await scope.bind(ScheduleRepository(db)).list_option_labels()
    if types.intersection(display.TEAM_TYPES):
        teams = {
            str(t["team_key"]): str(t["team_name"])
            for t in await scope.bind(MemberRepository(db)).list_teams(active_only=False)
        }
    return NameMaps(users=users, channels=channels, options=options, teams=teams)


def _visible_spec(scope: GuildScope, table_key: str) -> TableSpec:
    """表の定義を返す。権限が足りなければ 403、無い表なら 404。

    必要レベルは **TableSpec が持つ**（`min_level`）。ルータ側の if で
    守ると、表を足すときに書き忘れて素通りする（ADR 0016）。
    """
    try:
        spec = get_spec(table_key)
    except UnknownTableError:
        raise HTTPException(status_code=404, detail="その表は存在しません。") from None
    scope.require(Level(spec.min_level))
    return spec


def _validate_sort(spec: TableSpec, sort: str | None, dir: str) -> None:
    """`?sort=` / `?dir=` を検査する（不正は 400。500 にしない）。

    列名はリポジトリ側（sortable_columns）でも検査されるが、
    HTTP 400 への変換はここで行う。
    """
    if dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="dir は asc か desc を指定してください。")
    if sort is not None and sort not in spec.sortable_columns:
        raise HTTPException(status_code=400, detail="その列では並び替えできません。")


@router.get("/tables")
async def list_tables(scope: ScopedGuild):
    """閲覧できる表の一覧を返す（権限が足りない表は出さない）。"""
    return {
        "tables": [
            {"key": spec.key, "label": spec.label, "description": spec.description}
            for spec in TABLES.values()
            if scope.level >= spec.min_level
        ],
        "can_edit": scope.level >= 2,
    }


@router.get("/tables/{table_key}")
async def read_table(
    scope: ScopedGuild,
    table_key: str,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    sheet: Annotated[str | None, Query(max_length=SHEET_ID_MAX)] = None,
    q: Annotated[str | None, Query(max_length=SEARCH_MAX)] = None,
    sort: Annotated[str | None, Query(max_length=100)] = None,
    dir: Annotated[str, Query(max_length=10)] = "asc",
):
    """表の内容を返す（このサーバーの行のみ）。

    シート対応の表（出欠回答＝予定ごと / 桁巻き記録＝桁ごと）では
    `sheets` にタブ一覧を返し、`sheet` 未指定なら先頭のシートに絞る。
    予定・桁が 0 件でもエラーにしない（空のタブ一覧と空の行を返す）。
    """
    spec = _visible_spec(scope, table_key)
    q = q or None
    if q is not None and not spec.searchable:
        raise HTTPException(status_code=400, detail="この表は検索に対応していません。")
    sort = sort or None
    _validate_sort(spec, sort, dir)

    repo = scope.bind(TableRepository(get_database()))
    sheets_payload: dict | None = None
    sheet_id: str | None = None
    if table_key in SHEET_TABLES:
        items = display.build_sheets(await repo.list_sheets(table_key))
        sheet_id = sheet if sheet is not None else (items[0]["id"] if items else None)
        sheets_payload = {"noun": SHEET_TABLES[table_key], "items": items, "active": sheet_id}
    elif sheet is not None:
        raise HTTPException(status_code=400, detail="この表はシート切替に対応していません。")

    if table_key in SHEET_TABLES and sheet_id is None:
        # 予定・桁がまだ1件も無い（空状態）。行の取得は行わない
        rows: list[dict[str, Any]] = []
        total = 0
    else:
        rows = await repo.list_rows(
            table_key, limit=limit, offset=offset, sheet_id=sheet_id, q=q, sort=sort, dir=dir
        )
        total = await repo.count_rows(table_key, sheet_id=sheet_id, q=q)

    maps = await _name_maps(scope, spec)

    # 出欠回答はピボット表（1行 = 候補日時、セル = 表示名の列挙）も返す。
    # 回答対象者はダッシュボードではロールを解決できないため、
    # members 台帳の現役メンバー（active）を使う（dashboard/README.md 参照）
    pivot: dict[str, Any] | None = None
    if table_key == "schedule_votes" and sheet_id is not None:
        schedules = scope.bind(ScheduleRepository(get_database()))
        targets = [
            str(m["user_id"])
            for m in await scope.bind(MemberRepository(get_database())).list_members()
        ]
        pivot = display.build_attendance_pivot(
            await schedules.list_options(sheet_id),
            await schedules.list_schedule_votes(sheet_id),
            targets,
            maps.users,
        )

    return {
        "table": {
            "key": spec.key,
            "label": spec.label,
            "pk": spec.pk,
            "description": spec.description,
            # 検索対象列（無い表ではフロントが検索欄を出さない）
            "searchable": list(spec.searchable),
            # 並び替え可能列（ヘッダクリックの対象）
            "sortable": list(spec.sortable_columns),
        },
        "columns": _columns_payload(spec),
        "rows": display.attach_display(spec, rows, maps),
        "sheets": sheets_payload,
        "pivot": pivot,
        "total": total,
        "limit": limit,
        "offset": offset,
        "can_edit": scope.level >= 2,
    }


# 一覧 API と紛れないよう独立したパスにする
# （/tables/{table_key} が "members.csv" を拾ってしまうため）
@router.get("/tables/{table_key}/export.csv")
async def export_table_csv(
    scope: ScopedGuild,
    table_key: str,
    sheet: Annotated[str | None, Query(max_length=SHEET_ID_MAX)] = None,
    q: Annotated[str | None, Query(max_length=SEARCH_MAX)] = None,
    sort: Annotated[str | None, Query(max_length=100)] = None,
    dir: Annotated[str, Query(max_length=10)] = "asc",
):
    """表を CSV でダウンロードする（このサーバーの行のみ）。

    Google Sheets へのエクスポート連携（旧 /sheet_sync）の置き換え。
    人が Excel で読むための出力なので、画面と同じく ID は表示名へ、
    日時は JST（秒まで）へ変換して出す。生値のままの完全バックアップは
    bot 側の `/data export` が担う。

    **全件を出す。** 画面の上限（MAX_LIMIT）で切ると、数年運用したサーバーが
    引き継ぎ用に落とした CSV から古い行が黙って欠ける。
    監査ログには正常終了として残るため、欠落に気づく手段が無い。
    """
    spec = _visible_spec(scope, table_key)
    if sheet is not None and table_key not in SHEET_TABLES:
        raise HTTPException(status_code=400, detail="この表はシート切替に対応していません。")
    q = q or None
    if q is not None and not spec.searchable:
        raise HTTPException(status_code=400, detail="この表は検索に対応していません。")
    sort = sort or None
    _validate_sort(spec, sort, dir)

    repo = scope.bind(TableRepository(get_database()))
    rows = await repo.list_all_rows(table_key, sheet_id=sheet, q=q, sort=sort, dir=dir)
    maps = await _name_maps(scope, spec)

    detail = f"{len(rows)} 行を CSV 出力"
    if sheet is not None:
        detail += f"（シート: {sheet}）"
    if q is not None:
        detail += f"（検索: {q}）"
    await _audit(scope, "dashboard.export", spec.table, detail)
    # ファイル名にサーバー名を入れない（他者へ共有されたときの情報漏れを避ける）
    filename = f"{spec.key}_{scope.guild_id}.csv"
    return Response(
        content=rows_to_csv(spec, display.export_rows(spec, rows, maps)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _short(value: Any) -> str:
    text = "（空）" if value in (None, "") else str(value)
    return text if len(text) <= AUDIT_VALUE_MAX else text[:AUDIT_VALUE_MAX] + "…"


async def _audit(scope: GuildScope, action: str, target: str, detail: str) -> None:
    """監査ログへ記録する（記録の失敗で編集自体を失敗させない）。"""
    try:
        await scope.bind(AuditLogRepository(get_database())).record(
            actor_id=scope.user_id, action=action, target=target, detail=detail
        )
    except Exception as e:  # noqa: BLE001
        log.warning("監査ログの記録に失敗しました (guild=%s): %s", scope.guild_id, type(e).__name__)


@router.patch("/tables/{table_key}/{row_id}")
async def update_row(
    scope: EditorGuild,
    table_key: str,
    row_id: str,
    values: Annotated[dict[str, Any], Body(...)],
):
    """1行の編集可能な列を更新する（班長以上）。

    変更前後の値を audit_log に記録する。編集不可の列・存在しない行は
    エラーにし、その試み自体も監査ログへ残す。
    """
    spec = _visible_spec(scope, table_key)
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="変更内容がありません。")

    repo = scope.bind(TableRepository(get_database()))
    try:
        before = await repo.get_row(table_key, row_id)
    except UnknownRowError:
        # 主キーの型に変換できない ID。存在しない行と同じ扱いにする
        # （PostgreSQL ではここを通さないと asyncpg が DataError を投げ、
        #   利用者には 500 が返っていた）。update_row へは到達しない
        raise HTTPException(status_code=404, detail="対象の行がありません。") from None
    if before is None:
        # 他サーバーの行 ID を指定した場合もここに来る（guild_id 条件付きの
        # SELECT で見つからないため、存在の有無を区別しない）
        raise HTTPException(status_code=404, detail="対象の行がありません。")

    try:
        changed = await repo.update_row(table_key, row_id, values)
    except UnknownColumnError as e:
        await _audit(
            scope, "dashboard.update.rejected", f"{spec.table}#{row_id}", f"編集できない列: {e}"
        )
        raise HTTPException(status_code=400, detail="その列は編集できません。") from None
    except InvalidValueError as e:
        await _audit(
            scope, "dashboard.update.rejected", f"{spec.table}#{row_id}", f"値の形式が不正: {e}"
        )
        raise HTTPException(status_code=400, detail=str(e)) from None

    if not changed:
        raise HTTPException(status_code=404, detail="対象の行がありません。")

    after = await repo.get_row(table_key, row_id)
    diff = ", ".join(
        f"{name}: {_short(before.get(name))} → {_short(values[name])}" for name in values
    )
    await _audit(scope, "dashboard.update", f"{spec.table}#{row_id}", diff)
    maps = await _name_maps(scope, spec)
    return {"row": display.attach_display_row(spec, after, maps), "changed": list(values)}
