-- テスト専用データベースを作成する（業務 DB clubdb とは分離）。
-- tests/test_db_postgres.py のライブテスト（CLUB_TEST_PG_DSN）は
-- この DB を指すこと。本番データへの影響を防ぐ。
-- docker-entrypoint-initdb.d により、postgres コンテナの初回起動時のみ実行される。
CREATE DATABASE clubbot_test;
