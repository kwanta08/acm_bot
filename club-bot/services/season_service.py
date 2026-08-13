"""年度替わり（世代交代）の処理。

サークルには毎年必ず代替わりが来る。区切りを記録し、卒業者を仕分け、
班長を新体制のために一度リセットする。

**卒業者の行は削除しない。** 過去の作業記録に残る担当者名が引けなくなり、
去年の積層記録が「誰がやったか分からない」状態になってしまうため。
status を alumni に動かすだけにして、既定の一覧・検索からは外す。

DB 操作をここへ集約し、cogs からは呼ぶだけにしてテストしやすくする。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from repositories.member_repository import MemberRepository
from repositories.season_repository import SeasonRepository

STATUS_ACTIVE = "active"
STATUS_ALUMNI = "alumni"
STATUS_INACTIVE = "inactive"

# 一度の仕分けで選べる人数（Discord の選択メニューの上限）
MAX_SELECTABLE = 25


@dataclass
class RolloverResult:
    """年度替わりの実行結果（監査ログと報告に使う）。"""

    new_season: str
    ended_season: str | None = None
    alumni: list[str] = field(default_factory=list)
    leaders_reset: int = 0

    def summary(self) -> str:
        parts = []
        if self.ended_season:
            parts.append(f"{self.ended_season} を終了")
        parts.append(f"{self.new_season} を開始")
        parts.append(f"卒業 {len(self.alumni)} 名")
        parts.append(f"班長フラグ {self.leaders_reset} 件をリセット")
        return " / ".join(parts)


async def perform_rollover(db, guild_id: int, new_season_name: str,
                           alumni_user_ids: list[str], *,
                           at: str | None = None) -> RolloverResult:
    """年度を切り替える。

    1. 現年度に終了日を打ち、新しい年度を開始する
    2. 選ばれたメンバーを alumni にする（**行は消さない**）
    3. 班長フラグを全員リセットする（新体制で付け直す）

    選ばれなかったメンバーの status には触れない。
    """
    season_repo = SeasonRepository(db)
    member_repo = MemberRepository(db)

    ended, _ = await season_repo.start_new(guild_id, new_season_name, at=at)
    # 「卒業した年度」は在籍最後の年度。初回で前年度が無いときは
    # この切り替えを表す新年度名を入れておく（NULL だと時期が追えない）
    left_season = ended or new_season_name

    moved: list[str] = []
    for user_id in alumni_user_ids:
        if await member_repo.set_status(guild_id, user_id, STATUS_ALUMNI,
                                        left_season=left_season):
            moved.append(user_id)

    leaders_reset = await member_repo.reset_leaders(guild_id)
    return RolloverResult(new_season=new_season_name, ended_season=ended,
                          alumni=moved, leaders_reset=leaders_reset)
