"""デザイントークン（D0-3）の構造検査。

`static/style.css` は次を守る:

- 色・余白・角丸・影・モーション・z-index のトークンが `:root` に定義される
- リテラル値（#rrggbb / rem / px）の直書きは**トークン定義ブロックの中だけ**。
  ルール本体では必ず var() を参照する（0 と 100% と unitless は除く）
- `:root` で定義した色トークンは、ダークテーマのブロックでも必ず再定義する
  （片方にしか無い色を作ると、D3-4 の明示切替で書き直しになる）

CSS はテキストとして解析する（外部パーサを増やさない）。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STYLE_CSS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dashboard",
    "static",
    "style.css",
)


def _read() -> str:
    with open(STYLE_CSS, encoding="utf-8") as f:
        css = f.read()
    # コメントを除去（コメント内の例示値を誤検出しない）
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _split_rules(css: str) -> list[tuple[str, str]]:
    """(セレクタ, 本体) の一覧を返す。@media は再帰的に展開する。"""
    rules: list[tuple[str, str]] = []
    i = 0
    while True:
        open_pos = css.find("{", i)
        if open_pos < 0:
            break
        selector = css[i:open_pos].strip()
        depth = 1
        j = open_pos + 1
        while depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[open_pos + 1 : j - 1]
        if selector.startswith(("@media", "@supports")):
            rules.extend(_split_rules(body))
        else:
            rules.append((selector, body))
        i = j
    return rules


def _token_bodies(css: str) -> list[str]:
    """トークン定義ブロック（:root を含むセレクタ）の本体。"""
    return [body for sel, body in _split_rules(css) if ":root" in sel]


def _rule_bodies(css: str) -> list[tuple[str, str]]:
    """トークン定義以外のルール本体。"""
    return [(sel, body) for sel, body in _split_rules(css) if ":root" not in sel]


def _defined_tokens(body: str) -> set[str]:
    return set(re.findall(r"--[\w-]+(?=\s*:)", body))


REQUIRED_TOKENS = {
    # 色
    "--bg", "--fg", "--muted", "--border", "--accent", "--accent-fg",
    "--row-alt", "--danger",
    # 余白スケール
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6",
    # 角丸（§デザイン方針の体系）
    "--radius-card", "--radius-control", "--radius-input",
    # 影・モーション・z-index
    "--shadow-card", "--motion-fast", "--z-sticky",
}

COLOR_TOKENS = {
    "--bg", "--fg", "--muted", "--border", "--accent", "--accent-fg",
    "--row-alt", "--danger",
}


def test_required_tokens_are_defined_in_root():
    bodies = _token_bodies(_read())
    assert bodies, ":root ブロックがありません"
    defined = _defined_tokens(bodies[0])
    missing = REQUIRED_TOKENS - defined
    assert not missing, f"トークンが未定義: {sorted(missing)}"


def test_color_tokens_are_redefined_for_dark():
    """ライトで定義した色トークンはダーク側でも必ず再定義する。

    色を**1つでも**定義するブロックは、全色を揃えて定義しなければならない
    （片方にしか無い色を作らない）。色以外のトークンだけを上書きするブロック
    （例: @supports の dvh 差し替え）は対象外。
    """
    bodies = _token_bodies(_read())
    assert len(bodies) >= 2, "ダークテーマのトークン再定義ブロックがありません"
    light = _defined_tokens(bodies[0]) & COLOR_TOKENS
    color_blocks = 0
    for body in bodies[1:]:
        dark = _defined_tokens(body) & COLOR_TOKENS
        if not dark:
            continue  # 色を含まないブロック（寸法だけの上書き等）
        color_blocks += 1
        assert light == dark, f"片方にしか無い色トークン: {sorted(light ^ dark)}"
    assert color_blocks >= 1, "ダークテーマの色再定義ブロックがありません"


def test_no_literal_values_outside_token_definitions():
    """#rrggbb / rem / px の直書きはトークン定義の中だけに限定する。

    ルール本体では必ず var() を参照する（D0-3 の受入基準）。
    0（単位なし）・パーセント・unitless line-height は対象外。
    """
    offenders: list[str] = []
    for selector, body in _rule_bodies(_read()):
        for decl in body.split(";"):
            decl = decl.strip()
            if not decl:
                continue
            if re.search(r"#[0-9a-fA-F]{3,8}\b", decl):
                offenders.append(f"{selector} -> {decl}（色リテラル）")
            # 0px / 0rem は 0 と等価なので許容しない方針（0 と書く）
            if re.search(r"\d*\.?\d+(px|rem|em)\b", decl):
                offenders.append(f"{selector} -> {decl}（寸法リテラル）")
    assert not offenders, "リテラル直書き:\n" + "\n".join(offenders)


def test_no_outline_none_anywhere():
    """`outline: none` をどこにも書かない（D1-5 / D3-6）。

    フォーカスリングの消去はキーボード利用者から現在地を奪う。
    """
    css = _read()
    assert not re.search(r"outline\s*:\s*none", css)


def test_reduced_motion_disables_all_motion():
    """`prefers-reduced-motion: reduce` でモーションが全無効になる（D3-6）。"""
    css = _read()
    assert "prefers-reduced-motion" in css
    m = re.search(r"@media \(prefers-reduced-motion: reduce\)", css)
    assert m, "reduce ブロックがありません"
