"""設定キーの仕様表（H1-3）。

**ギルド別設定のキーはここに1箇所で定義する。** `/settings_set` `/settings_get`
`/setup` はこの表に無いキーを受け付けず、種別に合わない値を保存しない。

- 拒否は**コマンド層だけ**で行う。`SettingsRepository.set()` は従来どおり
  何でも書ける（起動時セットアップが内部マーカーを書くため。ここで弾くと
  全ギルドが起動できなくなる）
- 既に DB に入っている不正値は**移行しない**（既存データを動かさない）。
  読み出し側（`config.for_guild()` 等）は今までどおり「不正値は既定へ落とし、
  例外を投げない」
- 検証を通った値は、読み出し側で必ず期待どおりに読める形（保存形）へ正規化する
  （例: 数字は ASCII のみ、ID は `str(int)`、真偽は `1` / `0`、日付は ISO 形式）

Discord にも DB にも触らない純粋なモジュール。
"""

from __future__ import annotations

import difflib
import enum
import os
import re
from dataclasses import dataclass
from datetime import date

from config import SCHEDULE_UI_STYLES


class SettingKind(enum.Enum):
    CHANNEL = "channel"
    ROLE = "role"
    ROLE_LIST = "role_list"
    ROLE_MAP = "role_map"
    EMOJI = "emoji"
    INT = "int"
    BOOL = "bool"
    ENUM = "enum"
    DATE = "date"
    TEXT = "text"


@dataclass(frozen=True)
class SettingSpec:
    """1つの設定キーの仕様。

    種別ごとに要る項目（ENUM の choices、TEXT の max_len、INT の下限・上限）は
    既定値を持たせず、仕様表の検査テストで**書き忘れを落とす**。
    """

    key: str
    kind: SettingKind
    label: str  # 人間向けの説明（オートコンプリートの候補名・エラー文に出す）
    choices: tuple[str, ...] | None = None
    min_value: int | None = None
    max_value: int | None = None
    max_len: int | None = None


class SettingKeyError(ValueError):
    """キーが仕様表に無い（未知・内部・廃止・専用コマンド）。文面は利用者向け。"""


class SettingValueError(ValueError):
    """値が種別に合わない。文面は利用者向け（何が・どう駄目か・どう直すか）。"""


def _spec(key: str, kind: SettingKind, label: str, **kwargs) -> tuple[str, SettingSpec]:
    return key, SettingSpec(key, kind, label, **kwargs)


# Discord の ID（snowflake）は符号付き 64bit に収まる（PostgreSQL の BIGINT）
_MAX_DISCORD_ID = 2**63

