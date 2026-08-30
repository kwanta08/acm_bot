"""
データベース層（マルチテナント版 / SQLite・PostgreSQL 両対応）

- ローカル開発・テスト: SQLite（aiosqlite、DB_PATH）
- 本番（NocoDB 構成）: PostgreSQL（asyncpg、DATABASE_URL）
  DATABASE_URL が設定されていれば PostgreSQL、未設定なら SQLite に接続する。

マルチテナント化:
- 全テーブルに guild_id カラム（Discord ギルド ID, 64bit 整数）を保持する。
  SQLite 上は INTEGER、PostgreSQL 上は BIGINT（to_pg_ddl() で変換）。
  CHECK (guild_id >= 0) で負値を排除している。
- 既存 SQLite DB は _migrate() がテーブル再作成方式で guild_id を
  バックフィルする（バックフィル値は環境変数 GUILD_ID、未設定時は 0）。
- スキーマバージョンは SQLite では PRAGMA user_version、PostgreSQL では
  schema_meta テーブルに記録し、_migrate_versioned() が冪等に適用する。
- リポジトリ層の SQL は SQLite 方言（? プレースホルダ）に統一し、
  PostgreSQL 利用時は本モジュールが $n へ変換する。
"""

from __future__ import annotations

import os
import re

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")

try:
    import asyncpg
except ImportError:  # asyncpg 未導入でも SQLite だけは動くように
    asyncpg = None  # type: ignore

# Discord ギルド ID のカラム型。
# SQLite: INTEGER（8バイト符号付き）。PostgreSQL 移行時は BIGINT に読み替える。
GUILD_ID_TYPE = "INTEGER"

# guild_id カラム定義（CHECK で 0 以上に限定し、BIGINT 相当の非負整数を保証）
_GUILD_COL = f"guild_id {GUILD_ID_TYPE} NOT NULL CHECK (guild_id >= 0)"

# settings 更新をプロセス間で伝えるための PostgreSQL 通知チャンネル名。
# ダッシュボード（別プロセス）の更新を bot の config キャッシュへ伝播させる。
SETTINGS_CHANNEL = "clubbot_settings"

# ---------------------------------------------------------------------------
# asyncpg コネクションプールの既定値
#
# bot とダッシュボードは別プロセスで、それぞれ独立したプールを持つ。
# 旧既定の max_size=5 は、20分ごとの同期ジョブとダッシュボードの同時読み取りが
# 重なると枯渇しうるため引き上げた（設計方針 2.2）。
#
# 目安: PostgreSQL の max_connections（既定 100）に対し
#   bot(10) + ダッシュボード(10) + LISTEN 用(1) + 保守用の余裕
# で十分収まる。小さな VPS で絞りたい場合は環境変数で下げられる。
# ---------------------------------------------------------------------------
DEFAULT_POOL_MIN_SIZE = 1
DEFAULT_POOL_MAX_SIZE = 10
# 1クエリの上限時間（秒）。異常時に接続を握り続けないための保険
POOL_COMMAND_TIMEOUT = 30.0
# 使われていない接続を解放するまでの秒数（PostgreSQL 側の接続数を節約）
POOL_MAX_INACTIVE_LIFETIME = 300.0


def resolve_pool_size(
    min_size: int | None, max_size: int | None, env: dict[str, str] | None = None
) -> tuple[int, int]:
    """プールサイズを決める（引数 > 環境変数 > 既定値）。

    min <= max、いずれも 1 以上になるよう正規化する。
    """
    src = os.environ if env is None else env

    def _from_env(name: str, fallback: int) -> int:
        raw = (src.get(name) or "").strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    resolved_min = (
        min_size if min_size is not None else _from_env("DB_POOL_MIN_SIZE", DEFAULT_POOL_MIN_SIZE)
    )
    resolved_max = (
        max_size if max_size is not None else _from_env("DB_POOL_MAX_SIZE", DEFAULT_POOL_MAX_SIZE)
    )
    resolved_min = max(1, resolved_min)
    resolved_max = max(1, resolved_max)
    resolved_min = min(resolved_min, resolved_max)
    return resolved_min, resolved_max


