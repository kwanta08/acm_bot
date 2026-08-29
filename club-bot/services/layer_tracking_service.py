"""
桁巻き積層作業の開始/終了ロジック（仕様 11.8）。

進行中セッションと完了記録は SQLite（正本）に永続化する。
Google Sheets 連携は廃止され、記録の参照は DB（NocoDB）から行う。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from repositories.layer_session_repository import LayerSessionRepository
from utils.parser import from_iso, now, to_iso


@dataclass
class StaleSession:
    """閾値を超えた進行中セッション（G4-2 の押し忘れ検知）。"""

    session_id: int
    user_id: str
    keta: str
    layer_num: str
    elapsed_min: int


def classify_stale_sessions(
    sessions: Iterable[Mapping[str, Any]],
    current: datetime,
    alert_minutes: int,
    auto_cancel_minutes: int,
) -> tuple[list[StaleSession], list[StaleSession]]:
    """進行中セッションを「催促する」「自動で取り消す」に振り分ける（純関数）。

    戻り値は (催促, 自動取り消し)。**自動取り消しの対象は催促に入れない**
    （同じ tick で2通届いてしまうため）。

    閾値が 0 以下ならその分類は無効（空になる）。既定値は
    config.DEFAULT_LAYER_SESSION_* にあり、ギルド別設定で上書きできる。

    `started_at` が解釈できない行は**落とさずに飛ばす**。1行の壊れたデータで
    そのギルドの点検全体が例外になると、押し忘れが永久に検知されなくなる。
    """
    to_alert: list[StaleSession] = []
    to_cancel: list[StaleSession] = []
    for row in sessions:
        try:
            started = from_iso(str(row["started_at"]))
        except (TypeError, ValueError):
            continue
        elapsed = int((current - started).total_seconds() // 60)
        stale = StaleSession(
            session_id=int(row["session_id"]),
            user_id=str(row["user_id"]),
            keta=str(row["keta"]),
            layer_num=str(row["layer_num"]),
            elapsed_min=elapsed,
        )
        if auto_cancel_minutes > 0 and elapsed >= auto_cancel_minutes:
            to_cancel.append(stale)
        elif alert_minutes > 0 and elapsed >= alert_minutes:
            to_alert.append(stale)
    return to_alert, to_cancel


class LayerTrackingService:
    def __init__(self, session_repo: LayerSessionRepository):
        self.session_repo = session_repo

    async def has_active(self, user_id: str) -> bool:
        return await self.session_repo.get_by_user(user_id) is not None

    async def start(self, user_id: str, keta: str, layer_num: str) -> datetime:
        """開始を記録。開始時刻を返す。"""
        started = now()
        await self.session_repo.start(user_id, keta, layer_num, to_iso(started))
        return started

    async def end(self, user_id: str, display_name: str) -> dict:
        """
        進行中セッションを終了し、DBへ記録を保存する。
        戻り値: {keta, layer_num, minutes, started, ended}
        呼び出し側で事前にセッション存在を確認すること。
        """
        session = await self.session_repo.get_by_user(user_id)
        if session is None:
            raise ValueError("進行中セッションがありません。")

        started = from_iso(session["started_at"])
        ended = now()
        minutes = int((ended - started).total_seconds() // 60)  # 端数切り捨て（仕様 11.8.4）

        # 記録をDBへ保存し、進行中セッションを削除（仕様 11.8.5）
        record_id = await self.session_repo.add_record(
            user_id,
            session["keta"],
            session["layer_num"],
            session["started_at"],
            to_iso(ended),
            minutes,
        )
        await self.session_repo.end(user_id)
        # 外部同期先（Sheets）は廃止されたため、保存時点で同期済み扱いにする
        await self.session_repo.mark_synced(record_id)

        return {
            "keta": session["keta"],
            "layer_num": session["layer_num"],
            "minutes": minutes,
            "started": started,
            "ended": ended,
        }

    async def cancel(self, user_id: str) -> dict | None:
        """進行中セッションを**記録を残さずに**破棄する（G4-2）。

        `end` との違いはここだけ。`end` で終わらせると押し忘れの 1200 分が
        `layer_records` に入り、完了層数が増えて `/progress` の進捗率まで
        水増しされる。取り消したセッションの内容を返す（無ければ None）。
        """
        session = await self.session_repo.get_by_user(user_id)
        if session is None:
            return None
        await self.session_repo.end(user_id)
        return {
            "keta": session["keta"],
            "layer_num": session["layer_num"],
            "started_at": session["started_at"],
        }

    async def list_active(self) -> list[dict]:
        sessions = await self.session_repo.list_all()
        current = now()
        out = []
        for s in sessions:
            started = from_iso(s["started_at"])
            elapsed = int((current - started).total_seconds() // 60)
            out.append(
                {
                    "user_id": s["user_id"],
                    "keta": s["keta"],
                    "layer_num": s["layer_num"],
                    "started": started,
                    "elapsed_min": elapsed,
                }
            )
        return out
