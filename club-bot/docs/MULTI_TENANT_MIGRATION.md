# マルチテナント化 移行ドキュメント

discord.py 製 club-bot を「1プロセスで複数ギルドを安全に扱うマルチテナント
仕様」へ改修した際の変更内容・設計判断・移行手順をまとめる。

## 1. 変更内容サマリ

| レイヤ | 変更 |
|---|---|
| DB スキーマ | 全12テーブルに `guild_id` 追加。複合キー・インデックス再設計。自動マイグレーション内蔵 |
| config.py | `GUILD_ID` 必須を廃止。`config.for_guild(guild_id)` によるギルド別設定解決（キャッシュ付き）を導入 |
| repositories | 全メソッドの第1引数に `guild_id: int` を必須化。全 SQL に `guild_id` 条件を付与 |
| cogs | 各コマンドで `interaction.guild.id` を取得して下位へ伝播。DM 実行は拒否。バックグラウンドループは全ギルド巡回 |
| bot.py | `on_guild_join` 自動セットアップ実装。起動時に参加中全ギルドへ初期チーム投入・コマンド同期 |
| tests | `tests/test_multi_tenant.py` 追加（2ギルド分離・マイグレーション検証） |
| migrations | `migrations/001_add_guild_id.sql`（手動移行用） |
| docs | `docs/GUILD_VIEWS.sql`（NocoDB 向けギルド別ビュー雛形） |

`services/`（sheets_service / schedule_service / layer_tracking_service /
todoist_service）と Google Sheets 関連設定項目（spreadsheet_id, sheet_tasks
等）は変更していない。

## 2. DB スキーマ設計

### guild_id カラム

```sql
guild_id INTEGER NOT NULL CHECK (guild_id >= 0)
```

- Discord のギルド ID は 64bit 整数（スノーフレーク）。SQLite の INTEGER は
  8バイト符号付きなのでそのまま格納できる。
- 将来 PostgreSQL へ移行する場合は **BIGINT** に対応させる。カラム型は
  `utils/db.py` の `GUILD_ID_TYPE` に集約し、`CHECK (guild_id >= 0)` で
  非負を保証している。
- `guild_id = 0` は「レガシー/未帰属」データの sentinel。GUILD_ID 未設定の
  まま旧 DB を起動した場合のバックフィル値であり、実在のギルド ID と
  衝突しない。

### キー設計（複合キーは guild_id 先頭）

| テーブル | 主キー / ユニーク制約 |
|---|---|
| settings | PK `(guild_id, setting_key)` |
| teams | PK `team_id` + UNIQUE `(guild_id, team_key)` |
| members | PK `(guild_id, user_id)` |
| schedules | PK `schedule_id`（UUID） + guild_id 列 |
| schedule_options | PK `option_id`（UUID） + guild_id 列（親の冗長保持） |
| schedule_votes | PK `vote_id` + UNIQUE `(guild_id, option_id, user_id)` |
| tasks | PK `local_task_id` + guild_id 列 |
| reminders_log | PK `reminder_id` + guild_id 列 |
| todoist_sections | PK `(guild_id, section_id)` |
| layer_sessions | PK `session_id` + UNIQUE `(guild_id, user_id)` |
| layer_records | PK `record_id` + guild_id 列 |
| layer_keta | PK `keta_id` + UNIQUE `(guild_id, keta_name)` |

- schedule_options / schedule_votes / layer_records などの子テーブルは
  **親の guild_id を冗長保持**し、子だけを見てもスコープが確定するように
  した（JOIN なしで `WHERE guild_id = ?` を徹底できる）。
- 代表インデックス: `idx_schedules_guild(guild_id, closed_flag, deadline)`、
  `idx_tasks_guild_status(guild_id, status)`、
  `idx_members_guild(guild_id, active_flag)` など。

### NocoDB 対応

