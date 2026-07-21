"""Google Sheets の既存データを DB（正規化テーブル）へ移す一回限りの移行スクリプト。

前提:
    - DB（SQLite）はすでに正本として稼働しており、Sheets はそのミラーだった。
      このスクリプトは「Sheets にだけ存在する行」（手編集・過去分）を
      取りこぼさないために DB へ補完する。
    - gspread / google-auth は移行時のみ必要:
        venv/bin/pip install gspread google-auth

使い方:
    # dry-run（既定。DB を変更しない。対象件数の確認）
    venv/bin/python scripts/migrate_sheets_to_db.py --guild-id 123456789012345678

    # 実行（事前に自動で DB をバックアップ）
    venv/bin/python scripts/migrate_sheets_to_db.py --guild-id 123456789012345678 --apply

環境変数（移行時のみ使用）:
    GOOGLE_CREDENTIALS_PATH  サービスアカウント JSON（既定 ./credentials.json）
    SPREADSHEET_ID           運用台帳ブック ID
    SHEET_TASKS / SHEET_ATTENDANCE / SHEET_MEMBERS  各シート名（既定 tasks/attendance/members）
    LAYER_SPREADSHEET_ID     桁巻きブック ID（未設定なら SPREADSHEET_ID を使用）
    DB_PATH                  SQLite パス（既定 ./data/club.db）

出力: シートごとに 入力行数 / 移行数 / スキップ数 / エラー詳細（行番号と理由）。
      秘密情報（Todoist トークン等）は一切出力しない。
冪等: 各エンティティの自然キーで重複検知するため、再実行しても重複行は増えない。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db import Database  # noqa: E402
from utils.parser import now, to_iso  # noqa: E402

# 旧 Sheets の列構成（cogs/sheets.py / services/sheets_service.py のヘッダー定義に一致）
TASK_COLS = {"local_id": 0, "todoist_id": 1, "title": 2, "assignee": 3, "team": 4,
             "due": 5, "priority": 6, "status": 7, "created_by": 8,
             "created_at": 9, "completed_at": 10}
ATT_COLS = {"schedule_id": 0, "event": 1, "option": 2, "user": 3, "status": 4,
            "deadline": 5, "aggregated_at": 6}
MEM_COLS = {"user_id": 0, "display_name": 1, "primary": 2, "secondary": 3,
            "is_leader": 4, "skills": 5, "joined_at": 6, "active": 7}
LAYER_COLS = {"layer_num": 0, "worker": 1, "started": 2, "ended": 3, "minutes": 4}

VALID_TASK_STATUS = {"open", "done", "archived"}
VALID_VOTE_STATUS = {"ok", "maybe", "ng", "yes"}  # yes は旧形式（ok に読み替える）


@dataclass
class Stats:
    """シートごとの集計。"""
    input_rows: int = 0
    migrated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self, name: str) -> str:
        return (f"{name}: 入力 {self.input_rows} 行 / 移行 {self.migrated} 件 / "
                f"スキップ {self.skipped} 件 / エラー {len(self.errors)} 件")


def _cell(row: list, idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ""


async def _name_to_user_id(db: Database, guild_id: int) -> dict[str, str]:
    """display_name → user_id の一意マップ（同名が複数いる名前は解決しない）。"""
    rows = await db.fetchall(
        "SELECT user_id, display_name FROM members WHERE guild_id = ?", (guild_id,))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["display_name"]] = counts.get(r["display_name"], 0) + 1
    return {r["display_name"]: r["user_id"] for r in rows
            if counts[r["display_name"]] == 1}


async def _name_to_team_key(db: Database, guild_id: int) -> dict[str, str]:
    rows = await db.fetchall(
        "SELECT team_key, team_name FROM teams WHERE guild_id = ?", (guild_id,))
    return {r["team_name"]: r["team_key"] for r in rows}


# ---------------------------------------------------------------------
# インポータ（gspread 非依存。テストから直接呼べる）
# ---------------------------------------------------------------------
async def import_tasks(db: Database, guild_id: int, rows: list[list],
                       apply: bool) -> Stats:
    """tasks シート → tasks テーブル（ローカルID で重複検知）。"""
    stats = Stats()
    team_map = await _name_to_team_key(db, guild_id)
    for i, row in enumerate(rows, start=2):  # ヘッダー分 +1
        stats.input_rows += 1
        try:
            local_id = _cell(row, TASK_COLS["local_id"])
            title = _cell(row, TASK_COLS["title"])
            if not local_id.isdigit():
                stats.skipped += 1
                continue
            if not title:
                stats.errors.append(f"行{i}: タスク名が空のためスキップ")
                continue
            exists = await db.fetchone(
                "SELECT 1 FROM tasks WHERE guild_id = ? AND local_task_id = ?",
                (guild_id, int(local_id)))
            if exists:
                stats.skipped += 1
                continue
            if apply:
                team_name = _cell(row, TASK_COLS["team"])
                priority = _cell(row, TASK_COLS["priority"])
                status = _cell(row, TASK_COLS["status"]) or "open"
                await db.execute(
                    """
                    INSERT INTO tasks
                        (local_task_id, guild_id, todoist_task_id, title, assignee_id,
                         team_key, due_date, priority, location_key, status,
                         created_by, created_at, completed_at)
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (int(local_id), guild_id,
                     _cell(row, TASK_COLS["todoist_id"]) or None,
                     title,
                     team_map.get(team_name) if team_name else None,
                     _cell(row, TASK_COLS["due"]) or None,
                     int(priority) if priority.isdigit() else None,
                     status if status in VALID_TASK_STATUS else "open",
                     _cell(row, TASK_COLS["created_by"]) or "sheets-import",
                     _cell(row, TASK_COLS["created_at"]) or to_iso(now()),
                     _cell(row, TASK_COLS["completed_at"]) or None))
            stats.migrated += 1
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"行{i}: {type(e).__name__}")
    return stats


