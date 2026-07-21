# 設計書: NocoDB 移行・マルチテナント完成・トークン暗号化・班/技能のDB管理

本書は以下4件の実装前設計を定める。実装は本書承認後にフェーズ単位で行う。

1. Google Sheets から「NocoDB が接続する DB」への移行
2. guild_id によるマルチテナント化の完成（外部サービス設定のギルド分離）
3. Todoist トークンのコマンド登録と暗号化保存
4. 班と技能タグの config.py 固定配列から DB 管理への移行

調査済みの現状は前工程の調査報告（未実装メソッド呼び出しの存在、`attendance-rate` の
`yes`/`ok` 不整合バグ等を含む）を前提とする。

---

## 1. 目的・スコープ・非スコープ

### 目的

- データの正本を SQLite（将来 PostgreSQL）に一本化し、Google Sheets 依存を除去する
- NocoDB を「同じ DB に対する閲覧・編集用 Web UI」として導入する
  （bot は NocoDB API に一切依存しない）
- 1 bot で複数 Discord サーバーを安全に扱い、他サーバーのデータを
  取得・更新・表示できないことを最優先で保証する
- Todoist トークンを guild_id ごとに暗号化して DB 保存し、
  Discord コマンドで登録・削除・状態確認できるようにする
- 班（teams）と技能タグ（skill_tags）をサーバーごとにコマンドで管理できるようにする

### 非スコープ

- NocoDB 側の変更を bot に双方向同期する仕組み（NocoDB は閲覧・軽微な編集 UI とし、
  正本への書き込み主体は bot に限定する運用。詳細は「設計上の仮定」A-6）
- Todoist の OAuth 化（API トークン方式を継続）
- PostgreSQL への実移行作業（移行しやすい設計のみ行う）
- Web ダッシュボード等の新規 UI 開発

---

## 2. 全体アーキテクチャ

```
Discord ユーザー
   │  スラッシュコマンド / リアクション
   ▼
club-bot (discord.py)
   │  Cog → Repository（guild_id 必須）→ utils/db.py
   │  ※ NocoDB API への依存なし
   ▼
DB（SQLite data/club.db ── 将来 PostgreSQL）
   ▲
   │  同一 DB に接続する閲覧・編集 UI
NocoDB (Docker Compose)
```

- **正本は常に DB**。bot の Repository 層だけが書き込みの主経路。
- NocoDB は DB を直接参照し、運用者が手動で閲覧・修正するための UI。
- Google Sheets 連携コード（gspread / google-auth / `services/sheets_service.py` /
  `cogs/sheets.py` / 各所の Sheets 同期呼び出し）は最終的に削除する。
  移行スクリプトだけが例外的に gspread を使う（使用後に依存ごと除去）。

### 既存「services/ 変更禁止」制約の扱い

前回のマルチテナント改修では `services/` が変更禁止とされ、`for_guild()` プロキシで
回避していた。本設計ではその制約を**ユーザー要件により明示的に解除**し、
services 層も guild_id を受け取る形に改修する。プロキシは移行期間のみ残し、
最終的には削除する。

---

## 3. 採用する DB 種別と理由

### 決定: SQLite で開始、PostgreSQL 移行を見据えた型設計

| 観点 | 判断 |
|---|---|
| 開始時 | **SQLite**（現行 `data/club.db` を継続利用。新規インフラ不要・バックアップがファイルコピーで済む） |
| 将来 | **PostgreSQL**（NocoDB との同時アクセス増大・書き込み競合が出た段階で移行） |
| NocoDB 接続 | SQLite 開始時は NocoDB コンテナに DB ファイルをボリューム共有。PostgreSQL 移行後はネットワーク接続 |

### PostgreSQL 移行しやすさの規約

1. `guild_id` は現行どおり `GUILD_ID_TYPE`（utils/db.py）に集約し、
   PG 移行時は `BIGINT` に読み替える。`CHECK (guild_id >= 0)` を維持。
2. 日時は **ISO8601 文字列（TEXT、タイムゾーン付き）** で統一
   （現行どおり。PG 移行時に `TIMESTAMPTZ` へ変換するかは移行時に判断）。
3. 真偽値は `INTEGER (0/1)` を継続（SQLite/PG 両対応）。
4. 真の AUTOINCREMENT 依存を避け、採番は SQLite では `INTEGER PRIMARY KEY AUTOINCREMENT`、
   PG では `BIGSERIAL` / `GENERATED ALWAYS AS IDENTITY` に対応させる。
   Repository は `lastrowid` 取得箇所を1箇所に集約する。
5. UPSERT は SQLite 構文（`ON CONFLICT ... DO UPDATE`）を使うが、
   PG でも同構文が使えるため Repository 内に閉じ込める。
6. SQL はパラメータバインド必須・文字列連結禁止（既存どおり）。

---

## 4. テーブル定義案

### 凡例

- 全テーブルに `guild_id INTEGER NOT NULL CHECK (guild_id >= 0)`（`guilds` 自身を除く）。
- `guild_id = 0` はレガシー/未帰属データの sentinel（既存仕様を継承）。
- PG 移行時の型対応: `INTEGER → BIGINT`（guild_id）/ `TEXT → TEXT or VARCHAR` /
  `INTEGER(0/1) → SMALLINT or BOOLEAN`。

### 4.1 既存テーブルの変更点

