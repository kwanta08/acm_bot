"""中央スプレッドシートの機体進捗を DB（progress_nodes）へ移す一回限りの移行スクリプト。

背景:
    /progress の正本を Google Sheets から DB へ移した（migrations/009・
    スキーマ v10）。本スクリプトは移行前から運用していたサークル向けに、
    旧・中央スプレッドシートの内容を DB へ取り込む。

    移行後は GOOGLE_CREDENTIALS_PATH が不要になり、シートの共有作業も
    要らなくなる。詳細は docs/DESIGN_PUBLIC_DISTRIBUTION.md 1.3 / Phase 1。

取り込む内容:
    「進捗管理」シート     → progress_nodes（機体→パーツ→部品のツリー）
    「Todoist対応表」シート → progress_todoist_links
    「桁巻き対応表」＋桁巻きブックの「桁マスタ」→ progress_spar_links
    「設定」タブのデフォルト通知チャンネルID → settings（PROGRESS_DEFAULT_CHANNEL_ID）

重要（複数サーバーで1枚のシートを共有していた場合）:
    DB のツリーは **guild_id ごとに独立** する。旧構成で複数サーバーが
    同じシートを共有していた場合は、サーバーごとに本スクリプトを実行する
    （同じシートを複数ギルドへ取り込める）。取り込み後は各サーバーの
    進捗が別々に更新されるようになる。

前提:
    - gspread / google-auth は移行時のみ必要:
        venv/bin/pip install gspread google-auth
    - サービスアカウント JSON をシートへ「閲覧者」以上で共有しておく
      （本スクリプトはシートを読むだけで、書き込みは行わない）

使い方:
    # dry-run（既定。DB を変更しない。取り込み予定の件数と警告を確認）
    venv/bin/python scripts/migrate_progress_sheet_to_db.py \
        --guild-id 123456789012345678 --spreadsheet-id <シートID>

    # 実行（事前に自動で DB をバックアップ）
    venv/bin/python scripts/migrate_progress_sheet_to_db.py \
        --guild-id 123456789012345678 --spreadsheet-id <シートID> --apply

    # 既存の進捗ノードを消してから入れ直す（やり直し用）
    ... --apply --replace

環境変数:
    GOOGLE_CREDENTIALS_PATH  サービスアカウント JSON（既定 ./credentials.json）
    DB_PATH                  SQLite パス（既定 ./data/club.db）
    DATABASE_URL             PostgreSQL 接続 URL（設定時はこちらを使う）
    GUILD_ID                 --guild-id 未指定時のフォールバック

冪等:
    node_id / プロジェクト名 / 桁名をキーに upsert するため、
    再実行しても行は重複しない（--replace なしでも安全）。
出力:
    取り込み件数・スキップ理由・ツリーの検証結果（孤児・循環）。
    秘密情報は一切出力しない。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.progress_repository import ProgressRepository
from repositories.settings_repository import SettingsRepository
from services import progress_sheet_service as pss
from services import progress_sync_service
from services import spar_winding_service as sws
from services.progress_tree import build_tree
from utils.db import Database
from utils.parser import now, to_iso


@dataclass
class Stats:
    """取り込み結果の集計。"""

    input_rows: int = 0
    imported: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def line(self, name: str) -> str:
        return (
            f"{name}: 入力 {self.input_rows} 行 / 取り込み {self.imported} / "
            f"スキップ {self.skipped} / 警告 {len(self.warnings)}"
        )


# ---------------------------------------------------------------------
# 純粋関数: シートの行 → DB へ渡す値
# ---------------------------------------------------------------------
def node_to_upsert_kwargs(node) -> dict:
    """ProgressNode を ProgressRepository.upsert_node のキーワード引数へ変換する。

    集計進捗率（I 列）と深さ（D 列）はシート側の計算結果なので取り込まない
    （DB では services/progress_tree.py が毎回計算する）。
    """
    return {
        "node_id": node.node_id,
        "parent_id": node.parent_id,
        "sort_order": float(node.order or 0.0),
        "name": node.name,
        "assignee": node.assignee or None,
        "status": node.status or None,
        "manual_progress": node.manual_progress,
        "source": node.source or "manual",
        "todoist_task_id": node.todoist_task_id or None,
        "weight": float(node.weight or 1.0),
    }


def spar_links_from_sheets(
    spar_mappings: list[dict[str, str]], master: dict[str, int]
) -> tuple[list[dict], list[str]]:
    """桁巻き対応表と桁マスタから progress_spar_links の行を組み立てる。

    目標層数が桁マスタに無い桁はスキップし、理由を警告として返す。
    """
    rows: list[dict] = []
    warnings: list[str] = []
    for m in spar_mappings:
        key = m["spar_key"]
        target = master.get(key)
        if not target or target <= 0:
            warnings.append(
                f"桁「{key}」は桁マスタに目標層数が無いためスキップしました"
                "（移行後に /progress spar-link で登録してください）"
            )
            continue
        rows.append({"keta_name": key, "node_id": m["node_id"], "target_layers": int(target)})
    return rows, warnings


# ---------------------------------------------------------------------
# 取り込み
# ---------------------------------------------------------------------
async def import_nodes(
    repo: ProgressRepository, guild_id: int, grid: list[list], now_text: str, apply: bool
) -> Stats:
    stats = Stats()
    nodes = pss.grid_to_nodes(grid)
    stats.input_rows = max(len(grid) - 1, 0)

    # 取り込む前に構造を検証する（孤児・循環はシート側の書き間違い）
    tree = build_tree([*nodes])
    for err in tree.errors:
        stats.warnings.append(f"行 `{err.node_id or '(ID なし)'}`: {err.reason}")

    for node in nodes:
        if not node.node_id:
            stats.skipped += 1
            continue
        if apply:
            await repo.upsert_node(guild_id, now_text=now_text, **node_to_upsert_kwargs(node))
        stats.imported += 1
    return stats


async def import_todoist_links(
    repo: ProgressRepository, guild_id: int, grid: list[list], now_text: str, apply: bool
) -> Stats:
    stats = Stats()
    mappings = pss.parse_mapping_grid(grid)
    stats.input_rows = max(len(grid) - 1, 0)
    for m in mappings:
        # 旧シートは複数ギルドで共有していたため、他ギルドが登録した行も
        # 含まれうる。登録ギルドIDが入っていて別ギルドのものは取り込まない
        origin = (m.get("guild_id") or "").strip()
        if origin and origin.isdigit() and int(origin) != guild_id:
            stats.skipped += 1
            stats.warnings.append(
                f"プロジェクト「{m['project_name']}」は別サーバー"
                f"（{origin}）の登録のためスキップしました"
            )
            continue
        if apply:
            await repo.upsert_todoist_link(
                guild_id,
                m["project_name"],
                m["node_id"],
                now_text,
                notify_channel_id=m.get("notify_channel_id") or "",
            )
        stats.imported += 1
    return stats


async def import_spar_links(
    repo: ProgressRepository, guild_id: int, rows: list[dict], now_text: str, apply: bool
) -> Stats:
    stats = Stats(input_rows=len(rows))
    for row in rows:
        if apply:
            await repo.upsert_spar_link(
                guild_id, row["keta_name"], row["node_id"], row["target_layers"], now_text
            )
        stats.imported += 1
    return stats


async def import_default_channel(
    db: Database, guild_id: int, sheet_settings: dict[str, str], apply: bool
) -> str | None:
    """「設定」タブのデフォルト通知チャンネルID を settings へ移す。"""
    raw = (sheet_settings.get(pss.SHEET_KEY_DEFAULT_CHANNEL) or "").strip()
    if not raw.isdigit():
        return None
    if apply:
        await SettingsRepository(db).set(
            guild_id, progress_sync_service.SETTINGS_DEFAULT_CHANNEL_KEY, raw
        )
    return raw


# ---------------------------------------------------------------------
# シート読み込み
# ---------------------------------------------------------------------
def _read_sheets(client: pss.ProgressSheetClient, spreadsheet_id: str) -> dict:
    """必要なシートをまとめて読む（存在しないシートは空として扱う）。"""

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001  (シート未作成・権限不足)
            print(f"警告: シートを読めませんでした（{type(e).__name__}）。空として続行します。")
            return []

    out = {
        "progress": _safe(client.read_progress_grid, spreadsheet_id),
        "mapping": _safe(client.read_mapping_grid, spreadsheet_id),
        "settings": _safe(client.read_settings_grid, spreadsheet_id),
        "spar_mapping": _safe(client.read_spar_mapping_grid, spreadsheet_id),
    }
    sheet_settings = pss.parse_settings_grid(out["settings"])
    out["sheet_settings"] = sheet_settings

    # 桁巻きブック（別ファイル）の「桁マスタ」から目標層数を読む
    spar_book = (sheet_settings.get(pss.SHEET_KEY_SPAR_BOOK) or "").strip()
    out["spar_master"] = {}
    if spar_book:
        grid = _safe(client.read_grid, spar_book, sws.SPAR_MASTER_SHEET)
        out["spar_master"] = sws.parse_master_grid(grid)
    return out


# ---------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------
async def main(args: argparse.Namespace) -> None:
    guild_id = args.guild_id or int((os.getenv("GUILD_ID") or "0").strip() or 0)
    if not guild_id:
        print("ERROR: --guild-id または環境変数 GUILD_ID で対象ギルドを指定してください。")
        sys.exit(1)
    if not args.spreadsheet_id:
        print("ERROR: --spreadsheet-id で移行元のスプレッドシート ID を指定してください。")
        sys.exit(1)

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    db_path = args.db_path or (os.getenv("DB_PATH") or "./data/club.db").strip()
    if not database_url:
        if not os.path.exists(db_path):
            print(f"ERROR: DB が見つかりません: {db_path}")
            sys.exit(1)
        if args.apply:
            stamp = to_iso(now()).replace(":", "").replace(" ", "-")
            backup = f"{db_path}.bak.{stamp}"
            shutil.copy2(db_path, backup)
            print(f"バックアップを作成しました: {backup}")
    elif args.apply:
        print(
            "注意: PostgreSQL へ適用します。事前に pg_dump を取得して"
            "ください（本スクリプトは自動バックアップを行いません）。"
        )

    client = pss.ProgressSheetClient()
    try:
        sheets = _read_sheets(client, args.spreadsheet_id)
    except pss.ProgressSheetUnavailable as e:
        print(f"ERROR: Sheets へ接続できません: {e}")
        sys.exit(1)

    db = Database(db_path, database_url=database_url or None)
    await db.connect()
    try:
        repo = ProgressRepository(db)
        now_text = progress_sync_service._now_text()

        if args.replace:
            existing = await repo.count_nodes(guild_id)
            print(f"--replace: 既存の進捗ノード {existing} 件を削除します（guild={guild_id}）")
            if args.apply:
                await repo.delete_all_nodes(guild_id)

        results: list[tuple[str, Stats]] = []
        results.append(
            (
                "進捗管理 → progress_nodes",
                await import_nodes(repo, guild_id, sheets["progress"], now_text, args.apply),
            )
        )
        results.append(
            (
                "Todoist対応表 → progress_todoist_links",
                await import_todoist_links(repo, guild_id, sheets["mapping"], now_text, args.apply),
            )
        )

        spar_rows, spar_warnings = spar_links_from_sheets(
            pss.parse_spar_mapping_grid(sheets["spar_mapping"]), sheets["spar_master"]
        )
        spar_stats = await import_spar_links(repo, guild_id, spar_rows, now_text, args.apply)
        spar_stats.warnings.extend(spar_warnings)
        spar_stats.skipped += len(spar_warnings)
        results.append(("桁巻き対応表 → progress_spar_links", spar_stats))

        channel = await import_default_channel(db, guild_id, sheets["sheet_settings"], args.apply)

        print(f"\n===== 移行結果（guild={guild_id}{'' if args.apply else ' / dry-run'}） =====")
        for name, stats in results:
            print(stats.line(name))
            for warning in stats.warnings[:20]:
                print(f"  [警告] {warning}")
        print(
            "デフォルト通知チャンネル: "
            + (f"{channel} を settings へ保存" if channel else "未設定（スキップ）")
        )

        if args.apply:
            print(
                f"\n完了しました。`/progress view` で確認してください。"
                f"（登録ノード数: {await repo.count_nodes(guild_id)}）"
            )
        else:
            print("\ndry-run のため DB は変更していません。実行するには --apply を付けてください。")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="取り込み先のギルド ID（未指定時は環境変数 GUILD_ID）",
    )
    parser.add_argument("--spreadsheet-id", default=None, help="移行元の中央スプレッドシート ID")
    parser.add_argument("--db-path", default=None, help="SQLite のパス")
    parser.add_argument(
        "--replace", action="store_true", help="取り込み前に対象ギルドの進捗ノードを全削除する"
    )
    parser.add_argument(
        "--apply", action="store_true", help="実際に移行を実行する（既定は dry-run）"
    )
    asyncio.run(main(parser.parse_args()))
