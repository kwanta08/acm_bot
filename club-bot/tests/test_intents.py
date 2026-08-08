"""Gateway Intents の回帰テスト（公開 Bot 化 P0-1）。

`message_content` は特権インテントであり、本 Bot はスラッシュコマンドのみで
動作するため要求しない。将来の実装で誤って再び有効化されたり、
`on_message` ハンドラ・`message.content` 参照・prefix コマンドが持ち込まれた
場合にここで落ちる。
"""
from __future__ import annotations

import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot import build_intents

BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 走査対象（テスト自身とサードパーティは除く）
SCAN_DIRS = ["cogs", "services", "repositories", "utils", "scripts"]
SCAN_FILES = ["bot.py", "config.py"]
EXCLUDED_DIR_NAMES = {"venv", "__pycache__", ".git", "tests", "node_modules"}

# メッセージ本文へのアクセス／prefix コマンドの兆候
FORBIDDEN_PATTERNS = {
    "on_message ハンドラ": re.compile(r"(?:async\s+)?def\s+on_message\s*\("),
    "message.content 参照": re.compile(r"\b(?:message|msg|m)\.content\b"),
    "prefix コマンド定義": re.compile(r"@(?:commands|bot|self\.bot)\.(?:command|group)\s*\("),
    "message_content インテント": re.compile(r"message_content\s*="),
}


def _iter_source_files():
    for name in SCAN_FILES:
        path = os.path.join(BOT_ROOT, name)
        if os.path.isfile(path):
            yield path
    for rel in SCAN_DIRS:
        root_dir = os.path.join(BOT_ROOT, rel)
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
            for filename in filenames:
                if filename.endswith(".py"):
                    yield os.path.join(dirpath, filename)


def _code_only(source: str) -> str:
    """コメントと文字列リテラルを空白に潰す（行番号は保持する）。

    docstring やコメントでの言及を違反として拾わないようにするため、
    実行されるコードだけを走査対象にする。
    """
    lines = [list(line) for line in source.splitlines(keepends=True)]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source  # 解析できない場合は元のまま走査する（安全側）
    for tok in tokens:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, min(erow, len(lines)) + 1):
            line = lines[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            for i in range(start, min(end, len(line))):
                if line[i] != "\n":
                    line[i] = " "
    return "".join("".join(line) for line in lines)


def test_message_content_intent_is_not_requested():
    assert build_intents().message_content is False


def test_only_members_privileged_intent_is_requested():
    """特権インテントは members のみ（presences / message_content は要求しない）。"""
    intents = build_intents()
    assert intents.members is True
    assert intents.presences is False
    assert intents.message_content is False


def test_required_non_privileged_intents_remain_enabled():
    """スラッシュコマンドとリアクション投票に必要なインテントは維持する。"""
    intents = build_intents()
    assert intents.guilds is True
    assert intents.reactions is True


def test_no_message_content_dependent_code_in_sources():
    """message.content / on_message / prefix コマンドが存在しないこと。"""
    violations: list[str] = []
    for path in _iter_source_files():
        with open(path, encoding="utf-8") as f:
            source = _code_only(f.read())
        for label, pattern in FORBIDDEN_PATTERNS.items():
            for match in pattern.finditer(source):
                line_no = source.count("\n", 0, match.start()) + 1
                rel = os.path.relpath(path, BOT_ROOT)
                violations.append(f"{rel}:{line_no}: {label} ({match.group(0)!r})")
    assert not violations, (
        "message_content 特権インテントを必要とするコードが混入しています:\n"
        + "\n".join(violations)
    )


def test_scan_actually_covers_sources():
    """走査対象が空でないこと（テストが空振りしていないことの確認）。"""
    files = list(_iter_source_files())
    assert len(files) > 10
    assert any(os.path.basename(p) == "bot.py" for p in files)