| テーブル | 変更 |
|---|---|
| `settings` | 変更なし。ただし `TODOIST_API_TOKEN` / `TODOIST_PROJECT_ID` / `PRIMARY_TEAM_ROLE_IDS` / `SECONDARY_TEAM_ROLE_IDS` は**廃止キー**とし、新規書き込みを停止（後方互換の読み取りのみフェーズ内で許容） |
| `teams` | カラム追加: `member_role_id TEXT`、`created_at TEXT`、`updated_at TEXT`。`leader_role_id` は既存どおり |
| `members` | `skills` / `secondary_teams` の JSON 列は**読み取りフォールバックのみ**に降格。技能の正本は `member_skills` に移行（詳細は 4.3 / 仮定 A-5） |
| `schedules` | 変更なし（`sheet_title` カラムは Sheets 削除後に用途消滅。削除は PG 移行時に検討） |
| `schedule_options` | 変更なし |
| `schedule_votes` | 変更なし（attendance の正本） |
| `tasks` | 変更なし |
| `reminders_log` | 変更なし（通知ログとして継続） |
| `todoist_sections` | 変更なし（guild_id ごとのセクション↔班紐付けとして継続） |
| `layer_sessions` | 変更なし |
| `layer_records` | `synced_flag` は Sheets 同期用だったため用途消滅。新規 INSERT は常に 1（=同期済扱い）とするか、カラム自体を将来削除（仮定 A-7） |
| `layer_keta` | 変更なし |

### 4.2 新規テーブル DDL（SQLite 版）

```sql
-- ギルド台帳（guild_id 唯一の例外テーブル。正のギルド ID のみ）
CREATE TABLE IF NOT EXISTS guilds (
    guild_id      INTEGER PRIMARY KEY CHECK (guild_id > 0),
    guild_name    TEXT NOT NULL,
    joined_at     TEXT NOT NULL,
    setup_version INTEGER NOT NULL DEFAULT 2
);

-- Todoist 接続設定（トークンは Fernet 暗号文。平文を保存しない）
CREATE TABLE IF NOT EXISTS todoist_configs (
    guild_id             INTEGER PRIMARY KEY CHECK (guild_id > 0),
    api_token_encrypted  TEXT NOT NULL,
    project_id           TEXT,
    today_label_name     TEXT NOT NULL DEFAULT '今日やること',
    enabled_flag         INTEGER NOT NULL DEFAULT 1,
    created_by           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
);

-- 技能タグ マスタ（ギルド別）
CREATE TABLE IF NOT EXISTS skill_tags (
    skill_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL CHECK (guild_id >= 0),
    skill_name   TEXT NOT NULL,
    active_flag  INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (guild_id, skill_name),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
);

-- メンバー×技能タグ（技能の正本。members.skills JSON を置き換える）
CREATE TABLE IF NOT EXISTS member_skills (
    guild_id    INTEGER NOT NULL CHECK (guild_id >= 0),
    user_id     TEXT NOT NULL,
    skill_name  TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, skill_name),
    FOREIGN KEY (guild_id, user_id)
        REFERENCES members(guild_id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (guild_id, skill_name)
        REFERENCES skill_tags(guild_id, skill_name)
);

-- 監査ログ（管理者操作の証跡。機密値は絶対に書かない）
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL CHECK (guild_id >= 0),
    actor_id   TEXT NOT NULL,
    action     TEXT NOT NULL,   -- 例: 'todoist.setup' 'team.add' 'skill.remove'
    target     TEXT,            -- 例: team_key, skill_name（トークン等は含めない）
    detail     TEXT,            -- 人間向け補足（機密値禁止）
    created_at TEXT NOT NULL
);
```

### 4.3 teams テーブル変更 DDL

```sql
ALTER TABLE teams ADD COLUMN member_role_id TEXT;
ALTER TABLE teams ADD COLUMN created_at TEXT;
ALTER TABLE teams ADD COLUMN updated_at TEXT;
```

- `member_role_id`: 班メンバーロール（従来の settings `PRIMARY_TEAM_ROLE_IDS` を移行）。
- `SECONDARY_TEAM_ROLE_IDS`（副所属専用ロール）は**統合して廃止**し、
  主・副どちらの所属でも `member_role_id` を使う（仮定 A-4）。

### 4.4 ビュー（NocoDB 表示用・正本は既存テーブル）

```sql
-- 出欠一覧（Sheets の attendance シート相当）
CREATE VIEW IF NOT EXISTS v_attendance AS
SELECT s.guild_id,
       s.schedule_id,
       s.title       AS event_title,
       o.label       AS option_label,
       v.user_id,
       v.status,               -- ok / maybe / ng
       v.updated_at,
       s.deadline
FROM schedule_votes v
JOIN schedule_options o
  ON o.guild_id = v.guild_id AND o.option_id = v.option_id
JOIN schedules s
  ON s.guild_id = o.guild_id AND s.schedule_id = o.schedule_id;

-- 班サマリ（Sheets の team_summary シート相当）
CREATE VIEW IF NOT EXISTS v_team_summary AS
SELECT t.guild_id,
       t.team_key,
       t.team_name,
       COUNT(m.user_id)        AS member_count,
       COALESCE(SUM(m.is_leader), 0) AS leader_count
FROM teams t
LEFT JOIN members m
  ON m.guild_id = t.guild_id
 AND m.primary_team = t.team_key
 AND m.active_flag = 1
WHERE t.active_flag = 1
GROUP BY t.guild_id, t.team_key, t.team_name;

-- Todoist 設定の安全な表示用（暗号文を含めない）
CREATE VIEW IF NOT EXISTS v_todoist_status AS
SELECT guild_id, project_id, today_label_name, enabled_flag, updated_at
FROM todoist_configs;
```

### 4.5 インデックス追加案

```sql
CREATE INDEX IF NOT EXISTS idx_skill_tags_guild ON skill_tags(guild_id, active_flag);
CREATE INDEX IF NOT EXISTS idx_member_skills_guild_user ON member_skills(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_member_skills_guild_skill ON member_skills(guild_id, skill_name);
CREATE INDEX IF NOT EXISTS idx_audit_log_guild ON audit_log(guild_id, audit_id);
CREATE INDEX IF NOT EXISTS idx_teams_guild ON teams(guild_id, active_flag);  -- 既存
```

