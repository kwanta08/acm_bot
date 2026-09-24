"""設定キーの仕様表（H1-3）: 純粋関数と構造テスト。

`/settings_set` は以前、**任意のキーに任意の値**をそのまま保存していた。
`2026/07/25` と入れた大会日も `COMPETITON_DATE`（綴り違い）も成功表示になり、
読み出し側が黙って捨てるので、設定ミスに気づけなかった。

このファイルは次を固定する。

1. 値の検証（キー × 値の表）と、検証を通った値が読み出し側で**必ず読める**こと（往復）
2. 未知・内部・廃止キーの拒否の文面と、綴りの近い候補
3. **仕様表から漏れたキーを検出する構造テスト**（ADR 0008: 規律ではなく構造で守る）
   - 実行時: `for_guild` 等が実際に問い合わせたキーを記録して、仕様表と突き合わせる
   - 静的: コード中のキー形の文字列定数をすべて集め、どこかへ分類されていることを要求する
4. 仕様表そのものの形（種別ごとに要る項目の書き忘れ・他の定義との二重化）
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

sys.modules.setdefault("dotenv", mock.MagicMock())  # config が読む

from test_intents import _iter_source_files

from config import MULTI_ROLE_KEYS, SCHEDULE_UI_STYLES, Config
from repositories.settings_repository import SettingsRepository
from utils.db import Database
from utils.settings_spec import (
    COMMAND_ONLY_KEYS,
    INTERNAL_KEYS,
    SETTING_SPECS,
    SettingKeyError,
    SettingKind,
    SettingSpec,
    SettingValueError,
    env_fallback,
    is_deprecated_key,
    lookup,
    normalize_key,
    normalize_setting,
    normalize_value,
    setting_key_choices,
    suggest_keys,
)

BOT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
G1 = 111

#: 大会日の検証の表。/setup の Modal のテスト（test_setup_wizard.py）も**同じ表**を使う。
#: 期待値が None のものは拒否。
COMPETITION_DATE_CASES: list[tuple[str, str | None]] = [
    ("2026-07-25", "2026-07-25"),
    (" 2026-07-25 ", "2026-07-25"),
    ("2026/07/25", None),
    ("7月25日", None),
    ("", None),
    ("   ", None),
    ("2026-7-25", None),
    ("2026-02-30", None),
    ("２０２６-０７-２５", None),  # 全角数字（読み出し側で読めない）
    ("2026-07-25xyz", None),
    ("20260725", None),
]

#: キー × 値の表（受入基準の最低限の表 + 種別ごとの境界）。期待値は**保存形**。
VALUE_CASES: list[tuple[str, str, str | None]] = [
    # チャンネル
    ("DEFAULT_TASK_CHANNEL_ID", "123456789012345678", "123456789012345678"),
    ("DEFAULT_TASK_CHANNEL_ID", "<#123>", "123"),
    ("DEFAULT_TASK_CHANNEL_ID", " 00123 ", "123"),
    ("DEFAULT_TASK_CHANNEL_ID", "#general", None),
    ("DEFAULT_TASK_CHANNEL_ID", "abc", None),
    ("DEFAULT_TASK_CHANNEL_ID", "", None),
    ("DEFAULT_TASK_CHANNEL_ID", "²", None),  # isdigit() は真だが int() できない
    ("DEFAULT_TASK_CHANNEL_ID", "１２３", None),  # 全角数字
    ("DEFAULT_TASK_CHANNEL_ID", "0", None),
    ("DEFAULT_TASK_CHANNEL_ID", str(2**63), None),
    ("BOT_LOG_CHANNEL_ID", "42", "42"),
    ("PROGRESS_DEFAULT_CHANNEL_ID", "555", "555"),
    ("WELCOME_CHANNEL_ID", "12.5", None),
    # ロール
    ("EXEC_ROLE_ID", "<@&42>", "42"),
    ("ADMIN_ROLE_ID", "role", None),
    ("LEADER_ROLE_IDS", "1, 2,2,<@&3>", "1,2,3"),
    ("LEADER_ROLE_IDS", "1,abc", None),
    ("LEADER_ROLE_IDS", "", None),
    ("PRIMARY_TEAM_ROLE_IDS", "wing:1, cfrp:2", "wing:1,cfrp:2"),
    ("PRIMARY_TEAM_ROLE_IDS", "wing", None),
    ("PRIMARY_TEAM_ROLE_IDS", "wing:x", None),
    ("SECONDARY_TEAM_ROLE_IDS", ":1", None),
    ("PRIMARY_TEAM_ROLE_IDS", "wing:1,cfrp", None),  # 正しい組と壊れた組の混在
    ("PRIMARY_TEAM_ROLE_IDS", "wing:1,:2", None),
    ("PRIMARY_TEAM_ROLE_IDS", "", None),
    ("PRIMARY_TEAM_ROLE_IDS", " , ", None),
    # 絵文字
    ("SCHEDULE_EMOJI_OK_ID", "<:ok:77>", "77"),
    ("SCHEDULE_EMOJI_MAYBE_ID", "<a:maybe:78>", "78"),
    ("SCHEDULE_EMOJI_NG_ID", "✅", None),
    # 整数
    ("DATA_RETENTION_DAYS", "30", "30"),
    ("DATA_RETENTION_DAYS", "0", "0"),
    ("DATA_RETENTION_DAYS", "36500", "36500"),
    ("DATA_RETENTION_DAYS", "36501", None),
    ("DATA_RETENTION_DAYS", "-1", None),
    ("DATA_RETENTION_DAYS", "3.5", None),
    ("DATA_RETENTION_DAYS", "abc", None),
    ("DATA_RETENTION_DAYS", "²", None),  # isdigit() は真だが int() できない
    ("DATA_RETENTION_DAYS", "３０", None),  # 全角数字
    ("DATA_RETENTION_DAYS", "--3", None),
    ("LAYER_SESSION_ALERT_MINUTES", "10080", "10080"),
    ("LAYER_SESSION_ALERT_MINUTES", "10081", None),
    ("LAYER_SESSION_AUTO_CANCEL_MINUTES", "0", "0"),
    ("WEEKLY_DIGEST_WEEKDAY", "0", "0"),
    ("WEEKLY_DIGEST_WEEKDAY", "6", "6"),
    ("WEEKLY_DIGEST_WEEKDAY", "7", None),
    ("WEEKLY_DIGEST_WEEKDAY", "-1", None),
    ("WEEKLY_DIGEST_WEEKDAY", "月", None),
    # 真偽
    ("WELCOME_ENABLED", "true", "1"),
    ("WELCOME_ENABLED", "ON", "1"),
    ("WEEKLY_DIGEST_ENABLED", "0", "0"),
    ("WEEKLY_DIGEST_ENABLED", "no", "0"),
    ("WELCOME_ENABLED", "たぶん", None),
    ("WELCOME_ENABLED", "", None),
    # 列挙
    ("SCHEDULE_UI_STYLE", " Reaction ", "reaction"),
    ("SCHEDULE_UI_STYLE", "buttons", "buttons"),
    ("SCHEDULE_UI_STYLE", "button", None),
    ("SCHEDULE_UI_STYLE", "reactions", None),
    # 文字列
    ("CLUB_NAME", " 鳥人間サークル ", "鳥人間サークル"),
    ("CLUB_NAME", "あ" * 50, "あ" * 50),
    ("CLUB_NAME", "あ" * 51, None),
    ("CLUB_NAME", "", None),
] + [("COMPETITION_DATE", raw, expected) for raw, expected in COMPETITION_DATE_CASES]


def run(coro):
    return asyncio.run(coro)


# =====================================================================
# 1. 値の検証
# =====================================================================
@pytest.mark.parametrize(("key", "raw", "expected"), VALUE_CASES)
def test_normalize_setting_table(key, raw, expected):
    if expected is None:
        with pytest.raises(SettingValueError):
            normalize_setting(key, raw)
    else:
        assert normalize_setting(key, raw) == expected


def test_value_error_says_what_and_how():
    """エラー文に「どのキーか」「何を入れたか」「正しい形の例」が入る。"""
    with pytest.raises(SettingValueError) as e:
        normalize_setting("COMPETITION_DATE", "2026/07/25")
    message = str(e.value)
    assert "COMPETITION_DATE" in message
    assert "2026/07/25" in message
    assert "YYYY-MM-DD" in message and "2026-07-25" in message


def test_value_error_does_not_echo_huge_input():
    with pytest.raises(SettingValueError) as e:
        normalize_setting("CLUB_NAME", "あ" * 500)
    assert len(str(e.value)) < 200


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _read_back_value(key: str, stored: str):
    """保存形 → for_guild が返すべき値。"""
    kind = SETTING_SPECS[key].kind
    if kind in (SettingKind.CHANNEL, SettingKind.ROLE, SettingKind.EMOJI, SettingKind.INT):
        return int(stored)
    if kind is SettingKind.ROLE_LIST:
        return [int(x) for x in stored.split(",")]
    if kind is SettingKind.ROLE_MAP:
        return {k: int(v) for k, v in (p.split(":") for p in stored.split(","))}
    if kind is SettingKind.BOOL:
        return stored == "1"
    return stored


@pytest.mark.parametrize(("key", "raw", "expected"), [c for c in VALUE_CASES if c[2] is not None])
def test_accepted_values_are_read_back_by_the_readers(key, raw, expected):
    """**検証を通った値は、読み出し側で必ず期待どおりに読める**（往復）。

    検証だけ厳しくしても、読み出し側が読めない形で保存すれば黙って既定へ落ちる。
    共有の `config` は使わず新しい `Config()` で読む（共有キャッシュを汚さない）。
    """

    async def _main():
        from services.progress_sync_service import resolve_default_channel_id

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            stored = normalize_setting(key, raw)
            await SettingsRepository(db).set(G1, key, stored)
            if key == "PROGRESS_DEFAULT_CHANNEL_ID":
                assert await resolve_default_channel_id(db, G1) == int(stored)
                return
            gconf = await Config().for_guild(G1, db=db, force_reload=True)
            assert getattr(gconf, key.lower()) == _read_back_value(key, stored)
        finally:
            await db.close()

    run(_main())


def test_retention_upper_bound_is_usable_when_leaving():
    """上限いっぱいの保持日数でも、退出の記録（削除予定日の計算）が通る。"""

    async def _main():
        from repositories.guild_repository import GuildRepository

        db = Database(_tmp_db_path())
        await db.connect()
        try:
            days = int(normalize_setting("DATA_RETENTION_DAYS", "36500"))
            # 上限を超える日数だと now() + timedelta が OverflowError になり、
            # 退出時の削除予定が記録されない（on_guild_remove が握って黙る）
            left_iso, purge_iso = await GuildRepository(db).mark_left(G1, days)
            assert purge_iso > left_iso
        finally:
            await db.close()

    run(_main())


# =====================================================================
# 2. キーの扱い
# =====================================================================
def test_normalize_key():
    assert normalize_key(" competition_date ") == "COMPETITION_DATE"
    assert normalize_key(None) == ""


def test_unknown_key_suggests_close_spelling():
    assert suggest_keys("COMPETITON_DATE")[0] == "COMPETITION_DATE"
    assert suggest_keys("default_task_channel_id")[0] == "DEFAULT_TASK_CHANNEL_ID"
    with pytest.raises(SettingKeyError) as e:
        lookup("COMPETITON_DATE")
    assert "COMPETITION_DATE" in str(e.value)
    assert "候補" in str(e.value)


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("AUTO_SETUP_COMPLETED_AT", "内部"),
        ("GUILD_COMMANDS_CLEARED_AT", "内部"),
        ("TZ", "/set_common"),
        ("DB_PATH", "/set_common"),
        ("TODOIST_API_TOKEN", "廃止"),
        ("SHEET_CREDENTIALS", "廃止"),
        ("SPREADSHEET_ID", "廃止"),
    ],
)
def test_internal_and_legacy_keys_are_refused_with_reason(key, words):
    with pytest.raises(SettingKeyError) as e:
        normalize_setting(key, "1")
    assert words in str(e.value)


def test_lowercase_key_resolves_to_the_spec_key():
    assert lookup(" competition_date ").key == "COMPETITION_DATE"
    assert normalize_setting("schedule_ui_style", "Reaction") == "reaction"


@pytest.mark.parametrize("name", ["DEFAULT_TASK_CHANNEL_ID", " default_task_channel_id "])
def test_env_fallback_reads_only_the_spec_key(name):
    with mock.patch.dict(os.environ, {"DEFAULT_TASK_CHANNEL_ID": "999"}):
        assert env_fallback(name) == "999"


@pytest.mark.parametrize(
    "name",
    [
        "DISCORD_TOKEN",
        "discord_token",
        "ENCRYPTION_KEY",
        "DATABASE_URL",
        "ACM_H13_CANARY",
        "SOME_THIRD_PARTY_API_KEY",
    ],
)
def test_env_fallback_refuses_non_setting_names(name):
    """仕様表に無い名前では環境変数を引かない（Bot の秘密情報を読ませない）。"""
    with (
        mock.patch.dict(os.environ, {name.strip().upper(): "secret-value"}),
        pytest.raises(SettingKeyError),
    ):
        env_fallback(name)


def test_setting_key_choices():
    all_choices = setting_key_choices("")
    assert 0 < len(all_choices) <= 25
    assert all(len(name) <= 100 for name, _ in all_choices)
    assert all(value in SETTING_SPECS for _, value in all_choices)
    name, _value = next(c for c in all_choices if c[1] == "COMPETITION_DATE")
    assert SETTING_SPECS["COMPETITION_DATE"].label in name, "候補名に説明が入っていない"
    # キー名でも説明でも、大文字小文字を無視して絞れる
    assert [v for _, v in setting_key_choices("competition")] == ["COMPETITION_DATE"]
    assert "COMPETITION_DATE" in [v for _, v in setting_key_choices("大会")]


# =====================================================================
# 3. 仕様表の形
# =====================================================================
def test_spec_entries_are_complete_for_their_kind():
    """種別ごとに要る項目の書き忘れを落とす（上限の無い text・空の enum を作らせない）。"""
    for key, spec in SETTING_SPECS.items():
        assert spec.key == key
        assert isinstance(spec.kind, SettingKind)
        assert spec.label
        if spec.kind is SettingKind.ENUM:
            assert spec.choices, key
        if spec.kind is SettingKind.TEXT:
            assert spec.max_len, key
        if spec.kind is SettingKind.INT:
            assert spec.min_value is not None and spec.max_value is not None, key


def test_unknown_kind_is_not_passed_through():
    fake = SettingSpec("FAKE_KEY", "bogus", "偽")  # type: ignore[arg-type]
    with pytest.raises(SettingValueError):
        normalize_value(fake, "1")


def test_spec_does_not_redefine_other_lists():
    """値の定義を二重にしない（選択肢・複数ロールのキーは既存の定義と一致）。"""
    assert SETTING_SPECS["SCHEDULE_UI_STYLE"].choices == SCHEDULE_UI_STYLES
    role_lists = {k for k, s in SETTING_SPECS.items() if s.kind is SettingKind.ROLE_LIST}
    assert role_lists == set(MULTI_ROLE_KEYS)


def test_internal_keys_are_not_settable():
    assert not (INTERNAL_KEYS & set(SETTING_SPECS))
    assert not (set(COMMAND_ONLY_KEYS) & set(SETTING_SPECS))
    assert not any(is_deprecated_key(k) for k in SETTING_SPECS)


def test_setup_wizard_keys_are_in_spec_with_matching_kind():
    from cogs.setup_wizard import CHANNEL_SETTINGS, EXTRA_SETUP_KEYS, ROLE_SETTINGS

    for key, _ in CHANNEL_SETTINGS:
        assert SETTING_SPECS[key].kind is SettingKind.CHANNEL, key
    for key, _ in ROLE_SETTINGS:
        assert SETTING_SPECS[key].kind in (SettingKind.ROLE, SettingKind.ROLE_LIST), key
    assert set(EXTRA_SETUP_KEYS) <= set(SETTING_SPECS)


def test_dashboard_settings_agree_with_spec():
    """ダッシュボードは別の許可リストを持っている（統合は後回し）。食い違いだけは検出する。

    FastAPI が無い環境でも skip されないよう、ファイルを AST で読む。
    """
    path = os.path.join(BOT_ROOT, "dashboard", "routers", "settings.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    kind_of = {"channel": SettingKind.CHANNEL, "role": SettingKind.ROLE, "text": SettingKind.TEXT}
    found = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SettingSpec"):
            continue
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        kwargs = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
        key = args[0] if args else kwargs["key"]
        dash_type = args[2] if len(args) > 2 else kwargs.get("type", "text")
        assert key in SETTING_SPECS, f"ダッシュボードだけが知っているキー: {key}"
        assert SETTING_SPECS[key].kind is kind_of[dash_type], key
        found += 1
    assert found >= 8, "ダッシュボードの定義を読めていない"


# =====================================================================
# 4. 構造テスト: 仕様表から漏れたキーを検出する
# =====================================================================
#: config.for_guild() が問い合わせるキー（**ちょうどこの 24 個**）。
FOR_GUILD_KEYS = {
    "BOT_LOG_CHANNEL_ID",
    "DEFAULT_ANNOUNCE_CHANNEL_ID",
    "DEFAULT_SCHEDULE_CHANNEL_ID",
    "DEFAULT_PROGRESS_CHANNEL_ID",
    "DEFAULT_TASK_CHANNEL_ID",
    "TODAY_LABEL_CHANNEL_ID",
    "WELCOME_CHANNEL_ID",
    "EXEC_ROLE_ID",
    "ADMIN_ROLE_ID",
    "LEADER_ROLE_IDS",
    "PRIMARY_TEAM_ROLE_IDS",
    "SECONDARY_TEAM_ROLE_IDS",
    "SCHEDULE_EMOJI_OK_ID",
    "SCHEDULE_EMOJI_MAYBE_ID",
    "SCHEDULE_EMOJI_NG_ID",
    "DATA_RETENTION_DAYS",
    "LAYER_SESSION_ALERT_MINUTES",
    "LAYER_SESSION_AUTO_CANCEL_MINUTES",
    "WEEKLY_DIGEST_WEEKDAY",
    "WELCOME_ENABLED",
    "WEEKLY_DIGEST_ENABLED",
    "SCHEDULE_UI_STYLE",
    "COMPETITION_DATE",
    "CLUB_NAME",
}


class _RecordingDB:
    """問い合わされたキーを記録するだけの DB（値は常に無し）。"""

    def __init__(self):
        self.keys: list[str] = []

    async def get_setting(self, guild_id, key):
        self.keys.append(key)


def test_for_guild_reads_exactly_the_spec_keys():
    """実行時に for_guild が問い合わせたキーを記録する。

    ソースの書き方（直書き・定数・ループ）に依らず、実際に読むキーが仕様表から
    漏れたら落ちる。件数も固定する（空振りして空集合のまま緑にならないように）。
    """
    rec = _RecordingDB()
    run(Config().for_guild(G1, db=rec, force_reload=True))
    assert set(rec.keys) == FOR_GUILD_KEYS
    assert FOR_GUILD_KEYS <= set(SETTING_SPECS)


def test_channel_resolvers_read_only_spec_keys():
    from services.progress_sync_service import resolve_default_channel_id
    from utils.notify import resolve_notice_channel_id

    rec = _RecordingDB()
    run(resolve_default_channel_id(rec, G1))
    run(resolve_notice_channel_id(rec, G1))
    assert "PROGRESS_DEFAULT_CHANNEL_ID" in rec.keys
    assert set(rec.keys) <= set(SETTING_SPECS)


_KEY_SHAPE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
#: 環境変数名を受け取る関数（引数はキーではなく環境変数名）
_ENV_FUNCS = {"getenv", "_get_str", "_get_int", "_get_int_list", "_get_team_role_map"}
#: 設定キーではない、キー形の文字列定数（環境変数名を別の形で読んでいるもの）。件数も固定する。
NON_SETTING_CONSTANTS = {"DISCORD_TOKEN", "DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE"}
_SCAN_PREFIXES = ("cogs/", "services/", "utils/", "repositories/", "bot.py", "config.py")


def _key_constants(source: str) -> set[str]:
    """ソース中の「キー形」の文字列定数。環境変数名の引数と `code=` は除く。

    モジュール定数・タプル・辞書・直書きのどれで書いても拾う。
    アンダースコアを含まない名前（`TZ` 等）は拾わない（限界として申し送り済み）。
    """
    tree = ast.parse(source)
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            env_get = (
                name == "get"
                and isinstance(func, ast.Attribute)
                and getattr(func.value, "attr", None) == "environ"
            )
            if name in _ENV_FUNCS or env_get:
                excluded.update(id(a) for a in node.args[:1])
            excluded.update(id(k.value) for k in node.keywords if k.arg == "code")
        if isinstance(node, ast.Subscript) and getattr(node.value, "attr", None) == "environ":
            excluded.add(id(node.slice))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _KEY_SHAPE.match(node.value)
        and id(node) not in excluded
    }


def _scan_codebase() -> set[str]:
    found: set[str] = set()
    for path in _iter_source_files():
        rel = os.path.relpath(path, BOT_ROOT).replace(os.sep, "/")
        if not rel.startswith(_SCAN_PREFIXES) or rel == "utils/settings_spec.py":
            continue
        with open(path, encoding="utf-8") as f:
            found |= _key_constants(f.read())
    return found


@pytest.mark.parametrize(
    "source",
    [
        'repo.set(guild_id, "NEW_SETTING_KEY", "1")',
        'NEW_KEY_CONST = "NEW_SETTING_KEY"',
        'for key in ("OTHER_KEY", "NEW_SETTING_KEY"):\n    pass',
        'x = EMOJI_KEYS["NEW_SETTING_KEY"]',
    ],
)
def test_scanner_catches_every_way_of_writing_a_key(source):
    assert "NEW_SETTING_KEY" in _key_constants(source)


def test_scanner_skips_env_names_and_error_codes():
    source = 'a = os.getenv("SOME_ENV_NAME")\nb = error_embed("x", code="SOME_ERROR_CODE")'
    assert _key_constants(source) == set()


def test_every_key_like_constant_in_the_code_is_classified():
    """コードに出てくるキー形の定数は、仕様表・内部キー・専用コマンドのキー・
    廃止キー・非設定の定数のどれかに分類されていなければならない。

    新しい設定キーをコードに足して仕様表に書き忘れると、ここで落ちる。
    """
    found = _scan_codebase()
    # 下限（走査が空振りしていないこと）
    assert {
        "AUTO_SETUP_COMPLETED_AT",
        "GUILD_COMMANDS_CLEARED_AT",
        "PROGRESS_DEFAULT_CHANNEL_ID",
        "SCHEDULE_EMOJI_OK_ID",
    } <= found
    unclassified = sorted(
        k
        for k in found
        if k not in SETTING_SPECS
        and k not in INTERNAL_KEYS
        and k not in COMMAND_ONLY_KEYS
        and not is_deprecated_key(k)
        and k not in NON_SETTING_CONSTANTS
    )
    assert not unclassified, f"仕様表に無いキー形の定数: {unclassified}"
    # 非設定の許可リストが腐っていない（使われなくなった名前を残さない）
    assert NON_SETTING_CONSTANTS <= found


# =====================================================================
# 環境変数へフォールバックするキーは config が実際に読むものと一致する
# =====================================================================
_CONFIG_ENV_READERS = {"_get_str", "_get_int", "_get_int_list", "_get_team_role_map"}


def _config_env_names() -> set[str]:
    """config.py が環境変数から読む名前（`_get_*("NAME")` の第1引数）。"""
    with open(os.path.join(BOT_ROOT, "config.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) in _CONFIG_ENV_READERS
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_env_backed_keys_match_what_config_reads():
    """`/settings_get` が環境変数の値を出すのは、config が本当に環境変数を読むキーだけ。"""
    env_names = _config_env_names()
    assert "DISCORD_TOKEN" in env_names, "config.py の読み取りを拾えていない（空振り）"
    env_backed = {k for k, s in SETTING_SPECS.items() if s.env}
    assert env_backed == env_names & set(SETTING_SPECS)
    assert len(env_backed) == 15


@pytest.mark.parametrize(
    "key", ["COMPETITION_DATE", "WELCOME_CHANNEL_ID", "PROGRESS_DEFAULT_CHANNEL_ID"]
)
def test_env_fallback_is_none_for_keys_config_does_not_read_from_env(key):
    with mock.patch.dict(os.environ, {key: "999"}):
        assert env_fallback(key) is None