# ---------------------------------------------------------------------------
# テーブル定義（テーブル名 → CREATE TABLE 文）
# init_schema と既存 DB のマイグレーション（テーブル再作成）の両方から参照する。
# すべてのテーブルが guild_id を保持し、複合キー/ユニーク制約の先頭に置く。
# ---------------------------------------------------------------------------
TABLE_DDL: dict[str, str] = {
    # ギルド台帳。guild_id がそのまま PK（唯一 guild_id をカラムとして持たない
    # 形の例外ではなく、PK 自体が guild_id）。正のギルド ID のみ許可する。
    "guilds": """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id      INTEGER PRIMARY KEY CHECK (guild_id > 0),
    guild_name    TEXT NOT NULL,
    joined_at     TEXT NOT NULL,
    setup_version INTEGER NOT NULL DEFAULT 2,
    -- 退出日時と自動削除の予定日時（参加中はどちらも NULL）。
    -- 退出しただけではデータを消さず、purge_after を過ぎたものだけを消す。
    left_at       TEXT,
    purge_after   TEXT
);
""",
    "settings": f"""
CREATE TABLE IF NOT EXISTS settings (
    {_GUILD_COL},
    setting_key   TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (guild_id, setting_key)
);
""",
    "teams": f"""
CREATE TABLE IF NOT EXISTS teams (
    team_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    team_key          TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    leader_role_id    TEXT,
    member_role_id    TEXT,
    secondary_role_id TEXT,
    channel_id        TEXT,
    active_flag       INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT,
    updated_at        TEXT,
    UNIQUE (guild_id, team_key)
);
""",
    # member_id は外部 DB UI（Directus 等）向けの代理主キー。
    # Directus は単一列の主キーを必須とするため、業務上の自然キー
    # (guild_id, user_id) は UNIQUE 制約として維持しつつ代理キーを持つ。
    # リポジトリ層の参照・更新は従来どおり (guild_id, user_id) で行う。
    "members": f"""
CREATE TABLE IF NOT EXISTS members (
    member_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    user_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    primary_team    TEXT,
    secondary_teams TEXT,
    is_leader       INTEGER NOT NULL DEFAULT 0,
    skills          TEXT,
    notes           TEXT,
    joined_at       TEXT NOT NULL,
    active_flag     INTEGER NOT NULL DEFAULT 1,
    -- 在籍状態（active / alumni / inactive）。年度替わりの仕分けで使う。
    -- 卒業しても行は消さない（過去の作業記録の担当者名が壊れるため）。
    status          TEXT NOT NULL DEFAULT 'active',
    -- 卒業した年度の名前（seasons.name）。在籍中は NULL
    left_season     TEXT,
    UNIQUE (guild_id, user_id)
);
""",
    "schedules": f"""
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id        TEXT PRIMARY KEY,
    {_GUILD_COL},
    title              TEXT NOT NULL,
    description        TEXT,
    place              TEXT,
    target_role_id     TEXT,
    deadline           TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    channel_id         TEXT NOT NULL,
    closed_flag        INTEGER NOT NULL DEFAULT 0,
    reminder_sent_flag INTEGER NOT NULL DEFAULT 0,
    sheet_title        TEXT,
    -- 論理削除（v17）。1 なら一覧・集計・催促から外す。票は消さない
    -- （/schedule delete は投票メッセージを消すが、票データは残す）
    deleted_flag       INTEGER NOT NULL DEFAULT 0,
    -- 確定した候補（v17。schedule_options.option_id）。NULL は未確定
    confirmed_option_id TEXT
);
""",
    "schedule_options": f"""
CREATE TABLE IF NOT EXISTS schedule_options (
    option_id   TEXT PRIMARY KEY,
    {_GUILD_COL},
    schedule_id TEXT NOT NULL,
    label       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    message_id  TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(schedule_id) ON DELETE CASCADE
);
""",
    "schedule_votes": f"""
CREATE TABLE IF NOT EXISTS schedule_votes (
    vote_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    option_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (guild_id, option_id, user_id),
    FOREIGN KEY (option_id) REFERENCES schedule_options(option_id) ON DELETE CASCADE
);
""",
    "tasks": f"""
CREATE TABLE IF NOT EXISTS tasks (
    local_task_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    todoist_task_id TEXT,
    title           TEXT NOT NULL,
    assignee_id     TEXT,
    team_key        TEXT,
    due_date        TEXT,
    priority        INTEGER,
    location_key    TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);
""",
    "reminders_log": f"""
CREATE TABLE IF NOT EXISTS reminders_log (
    reminder_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    reminder_type  TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    target_user_id TEXT,
    sent_channel_id TEXT,
    sent_at        TEXT NOT NULL,
    status         TEXT NOT NULL,
    error_message  TEXT
);
""",
    "todoist_sections": f"""
CREATE TABLE IF NOT EXISTS todoist_sections (
    {_GUILD_COL},
    section_id   TEXT NOT NULL,
    team_key     TEXT NOT NULL,
    section_name TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (guild_id, section_id)
);
""",
    # layer_num は TEXT。/layer start の層番号は数字のほか「シュリンク」等の
    # テキストを受け付ける仕様（layer_records.layer_num と同じ）。
    "layer_sessions": f"""
CREATE TABLE IF NOT EXISTS layer_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    user_id    TEXT NOT NULL,
    keta       TEXT NOT NULL,
    layer_num  TEXT NOT NULL,
    started_at TEXT NOT NULL,
    UNIQUE (guild_id, user_id)
);
""",
    "layer_records": f"""
CREATE TABLE IF NOT EXISTS layer_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    user_id     TEXT NOT NULL,
    keta        TEXT NOT NULL,
    layer_num   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    minutes     INTEGER NOT NULL,
    synced_flag INTEGER NOT NULL DEFAULT 0
);
""",
    "layer_keta": f"""
CREATE TABLE IF NOT EXISTS layer_keta (
    keta_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    keta_name   TEXT NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (guild_id, keta_name)
);
""",
    # 監査ログ（管理者操作の証跡。機密値は保存しない運用）
    "audit_log": f"""
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    actor_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);
""",
    # 技能タグ マスタ（ギルド別。名前はギルド内で一意）
    "skill_tags": f"""
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    skill_name   TEXT NOT NULL,
    active_flag  INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (guild_id, skill_name)
);
""",
    # Todoist 接続設定（1ギルド1件。トークンは Fernet 暗号文で保存し、
    # 平文は保存しない。専用テーブルとすることで NocoDB 等の外部 UI で
    # テーブル単位の非表示・アクセス制限ができる）
    "todoist_configs": """
CREATE TABLE IF NOT EXISTS todoist_configs (
    guild_id            INTEGER PRIMARY KEY CHECK (guild_id > 0),
    api_token_encrypted TEXT NOT NULL,
    project_id          TEXT,
    today_label_name    TEXT NOT NULL DEFAULT '今日やること',
    enabled_flag        INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
""",
    # Directus（外部 DB UI）のギルド別アクセス発行状況（1ギルド1件）。
    # 秘密情報は持たない: パスワードは Directus のメール招待フローに委ね、
    # bot は生成も保存もしない。directus_user_id は Directus 側のユーザー
    # （UUID）で、これ自体は認証に使えない。
    # 専用テーブルとすることで外部 UI 側でテーブル単位に非表示にできる。
    #
    # 命名の注意: Directus は業務 DB と同じデータベースへ自身のシステム
    # テーブルを作成し、その名前空間は `directus_` プレフィックスで予約
    # されている。特に Directus 11 の `directus_access`（ユーザー/ロールと
    # ポリシーの紐付け）と名前が衝突すると、Directus の初回セットアップが
    # 自身のシステムテーブルへ INSERT する際に本テーブルの NOT NULL 制約に
    # 掛かり、"Value for field \"guild_id\" in collection \"directus_access\"
    # can't be null" で初期化不能になる。そのため本テーブルは
    # `directus_` で始まらない名前とすること。
    "guild_directus_access": """
CREATE TABLE IF NOT EXISTS guild_directus_access (
    guild_id         INTEGER PRIMARY KEY CHECK (guild_id > 0),
    directus_user_id TEXT NOT NULL,
    email            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'invited',
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
""",
    # 機体進捗ツリー（機体 → パーツ → 部品 → … の隣接リスト。深さ無制限）。
    #
    # /progress の正本を Google Sheets から DB へ移した中核テーブル
    # （migrations/009。スキーマ v10）。中央スプレッドシートを複数ギルドで
    # 共有していた旧構成と異なり、**ツリーはギルドごとに独立**する。
    #
    # - node_id: ギルド内で一意な業務キー（`pj_<プロジェクトID>` /
    #   `td_<タスクID>` / 人手入力の任意文字列）。表示・親子参照に使う
    # - parent_id: 同一ギルドの node_id。ルート（機体）は NULL
    # - parent_id に外部キーを張らないのは、移行・同期で親より先に子が
    #   入る順序を許すため。孤児・循環は services/progress_tree.py が
    #   検出してツリーから除外し、#bot-log へ通知する
    # - manual_progress: 0.0〜1.0。NULL は未入力（葉なら 0 として集計）
    # - source: manual / todoist / spar_winding。manual 行は同期処理が
    #   上書きしない
    "progress_nodes": f"""
CREATE TABLE IF NOT EXISTS progress_nodes (
    progress_node_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    node_id         TEXT NOT NULL,
    parent_id       TEXT,
    sort_order      REAL NOT NULL DEFAULT 0,
    name            TEXT NOT NULL DEFAULT '',
    assignee        TEXT,
    status          TEXT,
    manual_progress REAL,
    source          TEXT NOT NULL DEFAULT 'manual',
    todoist_task_id TEXT,
    weight          REAL NOT NULL DEFAULT 1,
    -- 重量（グラム固定。単位設定は作らない）。NULL は未入力。
    -- 人力飛行機は重量が競技成績に直結するため、進捗と同じ木で積み上げる。
    target_weight_g REAL,
    actual_weight_g REAL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (guild_id, node_id)
);
""",
    # Todoist プロジェクト → 進捗ノードの紐付け（旧「Todoist対応表」シート）。
    # /progress setup が1行追加し、同期ジョブが参照する。
    # notify_channel_id が空のときは settings の
    # PROGRESS_DEFAULT_CHANNEL_ID → ギルド既定チャンネルへフォールバックする。
    "progress_todoist_links": f"""
CREATE TABLE IF NOT EXISTS progress_todoist_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    project_name      TEXT NOT NULL,
    node_id           TEXT NOT NULL,
    notify_channel_id TEXT,
    created_by        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (guild_id, project_name)
);
""",
    # 桁 → 進捗ノードの紐付け（旧「桁巻き対応表」シート ＋「桁マスタ」の目標層数）。
    # 桁巻きの完了層数は layer_records（/layer end の記録）から数えるため、
    # 別ブックの桁巻きスプレッドシートは不要になる。
    # keta_name は layer_keta.keta_name と同じ値を指す（FK は張らない。
    # 桁名の無効化と進捗の紐付けを独立に扱うため）。
    "progress_spar_links": f"""
CREATE TABLE IF NOT EXISTS progress_spar_links (
    spar_link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    keta_name     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    target_layers INTEGER NOT NULL CHECK (target_layers > 0),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (guild_id, keta_name)
);
""",
    # 大会からの逆算アラート用のマイルストーン（F4）。
    #
    # node_id は progress_nodes と同じく外部キーを張らない（同期・移行で
    # 親より先に子が入る順序を許す既存方針に合わせる）。存在しないノードを
    # 指す行は表示から除外する。
    # due_date は 'YYYY-MM-DD'。大会日そのものはギルド別設定
    # COMPETITION_DATE に持ち、既定値は持たない（大会も日程もサークルごとに違う）。
    "progress_milestones": f"""
CREATE TABLE IF NOT EXISTS progress_milestones (
    milestone_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    node_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    due_date     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (guild_id, node_id, name)
);
""",
    # 年度（世代）の境界（F5）。
    #
    # 全テーブルに season_id は張らない。既存の全テーブルへの列追加と
    # 全クエリの改修は後方互換のリスクが大きすぎるうえ、各記録には
    # created_at があるので年度での絞り込みは日付範囲で足りる。
    # ここで持つのは「年度の境界」と members.status（在籍状態）だけ。
    #
    # 「現役の年度」は ended_at IS NULL の最新1件。年度名に既定値は持たない
    # （「2026年度」も「第30代」もサークルごとに違う）。
    "seasons": f"""
CREATE TABLE IF NOT EXISTS seasons (
    season_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    name       TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (guild_id, name)
);
""",
    # 進捗の日次スナップショット（G4-7）。
    #
    # progress_nodes は「現在値」しか持たないため、ペースが
    # 「作成日→最終更新日の平均」でしか出せず、停滞期間を含まない近似に
    # なっていた（ADR 0022）。1日1行だけ積むことで実測の履歴を持つ。
    #
    # - snapshot_date は 'YYYY-MM-DD'。UNIQUE (guild_id, node_id, snapshot_date)
    #   が「1日1行」を**構造で**保証する（アプリ側の if に頼らない）
    # - aggregated / actual_weight_g は **NULL 許容**。未集計・未計測を
    #   0.0 に丸めない（ADR 0021）
    # - node_id に外部キーを張らない（progress_nodes と同じ既存方針。ADR 0019）。
    #   ノードが消えても過去の履歴は残る
    "progress_snapshots": f"""
CREATE TABLE IF NOT EXISTS progress_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    node_id         TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    aggregated      REAL,
    actual_weight_g REAL,
    UNIQUE (guild_id, node_id, snapshot_date)
);
""",
    # 資材・消耗品の在庫（G4-8）。
    #
    # 人力飛行機で最も痛いのは「プリプレグが無くて桁が巻けない」。
    # カーボンプリプレグは納期が数週間で、切れてから気づくと工程が1ヶ月ずれる。
    #
    # - **品目名の初期値はコードに持たない**（サークルごとに違う）。
    #   マスタ管理は layer_keta と同型（有効フラグ・(guild_id, name) で一意）
    # - threshold は **NULL 許容**。閾値を決めていない品目を 0 扱いにしない
    #   （0 にすると「在庫0でも閾値割れではない」という嘘になる。ADR 0021）
    # - quantity / threshold は REAL。「2.5 m」「0.5 L」のような単位を扱う
    # - low_notified_flag は「閾値割れの即時通知を送ったか」。
    #   閾値以上へ戻ったときに 0 へ戻すので、割り込むたびに1回だけ飛ぶ
    "stock_items": f"""
CREATE TABLE IF NOT EXISTS stock_items (
    stock_item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    item_name         TEXT NOT NULL,
    unit              TEXT NOT NULL DEFAULT '個',
    quantity          REAL NOT NULL DEFAULT 0,
    threshold         REAL,
    note              TEXT,
    active_flag       INTEGER NOT NULL DEFAULT 1,
    low_notified_flag INTEGER NOT NULL DEFAULT 0,
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (guild_id, item_name)
);
""",
    # 在庫の増減履歴（G4-8）。「誰がいつ何をどれだけ使ったか」を残す。
    # stock_item_id に外部キーを張らない（progress_nodes と同じ既存方針。
    # ADR 0019）。品目を消しても履歴は残る
    "stock_movements": f"""
CREATE TABLE IF NOT EXISTS stock_movements (
    movement_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    stock_item_id INTEGER NOT NULL,
    delta         REAL NOT NULL,
    reason        TEXT,
    user_id       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
""",
    # 工具・機材の貸出（G4-9）。
    #
    # `/layer start` → `/layer end` とまったく同じ「開始 → 進行中 → 終了」モデル。
    # マスタ（tools）と貸出（tool_loans）を分け、貸出中かどうかは
    # **tool_loans に returned_at が NULL の行があるか**で表す
    # （tools 側にフラグを置くと、行を消したときに貸出の事実まで消える）。
    #
    # - due_date は **NULL 許容**。返却予定日を決めていない貸出を
    #   「本日返却」にしない（ADR 0021）。督促は due_date がある貸出だけ
    # - tools は layer_keta と同型（有効フラグ・(guild_id, tool_name) で一意）
    "tools": f"""
CREATE TABLE IF NOT EXISTS tools (
    tool_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    tool_name   TEXT NOT NULL,
    note        TEXT,
    active_flag INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (guild_id, tool_name)
);
""",
    # 貸出1回ぶん。returned_at が NULL なら貸出中。
    # tool_id に外部キーを張らない（progress_nodes と同じ既存方針。ADR 0019）
    "tool_loans": f"""
CREATE TABLE IF NOT EXISTS tool_loans (
    loan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    tool_id       INTEGER NOT NULL,
    user_id       TEXT NOT NULL,
    borrowed_at   TEXT NOT NULL,
    due_date      TEXT,
    returned_at   TEXT,
    note          TEXT,
    overdue_notified_flag INTEGER NOT NULL DEFAULT 0
);
""",
    # ヒヤリハット・事故報告（G4-10）。
    #
    # 工房での切削・溶剤・高所作業・機体運搬・テストフライトと危険度が高く、
    # 大学から安全管理体制の提示を求められることもある。今は雑談に流れて消える。
    #
    # **匿名の扱いに2つの列を使う。**
    #   - reporter_id は匿名でも必ず保存する（悪用・虚偽報告への対処に要る）。
    #     ただし**表示にもエクスポートにも出さない**（TABLES の列ホワイトリスト
    #     から外してある。ADR 0016 の仕組みをそのまま使う）
    #   - reporter_name は「表示してよい名前」。匿名報告では NULL。
    #     表示側はこちらしか見ないので、匿名の約束が構造で守られる
    #
    # injury（けがの有無）は自由記述。「軽い擦り傷」「無し」など、
    # 選択肢に収まらない実態を書けるようにする。
    "incidents": f"""
CREATE TABLE IF NOT EXISTS incidents (
    incident_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    {_GUILD_COL},
    occurred_at   TEXT NOT NULL,
    place         TEXT NOT NULL,
    description   TEXT NOT NULL,
    injury        TEXT,
    prevention    TEXT,
    anonymous_flag INTEGER NOT NULL DEFAULT 0,
    reporter_id   TEXT NOT NULL,
    reporter_name TEXT,
    created_at    TEXT NOT NULL
);
""",
    # Discord の表示名キャッシュ（ギルド別）。bot がギルドキャッシュから
    # 書き込み、ダッシュボード（Bot トークンを持たない別プロセス）が
    # ID → 表示名の解決に読む。name はユーザーなら「そのギルドでの表示名」
    # （ニックネーム → グローバル表示名 → ユーザー名）、チャンネルなら
    # チャンネル名。データの正本ではなくキャッシュ（消えても再同期できる）。
    "discord_name_cache": f"""
CREATE TABLE IF NOT EXISTS discord_name_cache (
    {_GUILD_COL},
    entity_type TEXT NOT NULL CHECK (entity_type IN ('user', 'channel')),
    entity_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (guild_id, entity_type, entity_id)
);
""",
}