### 4.6 マイグレーション方針

- `utils/db.py` の `_migrate()` に「スキーマバージョン」概念を導入する
  （`settings` ではなく専用の `schema_migrations` 的な記録、または PRAGMA user_version）。
- 今回の変更は `002_nocodb_multitenant.sql`（手動用）と `_migrate()` 自動適用の
  両方を用意する（既存の 001 と同じ二正面方式）。
- 自動マイグレーションは**冪等**とし、起動時に何度実行しても安全。
- 既存データ変換:
  - `settings.PRIMARY_TEAM_ROLE_IDS` → `teams.member_role_id` へバックフィル
  - `members.skills` JSON → `member_skills` へバックフィル
    （skill_tags に無い技能名は skill_tags にも自動登録）
  - 旧 `settings.TODOIST_API_TOKEN`（平文）→ 暗号化して `todoist_configs` へ移行し、
    settings 側のキーを削除（仮定 A-3。ENCRYPTION_KEY 未設定時は移行をスキップし警告）

---

## 5. guild_id を含む全テーブル一覧

| テーブル/ビュー | guild_id | 主キー / 一意制約 | 備考 |
|---|---|---|---|
| `guilds` | **PK そのもの** | PK `guild_id` | 新規。ギルド台帳 |
| `settings` | あり | PK `(guild_id, setting_key)` | 既存 |
| `teams` | あり | PK `team_id`, UNIQUE `(guild_id, team_key)` | カラム追加 |
| `members` | あり | PK `(guild_id, user_id)` | JSON 列はフォールバック化 |
| `skill_tags` | あり | PK `skill_tag_id`, UNIQUE `(guild_id, skill_name)` | 新規 |
| `member_skills` | あり | PK `(guild_id, user_id, skill_name)` | 新規 |
| `schedules` | あり | PK `schedule_id` | 既存 |
| `schedule_options` | あり | PK `option_id` | 既存（親 guild_id 冗長保持） |
| `schedule_votes` | あり | PK `vote_id`, UNIQUE `(guild_id, option_id, user_id)` | 既存 |
| `tasks` | あり | PK `local_task_id` | 既存 |
| `reminders_log` | あり | PK `reminder_id` | 既存 |
| `todoist_sections` | あり | PK `(guild_id, section_id)` | 既存 |
| `todoist_configs` | あり（PK） | PK `guild_id` | 新規。暗号文列を含む |
| `layer_sessions` | あり | PK `session_id`, UNIQUE `(guild_id, user_id)` | 既存 |
| `layer_records` | あり | PK `record_id` | 既存 |
| `layer_keta` | あり | PK `keta_id`, UNIQUE `(guild_id, keta_name)` | 既存 |
| `audit_log` | あり | PK `audit_id` | 新規 |
| `v_attendance`（view） | あり | — | 新規 |
| `v_team_summary`（view） | あり | — | 新規 |
| `v_todoist_status`（view） | あり | — | 新規。暗号文を含まない |

---

## 6. テーブル間の関係

```
guilds (1) ──┬─< settings            （ギルド別のキー値設定）
             ├─< teams ──< members.primary_team（論理参照）
             │      └── teams.member_role_id / leader_role_id（DiscordロールID）
             ├─< members ──< member_skills >── skill_tags
             ├─< schedules ──< schedule_options ──< schedule_votes
             │                                   └─ v_attendance が参照
             ├─< tasks
             ├─< todoist_sections（section_id ↔ team_key の論理参照）
             ├─< todoist_configs（1:1。暗号化トークン）
             ├─< layer_keta / layer_sessions / layer_records
             ├─< reminders_log
             └─< audit_log
```

- 物理 FK は `guilds → 各テーブル` と `member_skills → members / skill_tags`、
  既存の `schedule_options → schedules`、`schedule_votes → schedule_options` に限定。
- `members.primary_team` → `teams.team_key`、`tasks.team_key` → `teams.team_key`、
  `todoist_sections.team_key` → `teams.team_key` は**論理参照**とし、
  整合は Repository/Service 層で保証する（FK で縛ると班の無効化運用が困難なため）。
- attendance はテーブルを新設せず、`schedule_votes` を正本とする `v_attendance` ビュー。
- audit_log は他テーブルを参照しない独立した証跡テーブル（機密値を含めない）。

---

## 7. guild_id 伝播ルール（Repository 層から Cog 層まで）

以下を**強制規約**とし、レビューとテストで検査する。

| # | 層 | 規約 |
|---|---|---|
| R1 | Cog | コマンドハンドラは冒頭で `guild_id = await ensure_guild(interaction)` を呼び、`None` なら即 return（DM 拒否）。以降の全 DB アクセスにこの guild_id のみを使う |
| R2 | Cog | `interaction.guild.id` 以外の経路で guild_id を推測しない（レコードの `guild_id` 列から `bot.get_guild(record["guild_id"])` で逆引きする場合のみ許可） |
| R3 | Repository | 公開メソッドの**第1引数は必ず `guild_id: int`**。全 SQL に `WHERE guild_id = ?`（または INSERT 時の guild_id 指定）を含める |
| R4 | Repository | `guild_id` を引数に取らない公開メソッドを新規追加しない。`for_guild()` プロキシは移行期間の互換用途に限定し、新規コードでは使わない |
| R5 | Service | ギルド固有情報（トークン・プロジェクトID・設定）を**インスタンスのグローバル状態に持たない**。guild_id を明示引数で受けるか、ギルド別インスタンスをファクトリ経由で取得する |
| R6 | 背景ジョブ | `tasks.loop` は `for guild in self.bot.guilds:` で全ギルドを巡回し、**ギルド単位で try/except して例外を隔離**する。1ギルドの失敗が他ギルドに波及しない |
| R7 | 生 SQL | Cog から `bot.db.fetchall` 等を直接呼ばない（既存の `cogs/reports.py` 等の直叩きは Repository へ移管）。SQL は Repository 層に集約 |
| R8 | 設定解決 | ギルド別設定は `config.for_guild(guild_id)` 経由。変更後は必ず `config.invalidate_guild(guild_id)`。Todoist 設定は `TodoistServiceManager.invalidate(guild_id)` も併せて呼ぶ |
| R9 | 表示 | Embed / メッセージに他ギルドのデータが混入しないことを確認するため、一覧系クエリは必ず guild_id 条件付きにする（テストで検証） |
| R10 | NocoDB 経由の編集 | NocoDB で行を直接編集された場合に備え、bot は**読み取り時に値を再検証**する（存在しない team_key 等はエラー表示）。NocoDB 側の編集が他ギルドへ波及する経路は作らない |

