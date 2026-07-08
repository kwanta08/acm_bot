"""ログ初期化ユーティリティ（仕様 15.2）。"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR = "logs"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """ルートロガーを初期化し、コンソールとファイルへ出力する。"""
    os.makedirs(_LOG_DIR, exist_ok=True)

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

    file_handler = RotatingFileHandler(
        os.path.join(_LOG_DIR, "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # discord.py の冗長ログを抑制
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    return logging.getLogger("club-bot")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
