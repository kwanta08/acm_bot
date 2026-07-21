"""Todoist トークン管理（暗号化保存・ギルド別解決）のテスト。

- Fernet 暗号化/復号のラウンドトリップとエラー系
- todoist_configs のギルド分離・暗号文での保存（平文が DB に残らない）
- TodoistServiceManager.for_guild の解決（未登録/登録済み/復号失敗）
- 権限ロジック（admin_role_id 未設定でもオーナー/Administrator が L4）
- トークンが例外・ログに出ないこと
- 移行スクリプト（scripts/migrate_todoist_token.py）の動作

実行: venv/bin/python -m pytest tests/  （pytest 未導入なら直接実行も可）
"""
import asyncio
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet  # noqa: E402

from config import GuildConfig  # noqa: E402
from repositories.todoist_config_repository import TodoistConfigRepository  # noqa: E402
from services.todoist_service import TodoistServiceManager  # noqa: E402
from utils import crypto  # noqa: E402
from utils.db import SCHEMA_VERSION, Database  # noqa: E402
from utils.permissions import Level, get_level, has_level  # noqa: E402

G1 = 100000000000000001
G2 = 200000000000000002

TEST_KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
PLAIN_TOKEN = "test-todoist-token-0123456789abcdef"


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def run(coro):
    return asyncio.run(coro)


async def _connected_db() -> Database:
    db = Database(_tmp_db_path())
    await db.connect()
    return db


def _use_key(key: str | None) -> None:
    if key is None:
        os.environ.pop("ENCRYPTION_KEY", None)
    else:
        os.environ["ENCRYPTION_KEY"] = key
    crypto.reset_cache()


def teardown_function():
    _use_key(None)


# ---------------------------------------------------------------------
# crypto
# ---------------------------------------------------------------------
def test_encrypt_decrypt_roundtrip():
    _use_key(TEST_KEY)
    cipher = crypto.encrypt_token(PLAIN_TOKEN)
    assert cipher != PLAIN_TOKEN
    assert PLAIN_TOKEN not in cipher
    assert crypto.decrypt_token(cipher) == PLAIN_TOKEN
    assert crypto.is_encryption_ready() is True


def test_missing_and_invalid_key():
    _use_key(None)
    assert crypto.is_encryption_ready() is False
    try:
        crypto.encrypt_token(PLAIN_TOKEN)
        assert False, "未設定時は例外になるべき"
    except crypto.EncryptionKeyMissingError:
        pass

    _use_key("not-a-fernet-key")
    assert crypto.is_encryption_ready() is False
    try:
        crypto.encrypt_token(PLAIN_TOKEN)
        assert False, "不正鍵は例外になるべき"
    except crypto.EncryptionKeyMissingError:
        pass


def test_wrong_key_decrypt_fails_without_leak():
    _use_key(OTHER_KEY)
    cipher = crypto.encrypt_token(PLAIN_TOKEN)
    _use_key(TEST_KEY)
    try:
        crypto.decrypt_token(cipher)
        assert False, "鍵不一致は例外になるべき"
    except crypto.TokenDecryptError as e:
        # 例外メッセージに平文・暗号文・鍵を含まない
        msg = str(e)
        assert PLAIN_TOKEN not in msg
        assert cipher not in msg
        assert TEST_KEY not in msg and OTHER_KEY not in msg


# ---------------------------------------------------------------------
# スキーマ（v4）
# ---------------------------------------------------------------------
def test_schema_v4_has_todoist_configs():
    async def _main():
        db = await _connected_db()
        try:
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(todoist_configs)")}
            assert {"guild_id", "api_token_encrypted", "project_id", "today_label_name",
                    "enabled_flag", "created_by", "created_at", "updated_at"} <= cols
            row = await db.fetchone("PRAGMA user_version")
            assert row[0] == SCHEMA_VERSION
            # 安全ビューに暗号文列が含まれない
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(v_todoist_status)")}
            assert "api_token_encrypted" not in cols
            assert "guild_id" in cols
        finally:
            await db.close()
    run(_main())