- テーブル名・カラム名はスネークケースを維持。
- 各テーブルは単一整数 PK または `(guild_id, ...)` の分かりやすいキーを持つ。
- ギルド別ビューの雛形は [`GUILD_VIEWS.sql`](GUILD_VIEWS.sql) を参照
  （`v_<table>_g<guild_id>` 命名で `WHERE guild_id = :guild_id` を固定）。

## 3. ギルド別設定解決の仕組み（設計判断の要点）

### 解決フロー

```
config.for_guild(guild_id)
  ├─ キャッシュ命中 → GuildConfig を返す
  └─ ミス → GuildConfig を構築
       1. 環境変数由来のグローバル値（Config の各フィールド）をフォールバックとして設定
       2. settings テーブルから guild_id 一致の行を読み、存在するキーだけ上書き
       3. キャッシュに格納して返す
```

- 優先順: **ギルド別 DB 設定 > 環境変数 > デフォルト**。
- キャッシュは `/settings_set` 等の変更コマンドが
  `config.invalidate_guild(guild_id)` を呼んで明示的に破棄する
  （TTL なし・変更経路はコマンドに限定されるため）。
- `Config._db` に setup_hook 時の接続を保持するため、権限チェックなど
  どこからでも `await config.for_guild(guild.id)` で解決できる。

### GuildConfig に含まれるもの（ギルド固有）

- チャンネル ID: BOT_LOG / DEFAULT_ANNOUNCE / DEFAULT_SCHEDULE /
  DEFAULT_PROGRESS / DEFAULT_TASK / TODAY_LABEL
- ロール ID: EXEC_ROLE_ID / ADMIN_ROLE_ID / LEADER_ROLE_IDS /
  PRIMARY_TEAM_ROLE_IDS / SECONDARY_TEAM_ROLE_IDS

### グローバルのままのもの

- Discord トークン、TZ、DB_PATH
- Todoist 連携設定（トークン・プロジェクト・ラベル名）
- Google Sheets 関連設定（spreadsheet_id, sheet_tasks 等。変更禁止のため）

これらグローバル項目は従来どおり `config.load_from_db(db)` が
**レガシーギルド（env GUILD_ID）の settings 行**から補完する。
GUILD_ID 未設定時は環境変数のみで動作する。

### リポジトリの guild_id 強制と services 互換

- 全リポジトリの公開メソッドは `guild_id: int` を第1引数に必須化し、
  全 SQL に `WHERE guild_id = ?` を付けた。
- 変更禁止の `services/` は guild_id を渡せないため、
  `repositories/base.py` の **`repo.for_guild(guild_id)` プロキシ**を導入。
  プロキシは呼び出しの先頭に自動で guild_id を注入する。Cog は
  `LayerTrackingService(self.session_repo.for_guild(gid), ...)` のように
  ギルド固定スコープでサービスを構成する。

## 4. Cog / バックグラウンド処理の規約

- 各コマンドハンドラは冒頭で
  `guild_id = await ensure_guild(interaction)` を呼び、`None` なら return
  （DM は拒否メッセージを返す）。以降の全 DB アクセスに guild_id を渡す。
- 権限チェック（`utils/permissions.py`）も `config.for_guild()` の
  ギルド別ロール ID で判定する（サーバーオーナー/管理者権限は従来どおり L4）。
- tasks.loop 系は `for guild in self.bot.guilds:` で全ギルドを巡回し、
  ギルドごとにリポジトリ検索・通知先解決を行う。
- `bot.get_guild(config.guild_id)` のハードコードは排除し、レコードの
  `guild_id` から `bot.get_guild(record["guild_id"])` で解決する。
- `log_to_channel(message, guild_id=None)` は guild 指定時はそのギルドの
  ログチャンネルのみ、未指定時は全ギルドのログチャンネルへブロードキャスト。

## 5. 新規ギルドの自動セットアップ（on_guild_join）

