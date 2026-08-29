"""大会からの逆算（マイルストーンの遅延判定）。

進捗率は見えても「間に合うのか」が見えないので、期限までの残り日数と
これまでの進み方を突き合わせて遅延を先に知らせる。

**嘘の予測を出さないこと**を最優先にしている。ペースの出どころは3つあり、
確かなものから順に使う（G4-7 / ADR 0022 の更新）:

1. progress_snapshots の日次履歴（実測。停滞期間も含む）
2. layer_records の作業日（桁巻きに紐付いたノードだけ）
3. progress_nodes の created_at / updated_at（「作られてから最後に動くまでの
   平均」。停滞期間を含まない近似）

**1 は履歴が溜まるまで使えない。** そのときは 2 → 3 の順にフォールバックし、
それすら求まらないノードは判定を諦めて「判定不能」と明示する。
履歴が無い期間について予測を出さないのが、この設計の核。

DB にも Discord にも依存しない純粋関数として置き、cogs から呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from services.progress_tree import ProgressNode, ProgressTree

# 判定結果
VERDICT_DONE = "done"  # 完了済み
VERDICT_ON_TRACK = "on_track"  # 現在のペースで間に合う
VERDICT_BEHIND = "behind"  # 遅延
VERDICT_OVERDUE = "overdue"  # 期限を過ぎている
VERDICT_UNKNOWN = "unknown"  # 判定不能（履歴が足りない）

# ペースの出どころ
SOURCE_NODE = "node"  # progress_nodes の created_at / updated_at
SOURCE_LAYER_RECORDS = "layer_records"  # 桁巻きの作業記録
SOURCE_SNAPSHOTS = "snapshots"  # progress_snapshots の日次履歴（G4-7）

# スナップショットからペースを出すのに必要な最小の記録数と期間（日）。
#
# **少なすぎる履歴で予測を出さない**（ADR 0022 の核）。2点しかない、
# あるいは同じ日の中だけ、という状態は「たまたま動いた1回」と
# 区別できない。溜まるまでは従来の推定へフォールバックする。
MIN_SNAPSHOTS_FOR_PACE = 3
MIN_SNAPSHOT_SPAN_DAYS = 3

# 進捗率がこれ以上なら完了とみなす（浮動小数の丸め対策）
_DONE_THRESHOLD = 0.9999


def parse_date(text: str | None) -> date | None:
    """'YYYY-MM-DD' または ISO 日時文字列から日付を取り出す。

    解釈できなければ None（呼び出し側は判定不能として扱う）。
    """
    if not text:
        return None
    head = str(text).strip()
    if not head:
        return None
    try:
        return datetime.fromisoformat(head).date()
    except ValueError:
        pass
    try:
        # 'YYYY-MM-DD HH:MM' のような書式は先頭10文字だけ見る。
        # 日付だけ取り出すのでタイムゾーンは不要
        return datetime.strptime(head[:10], "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


@dataclass(frozen=True)
class Pace:
    """実績ペース（進捗率／日）。per_day が None なら判定不能。"""

    per_day: float | None = None
    source: str = ""
    reason: str = ""


def node_pace(node: ProgressNode, *, today: date) -> Pace:
    """progress_nodes の作成日〜最終更新日から平均ペースを出す。

    「作られてから最後に動くまでに、どれだけ進んだか」であり、
    停滞している期間は含まれない。1日分の幅も無ければ判定不能。
    """
    progress = node.aggregated
    if progress is None:
        return Pace(reason="進捗が集計されていない")
    start = parse_date(node.created_at)
    last = parse_date(node.updated_at)
    if start is None or last is None:
        return Pace(reason="作成日・更新日が記録されていない")
    days = (last - start).days
    if days <= 0:
        return Pace(reason="更新履歴が1日分もない")
    if progress <= 0:
        return Pace(per_day=0.0, source=SOURCE_NODE)
    return Pace(per_day=progress / days, source=SOURCE_NODE)


def spar_pace(record_dates: list[date], target_layers: int) -> Pace:
    """桁巻きノードのペースを layer_records の作業日から出す。

    record_dates は各積層記録の終了日。作業が2日以上にまたがっていないと
    ペースを出せない（1日だけの記録から速度は決められない）。
    """
    if target_layers <= 0:
        return Pace(reason="目標層数が設定されていない")
    if len(record_dates) < 2:
        return Pace(reason="積層の作業記録が足りない")
    ordered = sorted(record_dates)
    days = (ordered[-1] - ordered[0]).days
    if days <= 0:
        return Pace(reason="積層の作業記録が1日分しかない")
    layers_per_day = len(ordered) / days
    return Pace(per_day=layers_per_day / target_layers, source=SOURCE_LAYER_RECORDS)


def snapshot_pace(snapshots: list[dict]) -> Pace:
    """日次スナップショットの履歴から実績ペースを出す（G4-7）。

    `node_pace` の「作成日→最終更新日の平均」と違い、**実際に測った期間の
    伸び**なので停滞期間も含む。

    履歴が足りないときは `per_day=None` を返し、呼び出し側が従来の推定へ
    フォールバックできるようにする。**これが ADR 0022 の核**——
    履歴が無い期間について予測を出さない。

    snapshots は `snapshot_date`（'YYYY-MM-DD'）と `aggregated` を持つ辞書の列。
    `aggregated` が None の行（未集計）は使わない（0.0 に丸めない。ADR 0021）。
    """
    points: list[tuple[date, float]] = []
    for row in snapshots:
        value = row.get("aggregated")
        if value is None:
            continue
        day = parse_date(row.get("snapshot_date"))
        if day is None:
            continue
        points.append((day, float(value)))
    if len(points) < MIN_SNAPSHOTS_FOR_PACE:
        return Pace(reason="進捗の履歴がまだ足りない")
    points.sort()
    span = (points[-1][0] - points[0][0]).days
    if span < MIN_SNAPSHOT_SPAN_DAYS:
        return Pace(reason="進捗の履歴がまだ足りない")
    gained = points[-1][1] - points[0][1]
    if gained <= 0:
        # 期間中まったく進んでいない。**0 は「分からない」ではなく実測値**
        # なので、判定不能にはしない（このまま行けば間に合わない、が言える）
        return Pace(per_day=0.0, source=SOURCE_SNAPSHOTS)
    return Pace(per_day=gained / span, source=SOURCE_SNAPSHOTS)


def recent_gain(snapshots: list[dict], days: int, today: date) -> float | None:
    """直近 `days` 日での進捗の伸び。比較できる履歴が無ければ None。

    「先週から何%進んだか」を出すためのもの。**0.0 を返さない**——
    履歴が1点しか無い状態と「まったく進んでいない」は別物（ADR 0021）。
    """
    points: list[tuple[date, float]] = []
    for row in snapshots:
        value = row.get("aggregated")
        if value is None:
            continue
        day = parse_date(row.get("snapshot_date"))
        if day is None:
            continue
        points.append((day, float(value)))
    if len(points) < 2:
        return None
    points.sort()
    since = today - timedelta(days=days)
    older = [p for p in points if p[0] <= since]
    base = older[-1] if older else points[0]
    if base[0] == points[-1][0]:
        return None
    return points[-1][1] - base[1]


#: スパークラインに使うブロック文字（低い順）。外部依存を増やさない
SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float | None], blocks: str = SPARK_BLOCKS) -> str:
    """0.0〜1.0 の並びをテキストのスパークラインにする。

    None（未集計）は空白で表す。**0% と「測っていない」を同じ字にしない**。
    値の範囲は 0〜1 に固定する（系列ごとに正規化すると、
    5%→6% の変化が満杯のグラフに見える）。
    """
    out = []
    last = len(blocks) - 1
    for value in values:
        if value is None:
            out.append(" ")
            continue
        clamped = min(max(float(value), 0.0), 1.0)
        out.append(blocks[min(int(clamped * len(blocks)), last)])
    return "".join(out)


@dataclass(frozen=True)
class MilestoneStatus:
    """1つのマイルストーンの判定結果。"""

    node_id: str
    node_name: str
    name: str
    due_date: date
    progress: float
    days_left: int
    verdict: str
    required_per_day: float | None = None
    actual_per_day: float | None = None
    pace_source: str = ""
    reason: str = ""

    @property
    def is_behind(self) -> bool:
        return self.verdict in (VERDICT_BEHIND, VERDICT_OVERDUE)

    @property
    def remaining(self) -> float:
        return max(0.0, 1.0 - self.progress)


def evaluate_milestone(
    node: ProgressNode, name: str, due: date, *, today: date, pace: Pace
) -> MilestoneStatus:
    """1つのマイルストーンを判定する。

    - 進捗が 100% なら期限に関係なく done
    - 期限を過ぎていれば overdue
    - ペースが求まらなければ unknown（嘘の予測を出さない）
    - 残り日数 × 実績ペース < 残り進捗 なら behind
    """
    progress = node.aggregated or 0.0
    days_left = (due - today).days
    common = {
        "node_id": node.node_id,
        "node_name": node.name or node.node_id,
        "name": name,
        "due_date": due,
        "progress": progress,
        "days_left": days_left,
        "actual_per_day": pace.per_day,
        "pace_source": pace.source,
    }

    if progress >= _DONE_THRESHOLD:
        return MilestoneStatus(verdict=VERDICT_DONE, **common)
    if days_left < 0:
        return MilestoneStatus(verdict=VERDICT_OVERDUE, **common)

    remaining = 1.0 - progress
    if days_left == 0:
        # 当日で未完。ペースの有無に関わらず間に合わない
        return MilestoneStatus(verdict=VERDICT_BEHIND, required_per_day=remaining, **common)

    required = remaining / days_left
    if pace.per_day is None:
        return MilestoneStatus(
            verdict=VERDICT_UNKNOWN, required_per_day=required, reason=pace.reason, **common
        )

    verdict = VERDICT_BEHIND if days_left * pace.per_day < remaining else VERDICT_ON_TRACK
    return MilestoneStatus(verdict=verdict, required_per_day=required, **common)


def evaluate_all(
    tree: ProgressTree,
    milestones: list[dict],
    *,
    today: date,
    pace_by_node: dict[str, Pace] | None = None,
) -> list[MilestoneStatus]:
    """マイルストーン一覧を期限順に判定する。

    ツリーに存在しない node_id を指すマイルストーンは除外する
    （ノードが消されても行は残るため。外部キーを張っていない既存方針）。
    pace_by_node に桁巻き由来のペースを渡すと、そのノードだけ優先して使う。
    """
    overrides = pace_by_node or {}
    out: list[MilestoneStatus] = []
    for row in milestones:
        node = tree.by_id.get(str(row.get("node_id") or ""))
        if node is None:
            continue
        due = parse_date(row.get("due_date"))
        if due is None:
            continue
        pace = overrides.get(node.node_id) or node_pace(node, today=today)
        out.append(
            evaluate_milestone(node, str(row.get("name") or ""), due, today=today, pace=pace)
        )
    out.sort(key=lambda s: (s.due_date, s.name))
    return out


def days_until_competition(competition_date: str | None, today: date) -> int | None:
    """大会まで残り何日か。未設定・解釈不能なら None。"""
    due = parse_date(competition_date)
    if due is None:
        return None
    return (due - today).days
