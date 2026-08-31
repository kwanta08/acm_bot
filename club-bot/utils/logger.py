"""ログ初期化ユーティリティ（仕様 15.2）。

bot とダッシュボードの2プロセスが共用する。**同じログファイルを
2プロセスで掴むと RotatingFileHandler のローテーションで取り合いになる**ため、
呼び出し側がプロセスごとのファイル名を渡す（bot: bot.log /
ダッシュボード: dashboard.log。D2-5）。

出力ディレクトリは既定で相対 `logs/`。本番の systemd サービスは
ProtectHome=read-only ＋ ReadWritePaths で書き込み先を絞っているため、
環境変数 `LOG_DIR` で**書き込み可能な絶対パス**へ向けられるようにしてある
（deploy/club-bot-dashboard.service 参照。相対のまま起動すると
ディレクトリ作成に失敗し、Restart=always と相まって再起動ループになる）。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR = "logs"


def setup_logging(level: int = logging.INFO, filename: str = "bot.log") -> logging.Logger:
    """ルートロガーを初期化し、コンソールとファイルへ出力する。

    ログディレクトリが作れない・書けない場合は**ファイル出力だけを諦め、
    コンソールのみで続行する**。ログの都合でプロセスを落とすと、systemd の
    Restart=always と相まって再起動ループ（ダッシュボードでは Caddy の 502）
    になるため（LOG_DIR 未設定の旧ユニット + ProtectHome=read-only で実際に
    起きた障害。ログはあくまで従で、サービスの提供を妨げない）。
    """
    log_dir = os.environ.get("LOG_DIR") or _LOG_DIR

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 二重登録防止
    if root.handlers:
        return logging.getLogger("club-bot")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, filename),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        root.warning(
            "ログディレクトリ %s に書き込めないため、ファイルログなしで続行します"
            "（環境変数 LOG_DIR で書き込み可能な絶対パスを指定してください）: %s: %s",
            log_dir,
            type(e).__name__,
            e,
        )

    # discord.py の冗長ログを抑制
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    return logging.getLogger("club-bot")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