招待された瞬間に以下を実行（失敗しても落ちないよう try/except で保護）:

1. settings にギルド用デフォルト設定を INSERT（`GUILD_NAME` /
   `SETUP_VERSION` / `SETUP_AT`。未存在時のみ）
2. INITIAL_TEAMS 8班を guild_id 付きで upsert
3. 権限があればロール（`幹部` / `Bot管理者` / `{班名}班リーダー` × 8 /
   `{班名}班` × 8）と `#bot-log` チャンネルを作成し、ID を settings に保存
   （`set_if_absent` のみ。既存設定は上書きしない）
4. `AUTO_SETUP_DONE` マーカーを保存し、2重作成を防止（冪等）
5. スラッシュコマンドをそのギルドへ同期（`copy_global_to` + guild sync）

起動時（on_ready 初回）も参加中全ギルドに同じセットアップを適用するため、
オフライン中に招待されていたギルドや、過去のギルドでも初期チームの存在が
保証される。

## 6. 既存環境からの移行手順

### 自動移行（推奨）

1. `data/club.db` をバックアップ:
   `cp data/club.db data/club.db.bak.$(date +%Y%m%d)`
2. `.env` の `GUILD_ID` を従来どおり設定したまま Bot を起動。
3. `utils/db.py` の `_migrate()` が起動時に:
   - 全テーブルを `<table>_legacy` にリネーム → 新スキーマ作成 →
     `guild_id = GUILD_ID` でバックフィル → 旧テーブル削除
     （SQLite テーブル再作成方式・FK 一時停止付き）
   - 新インデックスを再作成
4. ログに `guild_id マイグレーションが完了しました` と出れば完了。
   GUILD_ID 未設定の場合は `guild_id = 0` でバックフィルされる
   （後から `UPDATE ... SET guild_id = <ID> WHERE guild_id = 0` で修正可能）。

### 手動移行

[`migrations/001_add_guild_id.sql`](../migrations/001_add_guild_id.sql) を使用:

```bash
cp data/club.db data/club.db.bak
sqlite3 data/club.db \
  -cmd ".parameter set :legacy_guild_id <GUILD_ID>" \
  ".read migrations/001_add_guild_id.sql"
```

### 後方互換

- env `GUILD_ID` 指定の既存運用は従来どおり動く（そのギルドの設定が
  グローバル設定としても読み込まれ、コマンドも当該ギルドへ即時同期される）。
- ギルド別設定が未登録のギルドは環境変数の値にフォールバックするため、
  env だけの運用でも複数ギルドで共通設定を使い回せる。

## 7. 既知の制約

- **Google Sheets**: スプレッドシートがグローバル1冊のため、複数ギルドで
  Sheets 連携を有効にすると同一シートへの上書き競合が起きる。日程調整の
  個別シートはタイトル単位なので共存可能だが、tasks/members/attendance の
  全行置換系は実質レガシー単一ギルド向け。
- **Todoist**: API トークンがグローバルのため、全ギルドが同一プロジェクトを
  共有する。また「今日やること」ラベルの通知（`_notify_today_label`）と
  未紐付けセクションの集約（`push_section_tasks`）は、設定済みチャンネルを
  持つ**全ギルドへ同一内容をブロードキャスト**するため、ギルド A のタスク
  内容がギルド B のチャンネルに表示され得る。ギルドごとのプロジェクト分離
  および送信先スコープの限定は今後の課題。
- **Sheets/Todoist の設定コマンド**: `/set_sheets`・`/set_todoist` は任意
  ギルドの settings に保存できるが、グローバルサービスはレガシーギルド
  （env GUILD_ID）の行からのみ読み込むため、非レガシーギルドで設定しても
  実際には反映されない点に注意。
- `guild_id = 0` のレガシーデータはどのギルドからも参照されない
  （手動で実ギルド ID へ付け替えること）。

## 8. 検証

