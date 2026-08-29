"""ドキュメントと実装のコマンド一覧が一致することの回帰テスト（F5-3）。

コマンドを足したのに `docs/OPERATION.md` の一覧を更新し忘れると、
導入サークルは新機能に気づけない。AGENTS.md も「実装とドキュメントが
矛盾したら両方直す」と定めているので、機械的に検出する。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord import app_commands
from discord.ext import commands

from bot import COGS

BOT_ROOT = Path(__file__).resolve().parent.parent
OPERATION_MD = BOT_ROOT / "docs" / "OPERATION.md"
GUIDE_MD = BOT_ROOT / "docs" / "GUIDE.md"

#: GUIDE.md でコマンド早見表が始まる見出し
GUIDE_APPENDIX_HEADING = "## 付録: コマンド早見表"


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


def _guide_appendix() -> str:
    """GUIDE.md の「付録: コマンド早見表」セクションだけを返す。

    **ファイル全体を対象にしてはいけない。** 3章・4章の本文にも
    コマンド名が出てくるので、全体を見ると「付録に無いのに緑」になる
    （例: `/schedule restore` は3章の幹部向け一覧にも書かれている）。
    """
    doc = GUIDE_MD.read_text(encoding="utf-8")
    start = doc.find(GUIDE_APPENDIX_HEADING)
    assert start >= 0, (
        f"GUIDE.md に見出し「{GUIDE_APPENDIX_HEADING}」が見つかりません"
        "（改名した場合は GUIDE_APPENDIX_HEADING も直してください）"
    )
    end = doc.find("\n## ", start + 1)
    return doc[start:] if end < 0 else doc[start:end]


def _commands_in(text: str) -> set[str]:
    """バッククォートで囲まれたコマンド名を全部拾う。

    1つのセルに複数のコマンドが並ぶ（`` `/ping` `/health` `` の形）が、
    それぞれが独立したバッククォート対なので個別に取れる。
    `/` で始まらない断片（`` `OPERATION.md` `` 等）は落とす。
    """
    return {
        chunk[1:] for chunk in re.findall(r"`([^`]+)`", text) if chunk.startswith("/")
    }


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


# ---------------------------------------------------------------------
# GUIDE.md の付録（コマンド早見表）
#
# **検査は双方向で行う。** 「載っているのに実装に無い」だけを見ると、
# この早見表の実際の欠陥（実装にあるのに載っていない）を素通りする。
# 対象は付録セクションのみ。3章・4章のコマンド例（コードブロック内）は
# 検査していない（現時点で齟齬が無いことは目視で確認済み）。
# ---------------------------------------------------------------------
def test_guide_appendix_exists():
    assert GUIDE_MD.is_file()
    section = _guide_appendix()
    found = _commands_in(section)
    # 実装のコマンド数と突き合わせる。件数を直書きすると、
    # 抽出が壊れて塊しか取れていない状態でも閾値を超えて緑になる
    expected = len(run(_command_names()))
    assert len(found) >= expected, (
        f"付録からコマンドを {len(found)} 件しか抽出できていない"
        f"（実装は {expected} 件。表の書式が変わって検査が空振りしている可能性）"
    )


def test_every_command_is_in_the_guide_appendix():
    """実装にあるコマンドが全部、早見表に載っていること。"""
    section = _guide_appendix()
    names = run(_command_names())
    assert names, "コマンドが1件も収集できていない（テストが空振りしている）"

    missing = [name for name in names if not _is_documented(section, name)]
    assert not missing, (
        "docs/GUIDE.md の「付録: コマンド早見表」に載っていないコマンドがあります:\n"
        + "\n".join(f"  /{name}" for name in missing)
    )


def test_the_guide_appendix_has_no_stale_commands():
    """早見表にあるのに実装から消えたコマンドを検出する。"""
    names = set(run(_command_names()))
    stale = sorted(
        body for body in _commands_in(_guide_appendix()) if _match_command(body, names) is None
    )
    assert not stale, (
        "docs/GUIDE.md の「付録: コマンド早見表」に、実装に存在しないコマンドが載っています:\n"
        + "\n".join(f"  /{name}" for name in stale)
    )


def test_the_export_table_count_matches_the_whitelist():
    """「主要7テーブル」の数字が `TABLES` と一致すること。

    G4-3 で `audit_log` 等が加わったときに、直し忘れではなく
    テスト失敗として現れるようにする。
    """
    from repositories.table_repository import TABLES

    expected = f"主要{len(TABLES)}テーブル"
    # docs だけでなく **実装側の docstring も**見る。数字が散っているので、
    # 片方だけ直して片方が静かに古いまま残る形を防ぐ
    targets = (
        GUIDE_MD,
        OPERATION_MD,
        BOT_ROOT / "docs" / "PRIVACY.md",
        BOT_ROOT / "README.md",
        BOT_ROOT / "cogs" / "data.py",
        BOT_ROOT / "cogs" / "season.py",
    )
    for path in targets:
        text = path.read_text(encoding="utf-8")
        # **guard を置かない。** 「主要N」が無いときに continue すると、
        # 「全データ」へ差し戻す変更（G3-7 が直した誇張表記そのもの）を
        # 素通りさせる
        stale = re.findall(r"主要\d+テーブル", text)
        assert stale, f"{path.name} に「主要Nテーブル」の記述が見つからない"
        assert set(stale) == {expected}, (
            f"{path.name} のテーブル数が実装と食い違っています（実装: {len(TABLES)}）: {set(stale)}"
        )
