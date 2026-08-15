"""Discord OAuth2 ログインとセッションの組み立て（P2-2）。

- スコープは `identify` + `guilds` のみ（メッセージも DM も読まない）
- セッションは**署名付きクッキー**に保存し、サーバー側セッションストアは持たない
  （starlette の SessionMiddleware。改ざんされた Cookie は復号に失敗して破棄される）
- アクセスできるサーバーは「利用者が所属していて、かつ bot も参加している」
  サーバーだけ。後者は DB の guilds 台帳で判定するため、
  **Discord Bot トークンを持たずに**判定できる

Cookie のサイズ上限（4KB）に収めるため、セッションには
「アクセスできるサーバーの最小情報」だけを入れる。
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from dashboard.config import (
    DISCORD_API_BASE,
    DISCORD_AUTHORIZE_URL,
    DISCORD_TOKEN_URL,
    OAUTH_SCOPES,
    DashboardConfig,
)
from utils.logger import get_logger

log = get_logger("dashboard.auth")

# Discord の権限ビット
PERM_ADMINISTRATOR = 0x8
PERM_MANAGE_GUILD = 0x20

# セッションに載せるサーバー数の上限（Cookie 4KB 制限への保険）
MAX_SESSION_GUILDS = 50

SESSION_USER_KEY = "user"
SESSION_GUILDS_KEY = "guilds"
SESSION_STATE_KEY = "oauth_state"


class OAuthError(Exception):
    """OAuth2 のフローが完了できない（state 不一致・トークン交換失敗など）。"""


@dataclass(frozen=True)
class SessionUser:
    """ログイン中の利用者（セッションに保存する最小情報）。"""

    id: str
    name: str
    avatar: str | None = None


@dataclass(frozen=True)
class SessionGuild:
    """利用者がアクセスできるサーバー（セッションに保存する最小情報）。

    manage_guild は Discord 側の「サーバー管理」権限。編集操作の可否は
    これに DB 上の役割（班長など）を加えて dashboard/security.py が決める。
    """

    id: str
    name: str
    manage_guild: bool = False


def build_authorize_url(config: DashboardConfig, state: str) -> str:
    """Discord の認可画面 URL を組み立てる。"""
    params = httpx.QueryParams(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "state": state,
            # 毎回同意画面を出さない（すでに許可済みならそのまま戻る）
            "prompt": "none",
        }
    )
    return f"{DISCORD_AUTHORIZE_URL}?{params}"


def new_state() -> str:
    """CSRF 対策の state（推測不能な乱数）。"""
    return secrets.token_urlsafe(24)


def has_manage_guild(permissions: Any) -> bool:
    """guilds レスポンスの permissions から「サーバー管理」権限を判定する。

    permissions は文字列で返る（64bit をそのまま扱うため）。
    Administrator を持つ場合も管理権限ありとみなす。
    """
    try:
        bits = int(permissions)
    except (TypeError, ValueError):
        return False
    return bool(bits & (PERM_MANAGE_GUILD | PERM_ADMINISTRATOR))


async def exchange_code(config: DashboardConfig, code: str, client: httpx.AsyncClient) -> str:
    """認可コードをアクセストークンに交換する。

    アクセストークンは**セッションに保存しない**（ログイン時に一度だけ使い、
    利用者情報と所属サーバーを取得したら破棄する）。
    """
    try:
        res = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as e:
        raise OAuthError("Discord へ接続できませんでした。") from e
    if res.status_code != 200:
        # レスポンス本文にはコードやシークレットが含まれうるためログに出さない
        log.warning("トークン交換に失敗しました（status=%s）", res.status_code)
        raise OAuthError("ログインに失敗しました。もう一度お試しください。")
    token = res.json().get("access_token")
    if not token:
        raise OAuthError("ログインに失敗しました。もう一度お試しください。")
    return str(token)


async def _get(client: httpx.AsyncClient, path: str, token: str) -> Any:
    try:
        res = await client.get(
            f"{DISCORD_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}
        )
    except httpx.HTTPError as e:
        raise OAuthError("Discord へ接続できませんでした。") from e
    if res.status_code != 200:
        log.warning("Discord API 呼び出しに失敗しました（%s status=%s）", path, res.status_code)
        raise OAuthError("Discord から情報を取得できませんでした。")
    return res.json()


async def fetch_user(client: httpx.AsyncClient, token: str) -> SessionUser:
    """`identify` スコープで利用者情報を取得する。"""
    data = await _get(client, "/users/@me", token)
    name = data.get("global_name") or data.get("username") or str(data.get("id", ""))
    return SessionUser(id=str(data["id"]), name=str(name), avatar=data.get("avatar"))


async def fetch_guilds(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    """`guilds` スコープで所属サーバー一覧を取得する。"""
    data = await _get(client, "/users/@me/guilds", token)
    return list(data) if isinstance(data, list) else []


def select_accessible_guilds(
    user_guilds: list[dict[str, Any]], bot_guild_ids: set[int]
) -> list[SessionGuild]:
    """利用者の所属サーバーのうち bot も参加しているものだけを返す。

    bot が居ないサーバーを候補に出さないことで、
    「ダッシュボードには出るがデータが無い」状態を避ける。
    """
    out: list[SessionGuild] = []
    for guild in user_guilds:
        raw_id = str(guild.get("id") or "")
        if not raw_id.isdigit() or int(raw_id) not in bot_guild_ids:
            continue
        out.append(
            SessionGuild(
                id=raw_id,
                name=str(guild.get("name") or raw_id),
                manage_guild=has_manage_guild(guild.get("permissions")),
            )
        )
    if len(out) > MAX_SESSION_GUILDS:
        log.warning(
            "アクセス可能サーバーが %d 件あるため %d 件に切り詰めます", len(out), MAX_SESSION_GUILDS
        )
        out = out[:MAX_SESSION_GUILDS]
    return out


def store_session(session: dict[str, Any], user: SessionUser, guilds: list[SessionGuild]) -> None:
    """セッション（署名付き Cookie の中身）を書き込む。

    アクセストークンは保存しない。保持するのは利用者の表示情報と、
    アクセスを許可したサーバーの ID・名前・管理権限のみ。
    """
    session[SESSION_USER_KEY] = asdict(user)
    session[SESSION_GUILDS_KEY] = [asdict(g) for g in guilds]
    session.pop(SESSION_STATE_KEY, None)


def read_user(session: dict[str, Any]) -> SessionUser | None:
    raw = session.get(SESSION_USER_KEY)
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return SessionUser(id=str(raw["id"]), name=str(raw.get("name") or ""), avatar=raw.get("avatar"))


def read_guilds(session: dict[str, Any]) -> list[SessionGuild]:
    raw = session.get(SESSION_GUILDS_KEY)
    if not isinstance(raw, list):
        return []
    out: list[SessionGuild] = []
    for item in raw:
        if isinstance(item, dict) and str(item.get("id", "")).isdigit():
            out.append(
                SessionGuild(
                    id=str(item["id"]),
                    name=str(item.get("name") or item["id"]),
                    manage_guild=bool(item.get("manage_guild")),
                )
            )
    return out


def find_session_guild(session: dict[str, Any], guild_id: int) -> SessionGuild | None:
    """セッションで検証済みのサーバーだけを返す（未検証なら None）。

    **アクセス制御の入口**。リクエストで与えられた guild_id は、
    必ずこの関数を通してからでないとリポジトリ層へ渡してはならない。
    """
    for guild in read_guilds(session):
        if guild.id == str(guild_id):
            return guild
    return None