- `python -m compileall club-bot` で全ファイルの構文チェック。
- `python -m pytest tests/` で 11 件パス:
  - 新規スキーマに guild_id が存在すること
  - 旧スキーマ DB からの自動マイグレーション（全12テーブルのバックフィル）
  - members / teams / tasks / schedules / settings / sections / layer の
    2ギルド分離（同名ユーザー・同一 section_id・同一ユーザー同時セッション
    などが混ざらないこと）
  - `for_guild` プロキシの guild_id 注入
  - `config.for_guild` の解決順（DB > env）とキャッシュ無効化

## 9. 002 マイグレーション: DB 基盤強化（guilds / audit_log / スキーマバージョン）

### 変更内容

| レイヤ | 変更 |
|---|---|
| DB スキーマ | `guilds`（ギルド台帳）と `audit_log`（監査ログ）を追加。`audit_log` に `(guild_id, audit_id)` インデックス |
| バージョン管理 | `PRAGMA user_version` によるスキーマバージョンを導入（現在値: 2。guild_id 導入済みの従来スキーマは 1 相当、user_version=0 として扱う） |
| バックフィル | settings に存在する正の guild_id を `guilds` 台帳へ自動登録（名称は `GUILD_NAME` 設定、無ければ `(unknown)`。起動時の自動セットアップが正しい名称で上書き） |
| 接続設定 | `PRAGMA busy_timeout = 5000` を追加（NocoDB 等の外部ツールとの同時アクセス対策） |
| bot.py | `_ensure_guild_setup()` がギルド台帳へ冪等登録（既存なら名称のみ更新） |
| repositories | `GuildRepository` / `AuditLogRepository` / `RemindersLogRepository` を追加。`ScheduleRepository.list_all()` を追加 |
| cogs | `cogs/reminders.py`・`cogs/reports.py` の生 SQL を Repository へ移管（設計書 R7。振る舞いは変更なし） |

### 実行方法

**自動（推奨）**: Bot を起動するだけで適用される。

1. `data/club.db` をバックアップ:
   `cp data/club.db data/club.db.bak.$(date +%Y%m%d)`
2. Bot を起動。`utils/db.py` の `_migrate_versioned()` が
   `guilds` / `audit_log` を作成し、台帳をバックフィルした上で
   `user_version` を 2 に更新する（冪等。何度起動しても安全）。
3. ログに `スキーマバージョンを 2 に更新しました` と出れば完了。

**手動**: [`migrations/002_guild_foundation.sql`](../migrations/002_guild_foundation.sql) を使用:

```bash
cp data/club.db data/club.db.bak
sqlite3 data/club.db ".read migrations/002_guild_foundation.sql"
```

前提: 001（guild_id 導入）適用済みであること。実行後の検証:

```sql
SELECT * FROM guilds;
PRAGMA user_version;  -- 2 が返ること
```

### ロールバック

- 本マイグレーションは**テーブル追加と INSERT のみ**で、既存テーブルの
  変更・削除を行わない。問題時はバックアップから DB ファイルを差し戻す。

## 10. 003 マイグレーション: 班・技能タグの DB 管理化（スキーマバージョン 3）

### 変更内容

| レイヤ | 変更 |
|---|---|
| DB スキーマ | `skill_tags`（技能タグ マスタ）追加。`teams` に `member_role_id` / `secondary_role_id` / `created_at` / `updated_at` 追加 |
| バックフィル | settings の `PRIMARY_TEAM_ROLE_IDS` / `SECONDARY_TEAM_ROLE_IDS`（`team_key:role_id` の CSV）を `teams.member_role_id` / `secondary_role_id` へ移行。settings のキー自体は後方互換のフォールバックとして残す |
| config.py | `INITIAL_TEAMS` / `SKILL_TAGS` の固定配列を**削除**。班・技能タグは DB 管理に一本化 |
| bot.py | 新規ギルドへの初期班シード・班ロール自動作成を**廃止**。新規ギルドは班・技能タグが空の状態で開始し、管理者がコマンドで登録する（幹部/Bot管理者ロールと bot-log の自動作成は継続） |
| cogs | 新規 `cogs/teams.py`（`/team-add` `/team-remove` `/team-list` `/team-role` `/skill-add` `/skill-remove` `/skill-list`。すべて管理者 L4 限定）。`cogs/members.py`・`cogs/tasks.py` の班・技能の選択肢を固定 Choice から DB 駆動 autocomplete に変更 |
| ロール同期 | `cogs/members.py` の `_sync_roles` は teams テーブルのロール紐付けを優先し、settings の旧マップは teams 未設定キーの補完に限定 |

