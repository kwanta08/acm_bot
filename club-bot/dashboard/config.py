"""ダッシュボードの設定（環境変数から読み込む）。

bot 本体の config.py とは独立している。ダッシュボードは bot とは
**別プロセス**で動くため、Discord Bot トークンは一切必要としない
（必要なのは OAuth2 のクライアント ID / シークレットと DB 接続だけ）。

DB は bot と同じものを共有する（DATABASE_URL / DB_PATH）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# セッション Cookie 名（署名付き。サーバー側セッションストアは持たない）
SESSION_COOKIE = "clubbot_dashboard_session"

# 既定のセッション有効期間（秒）。7日
DEFAULT_SESSION_MAX_AGE = 7 * 24 * 60 * 60

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"

# 要求する OAuth2 スコープ（設計方針 2.2）
OAUTH_SCOPES = ("identify", "guilds")


def _clean(value: str | None) -> str:
    """.env の手書きミス（前後空白・囲い引用符）に耐える。"""
    return (value or "").strip().strip('"').strip("'")


def _optional_int(value: str | None) -> int | None:
    """未指定・不正値は None（呼び出し先の既定値を使う）。"""
    try:
        return int(_clean(value))
    except (TypeError, ValueError):
        return None


def _int(value: str | None, default: int) -> int:
    try:
        return int(_clean(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DashboardConfig:
    """ダッシュボードの設定値。"""

    # --- 認証（P2-2 で使用） ---
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    secret_key: str = ""
    session_max_age: int = DEFAULT_SESSION_MAX_AGE

    # --- DB（bot と共有。プールはプロセスごとに独立） ---
    db_path: str = "./data/club.db"
    database_url: str | None = None
    db_pool_min_size: int | None = None
    db_pool_max_size: int | None = None

    # --- 動作 ---
    base_url: str = "http://127.0.0.1:8000"
    # HTTPS 配信時に Cookie へ Secure 属性を付ける（本番は必ず True）
    secure_cookie: bool = True
    # 開発用: OAuth を経由せずログイン状態を作るためのダミーユーザー
    dev_login_user_id: str = ""

    @property
    def oauth_ready(self) -> bool:
        """OAuth2 ログインに必要な設定がそろっているか。"""
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def missing(self) -> list[str]:
        """起動に必須の設定のうち不足しているものを返す。"""
        missing: list[str] = []
        if not self.secret_key:
            missing.append("DASHBOARD_SECRET_KEY")
        if not self.oauth_ready:
            for name, value in (("DISCORD_CLIENT_ID", self.client_id),
                                ("DISCORD_CLIENT_SECRET", self.client_secret),
                                ("DASHBOARD_REDIRECT_URI", self.redirect_uri)):
                if not value:
                    missing.append(name)
        return missing


def load_config(env: dict[str, str] | None = None) -> DashboardConfig:
    """環境変数から設定を組み立てる（テストでは env を差し替えられる）。"""
    src = os.environ if env is None else env
    database_url = _clean(src.get("DATABASE_URL")) or None
    return DashboardConfig(
        client_id=_clean(src.get("DISCORD_CLIENT_ID")),
        client_secret=_clean(src.get("DISCORD_CLIENT_SECRET")),
        redirect_uri=_clean(src.get("DASHBOARD_REDIRECT_URI")),
        secret_key=_clean(src.get("DASHBOARD_SECRET_KEY")),
        session_max_age=_int(src.get("DASHBOARD_SESSION_MAX_AGE"),
                             DEFAULT_SESSION_MAX_AGE),
        db_path=_clean(src.get("DB_PATH")) or "./data/club.db",
        database_url=database_url,
        # ダッシュボード側のプールは bot と独立に調整できる
        # （未指定なら utils/db.py の DB_POOL_* / 既定値にフォールバック）
        db_pool_min_size=_optional_int(src.get("DASHBOARD_DB_POOL_MIN_SIZE")),
        db_pool_max_size=_optional_int(src.get("DASHBOARD_DB_POOL_MAX_SIZE")),
        base_url=_clean(src.get("DASHBOARD_BASE_URL")) or "http://127.0.0.1:8000",
        secure_cookie=_clean(src.get("DASHBOARD_SECURE_COOKIE")).lower()
        not in ("0", "false", "no"),
        dev_login_user_id=_clean(src.get("DASHBOARD_DEV_LOGIN_USER_ID")),
    )


@lru_cache(maxsize=1)
def get_config() -> DashboardConfig:
    """プロセス内で共有する設定（起動時に1回だけ読む）。"""
    return load_config()
