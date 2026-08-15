"""ドキュメントと実装のコマンド一覧が一致することの回帰テスト（F5-3）。

コマンドを足したのに `docs/OPERATION.md` の一覧を更新し忘れると、
導入サークルは新機能に気づけない。AGENTS.md も「実装とドキュメントが
矛盾したら両方直す」と定めているので、機械的に検出する。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord import app_commands
from discord.ext import commands

from bot import COGS

BOT_ROOT = Path(__file__).resolve().parent.parent
OPERATION_MD = BOT_ROOT / "docs" / "OPERATION.md"


def run(coro):
    return asyncio.run(coro)


class _FakeBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!club ", intents=discord.Intents.none())
        self.db = None
        self.todoist_manager = None


async def _command_names() -> list[str]:
    bot = _FakeBot()
    for cog in COGS:
        await bot.load_extension(cog)
    try:
        return sorted(
            c.qualified_name
            for c in bot.tree.walk_commands()
            if isinstance(c, app_commands.Command)
        )
    finally:
        for name in list(bot.extensions):
            await bot.unload_extension(name)


def _is_documented(doc: str, name: str) -> bool:
    """`/name` または `/name <引数>` の形で載っているか。

    `/schedule list` が `/schedule list-closed` に誤ってマッチしないよう、
    直後がバッククォートかスペースであることを要求する。
    """
    return f"`/{name}`" in doc or f"`/{name} " in doc


def test_operation_md_exists():
    assert OPERATION_MD.is_file()


def test_every_command_is_documented():
    """登録済みの全コマンドが docs/OPERATION.md に載っていること。"""
    doc = OPERATION_MD.read_text(encoding="utf-8")
    names = run(_command_names())
    assert names, "コマンドが1件も収集できていない（テストが空振りしている）"

    missing = [name for name in names if not _is_documented(doc, name)]
    assert not missing, "docs/OPERATION.md に載っていないコマンドがあります:\n" + "\n".join(
        f"  /{name}" for name in missing
    )


def _match_command(body: str, names: set[str]) -> str | None:
    """ドキュメントの表記から、実在するコマンド名を取り出す。

    表記には引数が続く（`/member profile [user]` / `/team-add slug:`）ため、
    実装側の名前で前方一致させ、最も長く一致したものを採る
    （`/schedule list` より `/schedule list-closed` を優先する）。
    """
    hits = [n for n in names if body == n or body.startswith(n + " ")]
    return max(hits, key=len) if hits else None


def test_documented_commands_still_exist():
    """ドキュメントにあるのに実装から消えたコマンドを検出する。"""
    doc = OPERATION_MD.read_text(encoding="utf-8")
    names = set(run(_command_names()))

    stale: list[str] = []
    for line in doc.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `/"):
            continue
        body = stripped.split("`")[1].lstrip("/").strip()
        if not body:
            continue
        if _match_command(body, names) is None:
            stale.append(body)
    assert not stale, "docs/OPERATION.md に、実装に存在しないコマンドが載っています:\n" + "\n".join(
        f"  /{name}" for name in sorted(set(stale))
    )
