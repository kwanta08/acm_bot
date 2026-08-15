"""進捗バーのテキスト描画（依存なし）。

/progress のドリルダウン表示で、現在の階層の子ノード一覧を
Embed 内のテキストとして描画する。

旧実装は matplotlib で PNG を生成していたが、常駐メモリを 37.5MB
消費するうえ、日本語ラベルには CJK フォントの導入が必要だった。
本モジュールは標準ライブラリだけで動き、フォントの有無にも左右されない。
詳細なグラフはブラウザ（Web ダッシュボード）側で描画する
（docs/DESIGN_PUBLIC_DISTRIBUTION.md 4章 / Phase 3）。
"""

from __future__ import annotations

# 塗り・空白に使う文字（等幅でなくても崩れにくい全角ブロック）
FILLED = "█"
EMPTY = "░"
DEFAULT_WIDTH = 10

# 名前が長い場合の省略幅（Embed のフィールド名が折り返さない程度）
MAX_LABEL = 20


def clamp(value: float | None) -> float:
    """進捗率を 0.0〜1.0 に丸める（None は 0.0）。"""
    if value is None:
        return 0.0
    return min(max(float(value), 0.0), 1.0)


def bar(value: float | None, width: int = DEFAULT_WIDTH) -> str:
    """進捗率を `███░░░░░░░` 形式のバーにする。

    width は1以上。0% でも 100% でも必ず width 文字になる。
    """
    width = max(1, int(width))
    filled = round(clamp(value) * width)
    return FILLED * filled + EMPTY * (width - filled)


def percent(value: float | None) -> str:
    return f"{clamp(value) * 100:.0f}%"


def truncate(label: str, limit: int = MAX_LABEL) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"


def render_lines(items: list[tuple[str, float]], *, width: int = DEFAULT_WIDTH) -> list[str]:
    """(名前, 進捗率) の一覧を1行ずつのテキストへ整形する。

    名前は最長のものに合わせて左詰めし、バーの開始位置を揃える。
    """
    if not items:
        return []
    labels = [truncate(name) for name, _ in items]
    pad = max(len(label) for label in labels)
    return [
        f"{label.ljust(pad)} {bar(value, width)} {percent(value):>4}"
        for label, (_, value) in zip(labels, items, strict=False)
    ]


def render_block(
    items: list[tuple[str, float]], *, width: int = DEFAULT_WIDTH, max_rows: int = 25
) -> str:
    """Embed に貼り付けるコードブロック（等幅表示）を返す。

    行数が max_rows を超える場合は打ち切り、残件数を末尾に添える。
    空リストは空文字（呼び出し側でバーを出さない）。
    """
    lines = render_lines(items[:max_rows], width=width)
    if not lines:
        return ""
    rest = len(items) - max_rows
    if rest > 0:
        lines.append(f"… 他 {rest} 件")
    return "```\n" + "\n".join(lines) + "\n```"
