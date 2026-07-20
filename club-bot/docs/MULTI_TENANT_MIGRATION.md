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
