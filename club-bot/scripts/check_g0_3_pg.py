"""G0-3: PostgreSQL でダッシュボードの行編集（row_id）が通るかを判定する。

背景:
    dashboard/routers/tables.py の PATCH ハンドラは row_id を **str** で受け取り、
    repositories/table_repository.py がそれをそのままバインド値にする。
    utils/db.py の _prepare() は「? → $N」の書き換えしかせず型変換をしない。
    ホワイトリスト7表のうち6表は主キーが BIGINT（TEXT は schedules だけ）。

    asyncpg はパラメータをクライアント側で型どおりにエンコードするため、
    int8 のパラメータに Python の str を渡すと Bind の時点で拒否される可能性がある。
    SQLite は型親和性で通ってしまうので、既存テスト（SQLite のみ）では検出できない。

    **psql では代用できない。** psql はパラメータをテキスト形式で送るため
    サーバー側が '5' → 5 に暗黙変換して通ってしまい、偽の「問題なし」が出る。
    問題は asyncpg のクライアント側エンコーダにある。

判定の読み方:
    [1] は **ドライバ仕様の確認**であって合否ではない。NG が正常。
        club-bot のコードは1行も関与しないので、アプリを直しても NG のまま。
    [2] が判定対象。ここが OK になれば修正済み。
        接続先 DB 名に "test" が含まれない場合はスキップされ、判定不能になる。

使い方:
    cd club-bot
    venv\\Scripts\\python.exe scripts\\check_g0_3_pg.py postgresql://user:pass@127.0.0.1:5432/clubbot_test

    接続先は **テスト用 DB** を指すこと（一時テーブルしか作らないが念のため）。
    PostgreSQL が手元に無ければ:
        cd deploy && docker compose -f docker-compose.postgres.yml up -d
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


async def check_raw_asyncpg(dsn: str) -> tuple[bool, str]:
    """asyncpg に直接、int8 列へ str を渡す。"""
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        await con.execute(
            "CREATE TEMP TABLE g03_probe (member_id bigint PRIMARY KEY, guild_id bigint)"
        )
        await con.execute("INSERT INTO g03_probe VALUES (5, 111)")
        try:
            row = await con.fetchrow(
                "SELECT * FROM g03_probe WHERE guild_id = $1 AND member_id = $2", 111, "5"
            )
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__module__}.{type(e).__name__}: {e}"
        return True, f"通った（row={dict(row) if row else None}）"
    finally:
        await con.close()


async def _db_name(dsn: str) -> str:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchval("SELECT current_database()")
    finally:
        await con.close()


async def check_real_code_path(dsn: str) -> tuple[bool | None, str]:
    """本物の Database / TableRepository 経由で get_row を呼ぶ。

    ダッシュボードの PATCH は update_row より先に get_row を呼ぶので、
    落ちるならここで落ちる（＝部分書き込みは起きず 500 になる）。

    **注意: Database.connect() はマイグレーションを走らせて全テーブルを作る。**
    本番 DB を誤って触らないよう、DB 名に "test" を含む場合だけ実行する
    （tests/test_db_postgres.py の _guarded_dsn と同じ方針）。
    """
    from repositories.table_repository import TableRepository
    from utils.db import Database

    name = await _db_name(dsn)
    if "test" not in name:
        return None, (
            f"スキップ（接続先 DB 名 '{name}' に 'test' が含まれていない）。\n"
            "    Database.connect() はマイグレーションで全テーブルを作るため、"
            "テスト専用 DB でのみ実行する。\n"
            "    **判定はできない。** テスト専用 DB を指して再実行すること。"
        )

    db = Database("unused.db", database_url=dsn)
    await db.connect()
    try:
        repo = TableRepository(db).for_guild(111)
        try:
            await repo.get_row("members", "5")  # ← 文字列の row_id
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__module__}.{type(e).__name__}: {e}"
        return True, "通った（get_row が例外を投げなかった）"
    finally:
        await db.close()


HOW_TO_START_PG = """\
PostgreSQL に接続できませんでした。判定はまだ行われていません。