### 権限チェックとの関係

- `utils/permissions.py` の L1-L4 判定は現行どおり `config.for_guild()` のギルド別ロール ID を使用。
- **変更点**: 班長（L2）判定は従来の settings `LEADER_ROLE_IDS` から、
  `teams.leader_role_id` の一覧（`MemberRepository.list_leader_role_ids(guild_id)` を新設）
  へ段階的に移行する。移行期間は両方を OR で評価する（仮定 A-4）。

---

## 8. Todoist トークンの暗号化・復号・権限チェック・エラー仕様

### 8.1 暗号化方式

- **Fernet（AES-128-CBC + HMAC-SHA256、`cryptography` パッケージ）** を採用。
  復号が必要なためハッシュ化は不可。対称鍵で十分（鍵の配布先が bot 1プロセスのみ）。
- 新規依存: `cryptography>=42.0.0`（requirements.txt に追加。実装フェーズで行う）。
- **ENCRYPTION_KEY は `.env` にのみ保持**する。DB・settings テーブル・コマンド引数・
  ログのいずれにも書かない。`.gitignore` の `.env` 除外は既存どおり有効。
- 鍵生成手順（運用ドキュメントに記載）:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

### 8.2 新規モジュール `utils/crypto.py`（設計案）

```
get_fernet()           -> Fernet            # ENCRYPTION_KEY を読み検証（起動時・初回利用時）
encrypt_token(plain)   -> str               # Fernet 暗号文（urlsafe base64 テキスト）
decrypt_token(cipher)  -> str               # 復号。失敗時は TokenDecryptError
is_encryption_ready()  -> bool              # 鍵が設定済みで有効か
```

例外:

- `EncryptionKeyMissingError`: ENCRYPTION_KEY 未設定または形式不正
- `TokenDecryptError`: 復号失敗（鍵変更・暗号文破損）

### 8.3 保存・利用フロー

```
/todoist-setup（L4, Modal でトークン入力）
  → ENCRYPTION_KEY 検証（NG なら明示エラー）
  → Todoist API でトークン検証（get_projects 1回。401/403 なら登録拒否）
  → encrypt_token(plain) → todoist_configs に UPSERT
  → audit_log に 'todoist.setup' を記録（トークンは含めない）
  → TodoistServiceManager.invalidate(guild_id)
  → 応答はマスク表示（例: 「先頭4文字…末尾2文字」）で ephemeral

各機能からの利用（/task add、朝通知など）
  → TodoistServiceManager.for_guild(guild_id)
     ├─ キャッシュ命中 → ギルド別 TodoistService を返す
     └─ ミス → todoist_configs を読み decrypt_token() → TodoistAPI(token) を構築しキャッシュ
  → 未登録ギルドでは enabled=False の no-op サービスを返す
```

### 8.4 TodoistServiceManager（設計案）

- Bot 起動時に1つだけ生成し `bot.todoist_manager` に保持。
- 内部に `dict[int, TodoistService]` のキャッシュを持つ。
- `for_guild(guild_id)` はそのギルド専用の `TodoistService`（トークン・
  project_id・label を保持）を返す。ギルド未登録なら `enabled=False`。
- `invalidate(guild_id)` / `invalidate_all()` を設定変更コマンドから呼ぶ。
- 現行のグローバル `bot.todoist` は移行期間のみ残し、最終的に削除。
  `cogs/tasks.py`・`cogs/reminders.py` の全呼び出しを `for_guild(guild_id)` 経由に書き換える。

### 8.5 後方互換（移行期間）

- 解決順: **`todoist_configs`（ギルド別DB） > 環境変数 `TODOIST_API_TOKEN`（レガシー、非推奨）**。
- env フォールバック使用時は起動ログに非推奨警告を出す（仮定 A-3）。

### 8.6 権限チェック

| コマンド | 権限 | 理由 |
|---|---|---|
| `/todoist-setup` | L4 | 機密情報の登録。監査ログ対象 |
| `/todoist-remove` | L4 | 機密情報の削除。監査ログ対象 |
| `/todoist-status` | L3 | 有効/無効・プロジェクト・マスク済みトークンのみ表示 |

### 8.7 エラー時仕様

| 状況 | 振る舞い |
|---|---|
| ENCRYPTION_KEY 未設定/不正 | `/todoist-setup` を「管理者が ENCRYPTION_KEY を .env に設定してください」で拒否。bot-log にも記録。既存の暗号文は復号不可のため Todoist 機能は全ギルドで無効扱い |
| 復号失敗（鍵ローテーション・破損） | 該当ギルドの Todoist を無効化し、`/todoist-status` に「復号失敗。再登録が必要」を表示。bot-log に警告（**暗号文・平文はログに出さない**） |
| トークン検証失敗（401/403） | セットアップ拒否。「トークンが無効です」を ephemeral で返す。DB には保存しない |
| Todoist API 障害・タイムアウト | 既存の `TODOIST_API_FAILED` と同じ扱い。時間をおいて再試行を案内 |
| 未登録ギルドで Todoist 依存コマンド実行 | 「このサーバーでは Todoist が未設定です。`/todoist-setup` で登録してください」を ephemeral で返す |
| ログ出力 | 平文トークン・暗号文・ENCRYPTION_KEY を**いかなるログにも出力しない**（コードレビューとテストで検査） |