#: ギルド別設定の仕様表。`config.for_guild()` が読む 24 キー +
#: `PROGRESS_DEFAULT_CHANNEL_ID`（進捗通知・告知の送信先として直接読まれる）。
SETTING_SPECS: dict[str, SettingSpec] = dict(
    [
        # ---- チャンネル ----
        _spec("BOT_LOG_CHANNEL_ID", SettingKind.CHANNEL, "Bot のログを出すチャンネル"),
        _spec(
            "DEFAULT_ANNOUNCE_CHANNEL_ID",
            SettingKind.CHANNEL,
            "お知らせ（サークル全体への告知）チャンネル",
        ),
        _spec("DEFAULT_SCHEDULE_CHANNEL_ID", SettingKind.CHANNEL, "日程調整の既定の投稿先"),
        _spec(
            "PROGRESS_DEFAULT_CHANNEL_ID",
            SettingKind.CHANNEL,
            "進捗通知の共通送信先（DEFAULT_PROGRESS_CHANNEL_ID より優先）",
        ),
        _spec(
            "DEFAULT_PROGRESS_CHANNEL_ID",
            SettingKind.CHANNEL,
            "進捗チャンネル（PROGRESS_DEFAULT_CHANNEL_ID が無いときの進捗通知先）",
        ),
        _spec("DEFAULT_TASK_CHANNEL_ID", SettingKind.CHANNEL, "タスク通知チャンネル"),
        _spec("TODAY_LABEL_CHANNEL_ID", SettingKind.CHANNEL, "「今日やること」通知チャンネル"),
        _spec("WELCOME_CHANNEL_ID", SettingKind.CHANNEL, "新入生の案内チャンネル"),
        # ---- ロール ----
        _spec("EXEC_ROLE_ID", SettingKind.ROLE, "幹部（実行役）ロール"),
        _spec("ADMIN_ROLE_ID", SettingKind.ROLE, "Bot 管理者ロール"),
        _spec("LEADER_ROLE_IDS", SettingKind.ROLE_LIST, "班長ロール（カンマ区切りで複数可）"),
        _spec(
            "PRIMARY_TEAM_ROLE_IDS",
            SettingKind.ROLE_MAP,
            "【旧方式】班→ロールの対応（班キー:ロールID。/team-role を推奨）",
        ),
        _spec(
            "SECONDARY_TEAM_ROLE_IDS",
            SettingKind.ROLE_MAP,
            "【旧方式】班→副ロールの対応（班キー:ロールID。/team-role を推奨）",
        ),
        # ---- 出欠の絵文字 ----
        _spec(
            "SCHEDULE_EMOJI_OK_ID",
            SettingKind.EMOJI,
            "出欠「参加」のカスタム絵文字（/schedule emoji set を推奨）",
        ),
        _spec(
            "SCHEDULE_EMOJI_MAYBE_ID",
            SettingKind.EMOJI,
            "出欠「未定」のカスタム絵文字（/schedule emoji set を推奨）",
        ),
        _spec(
            "SCHEDULE_EMOJI_NG_ID",
            SettingKind.EMOJI,
            "出欠「不参加」のカスタム絵文字（/schedule emoji set を推奨）",
        ),
        # ---- 数値 ----
        _spec(
            "DATA_RETENTION_DAYS",
            SettingKind.INT,
            "退出後にデータを残す日数（0〜36500）",
            min_value=0,
            max_value=36500,
        ),
        _spec(
            "LAYER_SESSION_ALERT_MINUTES",
            SettingKind.INT,
            "積層の押し忘れを知らせるまでの分数（0〜10080。0 で無効）",
            min_value=0,
            max_value=10080,
        ),
        _spec(
            "LAYER_SESSION_AUTO_CANCEL_MINUTES",
            SettingKind.INT,
            "積層を自動で取り消すまでの分数（0〜10080。0 で無効）",
            min_value=0,
            max_value=10080,
        ),
        _spec(
            "WEEKLY_DIGEST_WEEKDAY",
            SettingKind.INT,
            "週次ダイジェストの曜日（0=月曜〜6=日曜）",
            min_value=0,
            max_value=6,
        ),
        # ---- 真偽 ----
        _spec("WELCOME_ENABLED", SettingKind.BOOL, "新入生オンボーディング（1=ON / 0=OFF）"),
        _spec(
            "WEEKLY_DIGEST_ENABLED",
            SettingKind.BOOL,
            "週次ダイジェストの公開投稿（1=ON / 0=OFF）",
        ),
        # ---- 列挙・日付・文字列 ----
        _spec(
            "SCHEDULE_UI_STYLE",
            SettingKind.ENUM,
            "日程調整の投票方式（buttons / reaction）",
            choices=SCHEDULE_UI_STYLES,
        ),
        _spec("COMPETITION_DATE", SettingKind.DATE, "大会日（YYYY-MM-DD）"),
        _spec("CLUB_NAME", SettingKind.TEXT, "サークル名（50字まで）", max_len=50),
    ]
)

#: Bot が内部で使うキー。コマンドからは設定できない（`SettingsRepository.set()` は書ける）。
INTERNAL_KEYS: frozenset[str] = frozenset(
    {
        "GUILD_NAME",
        "SETUP_VERSION",
        "SETUP_AT",
        "AUTO_SETUP_COMPLETED_AT",
        "AUTO_SETUP_DONE",
        "GUILD_COMMANDS_CLEARED_AT",
    }
)

#: 専用コマンドで設定する旧運用のキー → そのコマンド。
COMMAND_ONLY_KEYS: dict[str, str] = {"TZ": "/set_common", "DB_PATH": "/set_common"}

#: 廃止されたキー（接頭辞も含む）。読まれないので保存させない。
DEPRECATED_KEYS: frozenset[str] = frozenset(
    {
        "SPREADSHEET_ID",
        "LAYER_SPREADSHEET_ID",
        "PROGRESS_SPREADSHEET_ID",
        "GOOGLE_CREDENTIALS_PATH",
    }
)
DEPRECATED_PREFIXES: tuple[str, ...] = ("TODOIST_", "SHEET_")

