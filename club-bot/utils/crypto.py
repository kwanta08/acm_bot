"""Fernet による対称鍵暗号ユーティリティ（Todoist トークン保管用）。

- 暗号鍵は ENCRYPTION_KEY 環境変数のみから読み込む（DB・settings・ログには出さない）。
- Todoist トークンは復号が必要なためハッシュ化ではなく Fernet
  （AES-128-CBC + HMAC-SHA256）で暗号化する。
- 例外メッセージ・ログに鍵・平文・暗号文を含めない。

鍵の生成:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from utils.logger import get_logger

log = get_logger("crypto")


class EncryptionKeyMissingError(RuntimeError):
    """ENCRYPTION_KEY が未設定または形式不正。"""


class TokenDecryptError(RuntimeError):
    """暗号文を復号できない（鍵の不一致またはデータ破損）。"""


_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    """ENCRYPTION_KEY から Fernet を構築する（成功時のみキャッシュ）。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    # 前後空白・囲い引用符を除去（手書き .env への防衛。config._clean と同趣旨）
    key = (os.getenv("ENCRYPTION_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise EncryptionKeyMissingError(
            "ENCRYPTION_KEY が設定されていません。"
            ".env に Fernet 鍵を設定してください。")
    try:
        _fernet = Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as e:
        raise EncryptionKeyMissingError(
            "ENCRYPTION_KEY の形式が不正です（Fernet 鍵ではありません）。"
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" で生成してください。") from e
    return _fernet


def is_encryption_ready() -> bool:
    """暗号化が利用可能か（起動時チェック・コマンドの事前検証用）。"""
    try:
        get_fernet()
        return True
    except EncryptionKeyMissingError:
        return False


def encrypt_token(plain: str) -> str:
    """平文トークンを暗号化して urlsafe base64 テキストで返す。"""
    return get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_token(cipher: str) -> str:
    """暗号文を復号する。失敗時は TokenDecryptError（内容は含めない）。"""
    try:
        return get_fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise TokenDecryptError(
            "暗号文を復号できません（ENCRYPTION_KEY の不一致またはデータ破損）。"
            "/todoist-setup で再登録してください。") from e


def reset_cache() -> None:
    """キャッシュした Fernet を破棄する（鍵変更時・テスト用）。"""
    global _fernet
    _fernet = None