### 8.8 トークン入力に Modal を使う理由

スラッシュコマンドのオプション値はチャンネルのコマンド履歴に表示され、
他メンバーが閲覧できる可能性がある。`/todoist-setup` は引数を取らず、
**`discord.ui.Modal` + TextInput でトークンを入力**させる。
Modal の入力値はコマンド履歴に残らないため、漏えい経路を減らせる。
応答は常に ephemeral とし、トークンはマスク表示のみ。

---

## 9. 新規スラッシュコマンド完全仕様

### 共通仕様

- すべて `ensure_guild()` でギルド内実行を強制し、応答は ephemeral。
- 変更系コマンドは `audit_log` に `(actor_id, action, target, detail)` を記録する
  （**機密値は記録しない**）。
- 変更後は関連キャッシュを無効化する（`config.invalidate_guild` /
  `TodoistServiceManager.invalidate`）。
- エラーは既存の `error_embed` / グローバルエラーハンドラに統一。

### 9.1 班管理（新 Cog `cogs/teams.py` に集約 or `cogs/members.py` 拡張）

| コマンド | 権限 | 引数 | 仕様 |
|---|---|---|---|
| `/team-add` | L3 | `key`(必須), `name`(必須), `channel`(任意), `member_role`(任意), `leader_role`(任意) | 班を追加。`key` は `^[a-z0-9_-]{1,32}$` で検証。重複 key は拒否。`teams` に INSERT（created_at/updated_at 記録）。audit_log に `team.add` |
| `/team-remove` | L3 | `key`(必須, autocomplete) | 班を**無効化**（`active_flag=0`、物理削除しない）。主所属に設定しているメンバーが存在する場合は件数を警告し、確認フラグ `confirm:true` を要求。無効化後は autocomplete 候補から消えるが、既存メンバーの `primary_team` 値は保持（再登録時の復活に対応）。audit_log に `team.remove` |
| `/team-list` | L1 | なし | 有効/無効の班一覧を表示（key・表示名・通知チャンネル・ロール設定有無・所属人数）。閲覧のみ |
| `/team-role` | L3 | `key`(必須, autocomplete), `member_role`(任意), `leader_role`(任意) | 班のロール ID を更新。少なくとも一方は必須。`teams.member_role_id` / `leader_role_id` を更新し、権限キャッシュを無効化。audit_log に `team.role` |

### 9.2 技能タグ管理

| コマンド | 権限 | 引数 | 仕様 |
|---|---|---|---|
| `/skill-add` | L3 | `name`(必須) | 技能タグを追加。`skill_tags` に INSERT（重複は拒否または再有効化）。audit_log に `skill.add` |
| `/skill-remove` | L3 | `name`(必須, autocomplete) | 技能タグを**無効化**（`active_flag=0`）。付与済みの `member_skills` 行は保持し、表示時に「(廃止)」を付記。audit_log に `skill.remove` |
| `/skill-list` | L1 | なし | 有効/無効の技能タグ一覧と付与人数を表示 |

- 既存の `/member skill add` `/member skill remove`（メンバーへの付与/剥奪）は**存続**するが、
  選択肢を固定 `SKILL_CHOICES` から DB の `skill_tags` を使う **autocomplete** に変更し、
  書き込み先を `member_skills` に切り替える。

### 9.3 Todoist 管理

| コマンド | 権限 | 引数 | 仕様 |
|---|---|---|---|
| `/todoist-setup` | L4 | なし（Modal で `api_token` 必須・`project_id` 任意・`today_label_name` 任意を入力） | 8.3 のフローで登録。既存設定がある場合は上書き（確認文言を応答に含める）。成功時はマスク表示＋プロジェクトIDを ephemeral で返す |
| `/todoist-remove` | L4 | なし | `todoist_configs` の当該ギルド行を削除し、キャッシュを無効化。audit_log に `todoist.remove`。以降そのギルドの Todoist 機能は no-op |
| `/todoist-status` | L3 | なし | 有効/無効、project_id、today_label_name、マスク済みトークン、最終更新日時、復号可否を表示。平文は表示しない |

---

## 10. 既存コマンドへの影響（固定 Choice → DB 駆動 autocomplete）

`config.INITIAL_TEAMS` / `config.SKILL_TAGS` を削除するため、以下のコマンドの
選択肢生成を `teams` / `skill_tags` テーブルを参照する autocomplete に変更する。

| コマンド | 現状 | 変更後 |
|---|---|---|
| `/member register`, `assign-team`, `assign-sub-team`, `setup` | `TEAM_CHOICES` 固定 | `teams`（active のみ）の autocomplete |
| `/member set-channel`, `support` | 同上 | 同上 |
| `/member skill add/remove` | `SKILL_CHOICES` 固定 | `skill_tags`（active のみ）の autocomplete |
| `/task add`, `team`, `link-section`, `unlink-team-sections` | `TEAM_CHOICES` 固定 | `teams` の autocomplete |
| `cogs/sheets.py` の `TEAM_NAME` 辞書 | `INITIAL_TEAMS` から生成 | Sheets Cog ごと削除（フェーズ3） |
| `cogs/reports.py` の `TEAM_NAME` 辞書 | `INITIAL_TEAMS` から生成 | `MemberRepository.list_teams(guild_id)` で key→name を解決 |
| `bot.py` `_seed_teams()` | `INITIAL_TEAMS` を投入 | 新規ギルドセットアップ時に `utils/seed.py` の既定セットを投入（11 章参照） |

