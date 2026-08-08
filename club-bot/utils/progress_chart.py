"""進捗横棒グラフの PNG 生成（matplotlib）。

/progress のドリルダウン表示で、現在の階層の子ノード一覧を
横棒グラフとして描画する。同期関数のため呼び出し側は
asyncio.to_thread() で実行すること。

日本語ラベル対応: 環境にインストール済みの CJK フォントを自動検出する
（VPS では `apt install fonts-noto-cjk` を推奨。見つからない場合も
描画自体は行う。ラベルが豆腐になるだけで例外にはしない）。
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # ヘッドレス環境用（import 直後・pyplot より先に設定）

from matplotlib import font_manager
from matplotlib import pyplot as plt

# 優先順の日本語フォント候補（Linux / Windows / macOS）
_JP_FONT_CANDIDATES = [
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic",
    "Yu Gothic", "Meiryo", "MS Gothic", "Hiragino Sans",
]

_BAR_COLOR = "#4a86e8"      # シートの SPARKLINE と揃える
_BG_COLOR = "#2b2d31"       # Discord ダークテーマに馴染む配色
_TEXT_COLOR = "#e0e0e0"

_font_configured = False


def _configure_font() -> None:
    """利用可能な日本語フォントを matplotlib に設定する（初回のみ）。"""
    global _font_configured
    if _font_configured:
        return
    _font_configured = True
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in _JP_FONT_CANDIDATES:
        if name in installed:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def render_progress_bars(items: list[tuple[str, float]]) -> bytes:
    """(名前, 進捗率 0.0〜1.0) の一覧を横棒グラフ PNG にして返す。

    上から items の順に表示する。空リストは ValueError。
    """
    if not items:
        raise ValueError("items が空です")
    _configure_font()

    labels = [name if len(name) <= 20 else name[:19] + "…"
              for name, _ in items]
    values = [max(0.0, min(1.0, v)) for _, v in items]

    height = max(1.6, 0.55 * len(items) + 0.8)
    fig, ax = plt.subplots(figsize=(7, height), dpi=144)
    fig.patch.set_facecolor(_BG_COLOR)
    ax.set_facecolor(_BG_COLOR)

    y = range(len(items))
    ax.barh(y, values, color=_BAR_COLOR, height=0.6, zorder=3)
    ax.barh(y, [1.0] * len(items), color="#44474e", height=0.6, zorder=2)

    ax.set_yticks(list(y), labels=labels, color=_TEXT_COLOR, fontsize=11)
    ax.invert_yaxis()  # リスト順を上から表示
    ax.set_xlim(0, 1.0)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for i, v in enumerate(values):
        ax.text(1.01, i, f"{v * 100:.0f}%", va="center",
                color=_TEXT_COLOR, fontsize=11)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG_COLOR,
                bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