# ---------------------------------------------------------------------
# リポジトリ: 暗号化保存・ギルド分離・平文が DB に残らない
# ---------------------------------------------------------------------
def test_repository_isolation_and_no_plaintext_in_db():
    async def _main():
        _use_key(TEST_KEY)
        path = _tmp_db_path()
        db = Database(path)
        await db.connect()
        try:
            repo = TodoistConfigRepository(db)
            await repo.upsert(G1, crypto.encrypt_token(PLAIN_TOKEN),
                              "proj1", "今日やること", "admin1")
            await repo.upsert(G2, crypto.encrypt_token("g2-token-xyz"),
                              None, "今日やること", "admin2")

            c1 = await repo.get(G1)
            c2 = await repo.get(G2)
            assert c1["project_id"] == "proj1"
            assert c2["project_id"] is None
            # 暗号文は平文と異なり、互いにも異なる
            assert c1["api_token_encrypted"] != PLAIN_TOKEN
            assert c1["api_token_encrypted"] != c2["api_token_encrypted"]
            # 復号はギルドごとに正しく行える
            assert crypto.decrypt_token(c1["api_token_encrypted"]) == PLAIN_TOKEN
            assert crypto.decrypt_token(c2["api_token_encrypted"]) == "g2-token-xyz"
            # 削除はギルド単位
            assert await repo.delete(G2) is True
            assert await repo.get(G2) is None
            assert await repo.get(G1) is not None
            assert await repo.delete(G2) is False
        finally:
            await db.close()

        # DB ファイル全体に平文トークンが残っていないこと
        with open(path, "rb") as f:
            content = f.read()
        assert PLAIN_TOKEN.encode() not in content
    run(_main())


# ---------------------------------------------------------------------
# TodoistServiceManager
# ---------------------------------------------------------------------
def test_manager_for_guild_resolution():
    async def _main():
        _use_key(TEST_KEY)
        db = await _connected_db()
        try:
            mgr = TodoistServiceManager(db)
            repo = TodoistConfigRepository(db)

            # 未登録ギルド → 無効サービス
            svc = await mgr.for_guild(G1)
            assert svc.enabled is False
            assert await mgr.is_configured(G1) is False

            # 登録 → 有効サービス（SDK 有無に依存するため API インスタンスの有無は問わない）
            await repo.upsert(G1, crypto.encrypt_token(PLAIN_TOKEN),
                              "proj1", "マイラベル", "admin1")
            svc = await mgr.for_guild(G1)
            assert svc.project_id == "proj1"
            assert svc.label_name == "マイラベル"
            assert await mgr.is_configured(G1) is True

            # 他ギルドは影響なし
            svc2 = await mgr.for_guild(G2)
            assert svc2.enabled is False
        finally:
            await db.close()
    run(_main())


def test_manager_decrypt_failure_disables_without_leak(caplog=None):
    async def _main():
        _use_key(OTHER_KEY)
        cipher = crypto.encrypt_token(PLAIN_TOKEN)
        _use_key(TEST_KEY)  # 別鍵に差し替わった状態を再現
        db = await _connected_db()
        try:
            repo = TodoistConfigRepository(db)
            await repo.upsert(G1, cipher, None, "今日やること", "admin1")
            mgr = TodoistServiceManager(db)
            with _caplog_records() as records:
                svc = await mgr.for_guild(G1)
            assert svc.enabled is False
            # ログに平文・暗号文・鍵が出ていないこと
            for rec in records:
                assert PLAIN_TOKEN not in rec
                assert cipher not in rec
                assert TEST_KEY not in rec and OTHER_KEY not in rec
        finally:
            await db.close()
    run(_main())


class _caplog_records:
    """logging の出力を捕捉する簡易ハンドラ（pytest 非依存）。"""

    def __init__(self):
        self.records: list[str] = []

    def __enter__(self):
        root = logging.getLogger()

        class _H(logging.Handler):
            def emit(_, record):
                self.records.append(record.getMessage())

        self._handler = _H()
        root.addHandler(self._handler)
        return self.records

    def __exit__(self, *args):
        logging.getLogger().removeHandler(self._handler)


# ---------------------------------------------------------------------
# 権限ロジック（admin_role_id 未設定でもオーナー/Administrator が L4）
# ---------------------------------------------------------------------
class _Perms:
    def __init__(self, administrator: bool = False):
        self.administrator = administrator