### 班名表示の解決ルール

- 各 Cog は「班 key → 表示名」の解決に `MemberRepository.get_team(guild_id, key)` または
  キャッシュ付きの `TeamResolver`（Service 層に新設）を使う。
- `config.py` の `INITIAL_TEAMS` / `SKILL_TAGS` と、それらを参照するモジュール定数
  （`TEAM_CHOICES` / `SKILL_CHOICES` / `TEAM_NAME`）は**完全に削除**する。

---

## 11. 新規ギルド参加時の自動セットアップ

現行の `_ensure_guild_setup()` を拡張する。

1. `guilds` テーブルへ `(guild_id, guild_name, joined_at, setup_version=2)` を冪等 INSERT
2. settings 既定値（`GUILD_NAME` / `SETUP_VERSION` / `SETUP_AT`）を `set_if_absent`
3. 既定の班セットを `teams` へ upsert（`utils/seed.py` の `DEFAULT_TEAMS`、内容は現行
   `INITIAL_TEAMS` 相当の8班。**config.py からは削除し、seed 専用モジュールに移す**）
4. 既定の技能タグを `skill_tags` へ INSERT（`DEFAULT_SKILL_TAGS`、現行 `SKILL_TAGS` 相当）
5. ロール自動作成（権限がある場合）: `幹部` / `Bot管理者` / 各班リーダー / 各班ロール。
   作成したロール ID は `teams.leader_role_id` / `teams.member_role_id` と
   settings の `EXEC_ROLE_ID` / `ADMIN_ROLE_ID` に保存（`LEADER_ROLE_IDS` は廃止方向）
6. `#bot-log` チャンネル作成（権限がある場合）
7. `AUTO_SETUP_DONE` マーカーで冪等化（現行どおり）

- `todoist_configs` は**自動作成しない**（トークンは人手で登録するため）。
- 既存ギルドへの移行は自動マイグレーション（4.6）で行い、ギルド台帳・既定班・
  既定技能タグの欠落は起動時に補完する。

---

## 12. Google Sheets からのデータ移行手順とロールバック方針

### 12.1 移行スクリプト

- 配置: `scripts/migrate_sheets_to_db.py`（一回限りの運用スクリプト。
  bot 本体からは import しない）。
- 役割: 既存 Google Sheets に**直接編集された・または DB に存在しない**データを
  DB へ取り込む。多くのデータは DB が正本で Sheets はコピーだが、
  過去の出欠・Sheets 上で直接編集された行が取りこぼしになる可能性に備える。
- 依存: gspread / google-auth（スクリプト実行時のみ使用。完了後に依存ごと削除）。
- モード:
  - **dry-run（既定）**: 取り込み対象件数と差分サンプルを表示するだけで DB を変更しない。
  - **--apply**: 実際に INSERT する。
- 冪等性: 自然キーで重複判定し、2回実行しても件数が増えないこと。
  - members: `(guild_id, user_id)`
  - 出欠（schedule_votes）: `(guild_id, option_id, user_id)`
  - tasks: `(guild_id, local_task_id)`（Sheets の「ローカルID」列）
  - layer_records: `(guild_id, user_id, keta, started_at)` の一致で重複判定
- 対象ギルド: `--guild-id` 必須。取り込み先の guild_id を明示する
  （Sheets に guild 概念が無いため）。
- 取り込み対象:
  | Sheets | 取り込み先 | 判定 |
  |---|---|---|
  | `members` シート | `members` | DB に無い user_id のみ INSERT。技能は `skill_tags` 自動登録のうえ `member_skills` にも展開 |
  | `attendance` シート | `schedule_votes` | 対応する schedule/option が DB に存在する行のみ。存在しない schedule_id はスキップして報告 |
  | `tasks` シート | `tasks` | DB に同じ local_task_id が無い行のみ（Todoist 由来の正本は DB のため原則差分なしのはず） |
  | 桁別シート | `layer_records` | 重複判定のうえ未登録のみ INSERT。桁名は `layer_keta` にも登録 |
  | `audit_log` シート | 取り込まない | 旧形式の監査は Sheets に残し、DB の `audit_log` は新規分から |
- スキップ・重複・取り込みの件数をシートごとにレポート出力する。

### 12.2 移行手順

1. `data/club.db` のバックアップ（`cp data/club.db data/club.db.bak.$(date +%Y%m%d)`）
2. Bot を停止（systemd stop）
3. dry-run 実行で差分を確認
4. `--apply` で取り込み
5. Bot を起動し、主要機能（`/task list`、`/schedule list`、`/layer status`、
   NocoDB での表示）を確認
6. 問題なければ Sheets 共有設定を閲覧専用に変更（誤編集防止）

### 12.3 ロールバック方針

- **スクリプトは INSERT のみ**で UPDATE/DELETE を行わないため、
  最悪でも「余分な行が入った」状態にしかならない。
- ロールバックは**バックアップからの DB ファイル差し戻し**を正とする。
- Sheets 側は読み取りしかしないため、Sheets 自体は無変更（ロールバック不要）。
- 取り込み直後に不整合を検出した場合は、移行前バックアップに戻してやり直す。

---

## 13. NocoDB 運用構成（Docker Compose）

### 13.1 構成案（SQLite 共有・開始時）

`deploy/docker-compose.nocodb.yml`（実装フェーズで作成）:

```yaml
services:
  nocodb:
    image: nocodb/nocodb:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"   # ローカルのみ公開。外部は SSH トンネル or 認証付きリバプロ経由
    volumes:
      - ./data:/data:rw          # bot の SQLite (data/club.db) を共有
      - nocodb_meta:/usr/app/data  # NocoDB 自身のメタDB
    environment:
      NC_AUTH_JWT_SECRET: ${NOCODB_JWT_SECRET}
volumes:
  nocodb_meta:
```