_BOOL_TRUE = ("1", "true", "yes", "on")  # SettingsRepository.get_bool と同じ集合
_BOOL_FALSE = ("0", "false", "no", "off")
_ASCII_DIGITS = re.compile(r"[0-9]+")
_ASCII_INT = re.compile(r"-?[0-9]+")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CHANNEL_MENTION = re.compile(r"<#([0-9]+)>")
_ROLE_MENTION = re.compile(r"<@&([0-9]+)>")
_EMOJI_MENTION = re.compile(r"<a?:[^:<>]+:([0-9]+)>")

#: エラー文に入力値を引用するときの上限（巨大な入力をそのまま返さない）
_QUOTE_LIMIT = 50


def normalize_key(raw: str | None) -> str:
    """利用者が打ったキーを仕様表の形へ（前後の空白を除いて大文字化）。

    **`/settings_delete` には使わない。** DB の主キーは大文字小文字を区別するので、
    削除は入力をそのまま照合する（大文字化すると正しいキーのほうを消してしまう）。
    """
    return (raw or "").strip().upper()


def is_deprecated_key(key: str) -> bool:
    return key in DEPRECATED_KEYS or key.startswith(DEPRECATED_PREFIXES)


def suggest_keys(raw_key: str, limit: int = 3) -> list[str]:
    """綴りの近い設定キー（仕様表から）。"""
    return difflib.get_close_matches(
        normalize_key(raw_key), list(SETTING_SPECS), n=limit, cutoff=0.6
    )


def _quote(raw: str) -> str:
    text = (raw or "").strip()
    return text if len(text) <= _QUOTE_LIMIT else text[:_QUOTE_LIMIT] + "…"


def key_error_message(raw_key: str) -> str:
    """仕様表に無いキーを拒否するときの文面。"""
    key = normalize_key(raw_key)
    if key in INTERNAL_KEYS:
        return f"`{key}` は Bot が内部で使うキーなので、コマンドからは変更できません。"
    if key in COMMAND_ONLY_KEYS:
        return (
            f"`{key}` は `{COMMAND_ONLY_KEYS[key]}` で設定します"
            "（`GUILD_ID` を指定した旧運用のサーバーでだけ効きます）。"
        )
    if is_deprecated_key(key):
        hint = "`/todoist-setup` を使ってください。" if key.startswith("TODOIST_") else ""
        return f"`{key}` は廃止されたキーです。{hint}"
    message = f"`{_quote(raw_key)}` という設定キーはありません。"
    candidates = suggest_keys(raw_key)
    if candidates:
        message += "\nもしかして: " + " / ".join(
            f"`{c}`（{SETTING_SPECS[c].label}）" for c in candidates
        )
    return message + "\n`setting_key` の候補から選んでください。"


def lookup(raw_key: str) -> SettingSpec:
    """仕様表からキーを引く。無ければ `SettingKeyError`（文面つき）。"""
    spec = SETTING_SPECS.get(normalize_key(raw_key))
    if spec is None:
        raise SettingKeyError(key_error_message(raw_key))
    return spec


def env_fallback(raw_key: str) -> str | None:
    """仕様表のキーについて、DB に値が無いときに効いている環境変数の値。

    **環境変数は仕様表のキー（`spec.key`）でしか引かない。** 利用者の入力を
    そのまま `os.getenv` に渡すと、設定キーではない環境変数（Bot の秘密情報）まで
    読めてしまう。仕様表に無いキーは `SettingKeyError`。
    """
    return os.getenv(lookup(raw_key).key)


def _value_error(spec: SettingSpec, raw: str, expected: str) -> SettingValueError:
    return SettingValueError(
        f"`{spec.key}`（{spec.label}）には{expected}を入れてください。入力: `{_quote(raw)}`"
    )


def _discord_id(spec: SettingSpec, raw: str, token: str, mention: re.Pattern | None) -> str:
    text = token.strip()
    if mention is not None:
        m = mention.fullmatch(text)
        if m:
            text = m.group(1)
    if not _ASCII_DIGITS.fullmatch(text) or not 0 < int(text) < _MAX_DISCORD_ID:
        raise _value_error(spec, raw, "ID（半角数字。例: `123456789012345678`）")
    return str(int(text))


