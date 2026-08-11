"""表グリッド API（P2-4: 参照 / P2-5: 編集）。

すべてのハンドラは検証済みの `GuildScope` だけを受け取り、
`scope.bind(TableRepository(db))` 経由でしかデータへ触れない。
テーブル名・列名はリポジトリ側のホワイトリストで解決する。

編集（PATCH）は班長以上（EditorGuild）に限定し、
**成功・失敗にかかわらず audit_log へ必ず記録する**。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query

from dashboard.db import get_database
from dashboard.security import EditorGuild, GuildScope, ScopedGuild
from repositories.audit_log_repository import AuditLogRepository
from repositories.table_repository import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TABLES,
    TableRepository,
    UnknownColumnError,
    UnknownTableError,
    get_spec,
)
from utils.logger import get_logger

log = get_logger("dashboard.tables")

router = APIRouter(prefix="/api/guilds/{guild_id}")

# 監査ログに残す値の最大長（1セルに長文が入っていてもログを膨らませない）
AUDIT_VALUE_MAX = 200


def _columns_payload(spec) -> list[dict]:
    return [{"name": c.name, "label": c.label, "type": c.type,
             "editable": c.editable} for c in spec.columns]


@router.get("/tables")
async def list_tables(scope: ScopedGuild):
    """閲覧できる表の一覧を返す。"""
    return {
        "tables": [
            {"key": spec.key, "label": spec.label,
             "description": spec.description}
            for spec in TABLES.values()
        ],
        "can_edit": scope.level >= 2,
    }


@router.get("/tables/{table_key}")
async def read_table(
    scope: ScopedGuild,
    table_key: str,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """表の内容を返す（このサーバーの行のみ）。"""
    try:
        spec = get_spec(table_key)
    except UnknownTableError:
        raise HTTPException(status_code=404,
                            detail="その表は存在しません。") from None

    repo = scope.bind(TableRepository(get_database()))
    rows = await repo.list_rows(table_key, limit=limit, offset=offset)
    return {
        "table": {"key": spec.key, "label": spec.label, "pk": spec.pk,
                  "description": spec.description},
        "columns": _columns_payload(spec),
        "rows": rows,
        "total": await repo.count_rows(table_key),
        "limit": limit,
        "offset": offset,
        "can_edit": scope.level >= 2,
    }


def _short(value: Any) -> str:
    text = "（空）" if value in (None, "") else str(value)
    return text if len(text) <= AUDIT_VALUE_MAX else text[:AUDIT_VALUE_MAX] + "…"


async def _audit(scope: GuildScope, action: str, target: str,
                 detail: str) -> None:
    """監査ログへ記録する（記録の失敗で編集自体を失敗させない）。"""
    try:
        await scope.bind(AuditLogRepository(get_database())).record(
            actor_id=scope.user_id, action=action, target=target,
            detail=detail)
    except Exception as e:  # noqa: BLE001
        log.warning("監査ログの記録に失敗しました (guild=%s): %s",
                    scope.guild_id, type(e).__name__)


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
    try:
        spec = get_spec(table_key)
    except UnknownTableError:
        raise HTTPException(status_code=404,
                            detail="その表は存在しません。") from None
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="変更内容がありません。")

    repo = scope.bind(TableRepository(get_database()))
    before = await repo.get_row(table_key, row_id)
    if before is None:
        # 他サーバーの行 ID を指定した場合もここに来る（guild_id 条件付きの
        # SELECT で見つからないため、存在の有無を区別しない）
        raise HTTPException(status_code=404, detail="対象の行がありません。")

    try:
        changed = await repo.update_row(table_key, row_id, values)
    except UnknownColumnError as e:
        await _audit(scope, "dashboard.update.rejected",
                     f"{spec.table}#{row_id}", f"編集できない列: {e}")
        raise HTTPException(
            status_code=400,
            detail="その列は編集できません。") from None

    if not changed:
        raise HTTPException(status_code=404, detail="対象の行がありません。")

    after = await repo.get_row(table_key, row_id)
    diff = ", ".join(
        f"{name}: {_short(before.get(name))} → {_short(values[name])}"
        for name in values)
    await _audit(scope, "dashboard.update", f"{spec.table}#{row_id}", diff)
    return {"row": after, "changed": list(values)}