手元に PostgreSQL が無い場合、使い捨てのコンテナを1つ立てるのが速いです。

  docker version                      # まず Docker が動いているか確認
  docker run -d --name pg-g03 -p 5432:5432 ^
    -e POSTGRES_USER=clubbot -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=clubbot_test ^
    postgres:16

  venv\\Scripts\\python.exe scripts\\check_g0_3_pg.py ^
    postgresql://clubbot:pw@127.0.0.1:5432/clubbot_test

  docker rm -f pg-g03                 # 判定が終わったら破棄

Docker が無い場合:
  winget install PostgreSQL.PostgreSQL.16
  （インストール時に設定した postgres ユーザーのパスワードで DSN を組み立て、
    先に  createdb -U postgres clubbot_test  でテスト用 DB を作る）

リポジトリの deploy/docker-compose.postgres.yml でも立てられますが、
deploy/.env に POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB が必要です。
"""


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("エラー: DSN を引数で指定してください。")
        return 2
    dsn = sys.argv[1]

    print("=" * 70)
    print("G0-3: PostgreSQL で row_id(str) が BIGINT 主キーに通るか")
    print("=" * 70)

    if "user:pass@" in dsn:
        print("\nエラー: DSN が例のままです（user:pass）。実際の値に置き換えてください。")
        print(HOW_TO_START_PG)
        return 2

    try:
        ok_raw, detail_raw = await check_raw_asyncpg(dsn)
    except OSError as e:
        print(f"\n接続失敗: {type(e).__name__}: {e}")
        print(HOW_TO_START_PG)
        return 2
    except Exception as e:  # noqa: BLE001
        # 認証失敗・DB 不在などは asyncpg の例外（OSError ではない）
        print(f"\n接続失敗: {type(e).__module__}.{type(e).__name__}: {e}")
        print(HOW_TO_START_PG)
        return 2
    # ------------------------------------------------------------------
    # [1] はドライバ仕様の確認であって、判定材料ではない。
    #
    # asyncpg へ直接 str を渡す探針には club-bot のコードが1行も関与しない。
    # アプリ側をどう直しても NG のままなので、これを合否に使うと
    # 「直したのに永久に落ちたまま」になる（当初の受入基準4の誤り）。
    # 判定は [2]（本物のコード経路）だけで行う。
    # ------------------------------------------------------------------
    print("\n[1] ドライバ仕様の確認（asyncpg へ直接 str を渡す）— 判定には使わない")
    print("    期待値は NG。asyncpg は int8 の引数に str を受け付けない。")
    print("    これはドライバの仕様なので、アプリを修正しても変わらない。")
    print(f"    結果: {'NG（想定どおり）' if not ok_raw else '★OK（想定外。環境を確認すること）'}")
    print(f"    {detail_raw}")

    try:
        ok_real, detail_real = await check_real_code_path(dsn)
        label = {True: "OK", False: "NG", None: "判定不能"}[ok_real]
        print(f"\n[2] 本物のコード経路（Database → TableRepository.get_row）: {label}  ← 判定対象")
        print(f"    {detail_real}")
    except Exception as e:  # noqa: BLE001
        ok_real = None
        print(f"\n[2] 本物のコード経路: 判定不能（{type(e).__name__}: {e}）")
        print("    スキーマ未作成などの環境要因。テスト専用 DB を指して再実行すること。")

    print("\n" + "=" * 70)
    if ok_real is None:
        print("判定: できませんでした。[2] が走っていません。")
        print("     DB 名に 'test' を含むテスト専用 DB を指して再実行してください。")
        return 2
    if ok_real is False:
        print("判定: 落ちた → G1-0『row_id を主キーの型へ変換する』を Phase G1 の先頭に起票")
        print("     上の例外種別とメッセージをそのまま完了ログへ貼ること")
        return 1
    print("判定: 通った → G0-3 に『PostgreSQL でも緑』と記録してクローズ")
    print("     [1] が NG なのは正常。ドライバは str を受け付けないままでよく、")
    print("     アプリ側が主キーの型へ変換していることを [2] が示している。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
