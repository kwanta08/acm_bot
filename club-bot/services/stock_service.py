"""資材・消耗品の在庫判定（G4-8）。

`/stock` が使う純関数だけを置く。DB も Discord も触らない。

**閾値が未設定の品目を「閾値割れではない」とも「閾値割れ」とも言わない。**
`threshold` が NULL のときは判定そのものを行わない（ADR 0021:
分からないものを数字にしない）。0 を既定値にすると
「在庫0でも閾値割れではない」という嘘になる。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def is_low(quantity: float | None, threshold: float | None) -> bool:
    """閾値を割っているか。閾値未設定・数量不明なら False（判定しない）。

    「以下」で判定する。閾値ちょうどは**割れている**扱い——
    「残り1本になったら発注」と設定した人は、1本になった時点で
    知らせてほしいのであって、0本になってからでは納期に間に合わない。
    """
    if threshold is None or quantity is None:
        return False
    return float(quantity) <= float(threshold)


def crossed_below(before: float | None, after: float | None, threshold: float | None) -> bool:
    """今回の増減で「割っていない → 割った」に変わったか。

    即時通知を1回だけ飛ばすための判定。既に割っている状態での
    さらなる消費では True にしない（毎回 DM が飛ぶのを防ぐ）。
    """
    return is_low(after, threshold) and not is_low(before, threshold)


def format_amount(value: float | None, unit: str = "") -> str:
    """数量の表示。整数なら小数点を出さない。未設定は `—`。"""
    if value is None:
        return "—"
    number = float(value)
    text = str(int(number)) if number == int(number) else f"{number:g}"
    return f"{text}{unit}" if unit else text


def low_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """閾値割れの品目だけを、余裕の少ない順に返す（純関数）。

    余裕 = 数量 − 閾値。同じなら品目名順。閾値未設定の品目は含まない。
    """
    out = [
        dict(item)
        for item in items
        if is_low(item.get("quantity"), item.get("threshold"))
    ]
    out.sort(
        key=lambda i: (
            float(i["quantity"]) - float(i["threshold"]),
            str(i.get("item_name") or ""),
        )
    )
    return out
