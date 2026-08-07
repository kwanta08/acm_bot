"""Directus（外部 DB 閲覧・編集 UI）連携サービス。

各ギルドの管理者がセルフサービスで Directus アカウントを取得できるように、
Directus の Admin API を呼んでユーザーを招待し、そのユーザーの
カスタムフィールド `guild_id` に自ギルドの ID を書き込む。

Directus 側では `guild_id` を使った行レベル権限フィルタ
（`{"guild_id": {"_eq": "$CURRENT_USER.guild_id"}}`）を **一度だけ**
設定しておく。以降は新しいギルドが増えても運用者の追加作業なしに
データが分離される（設定手順は docs/DIRECTUS.md）。

セキュリティ方針:
- パスワードは bot が生成も保存もしない。Directus のメール招待フローに委ねる。
- 管理トークン（DIRECTUS_ADMIN_TOKEN）とメールアドレスを
  ログ・例外メッセージに出力しない（HTTP ステータスと Directus の
  エラーコードのみを扱う）。
- 未設定（DIRECTUS_URL 等が無い）環境では DirectusUnavailable を送出し、
  呼び出し側が案内メッセージに変換する（services/sheets_service.py の
  SheetsUnavailable と同じ扱い）。
"""
from __future__ import annotations

from typing import Any

from config import config
from utils.logger import get_logger

log = get_logger("directus")

try:
    import aiohttp
except ImportError:  # discord.py の依存だが、欠けていても import は失敗させない
    aiohttp = None  # type: ignore

# Directus のユーザー状態（PATCH /users/{id} の status）
USER_STATUS_SUSPENDED = "suspended"

_TIMEOUT_SECONDS = 15


class DirectusUnavailable(Exception):
    """未設定・aiohttp 不在などで Directus 連携を実行できない。"""


class DirectusEmailInUse(Exception):
    """そのメールアドレスの Directus ユーザーが既に存在する。

    1 Directus ユーザーにつき guild_id は1つしか持てないため、
    同じメールアドレスを複数のサークルで使うことはできない。
    """


class DirectusError(Exception):
    """Directus API 呼び出しに失敗した（メッセージに秘密情報を含めない）。"""


class DirectusClient:
    """
    Directus Admin API の薄いラッパー。

    session を注入すると実際の HTTP を発生させずにテストできる
    （session は aiohttp.ClientSession 互換の request() を持つオブジェクト）。
    """

    def __init__(self, base_url: str, admin_token: str, role_id: str,
                 session: Any = None):
        self.base_url = (base_url or "").rstrip("/")
        self._admin_token = admin_token or ""
        self.role_id = role_id or ""
        self._session = session
        if not self.base_url or not self._admin_token or not self.role_id:
            raise DirectusUnavailable(
                "DIRECTUS_URL / DIRECTUS_ADMIN_TOKEN / DIRECTUS_ROLE_ID が"
                "未設定です。")

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._admin_token}",
                "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, *,
                       json: dict[str, Any] | None = None,
                       params: dict[str, str] | None = None) -> Any:
        """Directus API を呼び、JSON の data 部を返す（本文が無ければ None）。

        例外メッセージにはトークン・メールアドレスを含めない。
        """
        if self._session is None and aiohttp is None:
            raise DirectusUnavailable(
                "aiohttp が見つかりません。"
                "`pip install -r requirements.txt` を実行してください。")

        url = f"{self.base_url}{path}"
        session = self._session
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS))
        try:
            async with session.request(method, url, json=json, params=params,
                                       headers=self._headers()) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001  (空応答・非 JSON 応答)
                    body = None
                if status >= 400:
                    code = _error_code(body)
                    if code == "RECORD_NOT_UNIQUE":
                        raise DirectusEmailInUse(code)
                    log.warning("Directus API 失敗: %s %s -> HTTP %s (%s)",
                                method, path, status, code or "-")
                    raise DirectusError(f"HTTP {status}")
                return body.get("data") if isinstance(body, dict) else None
        except (DirectusError, DirectusEmailInUse, DirectusUnavailable):
            raise
        except Exception as e:  # 通信エラー等はすべて DirectusError に集約する
            log.warning("Directus API 呼び出しに失敗しました: %s", type(e).__name__)
            raise DirectusError(type(e).__name__) from e
        finally:
            if owns_session:
                await session.close()

    # ------------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------------
    async def invite_user(self, email: str, guild_id: int) -> str:
        """ユーザーを招待し、guild_id を設定して Directus のユーザー ID を返す。

        Directus の POST /users/invite は作成したユーザーを返さず、
        カスタムフィールドも受け取らないため、招待 → メールで検索 →
        PATCH の3段構成で行う。招待メールの送信は Directus 側が担当する。
        """
        await self._request("POST", "/users/invite",
                            json={"email": email, "role": self.role_id})
        user_id = await self.find_user_id(email)
        if not user_id:
            raise DirectusError("INVITED_USER_NOT_FOUND")
        await self.patch_user_guild_field(user_id, guild_id)
        return user_id

    async def find_user_id(self, email: str) -> str | None:
        """メールアドレスから Directus のユーザー ID を引く。無ければ None。"""
        data = await self._request(
            "GET", "/users",
            params={"filter[email][_eq]": email, "fields": "id", "limit": "1"})
        if isinstance(data, list) and data:
            return str(data[0].get("id") or "") or None
        return None

    async def patch_user_guild_field(self, user_id: str, guild_id: int) -> None:
        """ユーザーのカスタムフィールド guild_id を設定する。

        この値が未設定だと行レベル権限フィルタが何もマッチせず、
        そのユーザーには一切データが見えない（fail-closed）。
        """
        await self._request("PATCH", f"/users/{user_id}",
                            json={"guild_id": guild_id})

    async def revoke_user(self, user_id: str) -> None:
        """ユーザーを停止（suspended）してログインできないようにする。"""
        await self._request("PATCH", f"/users/{user_id}",
                            json={"status": USER_STATUS_SUSPENDED})


def _error_code(body: Any) -> str | None:
    """Directus のエラー応答から extensions.code を取り出す（無ければ None）。"""
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    extensions = first.get("extensions")
    if isinstance(extensions, dict):
        code = extensions.get("code")
        if code:
            return str(code)
    return None


def is_configured() -> bool:
    """Directus 連携に必要な環境変数が揃っているか。"""
    return bool(config.directus_url and config.directus_admin_token
                and config.directus_role_id)


def client_from_config() -> DirectusClient:
    """環境変数（config）から DirectusClient を生成する。

    未設定なら DirectusUnavailable を送出する。
    """
    return DirectusClient(config.directus_url, config.directus_admin_token,
                          config.directus_role_id)