- 起動後、NocoDB の UI から「External Database」として SQLite 接続
  （ファイルパス `/data/club.db`）を追加する手順を運用ドキュメントに記載する。
- SQLite の同時アクセス対策:
  - bot 側は既に WAL モード。これに加えて `PRAGMA busy_timeout` を設定する（実装で追加）。
  - NocoDB からの書き込みは**マスタ系・修正系の軽微な操作に限定**する運用とし、
    大量更新は行わない。
- 公開範囲: `127.0.0.1` バインドを既定とし、外部からは SSH ポートフォワード
  （`ssh -L 8080:localhost:8080`）または認証付きリバースプロキシでアクセスする。

### 13.2 PostgreSQL 移行後の構成

- bot・NocoDB の双方が PostgreSQL に接続する構成に変更する。
- 接続情報（ホスト/DB名/ユーザー/パスワード）は `.env` に追加する設計とするが、
  具体的な移行手順は本書のスコープ外（仮定 A-8）。

### 13.3 暗号文列を一般メンバーに見せない運用（文書化必須）

運用ドキュメント（`docs/NOCODB_OPERATION.md` を実装フェーズで新規作成）に以下を明記する。

1. NocoDB のロールで `todoist_configs` テーブル自体を**管理者ロール以外から非表示**にする
   （テーブル単位の権限で遮断するのを正とする）。
2. やむを得ず参照を許可する場合は、暗号文を含まない **`v_todoist_status` ビュー**のみ公開する。
3. `audit_log`・`settings`（`EXEC_ROLE_ID` 等は機密ではないが運用情報）は閲覧ロールに限定。
4. NocoDB の初期管理者アカウントは運用者1名とし、一般メンバーには
   「閲覧専用ロール」を発行する。
5. ENCRYPTION_KEY は NocoDB 側には一切登録しない（NocoDB からは復号できないため、
   暗号文が見えても実害は限定的だが、見せないことを原則とする）。

---

## 14. セキュリティ設計

| 脅威 | 対策 |
|---|---|
| 他ギルドのデータ閲覧・更新 | R1-R10 の guild_id 強制。Repository 全メソッドに guild_id 条件。2ギルド分離テストで検証 |
| Todoist トークン漏えい（DB 流出） | Fernet 暗号化。ENCRYPTION_KEY は .env のみ。DB だけでは復号不可 |
| トークン漏えい（Discord 履歴） | Modal 入力 + ephemeral 応答 + マスク表示 |
| トークン漏えい（ログ） | 平文・暗号文・鍵をログ出力しない（テストで検査） |
| トークン漏えい（NocoDB） | テーブル非表示 + ビュー限定（13.3） |
| 設定一覧での機密表示 | `/settings_list` から `TODOIST_API_TOKEN` を除外し、値は `（暗号化保存）` 等の表記に変更 |
| NocoDB への不正アクセス | 127.0.0.1 バインド + SSH トンネル/認証付きリバプロ + NocoDB ロール分離 |
| NocoDB からの誤編集 | 変更系は bot コマンドを正とし、NocoDB は閲覧＋軽微な修正に限定する運用ルールを文書化 |
| 暗号鍵の紛失 | 復号不可になるため、鍵のバックアップ手順と再登録手順（`/todoist-setup` 再実行）を運用文書に記載 |

---

## 15. テスト戦略と受け入れ条件

### 15.1 テスト戦略

| 層 | 内容 | 追加先 |
|---|---|---|
| 単体 | `utils/crypto.py` の暗号化/復号ラウンドトリップ、鍵未設定・不正鍵・破損暗号文の例外 | `tests/test_crypto.py`（新規） |
| 単体 | teams / skill_tags / member_skills / todoist_configs / audit_log の各 Repository CRUD | `tests/test_repositories_v2.py`（新規） |
| 分離 | 2ギルドで teams・skills・todoist_configs・audit_log が混ざらないこと | 既存 `tests/test_multi_tenant.py` に追記 |
| 移行 | 旧スキーマ + 旧 settings（平文トークン・PRIMARY_TEAM_ROLE_IDS）からの自動マイグレーション | 同上 |
| 移行スクリプト | フィクスチャ Sheets データで dry-run/apply の冪等性（2回実行で件数不変） | `tests/test_migrate_script.py`（新規、gspread はモック） |
| 権限 | `/todoist-setup` 等が L4 未満で拒否されること（permissions 単体テスト） | 既存方針に準拠 |
| ログ検査 | トークン文字列がログ出力に含まれないこと（crypto/サービスのモック検証） | `tests/test_crypto.py` 内 |

### 15.2 受け入れ条件（チェックリスト）

- [ ] 既存 `tests/test_multi_tenant.py` および `tests/test_parser.py` が全件パス
- [ ] 新規テーブル・ビューが自動マイグレーションで作成され、2回起動しても冪等
- [ ] 2ギルドで互いの teams / skill_tags / member_skills / todoist_configs / audit_log が見えない
- [ ] DB ファイル内に Todoist トークンの平文が存在しない（`strings`/検索で確認）
- [ ] ENCRYPTION_KEY 未設定時に `/todoist-setup` が明確なエラーを返す
- [ ] `/todoist-setup` → `/todoist-status` → `/todoist-remove` が1ギルド単位で完結し、他ギルドに影響しない
- [ ] `/team-add` した班が `/task add` 等の autocomplete に即時反映される
- [ ] config.py から `INITIAL_TEAMS` / `SKILL_TAGS` が削除され、参照箇所が残っていない
- [ ] Sheets 依存コード削除後も `/task` `/schedule` `/layer` `/report` が正常動作する
- [ ] 移行スクリプトが dry-run で差分のみ表示し、--apply が冪等である
- [ ] NocoDB の一般ロールで `todoist_configs` が見えない（手動確認手順を文書化）