### 実行方法

**自動（推奨）**: Bot を起動するだけで適用される（冪等）。
`utils/db.py` の `_migrate_v3_teams_skills()` がカラム存在を確認してから
追加し、ロールマップをバックフィルしたうえで `user_version` を 3 に更新する。

**手動**: [`migrations/003_teams_skills.sql`](../migrations/003_teams_skills.sql)
を使用する（DDL のみ。バックフィルは Bot 起動時に行われる）:

```bash
cp data/club.db data/club.db.bak
sqlite3 data/club.db ".read migrations/003_teams_skills.sql"
```

前提: 001・002 適用済みであること。実行後の検証:

```sql
PRAGMA table_info(teams);   -- member_role_id 等 4 カラムがあること
PRAGMA user_version;        -- Bot 起動後に 3 になること
SELECT team_key, member_role_id, secondary_role_id FROM teams;
```

### 互換性上の注意

- **既存ギルドの班データは保持**される（削除されない）。ただし新規ギルドには
  班が投入されなくなるため、管理者が `/team-add` で登録するまで
  `/task add` 等の班選択肢は空になる。
- settings の旧ロールマップは読み取りフォールバックとして残るため、
  バックフィル前の環境でもロール同期は従来どおり動作する。
- 技能タグをメンバーに付与する `/member skill add` は、ギルドの
  `skill_tags` に登録されたタグのみ受け付ける。未登録のギルドでは
  管理者が先に `/skill-add` で登録する必要がある。

## 11. 004 マイグレーション: Todoist トークンのギルド別暗号化保存（スキーマバージョン 4）

### 変更内容

| レイヤ | 変更 |
|---|---|
| DB スキーマ | `todoist_configs`（1ギルド1件、PK = guild_id）追加。トークンは `api_token_encrypted`（Fernet 暗号文）に保存し、平文は保存しない。暗号文を含まない参照用ビュー `v_todoist_status` も追加 |
| 暗号化 | `utils/crypto.py`（Fernet）。暗号鍵は `ENCRYPTION_KEY` 環境変数のみから読み込む。起動時に未設定/不正なら ERROR ログを出し、トークンの登録・利用を安全に拒否する（Bot 自体は他機能のため動作継続） |
| config.py | `TODOIST_API_TOKEN` / `TODOIST_PROJECT_ID` / `TODAY_LABEL_NAME` の環境変数・settings からの読み込みを**廃止**（フォールバックを残さない）。`TODAY_LABEL_CHANNEL_ID`（通知先チャンネル）は従来どおり |
| サービス | `TodoistServiceManager.for_guild(guild_id)` が API 呼び出しの都度、暗号文を復号してギルド別サービスを構築（平文をキャッシュしない）。`cogs/tasks.py`・`cogs/reminders.py` の全呼び出しをギルド別解決に置換 |
| コマンド | 新規 `cogs/todoist_admin.py`: `/todoist-setup` `/todoist-status` `/todoist-remove`（すべて L4・ephemeral・トークン非表示）。`cogs/settings.py` の `/set_todoist`（平文保存経路）は廃止 |
| 専用テーブルの理由 | 設計書どおり settings ではなく専用テーブルとした。理由: (1) NocoDB 等の外部 UI で**テーブル単位の非表示・アクセス制限**ができる、(2) 1ギルド1件を PK で保証できる、(3) キー値汎用の settings に秘匿列を混ぜると一覧表示（`/settings_list` 等）からの漏えい防止が困難なため |