class _Guild:
    def __init__(self, owner_id: int):
        self.owner_id = owner_id


class _Role:
    def __init__(self, role_id: int):
        self.id = role_id


class _Member:
    def __init__(self, user_id: int, role_ids=(), administrator=False, owner_id=999):
        self.id = user_id
        self.roles = [_Role(r) for r in role_ids]
        self.guild = _Guild(owner_id)
        self.guild_permissions = _Perms(administrator)


def test_permission_levels_for_admin_commands():
    gconf = GuildConfig(guild_id=G1)  # admin_role_id 未設定の初期状態
    # 一般メンバーは L4 ではない（/todoist-setup 等は拒否される）
    assert has_level(_Member(1), gconf, Level.L4) is False
    # admin_role_id 未設定でもサーバーオーナーは L4
    assert has_level(_Member(999, owner_id=999), gconf, Level.L4) is True
    # admin_role_id 未設定でも Discord 管理者権限（Administrator）は L4
    assert has_level(_Member(2, administrator=True), gconf, Level.L4) is True
    # admin_role_id 設定時はそのロール所持者が L4
    gconf2 = GuildConfig(guild_id=G1, admin_role_id=555)
    assert get_level(_Member(3, role_ids=(555,)), gconf2) == Level.L4
    assert get_level(_Member(4, role_ids=(556,)), gconf2) == Level.L1


# ---------------------------------------------------------------------
# 移行スクリプト
# ---------------------------------------------------------------------
def test_migration_script_encrypts_and_deletes_plaintext():
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        import migrate_todoist_token as mig
    finally:
        sys.path.remove(scripts_dir)

    _use_key(TEST_KEY)
    path = _tmp_db_path()

    async def _prepare():
        db = Database(path)
        await db.connect()
        await db.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'TODOIST_API_TOKEN', ?)", (G1, PLAIN_TOKEN))
        await db.execute(
            "INSERT INTO settings (guild_id, setting_key, setting_value)"
            " VALUES (?, 'TODOIST_PROJECT_ID', 'proj-legacy')", (G1,))
        await db.close()

    run(_prepare())

    os.environ["DB_PATH"] = path
    try:
        run(mig.main(apply=True))
    finally:
        os.environ.pop("DB_PATH", None)

    async def _verify():
        db = Database(path)
        await db.connect()
        try:
            repo = TodoistConfigRepository(db)
            cfg = await repo.get(G1)
            assert cfg is not None
            assert cfg["project_id"] == "proj-legacy"
            # 暗号化されており復号できる
            assert cfg["api_token_encrypted"] != PLAIN_TOKEN
            assert crypto.decrypt_token(cfg["api_token_encrypted"]) == PLAIN_TOKEN
            # 平文の settings キーは削除されている
            row = await db.fetchone(
                "SELECT COUNT(*) AS c FROM settings"
                " WHERE guild_id = ? AND setting_key LIKE 'TODOIST_%'", (G1,))
            assert row["c"] == 0
        finally:
            await db.close()

    run(_verify())

    # DB ファイル全体に平文トークンが残っていないこと
    with open(path, "rb") as f:
        content = f.read()
    assert PLAIN_TOKEN.encode() not in content


if __name__ == "__main__":
    test_encrypt_decrypt_roundtrip()
    print("test_encrypt_decrypt_roundtrip: OK")
    test_missing_and_invalid_key()
    print("test_missing_and_invalid_key: OK")
    test_wrong_key_decrypt_fails_without_leak()
    print("test_wrong_key_decrypt_fails_without_leak: OK")
    test_schema_v4_has_todoist_configs()
    print("test_schema_v4_has_todoist_configs: OK")
    test_repository_isolation_and_no_plaintext_in_db()
    print("test_repository_isolation_and_no_plaintext_in_db: OK")
    test_manager_for_guild_resolution()
    print("test_manager_for_guild_resolution: OK")
    test_manager_decrypt_failure_disables_without_leak()
    print("test_manager_decrypt_failure_disables_without_leak: OK")
    test_permission_levels_for_admin_commands()
    print("test_permission_levels_for_admin_commands: OK")
    test_migration_script_encrypts_and_deletes_plaintext()
    print("test_migration_script_encrypts_and_deletes_plaintext: OK")
    print("全テスト成功")
