"""タスク通知の「親タスク」パンくず解決。

Todoist タスク通知（cogs/progress.py の push_project_tasks）で、
通知対象タスクが**どの部品・どの工程の配下か**を1行で示すためのモジュール。
サブタスクほど名前が短く文脈依存になりがち（例:「接着」「バリ取り」）で、
通知単体では意味が取れないことへの対処。

解決の原則:
- 進捗ツリー（progress_nodes から構築済みの ProgressTree）上で親 ID を
  遡るだけで解決する。**通知1件ごとに Todoist API を叩かない**
  （例外はキャッシュミス時の get_task 1回だけ。resolve_parent_field 参照）
- 遡上の停止条件: 親 ID が空になった / 対応表（progress_todoist_links）で
  Todoist プロジェクトに紐付けられているノードに到達した（**含めて**終了）
- 循環参照ガードは既存実装（cogs/progress.breadcrumb /
  progress_tree._find_invalid_ids）と同じ「訪問済み ID の集合」方式。
  同一 ID を2回訪れたら打ち切ってログに警告する
- 解決の失敗は例外を外へ漏らさない。**親が分からなくても通知は送る**
  （フィールドを省略するだけ）
"""

from __future__ import annotations

from dataclasses import dataclass

from services.progress_sync_service import TODOIST_ID_PREFIX
from services.progress_tree import ProgressNode, ProgressTree
from services.todoist_task_service import task_url
from utils.logger import get_logger

log = get_logger("parent_chain")

#: 親チェーン遡上の深さ上限。超過時は打ち切ってログに警告する
MAX_PARENT_DEPTH = 10

#: パンくず内の1ノード名の上限文字数。超過分は末尾を省略する
MAX_NODE_NAME_LEN = 100

#: Discord の Embed フィールド値の上限。パンくず全体をこれ以下に保証する
MAX_FIELD_VALUE_LEN = 1024

#: 通知に表示するフィールド名
PARENT_FIELD_NAME = "親タスク"

SEPARATOR = " > "
ELLIPSIS = "…"


@dataclass(frozen=True)
class ParentEntry:
    """パンくずの1要素。url は Todoist 由来（td_）ノードのみ設定される。"""

    name: str
    url: str | None = None


def _entry(node: ProgressNode) -> ParentEntry:
    """ノードをパンくず要素へ変換する。手入力ノードにはリンクを張らない。"""
    url = None
    if node.node_id.startswith(TODOIST_ID_PREFIX):
        task_id = node.todoist_task_id or node.node_id[len(TODOIST_ID_PREFIX) :]
        url = task_url(task_id)
    return ParentEntry(name=node.name or node.node_id, url=url)


def parent_chain(
    tree: ProgressTree, start_id: str | None, anchor_ids: set[str]
) -> list[ProgressNode]:
    """start_id から親 ID を遡り、ルート → 直近の順でノードを返す（純粋関数）。

    start_id には「通知対象タスクの親ノード ID」を渡す（タスク自身は
    タイトル等に出ているためパンくずに含めない）。

    停止条件（先に到達した方）:
    - 親 ID が空になった（ツリーのルートまで含めて終了）
    - anchor_ids（対応表で紐付けられたノード）に到達した — 含めて終了
    - 循環参照を検出した（訪問済み集合。打ち切ってログに警告）
    - 深さが MAX_PARENT_DEPTH を超えた（打ち切ってログに警告）
    """
    chain: list[ProgressNode] = []
    seen: set[str] = set()
    current_id = start_id or None
    while current_id:
        if current_id in seen:
            log.warning("親チェーンに循環参照を検出したため打ち切ります (node=%s)", current_id)
            break
        seen.add(current_id)
        node = tree.by_id.get(current_id)
        if node is None:
            break  # 親がツリーに無い（同期前など）。ここまでで打ち切る
        if len(chain) >= MAX_PARENT_DEPTH:
            log.warning(
                "親チェーンが深さ上限 %d を超えたため打ち切ります (node=%s)",
                MAX_PARENT_DEPTH,
                current_id,
            )
            break
        chain.append(node)
        if node.node_id in anchor_ids:
            break  # 対応表ノード（機体 / パーツ）はパンくずに含めて終了
        current_id = node.parent_id or None
    chain.reverse()
    return chain


def _clip_name(name: str) -> str:
    if len(name) <= MAX_NODE_NAME_LEN:
        return name
    return name[: MAX_NODE_NAME_LEN - 1] + ELLIPSIS


def _render(entry: ParentEntry, *, with_link: bool) -> str:
    name = _clip_name(entry.name)
    if with_link and entry.url:
        return f"[{name}]({entry.url})"
    return name