def normalize_value(spec: SettingSpec, raw: str) -> str:
    """spec に従って値を検証し、保存形の文字列を返す。不正なら `SettingValueError`。"""
    value = (raw or "").strip()
    kind = spec.kind
    if kind is SettingKind.CHANNEL:
        return _discord_id(spec, raw, value, _CHANNEL_MENTION)
    if kind is SettingKind.ROLE:
        return _discord_id(spec, raw, value, _ROLE_MENTION)
    if kind is SettingKind.EMOJI:
        return _discord_id(spec, raw, value, _EMOJI_MENTION)
    if kind is SettingKind.ROLE_LIST:
        parts = [p for p in (part.strip() for part in value.split(",")) if p]
        if not parts:
            raise _value_error(spec, raw, "ロール ID をカンマ区切りで（例: `111,222`）")
        ids: list[str] = []
        for part in parts:
            role_id = _discord_id(spec, raw, part, _ROLE_MENTION)
            if role_id not in ids:
                ids.append(role_id)
        return ",".join(ids)
    if kind is SettingKind.ROLE_MAP:
        parts = [p for p in (part.strip() for part in value.split(",")) if p]
        pairs: list[str] = []
        for part in parts:
            team, sep, role = part.partition(":")
            if not sep or not team.strip():
                raise _value_error(
                    spec, raw, "`班キー:ロールID` をカンマ区切りで（例: `wing:111`）"
                )
            pairs.append(f"{team.strip()}:{_discord_id(spec, raw, role, _ROLE_MENTION)}")
        if not pairs:
            raise _value_error(spec, raw, "`班キー:ロールID` をカンマ区切りで（例: `wing:111`）")
        return ",".join(pairs)
    if kind is SettingKind.INT:
        expected = f"{spec.min_value}〜{spec.max_value} の整数"
        if not _ASCII_INT.fullmatch(value):
            raise _value_error(spec, raw, expected)
        number = int(value)
        if spec.min_value is None or spec.max_value is None:
            raise SettingValueError(f"`{spec.key}` の範囲が定義されていません（Bot の不具合）。")
        if not spec.min_value <= number <= spec.max_value:
            raise _value_error(spec, raw, expected)
        return str(number)
    if kind is SettingKind.BOOL:
        lowered = value.lower()
        if lowered in _BOOL_TRUE:
            return "1"
        if lowered in _BOOL_FALSE:
            return "0"
        raise _value_error(spec, raw, "`1`（ON）か `0`（OFF）")
    if kind is SettingKind.ENUM:
        lowered = value.lower()
        if not spec.choices or lowered not in spec.choices:
            choices = " / ".join(f"`{c}`" for c in spec.choices or ())
            raise _value_error(spec, raw, f"{choices} のどれか")
        return lowered
    if kind is SettingKind.DATE:
        if _ISO_DATE.fullmatch(value):
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                pass
        raise _value_error(spec, raw, "`YYYY-MM-DD` 形式の日付（例: `2026-07-25`）")
    if kind is SettingKind.TEXT:
        if spec.max_len is None:
            raise SettingValueError(f"`{spec.key}` の上限が定義されていません（Bot の不具合）。")
        if not value or len(value) > spec.max_len:
            raise _value_error(spec, raw, f"1〜{spec.max_len} 文字の文字列")
        return value
    raise SettingValueError(f"`{spec.key}` の種別 `{kind}` は検証できません（Bot の不具合）。")


def normalize_setting(raw_key: str, raw_value: str) -> str:
    """キーを仕様表で引き、値を検証して保存形を返す。

    キーが無ければ `SettingKeyError`、値が不正なら `SettingValueError`。
    `/settings_set` と `/setup`（`SetupWizard.save_setting`）の両方がこれを通る。
    """
    return normalize_value(lookup(raw_key), raw_value)


def setting_key_choices(current: str | None) -> list[tuple[str, str]]:
    """`setting_key` のオートコンプリート候補 `(候補名, 値)`。

    候補名は「キー — 説明」（Discord の上限 100 字に切り詰める）。キー名・説明の
    どちらかに部分一致（大文字小文字無視）するものを最大 25 件。
    """
    query = (current or "").strip().lower()
    out: list[tuple[str, str]] = []
    for spec in SETTING_SPECS.values():
        if query and query not in spec.key.lower() and query not in spec.label.lower():
            continue
        out.append((f"{spec.key} — {spec.label}"[:100], spec.key))
        if len(out) >= 25:
            break
    return out