# インデックス（guild_id を先頭に含む複合インデックス）
INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_guilds_purge_after ON guilds(purge_after);
CREATE INDEX IF NOT EXISTS idx_teams_guild ON teams(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_members_guild ON members(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_schedules_guild ON schedules(guild_id, closed_flag, deadline);
CREATE INDEX IF NOT EXISTS idx_options_guild_schedule ON schedule_options(guild_id, schedule_id);
CREATE INDEX IF NOT EXISTS idx_votes_guild_option ON schedule_votes(guild_id, option_id);
CREATE INDEX IF NOT EXISTS idx_votes_option ON schedule_votes(option_id);
CREATE INDEX IF NOT EXISTS idx_tasks_guild_status ON tasks(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_guild ON reminders_log(guild_id, reminder_id);
CREATE INDEX IF NOT EXISTS idx_sections_guild_team ON todoist_sections(guild_id, team_key);
CREATE INDEX IF NOT EXISTS idx_layer_records_guild_synced ON layer_records(guild_id, synced_flag);
CREATE INDEX IF NOT EXISTS idx_layer_records_synced ON layer_records(synced_flag);
CREATE INDEX IF NOT EXISTS idx_audit_log_guild ON audit_log(guild_id, audit_id);
CREATE INDEX IF NOT EXISTS idx_skill_tags_guild ON skill_tags(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_progress_nodes_guild_parent ON progress_nodes(guild_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_progress_nodes_guild_source ON progress_nodes(guild_id, source);
CREATE INDEX IF NOT EXISTS idx_progress_todoist_links_guild ON progress_todoist_links(guild_id);
CREATE INDEX IF NOT EXISTS idx_progress_milestones_guild_due ON progress_milestones(guild_id, due_date);
CREATE INDEX IF NOT EXISTS idx_seasons_guild_ended ON seasons(guild_id, ended_at);
CREATE INDEX IF NOT EXISTS idx_members_guild_status ON members(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_progress_spar_links_guild ON progress_spar_links(guild_id);
CREATE INDEX IF NOT EXISTS idx_progress_snapshots_node ON progress_snapshots(guild_id, node_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_stock_items_guild ON stock_items(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_stock_movements_item ON stock_movements(guild_id, stock_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tools_guild ON tools(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_tool_loans_open ON tool_loans(guild_id, returned_at, tool_id);
CREATE INDEX IF NOT EXISTS idx_incidents_guild ON incidents(guild_id, occurred_at);
"""

# ---------------------------------------------------------------------------
# ビュー定義（NocoDB 等の外部 UI 向け。機密列を含まない安全な参照用）
#
# ビュー本体（SELECT 文）を1箇所に集約し、実行用 DDL はドライバ別に生成する:
#   SQLITE_VIEW_DDL   : DROP VIEW IF EXISTS + CREATE VIEW の安全な再作成方式
#                       （SQLite は CREATE OR REPLACE VIEW をサポートしない）
#   POSTGRES_VIEW_DDL : CREATE OR REPLACE VIEW
#                       （PostgreSQL は CREATE VIEW IF NOT EXISTS をサポートしない）
# 実行は両ドライバとも複数文をネイティブに処理する
# （aiosqlite executescript / asyncpg execute）ため、split(';') による
# 文字列分割は行わない。
# ---------------------------------------------------------------------------
_VIEW_BODIES: dict[str, str] = {
    # Todoist 連携状態（暗号文を含まない）
    "v_todoist_status": """
SELECT guild_id, project_id, today_label_name, enabled_flag, updated_at
FROM todoist_configs
""",
    # 出欠一覧（旧 Google Sheets の attendance シート相当。
    # 正本は schedule_votes / schedule_options / schedules）
    "v_attendance": """
SELECT s.guild_id,
       s.schedule_id,
       s.title       AS event_title,
       o.label       AS option_label,
       v.user_id,
       v.status,
       v.updated_at,
       s.deadline
FROM schedule_votes v
JOIN schedule_options o
  ON o.guild_id = v.guild_id AND o.option_id = v.option_id
JOIN schedules s
  ON s.guild_id = o.guild_id AND s.schedule_id = o.schedule_id
""",
    # 班サマリ（旧 Google Sheets の team_summary シート相当。正本は teams / members）
    "v_team_summary": """
SELECT t.guild_id,
       t.team_key,
       t.team_name,
       COUNT(m.user_id)              AS member_count,
       COALESCE(SUM(m.is_leader), 0) AS leader_count
FROM teams t
LEFT JOIN members m
  ON m.guild_id = t.guild_id
 AND m.primary_team = t.team_key
 AND m.active_flag = 1
WHERE t.active_flag = 1
GROUP BY t.guild_id, t.team_key, t.team_name
""",
}

SQLITE_VIEW_DDL = "\n".join(
    f"DROP VIEW IF EXISTS {name};\nCREATE VIEW {name} AS{body};"
    for name, body in _VIEW_BODIES.items()
)

POSTGRES_VIEW_DDL = "\n".join(
    f"CREATE OR REPLACE VIEW {name} AS{body};" for name, body in _VIEW_BODIES.items()
)

# スキーマバージョン（SQLite: PRAGMA user_version / PostgreSQL: schema_meta）。
# 1: guild_id 導入済みの初期マルチテナントスキーマ（旧版は user_version=0 として扱う）
# 2: guilds（ギルド台帳）・audit_log（監査ログ）追加
# 3: skill_tags 追加。teams に member_role_id / secondary_role_id /
#    created_at / updated_at を追加し、settings のロールマップをバックフィル
# 4: todoist_configs 追加（Todoist トークンのギルド別暗号化保存）
# 5: v_attendance / v_team_summary ビュー追加（Sheets 廃止に伴う NocoDB 表示用）
# 6: PostgreSQL の guild_id を BIGINT へ変更（int4 で作成された既存 DB の修復。
#    migrations/005_bigint_discord_ids.sql と同等の処理を自動適用。SQLite は no-op）
# 7: directus_access 追加（Directus のギルド別アクセス発行状況）
# 8: members に代理主キー member_id を追加（旧 PK (guild_id, user_id) は
#    UNIQUE 制約として維持）。単一列 PK を必須とする外部 DB UI（Directus）
#    から members を扱えるようにするため
# 9: directus_access を guild_directus_access へ改名（Directus 11 の
#    システムテーブル `directus_access` との名前衝突を解消。衝突すると
#    Directus の初回セットアップが失敗して起動できない）
# 10: progress_nodes / progress_todoist_links / progress_spar_links 追加
#    （/progress の正本を Google Sheets から DB へ移行。migrations/009）
# 11: guilds に left_at / purge_after を追加（サーバー退出後の猶予付き
#    自動削除。退出しただけでは消さない。migrations/010）
# 12: progress_nodes に target_weight_g / actual_weight_g を追加
#    （機体重量を進捗と同じツリーで積み上げる。migrations/011）
# 13: progress_milestones を追加（大会日からの逆算アラート。migrations/012）
# 14: seasons を追加し、members に status / left_season を追加
#    （年度替わり。既存メンバーはすべて active。migrations/013）
# 15: discord_name_cache を追加（ダッシュボードの ID → 表示名解決用。
#    bot がギルドキャッシュから書き、Web 側が読む。migrations/014）
# 21: incidents を追加（ヒヤリハット・事故報告。G4-10）
# 20: tools / tool_loans を追加（工具・機材の貸出。G4-9）
# 19: stock_items / stock_movements を追加（資材・消耗品の在庫。G4-8）
# 18: progress_snapshots を追加（進捗の日次履歴。G4-7）
# 16: layer_sessions.layer_num を INTEGER から TEXT へ変更（/layer start は
#    「シュリンク」等のテキスト層番号を受け付ける仕様。PostgreSQL では
#    asyncpg の DataError になっていた。migrations/015）
SCHEMA_VERSION = 22

# 改訂版スキーマ（マルチテナント版）。テーブル定義のみ。
SCHEMA = "\n".join(TABLE_DDL.values())

# マイグレーションの排他制御に使う advisory lock のキー（D2-6）。
#
# bot と dashboard が同じ DB へ同時に connect() すると
# _migrate_versioned() がレースする（デプロイ時の同時再起動で踏む）。
# PostgreSQL の pg_advisory_lock でプロセス間を直列化する。
# 値は b"clubbot1" の 64bit 表現（衝突しにくい固定値。意味は任意）
MIGRATION_ADVISORY_LOCK_KEY = int.from_bytes(b"clubbot1", "big")

# PostgreSQL: スキーマバージョン管理テーブル（SQLite は PRAGMA user_version を使用）
SCHEMA_META_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
"""

# SERIAL 相当の PK カラム（PostgreSQL の RETURNING / シーケンス修復に使用）
_PK_COLUMNS: dict[str, str] = {
    "teams": "team_id",
    "members": "member_id",
    "schedule_votes": "vote_id",
    "tasks": "local_task_id",
    "reminders_log": "reminder_id",
    "layer_sessions": "session_id",
    "layer_records": "record_id",
    "layer_keta": "keta_id",
    "audit_log": "audit_id",
    "skill_tags": "skill_tag_id",
    "progress_nodes": "progress_node_id",
    "progress_todoist_links": "link_id",
    "progress_spar_links": "spar_link_id",
    "progress_snapshots": "snapshot_id",
    "stock_items": "stock_item_id",
    "stock_movements": "movement_id",
    "tools": "tool_id",
    "tool_loans": "loan_id",
    "incidents": "incident_id",
}

_INSERT_TABLE_RE = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE)


def to_pg_ddl(sqlite_ddl: str) -> str:
    """SQLite 用 DDL を PostgreSQL 用に機械変換する。

    - INTEGER PRIMARY KEY AUTOINCREMENT → BIGINT GENERATED BY DEFAULT AS IDENTITY
      （明示的な ID 挿入を許可するため BY DEFAULT を使う。移行スクリプトが
      SQLite の ID をそのまま入れられる）
    - guild_id INTEGER → BIGINT（ギルド台帳の PK 含む。Discord Snowflake は
      64bit のため int4 では溢れる。アライメント（スペース数）に依存しない
      正規表現で変換する）
    - datetime('now', 'localtime') → to_char(CURRENT_TIMESTAMP, ...)（同じ書式）
    - REAL → DOUBLE PRECISION（PostgreSQL の REAL は 4 バイト float で
      SQLite の REAL（8 バイト IEEE754）より精度が低い。進捗率・重みを
      同じ精度で保持するため 8 バイトへ揃える）
    """
    s = sqlite_ddl.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    )
    s = _GUILD_ID_INT_RE.sub("guild_id BIGINT", s)
    s = _REAL_TYPE_RE.sub("DOUBLE PRECISION", s)
    s = s.replace(
        "datetime('now', 'localtime')", "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
    )
    return s


# guild_id の INTEGER 宣言をアライメント非依存で検出する
# （"guild_id INTEGER NOT NULL" / "guild_id            INTEGER PRIMARY KEY" 等）
_GUILD_ID_INT_RE = re.compile(r"\bguild_id\s+INTEGER\b")

# REAL 型宣言（PostgreSQL では DOUBLE PRECISION へ広げる）
_REAL_TYPE_RE = re.compile(r"\bREAL\b")


TABLE_DDL_PG: dict[str, str] = {name: to_pg_ddl(ddl) for name, ddl in TABLE_DDL.items()}


def legacy_guild_id() -> int:
    """
    既存単一サーバー運用のレガシー guild_id（環境変数 GUILD_ID）。
    未設定・不正値の場合は 0（レガシー/未帰属データ）を返す。
    """
    raw = (os.getenv("GUILD_ID") or "").strip().strip('"').strip("'")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


class _PgCursor:
    """aiosqlite.Cursor 相当のインターフェース（rowcount / lastrowid）。"""

    def __init__(self, rowcount: int, lastrowid: int | None = None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid


class Database:
    """
    SQLite / PostgreSQL 両対応の軽いラッパー。

    - database_url 指定時: PostgreSQL（asyncpg プール）
    - それ以外: SQLite（aiosqlite、path）
    リポジトリ層は SQLite 方言（? プレースホルダ）のまま利用できる。
    """

    def __init__(
        self,
        path: str,
        database_url: str | None = None,
        pool_min_size: int | None = None,
        pool_max_size: int | None = None,
    ):
        self.path = path
        self.database_url = (database_url or "").strip() or None
        self.pool_min_size, self.pool_max_size = resolve_pool_size(pool_min_size, pool_max_size)
        self._conn: aiosqlite.Connection | None = None
        self._pool = None  # asyncpg.Pool
        self._listener_conn = None  # asyncpg.Connection（LISTEN 専用）

    @property
    def _is_pg(self) -> bool:
        return self.database_url is not None

    @property
    def driver_name(self) -> str:
        """接続中の DB 種別（表示用）。"""
        return "PostgreSQL" if self._is_pg else "SQLite"

    # ------------------------------------------------------------------
    # 接続・スキーマ初期化
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._is_pg:
            await self._connect_pg()
            return

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        # NocoDB 等の外部ツールとの同時アクセスに備え、ロック待ちを許容する
        await self._conn.execute("PRAGMA busy_timeout = 5000;")
        await self.init_schema()
        await self._migrate()
        # インデックスは guild_id カラムの存在が確定した後に作成する
        await self._conn.executescript(INDEX_DDL)
        await self._conn.executescript(SQLITE_VIEW_DDL)
        await self._conn.commit()
        log.info("SQLite に接続しました: %s", self.path)

    async def _connect_pg(self) -> None:
        if asyncpg is None:
            raise RuntimeError(
                "DATABASE_URL が設定されていますが asyncpg がありません。"
                " pip install asyncpg を実行してください。"
            )
        self._pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size,
            command_timeout=POOL_COMMAND_TIMEOUT,
            max_inactive_connection_lifetime=POOL_MAX_INACTIVE_LIFETIME,
        )
        # テーブル作成（冪等）→ バージョン付きマイグレーション
        # → インデックス・ビュー → シーケンス修復
        #
        # インデックスとビューは必ずマイグレーションの**後**に作る。
        # CREATE TABLE IF NOT EXISTS は既にあるテーブルへ列を追加しないため、
        # 旧バージョンで作られた既存 DB には後から足した列
        # （guilds.purge_after / members.status など）がこの時点では無く、
        # それらを参照するインデックス作成が UndefinedColumnError で
        # 落ちて起動に失敗する。SQLite 側（connect()）も同じ順序。
        try:
            async with self._pool.acquire() as con:
                for name, ddl in TABLE_DDL_PG.items():
                    await self._pg_exec_ddl(con, f"table:{name}", ddl)
                await self._pg_exec_ddl(con, "table:schema_meta", SCHEMA_META_DDL)
            # マイグレーションの排他制御（D2-6）。ロックは**取得できるまで待つ**。
            # pg_try_advisory_lock で「取れなければ飛ばす」にすると、
            # 待たずに古いスキーマのまま起動して後で静かに壊れる。
            # advisory lock はセッション（接続）に紐づくため、**プール外の
            # 専用接続**で保持する。プールから借りると、_migrate_versioned()
            # 内のクエリも同じプールを使うため、DB_POOL_MAX_SIZE=1 の環境で
            # 唯一の接続をロック保持が占有して自己デッドロックする
            lock_con = await asyncpg.connect(dsn=self.database_url)
            try:
                await lock_con.fetchval(
                    "SELECT pg_advisory_lock($1)", MIGRATION_ADVISORY_LOCK_KEY
                )
                try:
                    await self._migrate_versioned()
                finally:
                    await lock_con.fetchval(
                        "SELECT pg_advisory_unlock($1)", MIGRATION_ADVISORY_LOCK_KEY
                    )
            finally:
                await lock_con.close()
            async with self._pool.acquire() as con:
                await self._pg_exec_ddl(con, "indexes", INDEX_DDL)
                await self._pg_exec_ddl(con, "views", POSTGRES_VIEW_DDL)
        except Exception:
            await self.close()
            raise
        await self._pg_fix_sequences()
        log.info(
            "PostgreSQL に接続しました（%s / プール %d〜%d）",
            re.sub(r"://[^@]*@", "://***@", self.database_url),
            self.pool_min_size,
            self.pool_max_size,
        )

    async def _pg_exec_ddl(self, con, label: str, sql: str) -> None:
        """DDL を実行し、失敗時は失敗した SQL を安全に記録する。

        DDL には秘密情報は含まれない（DATABASE_URL・パスワードは出力しない）。
        """
        try:
            await con.execute(sql)
        except Exception as e:
            log.error("PostgreSQL DDL 実行失敗 (%s): %s\n%s", label, type(e).__name__, sql.strip())
            raise

    async def init_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        await self.stop_settings_listener()
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def pool_stats(self) -> dict[str, int] | None:
        """プールの利用状況（監視用）。SQLite では None。

        接続文字列・認証情報は一切含めない。
        """
        if not self._is_pg or self._pool is None:
            return None
        try:
            size = self._pool.get_size()
            idle = self._pool.get_idle_size()
        except Exception:  # noqa: BLE001  (asyncpg の版差)
            return {"min_size": self.pool_min_size, "max_size": self.pool_max_size}
        return {
            "min_size": self.pool_min_size,
            "max_size": self.pool_max_size,
            "size": size,
            "idle": idle,
            "in_use": max(size - idle, 0),
        }

    async def is_healthy(self) -> bool:
        """接続確認（/health 用）。"""
        try:
            await self.fetchone("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # ドライバ差分の吸収
    # ------------------------------------------------------------------
    def _prepare(self, sql: str, params: tuple) -> tuple[str, list]:
        """SQLite 方言（? プレースホルダ）をドライバに合わせて変換する。"""
        if not self._is_pg:
            return sql, list(params)
        out: list[str] = []
        idx = 0
        for ch in sql:
            if ch == "?":
                idx += 1
                out.append(f"${idx}")
            else:
                out.append(ch)
        return "".join(out), list(params)

    def _now_sql(self) -> str:
        """現在時刻を返す SQL 式（settings.updated_at 等の互換用）。"""
        if self._is_pg:
            return "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')"
        return "datetime('now', 'localtime')"

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database が未接続です（SQLite ではありません）")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()):
        if self._is_pg:
            return await self._execute_pg(sql, params)
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def _execute_pg(self, sql: str, params: tuple) -> _PgCursor:
        assert self._pool is not None
        stmt, args = self._prepare(sql, params)
        upper = stmt.lstrip().upper()
        m = _INSERT_TABLE_RE.search(stmt)
        pk = _PK_COLUMNS.get(m.group(1)) if m else None
        async with self._pool.acquire() as con:
            if upper.startswith("INSERT") and pk and "RETURNING" not in upper:
                row = await con.fetchrow(f"{stmt} RETURNING {pk}", *args)
                return _PgCursor(1 if row is not None else 0, row[pk] if row is not None else None)
            status = await con.execute(stmt, *args)
        # ステータス文字列（"INSERT 0 1" / "UPDATE 3" 等）から件数を取り出す
        try:
            rowcount = int(status.split()[-1])
        except (ValueError, IndexError):
            rowcount = 0
        return _PgCursor(rowcount)

    async def fetchone(self, sql: str, params: tuple = ()):
        if self._is_pg:
            assert self._pool is not None
            stmt, args = self._prepare(sql, params)
            async with self._pool.acquire() as con:
                return await con.fetchrow(stmt, *args)
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: tuple = ()) -> list:
        if self._is_pg:
            assert self._pool is not None
            stmt, args = self._prepare(sql, params)
            async with self._pool.acquire() as con:
                return list(await con.fetch(stmt, *args))
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return list(rows)

    async def _executescript(self, sql: str) -> None:
        """複数文の実行（マイグレーション用）。"""
        if self._is_pg:
            assert self._pool is not None
            async with self._pool.acquire() as con:
                await con.execute(sql)
            return
        await self.conn.executescript(sql)

    async def _table_columns(self, table: str) -> list[str]:
        if self._is_pg:
            rows = await self.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)
            )
            return [r["column_name"] for r in rows]
        cur = await self.conn.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [row[1] for row in rows]

    # ------------------------------------------------------------------
    # マイグレーション
    # ------------------------------------------------------------------
    async def _migrate(self) -> None:
        # 排他制御（D2-6）はこの SQLite 経路では no-op。
        # SQLite は単一ファイルで、書き込みはファイルロック＋
        # busy_timeout=5000 により元々直列化される（ADR 0006 で SQLite は
        # ローカル開発・テスト専用。bot と dashboard の同時起動が問題になる
        # 本番は PostgreSQL 側の pg_advisory_lock が担う）
        """
        既存 DB の簡易マイグレーション（SQLite 専用）。

        1. schedules.sheet_title の追加（旧来の移行）
        2. 全テーブルへの guild_id 追加（テーブル再作成方式）。
           既存行の guild_id は環境変数 GUILD_ID（レガシーギルド）で
           バックフィルする。未設定時は 0（レガシー/未帰属）。
        """
        assert self._conn is not None
        cols = await self._table_columns("schedules")
        if cols and "sheet_title" not in cols:
            await self._conn.execute("ALTER TABLE schedules ADD COLUMN sheet_title TEXT")
            await self._conn.commit()
            log.info("schedules テーブルに sheet_title カラムを追加しました。")

        await self._migrate_guild_id()
        await self._migrate_versioned()

    async def _user_version(self) -> int:
        if self._is_pg:
            row = await self.fetchone("SELECT version FROM schema_meta WHERE id = 1")
            return int(row["version"]) if row else 0
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        await cur.close()
        return int(row[0]) if row else 0

    async def _set_user_version(self, version: int) -> None:
        if self._is_pg:
            await self.execute(
                "INSERT INTO schema_meta (id, version) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET version = excluded.version",
                (version,),
            )
            return
        await self.conn.execute(f"PRAGMA user_version = {version}")
        await self.conn.commit()

    async def _migrate_versioned(self) -> None:
        """
        スキーマバージョン管理によるバージョン付きマイグレーション。

        user_version=0 は「guild_id 導入前または v1 相当の DB」を表す。
        v1（guild_id 導入）は _migrate_guild_id() が担うため、ここでは
        v2 以降を適用する。各ステップは冪等。
        """
        version = await self._user_version()
        if version >= SCHEMA_VERSION:
            return

        if version < 2:
            await self._migrate_v2_guild_foundation()

        if version < 3:
            await self._migrate_v3_teams_skills()

        if version < 5:
            # v4: todoist_configs（init_schema で作成済み）
            # v5: Sheets 廃止に伴う NocoDB 表示用ビュー（最新定義で作り直す）
            await self._migrate_v5_views()

        if version < 6:
            await self._migrate_v6_pg_bigint()

        if version < 7:
            await self._migrate_v7_directus_access()

        if version < 8:
            await self._migrate_v8_members_surrogate_pk()

        if version < 9:
            await self._migrate_v9_rename_directus_access()

        if version < 10:
            await self._migrate_v10_progress_tables()

        if version < 11:
            await self._migrate_v11_guild_lifecycle()

        if version < 12:
            await self._migrate_v12_progress_weight()

        if version < 13:
            await self._migrate_v13_progress_milestones()

        if version < 14:
            await self._migrate_v14_seasons()

        if version < 15:
            await self._migrate_v15_name_cache()

        if version < 16:
            await self._migrate_v16_layer_num_text()

        if version < 17:
            await self._migrate_v17_schedule_confirmed()

        if version < 18:
            await self._migrate_v18_progress_snapshots()

        if version < 19:
            await self._migrate_v19_stock()

        if version < 20:
            await self._migrate_v20_tools()

        if version < 21:
            await self._migrate_v21_incidents()

        if version < 22:
            await self._migrate_v22_club_name_key()

        await self._set_user_version(SCHEMA_VERSION)
        log.info("スキーマバージョンを %d に更新しました。", SCHEMA_VERSION)

    # guild_id 列を BIGINT へ変換する対象テーブル（PostgreSQL のみ。
    # migrations/005_bigint_discord_ids.sql と同じ一覧）
    _PG_BIGINT_TABLES = (
        "guilds",
        "settings",
        "teams",
        "members",
        "schedules",
        "schedule_options",
        "schedule_votes",
        "tasks",
        "reminders_log",
        "todoist_sections",
        "todoist_configs",
        "layer_sessions",
        "layer_records",
        "layer_keta",
        "audit_log",
        "skill_tags",
    )

    async def _migrate_v6_pg_bigint(self) -> None:
        """
        v6: PostgreSQL の guild_id 列を int4 から BIGINT へ変更する（冪等）。

        過去の to_pg_ddl() 変換漏れで int4 として作成された既存 DB を修復する。
        2^31 を超える Discord ギルド ID で asyncpg の DataError
        （value out of int32 range）が発生する問題への対応。
        int4 → int8 は値を失わない拡張変換で、既に BIGINT の列への ALTER は
        実質 no-op。SQLite は動的型付けで 64bit を保持できるため何もしない。
        """
        if not self._is_pg:
            return
        # ビューは列の型変更を妨げるため、先に DROP して最後に再作成する
        for view in ("v_todoist_status", "v_attendance", "v_team_summary"):
            await self.execute(f"DROP VIEW IF EXISTS {view}")
        for table in self._PG_BIGINT_TABLES:
            await self.execute(f"ALTER TABLE {table} ALTER COLUMN guild_id TYPE BIGINT")
        await self._migrate_v5_views()
        log.info("PostgreSQL の guild_id 列を BIGINT に変更しました。")

    async def _migrate_v7_directus_access(self) -> None:
        """
        v7: Directus アクセス発行状況テーブルを追加する（冪等）。

        新規 DB では init_schema（SQLite）・_connect_pg（PostgreSQL）が
        CREATE TABLE IF NOT EXISTS で作成済みだが、既存 DB でも確実に
        作成されるようここでもドライバ別 DDL を実行する。

        テーブル名は v9 で directus_access から guild_directus_access へ
        改名済み（Directus のシステムテーブルとの衝突回避）。旧名で作成
        済みの DB は _migrate_v9_rename_directus_access() が引き継ぐ。
        """
        ddl = (TABLE_DDL_PG if self._is_pg else TABLE_DDL)["guild_directus_access"]
        await self._executescript(ddl)
        log.info("guild_directus_access テーブルを作成しました（v7）。")

    async def _migrate_v9_rename_directus_access(self) -> None:
        """
        v9: 旧 directus_access を guild_directus_access へ改名する（冪等）。

        Directus は業務 DB と同じデータベースへ自身のシステムテーブルを
        作成する。Directus 11 のシステムコレクション `directus_access`
        （ユーザー/ロールとポリシーの紐付け）と bot 側のテーブル名が衝突
        すると、Directus の初回セットアップが自身のシステムテーブルへ
        INSERT する際に bot 側の guild_id NOT NULL 制約に掛かり、
        "Value for field \"guild_id\" in collection \"directus_access\"
        can't be null" で初期化不能になる。

        安全性: Directus 自身のシステムテーブルには guild_id 列が無い。
        guild_id 列を持つ場合のみ「bot が作った旧テーブル」と判定して
        改名するため、Directus のテーブルを誤って壊すことはない。
        """
        legacy_cols = await self._table_columns("directus_access")
        if not legacy_cols or "guild_id" not in legacy_cols:
            # 旧テーブルが無い（新規 DB・適用済み）か、Directus 自身の
            # システムテーブルなので触らない
            return

        if not await self._table_columns("guild_directus_access"):
            await self.execute("ALTER TABLE directus_access RENAME TO guild_directus_access")
            log.info("directus_access を guild_directus_access へ改名しました（v9）。")
            return

        # 新名テーブルが既にある（init_schema が作成済み）ケース:
        # 行を移してから旧表を削除する。
        # WHERE true は INSERT ... SELECT ... ON CONFLICT の構文的曖昧さを
        # 解消するために必須（SQLite の仕様。PostgreSQL でも有効）。
        await self.execute(
            "INSERT INTO guild_directus_access"
            " (guild_id, directus_user_id, email, status,"
            "  created_by, created_at, updated_at)"
            " SELECT guild_id, directus_user_id, email, status,"
            "  created_by, created_at, updated_at FROM directus_access"
            " WHERE true"
            " ON CONFLICT (guild_id) DO NOTHING"
        )
        await self.execute("DROP TABLE directus_access")
        log.info(
            "旧 directus_access の行を guild_directus_access へ移行し、"
            "旧テーブルを削除しました（v9）。"
        )

    # v10 で追加する機体進捗テーブル（migrations/009_progress_nodes.sql と同一）
    _PROGRESS_TABLES = (
        "progress_nodes",
        "progress_todoist_links",
        "progress_spar_links",
    )

    async def _migrate_v10_progress_tables(self) -> None:
        """
        v10: 機体進捗ツリーのテーブルを追加する（冪等）。

        /progress の正本を Google Sheets から DB へ移すための土台。
        新規 DB では init_schema（SQLite）・_connect_pg（PostgreSQL）が
        CREATE TABLE IF NOT EXISTS で作成済みだが、既存 DB でも確実に
        作成されるよう v7 と同じくドライバ別 DDL をここでも実行する。

        既存データには触れないため後方互換（進捗データが無いギルドは
        空のツリーとして扱われる）。旧・中央スプレッドシートからの取り込みは
        scripts/migrate_progress_sheet_to_db.py が別途行う。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        for name in self._PROGRESS_TABLES:
            await self._executescript(ddl_map[name])
        log.info("機体進捗テーブル（%s）を作成しました（v10）。", ", ".join(self._PROGRESS_TABLES))

    async def _migrate_v11_guild_lifecycle(self) -> None:
        """
        v11: guilds に left_at / purge_after を追加する（冪等）。

        どちらも NULL 許容で追加するため既存行は壊れない。参加中のギルドは
        NULL のままで、退出時に on_guild_remove が値を入れる。
        既存の全ギルドは「参加中」として扱われる（勝手に削除予定にしない）。
        """
        cols = await self._table_columns("guilds")
        for col in ("left_at", "purge_after"):
            if col not in cols:
                await self.execute(f"ALTER TABLE guilds ADD COLUMN {col} TEXT")
                log.info("guilds テーブルに %s カラムを追加しました（v11）。", col)

    async def _migrate_v12_progress_weight(self) -> None:
        """
        v12: progress_nodes に目標重量・実測重量を追加する（冪等）。

        単位はグラム固定（列名の _g で明示する。単位設定は作らない）。
        NULL 許容で追加するため既存ノードは壊れず、重量未入力として扱われる。

        新規 DB は TABLE_DDL / TABLE_DDL_PG が作るので to_pg_ddl() の
        REAL → DOUBLE PRECISION 変換が効くが、ALTER 文はそこを通らないため
        ここでドライバ別に型を指定する（PostgreSQL の REAL は 4 バイトで
        SQLite の REAL より精度が低い）。
        """
        col_type = "DOUBLE PRECISION" if self._is_pg else "REAL"
        cols = await self._table_columns("progress_nodes")
        for col in ("target_weight_g", "actual_weight_g"):
            if col not in cols:
                await self.execute(f"ALTER TABLE progress_nodes ADD COLUMN {col} {col_type}")
                log.info("progress_nodes テーブルに %s カラムを追加しました（v12）。", col)

    async def _migrate_v13_progress_milestones(self) -> None:
        """
        v13: progress_milestones テーブルを追加する（冪等）。

        新規 DB では init_schema / _connect_pg が CREATE TABLE IF NOT EXISTS で
        作成済みだが、既存 DB でも確実に作られるようここでも実行する
        （v10 と同じ方式）。既存データには触れないため後方互換。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        await self._executescript(ddl_map["progress_milestones"])
        log.info("progress_milestones テーブルを作成しました（v13）。")

    async def _migrate_v14_seasons(self) -> None:
        """
        v14: seasons テーブルと members の在籍状態を追加する（冪等）。

        **既存メンバーはすべて status='active' になる。** NOT NULL DEFAULT で
        列を足すため既存行にはデフォルト値が入り、誰も勝手に卒業扱いには
        ならない（卒業の仕分けは /season rollover を実行したときだけ）。
        left_season は NULL 許容。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        await self._executescript(ddl_map["seasons"])

        cols = await self._table_columns("members")
        if "status" not in cols:
            await self.execute(
                "ALTER TABLE members ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
            log.info("members テーブルに status カラムを追加しました（v14）。")
        if "left_season" not in cols:
            await self.execute("ALTER TABLE members ADD COLUMN left_season TEXT")
            log.info("members テーブルに left_season カラムを追加しました（v14）。")

    async def _migrate_v15_name_cache(self) -> None:
        """
        v15: discord_name_cache テーブルを追加する（冪等）。

        ダッシュボード（Bot トークンを持たない別プロセス）が Discord の
        ユーザー ID / チャンネル ID を表示名へ解決するためのキャッシュ。
        bot がギルドキャッシュから書き込む（cogs/name_cache.py）。

        新規 DB では init_schema / _connect_pg が CREATE TABLE IF NOT EXISTS で
        作成済みだが、既存 DB でも確実に作られるようここでも実行する
        （v10 / v13 と同じ方式）。既存データには触れないため後方互換。
        キャッシュは空から始まり、bot の起動時同期で埋まる。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        await self._executescript(ddl_map["discord_name_cache"])
        log.info("discord_name_cache テーブルを作成しました（v15）。")

    async def _migrate_v22_club_name_key(self) -> None:
        """
        v22: サークル名の設定キーを `CLUB_NAME` に統一する（冪等。D2-1）。

        ダッシュボードは `GUILD_NAME` キーで保存していたが、週次サマリー等が
        読むのは `CLUB_NAME`（config.py）。保存しても反映されない不具合の解消。

        既存データの扱い（ADR 0024 に照らした判断）:
        - `GUILD_NAME` だけのギルド: 値を `CLUB_NAME` へ**コピー**する。
          利用者がダッシュボードで明示的に保存した値を初めて有効にする移行で、
          「既定値で勝手に動かす」ものではない
        - `CLUB_NAME` が既にあるギルド: **上書きしない**（現に効いている値を守る）
        - 旧キー `GUILD_NAME` の行は**消さない**（安全側。以後どのコードも
          読まないが、監査と巻き戻しの余地を残す。bot は新規ギルド参加時に
          今後も `GUILD_NAME` を Discord サーバー名で埋めるが（bot.py の
          set_if_absent）、v22 以降にできた行がここへ来ることはない）

        影響の注記: `GUILD_NAME` は bot がギルド参加時に **Discord サーバー名で
        自動設定**するため、ダッシュボードで一度も編集していないギルドにも
        値がある。そのため CLUB_NAME 未設定のギルドでは、週次サマリー等の
        表示が既定の「サークル」からサーバー名に変わる。破壊も不可逆もなく、
        /setup のサークル名モーダルでいつでも上書きできる。
        """
        await self.execute(
            """
            INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
            SELECT s.guild_id, 'CLUB_NAME', s.setting_value, s.updated_at
            FROM settings s
            WHERE s.setting_key = 'GUILD_NAME'
              AND NOT EXISTS (
                SELECT 1 FROM settings c
                WHERE c.guild_id = s.guild_id AND c.setting_key = 'CLUB_NAME'
              )
            """
        )
        log.info("サークル名の設定キーを CLUB_NAME に統一しました（v22）。")

    async def _migrate_v21_incidents(self) -> None:
        """
        v21: incidents テーブルを追加する（冪等）。

        ヒヤリハット・事故報告（G4-10）。

        **v19（在庫）にも v20（工具）にも足さない。**
        `_migrate_versioned()` は `version >= SCHEMA_VERSION` で早期 return する
        ため、既に適用済みの版へ後から CREATE を足しても既存 DB には届かない
        （gotcha `bot-wont-start-undefined-column`）。

        **既存データには一切触れない。** 追加されるのは空のテーブル1つだけ。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        await self._executescript(ddl_map["incidents"])
        log.info("incidents テーブルを作成しました（v21）。")

    async def _migrate_v20_tools(self) -> None:
        """
        v20: tools / tool_loans テーブルを追加する（冪等）。

        工具・機材の貸出管理（G4-9）。

        **v19（在庫）に足さない。** `_migrate_versioned()` は
        `version >= SCHEMA_VERSION` で早期 return するため、v19 済みの DB は
        二度と v19 の処理を通らない。後から v19 へ CREATE を足すと
        **新規 DB にだけテーブルがある**状態になり、本番だけ
        「relation does not exist」で落ちる（gotcha
        `bot-wont-start-undefined-column` と同型）。

        **既存データには一切触れない。** 追加されるのは空のテーブル2つだけで、
        工具の初期値も入れない（何を貸出管理するかはサークルごとに違う）。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        for name in ("tools", "tool_loans"):
            await self._executescript(ddl_map[name])
        log.info("tools / tool_loans テーブルを作成しました（v20）。")

    async def _migrate_v19_stock(self) -> None:
        """
        v19: stock_items / stock_movements テーブルを追加する（冪等）。

        資材・消耗品の在庫と発注アラート（G4-8）。

        新規 DB では init_schema / _connect_pg が CREATE TABLE IF NOT EXISTS で
        作成済みだが、既存 DB でも確実に作られるようここでも実行する
        （v10 / v13 / v15 / v18 と同じ方式）。

        **既存データには一切触れない。** 追加されるのは空のテーブル2つだけで、
        **品目の初期値も入れない**（何を在庫管理するかはサークルごとに違う）。
        品目が0件のギルドでは `/stock list` が空状態を出し、朝の通知も飛ばない。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        for name in ("stock_items", "stock_movements"):
            await self._executescript(ddl_map[name])
        log.info("stock_items / stock_movements テーブルを作成しました（v19）。")

    async def _migrate_v18_progress_snapshots(self) -> None:
        """
        v18: progress_snapshots テーブルを追加する（冪等）。

        進捗の日次履歴。`/progress history` とマイルストーンのペース算出が読む。

        新規 DB では init_schema / _connect_pg が CREATE TABLE IF NOT EXISTS で
        作成済みだが、既存 DB でも確実に作られるようここでも実行する
        （v10 / v13 / v15 と同じ方式）。

        **既存データには一切触れない。** 追加されるのは空のテーブルだけで、
        履歴は次の定期同期から1日1行ずつ積まれる。溜まるまでは
        ペース算出が従来の推定（作成日→更新日）にフォールバックするため、
        マイグレーション直後に判定結果が変わることはない（ADR 0024）。
        """
        ddl_map = TABLE_DDL_PG if self._is_pg else TABLE_DDL
        await self._executescript(ddl_map["progress_snapshots"])
        log.info("progress_snapshots テーブルを作成しました（v18）。")

    async def _migrate_v17_schedule_confirmed(self) -> None:
        """
        v17: schedules に deleted_flag と confirmed_option_id を追加する（冪等）。

        **1つの版に2列をまとめてあるのは意図的。** deleted_flag は G3-3
        （/schedule delete の論理削除）、confirmed_option_id は G3-4
        （/schedule confirm の確定日程）が使う。_migrate_versioned() は
        version >= SCHEMA_VERSION で早期 return するため、G3-3 が v17 を
        切ったあとに G3-4 が同じ版へ ALTER を足しても**既存 DB では二度と
        実行されない**（新規 DB にだけ列がある状態になる。gotcha
        `bot-wont-start-undefined-column` と同型）。だから先に両方入れる。

        どちらも既定値つき・NULL 許容の追加のみで、既存行の値は変わらない
        （deleted_flag は 0 = 削除されていない、confirmed_option_id は
        NULL = 未確定）。ALTER 前に列の有無を確認するのは、PostgreSQL で
        素の ADD COLUMN が DuplicateColumn になり起動できなくなるため。
        """
        cols = await self._table_columns("schedules")
        if "deleted_flag" not in cols:
            await self.execute(
                "ALTER TABLE schedules ADD COLUMN deleted_flag INTEGER NOT NULL DEFAULT 0"
            )
            log.info("schedules テーブルに deleted_flag カラムを追加しました（v17）。")
        if "confirmed_option_id" not in cols:
            await self.execute("ALTER TABLE schedules ADD COLUMN confirmed_option_id TEXT")
            log.info("schedules テーブルに confirmed_option_id カラムを追加しました（v17）。")

    async def _migrate_v16_layer_num_text(self) -> None:
        """
        v16: layer_sessions.layer_num を INTEGER から TEXT へ変更する（冪等）。

        /layer start の層番号は数字のほか「シュリンク」等のテキストを受け付ける
        仕様（layer_records.layer_num は当初から TEXT）だが、layer_sessions 側
        だけ INTEGER で作られていた。SQLite は動的型付けでテキストも保存できる
        ため顕在化しなかったが、PostgreSQL では asyncpg の DataError
        （'str' object cannot be interpreted as an integer）で /layer start が
        失敗する。

        - PostgreSQL: ALTER TABLE ... TYPE TEXT USING layer_num::text。
          既存の数値は '3' のような文字列になる（表示にしか使わない列で、
          数値として比較・集計する箇所は無い）
        - SQLite: INTEGER 親和性が数字文字列を整数へ丸めるため、宣言型を
          揃える目的でテーブル再作成方式により移行する（_migrate_v8 と同じ手順）
        """
        if self._is_pg:
            row = await self.fetchone(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name = 'layer_sessions' AND column_name = 'layer_num'"
            )
            if row is None or row["data_type"] == "text":
                return
            await self.execute(
                "ALTER TABLE layer_sessions ALTER COLUMN layer_num TYPE TEXT"
                " USING layer_num::text"
            )
            log.info("layer_sessions.layer_num を TEXT に変更しました（v16）。")
            return

        assert self._conn is not None
        cur = await self._conn.execute("PRAGMA table_info(layer_sessions)")
        rows = await cur.fetchall()
        await cur.close()
        if not rows:
            return
        declared = {row[1]: (row[2] or "").upper() for row in rows}
        if declared.get("layer_num") == "TEXT":
            return

        cols = [row[1] for row in rows]
        col_list = ", ".join(cols)
        await self._conn.execute("PRAGMA foreign_keys = OFF;")
        await self._conn.commit()
        try:
            await self._conn.execute("ALTER TABLE layer_sessions RENAME TO layer_sessions_legacy")
            await self._conn.execute(TABLE_DDL["layer_sessions"])
            await self._conn.execute(
                f"INSERT INTO layer_sessions ({col_list})"
                f" SELECT {col_list} FROM layer_sessions_legacy"
            )
            await self._conn.execute("DROP TABLE layer_sessions_legacy")
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        log.info(
            "layer_sessions.layer_num を TEXT に変更しました（v16, %d 行）。",
            await self._count("layer_sessions"),
        )

    async def _migrate_v8_members_surrogate_pk(self) -> None:
        """
        v8: members に代理主キー member_id を追加する（冪等）。

        旧スキーマの PRIMARY KEY (guild_id, user_id) は UNIQUE 制約として
        維持するため、リポジトリ層のクエリ（guild_id + user_id 指定）は
        変更不要。単一列 PK を必須とする外部 DB UI（Directus）から
        members を扱えるようにするための変更。

        member_id が既にあれば何もしない（新規 DB・適用済み DB）。
        """
        cols = await self._table_columns("members")
        if not cols or "member_id" in cols:
            return

        if self._is_pg:
            # 自然キーの一意性を先に確保してから主キーを差し替える
            await self.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS members_guild_user_uniq"
                " ON members (guild_id, user_id)"
            )
            await self.execute("ALTER TABLE members DROP CONSTRAINT IF EXISTS members_pkey")
            await self.execute(
                "ALTER TABLE members ADD COLUMN member_id BIGINT GENERATED BY DEFAULT AS IDENTITY"
            )
            await self.execute("ALTER TABLE members ADD PRIMARY KEY (member_id)")
            log.info("members に代理主キー member_id を追加しました（v8）。")
            return

        # SQLite: 主キーの変更は ALTER では行えないためテーブル再作成方式
        # （_migrate_guild_id と同じ手順）。member_id は自動採番させるため
        # コピー対象の列から除外する。
        assert self._conn is not None
        col_list = ", ".join(cols)
        await self._conn.execute("PRAGMA foreign_keys = OFF;")
        await self._conn.commit()
        try:
            await self._conn.execute("ALTER TABLE members RENAME TO members_legacy")
            await self._conn.execute(TABLE_DDL["members"])
            await self._conn.execute(
                f"INSERT INTO members ({col_list}) SELECT {col_list} FROM members_legacy"
            )
            await self._conn.execute("DROP TABLE members_legacy")
            await self._conn.executescript(INDEX_DDL)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        log.info(
            "members に代理主キー member_id を追加しました（v8, %d 行）。",
            await self._count("members"),
        )

    async def _migrate_v5_views(self) -> None:
        """v5: 表示用ビューを最新定義で作り直す（冪等）。

        PostgreSQL は CREATE OR REPLACE VIEW、SQLite は
        DROP VIEW IF EXISTS + CREATE VIEW の再作成方式
        （DDL 内に DROP を含む）で最新化する。
        """
        if self._is_pg:
            await self._executescript(POSTGRES_VIEW_DDL)
            return
        await self._executescript(SQLITE_VIEW_DDL)

    async def _migrate_v2_guild_foundation(self) -> None:
        """
        v2: guilds（ギルド台帳）と audit_log を導入する。

        テーブル自体は init_schema（CREATE TABLE IF NOT EXISTS）で作成済み。
        ここでは既存データからギルド台帳をバックフィルする。
        台帳の名称は settings の GUILD_NAME（あれば）を使い、
        無ければ '(unknown)' とする（起動時の _ensure_guild_setup が正しい名称で
        上書きする）。
        """
        rows = await self.fetchall("SELECT DISTINCT guild_id FROM settings WHERE guild_id > 0")
        for row in rows:
            gid = int(row["guild_id"])
            name_row = await self.fetchone(
                "SELECT setting_value FROM settings"
                " WHERE guild_id = ? AND setting_key = 'GUILD_NAME'",
                (gid,),
            )
            name = name_row["setting_value"] if name_row else "(unknown)"
            await self.execute(
                "INSERT INTO guilds (guild_id, guild_name, joined_at, setup_version)"
                f" VALUES (?, ?, {self._now_sql()}, 2)"
                " ON CONFLICT(guild_id) DO NOTHING",
                (gid, name),
            )
        if rows:
            log.info("ギルド台帳をバックフィルしました（%d ギルド）。", len(rows))

    async def _migrate_v3_teams_skills(self) -> None:
        """
        v3: 班・技能タグの DB 管理化。

        - skill_tags テーブルは init_schema（CREATE TABLE IF NOT EXISTS）で作成済み。
        - teams に member_role_id / secondary_role_id / created_at / updated_at を
          追加する（既に存在する場合はスキップ）。
        - settings の PRIMARY_TEAM_ROLE_IDS / SECONDARY_TEAM_ROLE_IDS（書式:
          "team_key:role_id,team_key:role_id"）を teams.member_role_id /
          secondary_role_id へバックフィルする（未設定の行のみ）。
          settings のキー自体は後方互換のフォールバックとして残す。
        """
        cols = await self._table_columns("teams")
        for col in ("member_role_id", "secondary_role_id", "created_at", "updated_at"):
            if col not in cols:
                await self.execute(f"ALTER TABLE teams ADD COLUMN {col} TEXT")
                log.info("teams テーブルに %s カラムを追加しました。", col)

        rows = await self.fetchall(
            "SELECT guild_id, setting_key, setting_value FROM settings"
            " WHERE setting_key IN ('PRIMARY_TEAM_ROLE_IDS', 'SECONDARY_TEAM_ROLE_IDS')"
        )
        backfilled = 0
        for row in rows:
            target_col = (
                "member_role_id"
                if row["setting_key"] == "PRIMARY_TEAM_ROLE_IDS"
                else "secondary_role_id"
            )
            for part in (row["setting_value"] or "").split(","):
                part = part.strip()
                if ":" not in part:
                    continue
                key, _, val = part.partition(":")
                key, val = key.strip(), val.strip()
                if not key or not val.isdigit():
                    continue
                cur = await self.execute(
                    f"UPDATE teams SET {target_col} = ?"
                    f" WHERE guild_id = ? AND team_key = ? AND {target_col} IS NULL",
                    (val, int(row["guild_id"]), key),
                )
                backfilled += cur.rowcount
        if backfilled:
            log.info("teams のロール紐付けをバックフィルしました（%d 件）。", backfilled)

    async def _migrate_guild_id(self) -> None:
        """
        guild_id を持たない旧テーブルを新スキーマへ移行する（テーブル再作成方式）。
        SQLite 専用（PostgreSQL では新規スキーマで開始する）。

        手順（migrations/001_add_guild_id.sql と同等）:
          1. 旧テーブルを <table>_legacy にリネーム
          2. 新スキーマでテーブルを作成
          3. guild_id をバックフィルしつつデータをコピー
          4. 旧テーブルを削除
        """
        assert self._conn is not None
        targets: dict[str, list[str]] = {}
        for table in TABLE_DDL:
            cols = await self._table_columns(table)
            if not cols:
                continue  # テーブル自体が無い（init_schema で作成済みのはずだが念のため）
            if "guild_id" not in cols:
                targets[table] = cols
        if not targets:
            return

        legacy = legacy_guild_id()
        log.warning(
            "guild_id を持たない旧テーブルを検出しました（%s）。"
            "guild_id=%d でバックフィルして移行します。",
            ", ".join(sorted(targets)),
            legacy,
        )

        # FK 参照の張り替えを避けるため、移行中は FK 強制を一時停止する
        await self._conn.execute("PRAGMA foreign_keys = OFF;")
        await self._conn.commit()
        try:
            for table, cols in targets.items():
                col_list = ", ".join(cols)
                await self._conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
                await self._conn.execute(TABLE_DDL[table])
                await self._conn.execute(
                    f"INSERT INTO {table} (guild_id, {col_list}) "
                    f"SELECT ?, {col_list} FROM {table}_legacy",
                    (legacy,),
                )
                await self._conn.execute(f"DROP TABLE {table}_legacy")
                log.info(
                    "%s テーブルを guild_id 付きスキーマへ移行しました（%d 行）",
                    table,
                    await self._count(table),
                )
            await self._conn.executescript(INDEX_DDL)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON;")
        log.warning("guild_id マイグレーションが完了しました。")

    async def _pg_fix_sequences(self) -> None:
        """
        PostgreSQL の IDENTITY シーケンスを既存最大値に合わせる。

        明示的な ID 挿入（SQLite からのデータ移行など）のあとに
        シーケンスが実データより小さいと PK 衝突が起きるため、
        接続のたびに冪等に修復する。
        """
        for table, pk in _PK_COLUMNS.items():
            await self.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'),"
                f" COALESCE((SELECT MAX({pk}) FROM {table}), 1),"
                f" (SELECT MAX({pk}) FROM {table}) IS NOT NULL)"
            )

    async def _count(self, table: str) -> int:
        row = await self.fetchone(f"SELECT COUNT(*) AS c FROM {table}")
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # 設定関連メソッド（guild_id スコープ）
    # ------------------------------------------------------------------
    async def get_setting(self, guild_id: int, key: str) -> str | None:
        """設定値を取得する"""
        row = await self.fetchone(
            "SELECT setting_value FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
        )
        return row["setting_value"] if row else None

    async def set_setting(self, guild_id: int, key: str, value: str) -> None:
        """設定値を保存する（存在すれば更新、なければ挿入）"""
        now_sql = self._now_sql()
        await self.execute(
            f"""INSERT INTO settings (guild_id, setting_key, setting_value, updated_at)
               VALUES (?, ?, ?, {now_sql})
               ON CONFLICT(guild_id, setting_key) DO UPDATE SET
               setting_value = excluded.setting_value,
               updated_at = {now_sql}""",
            (guild_id, key, value),
        )
        await self.notify_settings_changed(guild_id)

    async def delete_setting(self, guild_id: int, key: str) -> bool:
        """設定値を削除する"""
        cur = await self.execute(
            "DELETE FROM settings WHERE guild_id = ? AND setting_key = ?",
            (guild_id, key),
        )
        if cur.rowcount > 0:
            await self.notify_settings_changed(guild_id)
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 設定変更のプロセス間通知（PostgreSQL LISTEN/NOTIFY）
    #
    # bot は config.for_guild() の結果をプロセス内にキャッシュしている。
    # 別プロセスのダッシュボードが settings を更新しても bot 側は気づかない
    # ため、PostgreSQL の NOTIFY でキャッシュ無効化を伝播させる
    # （docs/DESIGN_PUBLIC_DISTRIBUTION.md 2.2）。
    #
    # SQLite（ローカル開発）では何もしない。単一プロセス運用が前提で、
    # ダッシュボードを併用する本番構成は PostgreSQL のため。
    # ------------------------------------------------------------------
    async def notify_settings_changed(self, guild_id: int) -> None:
        """settings 更新を他プロセスへ通知する（PostgreSQL のみ）。"""
        if not self._is_pg or self._pool is None:
            return
        try:
            async with self._pool.acquire() as con:
                await con.execute("SELECT pg_notify($1, $2)", SETTINGS_CHANNEL, str(guild_id))
        except Exception as e:  # noqa: BLE001  (通知失敗で更新自体は壊さない)
            log.warning("設定変更の通知に失敗しました (guild=%s): %s", guild_id, type(e).__name__)

    async def start_settings_listener(self, callback) -> bool:
        """settings 更新の通知を購読する（bot プロセスで1回だけ呼ぶ）。

        callback(guild_id: int) が通知のたびに呼ばれる。
        購読を開始できたら True（PostgreSQL 以外・asyncpg 未導入は False）。

        プール枠を占有しないよう、リスナー専用の接続を別に張る。
        """
        if not self._is_pg or asyncpg is None:
            return False
        if self._listener_conn is not None:
            return True

        def _on_notify(_conn, _pid, _channel, payload: str) -> None:
            try:
                guild_id = int(payload)
            except (TypeError, ValueError):
                return
            try:
                callback(guild_id)
            except Exception as e:  # noqa: BLE001  (通知処理で bot を止めない)
                log.warning(
                    "設定変更コールバックが失敗しました (guild=%s): %s", guild_id, type(e).__name__
                )

        try:
            con = await asyncpg.connect(dsn=self.database_url)
            await con.add_listener(SETTINGS_CHANNEL, _on_notify)
        except Exception as e:  # noqa: BLE001
            log.warning("設定変更の購読を開始できませんでした: %s", type(e).__name__)
            return False
        self._listener_conn = con
        log.info("設定変更の購読を開始しました（channel=%s）", SETTINGS_CHANNEL)
        return True

    async def stop_settings_listener(self) -> None:
        if self._listener_conn is not None:
            try:
                await self._listener_conn.close()
            except Exception:  # noqa: BLE001, S110
                pass
            self._listener_conn = None

    async def get_all_settings(self, guild_id: int) -> dict[str, str]:
        """指定ギルドの全ての設定を辞書で取得する"""
        rows = await self.fetchall(
            "SELECT setting_key, setting_value FROM settings WHERE guild_id = ?",
            (guild_id,),
        )
        return {row["setting_key"]: row["setting_value"] for row in rows}