def format_breadcrumb(entries: list[ParentEntry], limit: int = MAX_FIELD_VALUE_LEN) -> str:
    """パンくず文字列を組み立てる。必ず limit（既定 1024）以下に収める。

    - 区切りは " > "。リンクは末尾（直近の親）にだけ張る
    - limit を超える場合は中間ノードを ELLIPSIS へ省略する。
      **ルートと直近の親は必ず残す**（直近側の中間ノードから優先して残す）
    """
    if not entries:
        return ""
    parts = [_render(e, with_link=(i == len(entries) - 1)) for i, e in enumerate(entries)]
    text = SEPARATOR.join(parts)
    if len(text) <= limit:
        return text

    if len(parts) > 2:
        head, last = parts[0], parts[-1]
        kept: list[str] = [last]
        for part in reversed(parts[1:-1]):
            candidate = SEPARATOR.join([head, ELLIPSIS, part, *kept])
            if len(candidate) > limit:
                break
            kept.insert(0, part)
        text = SEPARATOR.join([head, ELLIPSIS, *kept])
        if len(text) <= limit:
            return text

    # ノード名上限（MAX_NODE_NAME_LEN）がある限りここへは来ないはずだが、
    # 万一に備えてリンクを捨てた素の「ルート > … > 直近の親」へ丸める
    fallback = SEPARATOR.join(
        [_clip_name(entries[0].name), ELLIPSIS, _clip_name(entries[-1].name)]
    )
    return fallback[:limit]


def _field_from_chain(chain: list[ProgressNode], anchor_ids: set[str]) -> str | None:
    if not chain:
        return None
    if len(chain) == 1 and chain[0].node_id in anchor_ids:
        # 直近の親が対応表ノード自身 = トップレベルタスク。プロジェクト名は
        # 通知に既に出ているため、フィールド自体を出さない
        return None
    return format_breadcrumb([_entry(n) for n in chain])


async def resolve_parent_field(tree: ProgressTree, raw_task, anchor_ids: set[str], get_task):
    """通知対象タスク1件の「親タスク」フィールド値を返す。表示不要なら None。

    - タスクがツリーに在る場合: 親 ID をツリー上で遡るだけ（API を叩かない）
    - キャッシュミス（前回同期後に作られたタスク等）: 親タスク ID があれば
      get_task を **1回だけ** 呼び、取れた名前を直近の親として表示する
      （そこから更に上へは遡らない）。取得失敗はログに残してフィールドを省略する
    - 想定外の例外も外へ漏らさない（親の解決失敗で通知を落とさない）
    """
    try:
        return await _resolve_parent_field(tree, raw_task, anchor_ids, get_task)
    except Exception as e:  # noqa: BLE001  (解決失敗で通知を止めない)
        log.warning(
            "親タスクの解決に失敗したためフィールドを省略します (task=%s): %s",
            getattr(raw_task, "id", "?"),
            type(e).__name__,
        )
        return None


async def _resolve_parent_field(
    tree: ProgressTree, raw_task, anchor_ids: set[str], get_task
) -> str | None:
    task_id = str(getattr(raw_task, "id", "") or "")
    node = tree.by_id.get(f"{TODOIST_ID_PREFIX}{task_id}") if task_id else None
    if node is not None:
        return _field_from_chain(parent_chain(tree, node.parent_id, anchor_ids), anchor_ids)

    # キャッシュミス。Todoist 上の親タスク ID から解決を試みる
    raw_parent = getattr(raw_task, "parent_id", None)
    if not raw_parent:
        return None  # トップレベルタスク
    parent_id = str(raw_parent)
    parent_node = tree.by_id.get(f"{TODOIST_ID_PREFIX}{parent_id}")
    if parent_node is not None:
        return _field_from_chain(
            parent_chain(tree, parent_node.node_id, anchor_ids), anchor_ids
        )

    if get_task is None:
        log.warning("get_task が使えないためフィールドを省略します (parent=%s)", parent_id)
        return None
    try:
        fetched = await get_task(parent_id)
    except Exception as e:  # noqa: BLE001  (API 失敗で通知を止めない)
        log.warning(
            "親タスクの取得に失敗したためフィールドを省略します (parent=%s): %s",
            parent_id,
            type(e).__name__,
        )
        return None
    if fetched is None:
        log.warning("親タスクが見つからないためフィールドを省略します (parent=%s)", parent_id)
        return None
    name = str(getattr(fetched, "content", "") or "") or parent_id
    return format_breadcrumb([ParentEntry(name=name, url=task_url(parent_id))])