### 既存トークンの移行（一回限り・明示的実行）

環境変数や settings に残る平文トークンを暗号化して移行するスクリプト:

```bash
cp data/club.db data/club.db.bak          # 事前バックアップ
venv/bin/python scripts/migrate_todoist_token.py          # dry-run（件数確認）
venv/bin/python scripts/migrate_todoist_token.py --apply  # 移行実行
```

- 対象: settings の `TODOIST_API_TOKEN` / `TODOIST_PROJECT_ID`（ギルド別）、
  および環境変数の同名キー（`GUILD_ID` で指定したレガシーギルドに紐付け）。
- `--apply` で暗号化 upsert + settings の平文キー削除 + `VACUUM`（物理除去）を行う。
- トークンは一切表示しない（件数とギルド ID のみ出力）。
- 実行後は `.env` から `TODOIST_API_TOKEN` / `TODOIST_PROJECT_ID` を手動で削除し、
  平文が残る移行前バックアップは不要になったら削除すること。

### NocoDB で暗号文を見せない運用手順

`todoist_configs` テーブル（特に `api_token_encrypted` 列）を一般メンバーに
見せないための手順:

1. **テーブル単位で隠す（推奨）**: NocoDB のプロジェクト設定（Team & Auth /
   ロール管理）で、一般メンバー向けロールから `todoist_configs` テーブルへの
   アクセスを許可しない。管理者ロールのみ閲覧可能にする。
2. **ビューで列を限定する代替案**: テーブルを参照させたい場合は、暗号文を
   含まない `v_todoist_status` ビュー（guild_id / project_id /
   today_label_name / enabled_flag / updated_at）のみを共有ビューとして公開し、
   ベーステーブルは非共有にする。
3. **ENCRYPTION_KEY を NocoDB 側に登録しない**: NocoDB からは復号できない
   ため暗号文が見えても実害は限定的だが、見せないことを原則とする。
4. 一般メンバーには閲覧専用（Viewer）ロールのみを付与し、行の編集・削除は
   運用者に限定する。

## 12. 005: Google Sheets 廃止・NocoDB 移行（スキーマバージョン 5）

### 変更内容

| レイヤ | 変更 |
|---|---|
| 削除 | `services/sheets_service.py`・`cogs/sheets.py` を削除。gspread / google-auth を requirements から除去（移行スクリプトのみ一時利用）。`SPREADSHEET_ID` / `SHEET_*` / `GOOGLE_CREDENTIALS_PATH` / `sheets_enabled()` 等の設定項目・`/set_sheets` コマンドを廃止 |
| DB スキーマ | 表示用ビュー `v_attendance`（旧 attendance 相当）・`v_team_summary`（旧 team_summary 相当）を追加（スキーマバージョン 5）。正本テーブル（tasks / members / schedule_votes / layer_records / audit_log）は既存のまま |
| Cog | `cogs/schedule.py`（日程調整シートの作成・更新・削除）、`cogs/layer_tracking.py`（シート追記・`/layer sync`）、`cogs/reminders.py`（Sheets 定期同期ループ）、`cogs/members.py` / `cogs/tasks.py`（変更時のシート同期呼び出し）から Sheets 依存を除去 |
| 移行スクリプト | `scripts/migrate_sheets_to_db.py`（一回限り。`--guild-id` 必須・dry-run 既定・`--apply` 時に DB 自動バックアップ・シートごとの入力/移行/スキップ/エラー件数出力・自然キーで冪等）。対象: tasks / members / attendance / 桁別シート |
| NocoDB | `deploy/docker-compose.nocodb.yml` と [`NOCODB.md`](NOCODB.md)（起動・接続・権限・暗号文列の非表示・移行手順）を追加 |