---

## 16. 実装順序（フェーズ分割）

| フェーズ | 内容 | 主な変更ファイル | 完了条件 |
|---|---|---|---|
| **P0: 準備** | 既存バグ修正（`attendance-rate` の `yes`→`ok`）、未実装メソッド呼び出しの整理（Sheets/Todoist の未定義メソッド）、`busy_timeout` 追加 | `cogs/reports.py`, `utils/db.py`, ほか | 既存テスト全パス＋バグ修正の回帰テスト |
| **P1: 暗号化・Todoist ギルド別化** | `utils/crypto.py`、`todoist_configs`、`TodoistServiceManager`、`/todoist-setup|remove|status`、既存 Todoist 呼び出しの `for_guild` 化、requirements に `cryptography` 追加 | `utils/crypto.py`, `services/todoist_service.py`, `cogs/settings.py` or 新 Cog, `cogs/tasks.py`, `cogs/reminders.py`, `requirements.txt` | 8.6/8.7 の仕様どおり動作、平文非保存の確認 |
| **P2: 班・技能のDB化** | `skill_tags`/`member_skills`/teams カラム追加、`/team-*` `/skill-*` コマンド、autocomplete 化、config.py から固定配列削除、`utils/seed.py` 新設、権限の `teams.leader_role_id` 対応 | `config.py`, `bot.py`, `cogs/members.py`, `cogs/tasks.py`, `cogs/reports.py`, `utils/permissions.py`, repositories | 10 章の変更が反映されテスト全パス |
| **P3: Sheets 削除・ビュー・監査** | `cogs/sheets.py`・`services/sheets_service.py` 削除、Sheets 同期呼び出し除去、`v_attendance`/`v_team_summary`/`v_todoist_status` 作成、`audit_log` 実装、requirements から gspread/google-auth 削除 | `cogs/sheets.py`, `services/sheets_service.py`, `cogs/reminders.py`, `utils/db.py`, `requirements.txt` | Sheets 無しで全機能動作 |
| **P4: 移行スクリプト** | `scripts/migrate_sheets_to_db.py` 作成、フィクスチャでの冪等性テスト、本番移行手順の実施記録 | `scripts/`, `tests/test_migrate_script.py` | dry-run/apply が仕様どおり |
| **P5: NocoDB 導入** | `deploy/docker-compose.nocodb.yml`、`docs/NOCODB_OPERATION.md`（接続手順・ロール分離・暗号文列の非表示運用） | `deploy/`, `docs/` | NocoDB から閲覧可能、権限分離確認 |
| **P6: PG 移行準備（任意）** | 型の抽象化確認、移行手順メモ | `utils/db.py`, `docs/` | 移行手順が文書化される |

各フェーズは独立してデプロイ可能とし、完了ごとにテスト全件パスを確認する。

---

## 17. 設計上の仮定

実装で勝手に補わず、以下は**仮定**として明示する。実装前に検証または判断が必要。

- **A-1: NocoDB の SQLite 外部接続対応**は未検証。接続できない場合は PostgreSQL 移行（P6）を前倒しする。
- **A-2: NocoDB が SQLite の VIEW（`v_attendance` 等）を表示できるか未検証**。
  表示できない場合は attendance / team_summary を実テーブル化し、
  更新トリガまたは定期ジョブで維持する設計に切り替える。
- **A-3: env の `TODOIST_API_TOKEN` は移行期間のレガシーフォールバック**として残し、
  非推奨警告を出す。完全削除時期は別途決定する。
- **A-4: 班ロールは `member_role_id` の1系統に統合**し、
  従来の `SECONDARY_TEAM_ROLE_IDS`（副所属専用ロール）は廃止する。
  副所属専用ロールが運用上必要な場合は `teams.secondary_member_role_id` を追加する。
  また L2 判定は移行期間 `LEADER_ROLE_IDS` と `teams.leader_role_id` の OR とする。
- **A-5: `members.skills` / `secondary_teams` の JSON 列は読み取りフォールバックのみ**とし、
  書き込み正本は `member_skills` / 既存ロジックに集約。JSON 列の削除は将来の
  マイグレーションで行う（二重書きは不整合の元のため行わない）。
- **A-6: NocoDB からの編集はマスタ系・軽微な修正に限定**する運用とし、
  双方向同期の仕組みは作らない。競合時は DB（bot 書き込み）を正とする。
- **A-7: `layer_records.synced_flag` は Sheets 同期用のため用途消滅**。
  新規 INSERT を常に 1 とするか、カラム削除は将来マイグレーションで判断する。
- **A-8: PostgreSQL 移行の具体的な接続設計・手順は本書スコープ外**とし、
  P6 で別途設計する。
- **A-9: 監査ログの記録対象は管理者操作（todoist/team/skill/settings 変更）に限定**し、
  一般コマンド（/task add 等）は記録しない。記録範囲の拡大は別途検討。
- **A-10: 班・技能の既定セット（seed）の内容**は現行 `INITIAL_TEAMS` / `SKILL_TAGS`
  と同一とするが、新規ギルドごとに変更したい場合は seed モジュールを編集する
  （ギルド個別の初期値指定は行わない）。
- **A-11: `/team-remove` 無効化時の既存メンバー扱い**は「`primary_team` の値は保持、
  表示時に無効班である旨を示す」とする。無効班への新規割当は autocomplete に
  出さないことで防止する。
- **A-12: `guild_id = 0` のレガシーデータ**は既存ドキュメントどおり手動で
  実ギルド ID へ付け替える運用とし、自動付け替えは行わない。