async def import_members(db: Database, guild_id: int, rows: list[list],
                         apply: bool) -> Stats:
    """members シート → members テーブル（ユーザーID で重複検知）。
    技能は skill_tags にも自動登録する。"""
    stats = Stats()
    team_map = await _name_to_team_key(db, guild_id)
    for i, row in enumerate(rows, start=2):
        stats.input_rows += 1
        try:
            user_id = _cell(row, MEM_COLS["user_id"])
            display_name = _cell(row, MEM_COLS["display_name"])
            if not user_id.isdigit():
                stats.skipped += 1
                continue
            if not display_name:
                stats.errors.append(f"行{i}: 表示名が空のためスキップ")
                continue
            exists = await db.fetchone(
                "SELECT 1 FROM members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id))
            if exists:
                stats.skipped += 1
                continue
            if apply:
                import json
                primary_name = _cell(row, MEM_COLS["primary"])
                secondary_names = [s.strip() for s in _cell(row, MEM_COLS["secondary"]).split("、") if s.strip()]
                skills = [s.strip() for s in _cell(row, MEM_COLS["skills"]).split("、") if s.strip()]
                # 技能タグをギルドのマスタに自動登録
                for skill in skills:
                    await db.execute(
                        "INSERT INTO skill_tags (guild_id, skill_name, active_flag,"
                        " created_by, created_at) VALUES (?, ?, 1, 'sheets-import', ?)"
                        " ON CONFLICT(guild_id, skill_name) DO UPDATE SET active_flag = 1",
                        (guild_id, skill, to_iso(now())))
                await db.execute(
                    """
                    INSERT INTO members
                        (guild_id, user_id, display_name, primary_team, secondary_teams,
                         is_leader, skills, notes, joined_at, active_flag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (guild_id, user_id, display_name,
                     team_map.get(primary_name) if primary_name else None,
                     json.dumps([team_map[n] for n in secondary_names if n in team_map],
                                ensure_ascii=False),
                     1 if _cell(row, MEM_COLS["is_leader"]) else 0,
                     json.dumps(skills, ensure_ascii=False),
                     _cell(row, MEM_COLS["joined_at"]) or to_iso(now()),
                     1 if _cell(row, MEM_COLS["active"]) in ("在籍", "") else 0))
            stats.migrated += 1
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"行{i}: {type(e).__name__}")
    return stats


async def import_attendance(db: Database, guild_id: int, rows: list[list],
                            apply: bool) -> Stats:
    """attendance シート → schedule_votes（schedule/option/ユーザーを解決できる行のみ）。"""
    stats = Stats()
    name_map = await _name_to_user_id(db, guild_id)
    for i, row in enumerate(rows, start=2):
        stats.input_rows += 1
        try:
            schedule_id = _cell(row, ATT_COLS["schedule_id"])
            option_label = _cell(row, ATT_COLS["option"])
            user_name = _cell(row, ATT_COLS["user"])
            status = _cell(row, ATT_COLS["status"])
            if status == "yes":
                status = "ok"
            if status not in {"ok", "maybe", "ng"}:
                stats.skipped += 1
                continue
            schedule = await db.fetchone(
                "SELECT 1 FROM schedules WHERE guild_id = ? AND schedule_id = ?",
                (guild_id, schedule_id))
            if not schedule:
                stats.skipped += 1
                continue
            option = await db.fetchone(
                "SELECT option_id FROM schedule_options"
                " WHERE guild_id = ? AND schedule_id = ? AND label = ?",
                (guild_id, schedule_id, option_label))
            if not option:
                stats.skipped += 1
                continue
            user_id = name_map.get(user_name)
            if not user_id:
                stats.skipped += 1  # 表示名からユーザーを一意に解決できない
                continue
            exists = await db.fetchone(
                "SELECT 1 FROM schedule_votes"
                " WHERE guild_id = ? AND option_id = ? AND user_id = ?",
                (guild_id, option["option_id"], user_id))
            if exists:
                stats.skipped += 1
                continue
            if apply:
                await db.execute(
                    "INSERT INTO schedule_votes (guild_id, option_id, user_id, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (guild_id, option["option_id"], user_id, status,
                     _cell(row, ATT_COLS["aggregated_at"]) or to_iso(now())))
            stats.migrated += 1
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"行{i}: {type(e).__name__}")
    return stats


async def import_layer_rows(db: Database, guild_id: int, keta: str,
                            rows: list[list], apply: bool) -> Stats:
    """桁別シート → layer_records（keta + ユーザー + 開始時刻で重複検知）。"""
    stats = Stats()
    name_map = await _name_to_user_id(db, guild_id)
    for i, row in enumerate(rows, start=2):
        stats.input_rows += 1
        try:
            layer_num = _cell(row, LAYER_COLS["layer_num"])
            started = _cell(row, LAYER_COLS["started"])
            ended = _cell(row, LAYER_COLS["ended"])
            minutes = _cell(row, LAYER_COLS["minutes"])
            user_id = name_map.get(_cell(row, LAYER_COLS["worker"]))
            if not layer_num or not started or not ended or not minutes.isdigit():
                stats.skipped += 1
                continue
            if not user_id:
                stats.skipped += 1
                continue
            exists = await db.fetchone(
                "SELECT 1 FROM layer_records"
                " WHERE guild_id = ? AND user_id = ? AND keta = ? AND started_at = ?",
                (guild_id, user_id, keta, started))
            if exists:
                stats.skipped += 1
                continue
            if apply:
                await db.execute(
                    "INSERT INTO layer_keta (guild_id, keta_name, active_flag,"
                    " created_by, created_at) VALUES (?, ?, 1, 'sheets-import', ?)"
                    " ON CONFLICT(guild_id, keta_name) DO UPDATE SET active_flag = 1",
                    (guild_id, keta, to_iso(now())))
                await db.execute(
                    """
                    INSERT INTO layer_records
                        (guild_id, user_id, keta, layer_num, started_at, ended_at,
                         minutes, synced_flag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (guild_id, user_id, keta, layer_num, started, ended, int(minutes)))
            stats.migrated += 1
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"行{i}: {type(e).__name__}")
    return stats


# ---------------------------------------------------------------------
# gspread による Sheets 読み取り（移行時のみ）
# ---------------------------------------------------------------------
def _load_sheets() -> dict[str, dict[str, list[list]]]:
    """{ブック種別: {シート名: 行データ}} を返す。gspread 未導入なら案内して終了。"""
    try:
        import gspread  # noqa: PLC0415
        from google.oauth2.service_account import Credentials  # noqa: PLC0415
    except ImportError:
        print("ERROR: gspread / google-auth が見つかりません。"
              "移行時のみ次を実行してください:\n"
              "  venv/bin/pip install gspread google-auth")
        sys.exit(1)

    creds_path = (os.getenv("GOOGLE_CREDENTIALS_PATH") or "./credentials.json").strip()
    spreadsheet_id = (os.getenv("SPREADSHEET_ID") or "").strip()
    if not spreadsheet_id or not os.path.exists(creds_path):
        print("ERROR: SPREADSHEET_ID または GOOGLE_CREDENTIALS_PATH（JSON）が"
              " 見つかりません。移行時のみ環境変数で指定してください。")
        sys.exit(1)

    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    client = gspread.authorize(
        Credentials.from_service_account_file(creds_path, scopes=scopes))

    sheet_tasks = (os.getenv("SHEET_TASKS") or "tasks").strip()
    sheet_attendance = (os.getenv("SHEET_ATTENDANCE") or "attendance").strip()
    sheet_members = (os.getenv("SHEET_MEMBERS") or "members").strip()

    out: dict[str, dict[str, list[list]]] = {"main": {}, "layer": {}}
    book = client.open_by_key(spreadsheet_id)
    for name in (sheet_tasks, sheet_attendance, sheet_members):
        try:
            out["main"][name] = book.worksheet(name).get_all_values()[1:]  # ヘッダー除外
        except Exception as e:  # noqa: BLE001
            print(f"警告: シート {name} の読み取りに失敗: {type(e).__name__}")
            out["main"][name] = []

    layer_book_id = (os.getenv("LAYER_SPREADSHEET_ID") or "").strip() or spreadsheet_id
    layer_book = client.open_by_key(layer_book_id)
    main_titles = {sheet_tasks, sheet_attendance, sheet_members}
    for ws in layer_book.worksheets():
        if layer_book_id == spreadsheet_id and ws.title in main_titles:
            continue
        try:
            out["layer"][ws.title] = ws.get_all_values()[1:]
        except Exception as e:  # noqa: BLE001
            print(f"警告: 桁シート {ws.title} の読み取りに失敗: {type(e).__name__}")
    return out


async def main(args: argparse.Namespace) -> None:
    guild_id = args.guild_id or int((os.getenv("GUILD_ID") or "0").strip() or 0)
    if not guild_id:
        print("ERROR: --guild-id または環境変数 GUILD_ID で対象ギルドを指定してください。")
        sys.exit(1)

    db_path = args.db_path or (os.getenv("DB_PATH") or "./data/club.db").strip()
    if not os.path.exists(db_path):
        print(f"ERROR: DB が見つかりません: {db_path}")
        sys.exit(1)

    if args.apply:
        backup = f"{db_path}.bak.{__import__('datetime').datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(db_path, backup)
        print(f"バックアップを作成しました: {backup}")

    sheets = _load_sheets()
    db = Database(db_path)
    await db.connect()
    try:
        all_stats: list[tuple[str, Stats]] = []
        tasks_name = (os.getenv("SHEET_TASKS") or "tasks").strip()
        att_name = (os.getenv("SHEET_ATTENDANCE") or "attendance").strip()
        mem_name = (os.getenv("SHEET_MEMBERS") or "members").strip()

        all_stats.append((f"tasks({tasks_name})", await import_tasks(
            db, guild_id, sheets["main"].get(tasks_name, []), args.apply)))
        all_stats.append((f"members({mem_name})", await import_members(
            db, guild_id, sheets["main"].get(mem_name, []), args.apply)))
        all_stats.append((f"attendance({att_name})", await import_attendance(
            db, guild_id, sheets["main"].get(att_name, []), args.apply)))
        for keta, rows in sheets["layer"].items():
            all_stats.append((f"layer({keta})", await import_layer_rows(
                db, guild_id, keta, rows, args.apply)))

        print("\n===== 移行結果 =====")
        for name, stats in all_stats:
            print(stats.line(name))
            for err in stats.errors[:10]:
                print(f"  [エラー] {err}")
        if not args.apply:
            print("\ndry-run のため DB は変更していません。"
                  "実行するには --apply を付けてください。")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild-id", type=int, default=None,
                        help="取り込み先のギルド ID（未指定時は環境変数 GUILD_ID）")
    parser.add_argument("--db-path", default=None, help="SQLite のパス")
    parser.add_argument("--apply", action="store_true",
                        help="実際に移行を実行する（既定は dry-run）")
    asyncio.run(main(parser.parse_args()))
