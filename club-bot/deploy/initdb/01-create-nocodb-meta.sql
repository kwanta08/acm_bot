-- NocoDB 自身のメタデータ DB を作成する（業務 DB とは分離）。
-- docker-entrypoint-initdb.d により、postgres コンテナの初回起動時のみ実行される。
CREATE DATABASE nocodb_meta;