### DB 種別の決定（確認結果）

- NocoDB は SQLite を外部データソースとして接続できる（2024年8月に一時削除
  された後、OSS 版に復帰）。よって **SQLite を本番 DB として継続採用**する。
- 既知の制約: NocoDB 側から pragma を調整できない、バージョンによって
  SQLite の VIEW が表示されない、bot との同時書き込みでロック待ちが起き得る。
  bot は WAL + busy_timeout=5000 設定済み。NocoDB は閲覧・軽微な修正に限定する
  運用とし、問題が顕在化した場合は PostgreSQL へ移行する（設計書 P6）。

### 移行手順

1. Bot を最新コードで起動（スキーマ v5 へ自動マイグレーション。
   ビューが作成される）
2. 旧 Sheets データの取り込み（任意・該当環境のみ）:
   [`NOCODB.md`](NOCODB.md) 8章の手順で `scripts/migrate_sheets_to_db.py` を
   dry-run → `--apply` の順に実行
3. NocoDB を起動して接続確認（[`NOCODB.md`](NOCODB.md) 1〜2章）
4. 旧 Sheets の共有設定を閲覧専用に変更、`credentials.json` は削除してよい

## 13. 006: 本番 DB の PostgreSQL 統一・Todoist トークンの Modal 入力化

### 変更内容

| レイヤ | 変更 |
|---|---|
| DB 層 | `utils/db.py` を SQLite/PostgreSQL 両対応に拡張。`DATABASE_URL` 設定時は asyncpg プールで PostgreSQL に接続（`?` プレースホルダを `$n` に変換、DDL を機械変換 `to_pg_ddl()`、`AUTOINCREMENT` → `BIGINT GENERATED BY DEFAULT AS IDENTITY`、スキーマバージョンは `schema_meta` テーブル、IDENTITY シーケンスの自動修復）。リポジトリ層の SQL は無変更 |
| NocoDB 構成 | 本番の業務 DB を PostgreSQL に統一。`deploy/docker-compose.nocodb.yml` に postgres サービスを追加し、業務 DB（clubdb）と NocoDB メタ DB（nocodb_meta）を別データベースに分離。SQLite はローカル開発・テスト専用とし、本番の NocoDB 接続先としては案内しない |
| 移行スクリプト | `scripts/migrate_sqlite_to_pg.py`（dry-run 既定・対象非空時は `--force` 必須・FK 安全な順序でコピー・件数検証・シーケンス修復） |
| Todoist 登録 UI | `/todoist-setup` の `token:` 引数を廃止。引数なしで実行すると管理者のみに見えるボタンを表示し、ボタンから Modal を開いてトークンを入力（Modal の入力値は Discord の履歴に残らない）。Modal 送信者とコマンド実行者の同一性を検証し、タイムアウト・キャンセル時は DB を変更しない |
| 接続情報の管理 | `DATABASE_URL`・`POSTGRES_*`・`NOCODB_JWT_SECRET` はすべて環境変数（bot の `.env` / `deploy/.env`）で管理。`deploy/.env.example` を用意し、秘密情報はコミットしない |

### 移行手順（SQLite → PostgreSQL）

1. `deploy/.env` を作成し、`docker compose -f deploy/docker-compose.nocodb.yml up -d` で PostgreSQL と NocoDB を起動
2. bot の `.env` に `DATABASE_URL` を設定して bot を起動（PG にスキーマが自動作成される）
3. `scripts/migrate_sqlite_to_pg.py` を dry-run → `--apply` でデータ移行
4. 以降のバックアップは `pg_dump`（[`NOCODB.md`](NOCODB.md) 5章）
5. SQLite の `data/club.db` はローカル開発・テスト用途として残す（本番参照先ではない）

