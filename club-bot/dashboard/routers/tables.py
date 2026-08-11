"""表グリッド API（P2-4: 読み取り専用）。

すべてのハンドラは検証済みの `GuildScope` だけを受け取り、
`scope.bind(TableRepository(db))` 経由でしかデータへ触れない。
テーブル名・列名はリポジトリ側のホワイトリストで解決する。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from dashboard.db import get_database
from dashboard.security import ScopedGuild
from repositories.table_repository import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TABLES,
    TableRepository,
    UnknownTableError,
    get_spec,
)

router = APIRouter(prefix="/api/guilds/{guild_id}")


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
