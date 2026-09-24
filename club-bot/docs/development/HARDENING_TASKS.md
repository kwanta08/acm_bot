# 堅牢化タスク管理（H1）

> **内部の作業用ドキュメントです（[development/README.md](README.md)）。**
> 書かれた時点のスナップショットで、現在のコードとは食い違う記述を含みます。
> **現状の仕様の根拠には使えません。** 使い方は [`../GUIDE.md`](../GUIDE.md)、
> 運用は [`../OPERATION.md`](../OPERATION.md) を参照してください。

`docs/development/IMPROVEMENT_TASKS.md`（G0〜G4）に続く第4の管理表。
G4 完了後の全コード分析で見つかった**「一度決めた原則が守られていない場所」**を5件に絞ってある。

この表の5件には共通の性格がある。

- **どれも新機能ではない。** 既に決めた規約（ADR / AGENTS.md / `utils/notify.py` の明文化された約束）が
  守られていない箇所を揃えるだけ
- **どれもスキーマを変えない。** マイグレーションは1件も発生しない（下表を参照）
- **どれも1ファイル〜3ファイルで閉じる。** 相互依存が無いので順不同で実装できるが、
  番号順に回すと差分が読みやすい

`/autonomous-dev-loop` で1タスクずつ回すために分解してある。
起動プロンプトは [`HARDENING_LOOP_PROMPT.md`](HARDENING_LOOP_PROMPT.md)。

## 運用ルール

- 実装は **必ず `/autonomous-dev-loop` の手順**（plan → 評価 → plan修正 → 実装 → レビュー → 修正 → 記録）で回す。
  「実装したので確認してください」で止めない。全ゲートを通って初めて完了
- 1イテレーションにつき未完了で最も若い番号のタスクを **1つだけ** 実施する
- タスクごとに `fix/<タスクID小文字>` ブランチを切る（ADR 0014: 1タスク＝1ブランチ＝1PR）
- 完了時にチェックを入れ、末尾の完了ログへ「完了内容 / 設計判断 / 次タスクへの申し送り」を追記する
- 【人間タスク】はエージェントが飛ばす（この表には無い）
- push は**ユーザーから明示指示があったときのみ**。コミットはタスク単位で行ってよい

## 全タスク共通の受入基準（AGENTS.md より。各タスクで再掲しない）

- 新規データ・新規設定はすべて `guild_id` スコープ。ギルド別設定は `config.for_guild(guild_id)` 経由
- コマンドは `interaction.guild.id` でスコープし、DM 実行は `ensure_guild()` で拒否する
- Discord API 呼び出しは `discord.HTTPException` を捕捉する
- 班名・チャンネル・ロール・機体名・桁構成をコードに埋め込まない
- 実装とドキュメント（`README.md` / `docs/`）が矛盾したら両方直す
- `ruff check .` と `python -m pytest tests/ -q -rs` がフルセットで緑
- **skip を「緑」と数えない。** `-rs` の skip 理由を報告に書く

## この表に固有の受入基準

- **ADR に反する変更をしない。** 衝突したら実装せず、完了ログに
  「ADR NNNN と衝突。判断を仰ぐ」と書いて止まる
- **既存の挙動を変えるタスクが2件ある**（H1-3 の未知キー拒否 / H1-5 の過去日拒否）。
  どちらも「今まで通っていた入力が通らなくなる」変更なので、
  **何が通らなくなるかを完了ログに列挙する**
- 5件すべて「再発を検出する仕組み」まで含めて完了とする。
  直すだけで終わらせない（ADR 0008: 規律ではなく構造で守る）

## スキーマバージョンの割り当て

**この表にスキーマ変更は無い。** `migrations/` にファイルを足さず、
`utils/db.py` の `SCHEMA_VERSION`（現行 24）も動かさない。

もし実装中に「列を足したい」と思ったら、それは受入基準の読み違いを疑うこと。
5件とも既存の列・既存のテーブルだけで完結する。

---

## Phase H1: 決めた原則が守られていない場所を揃える

---

- [x] **H1-1** `bot.get_channel()` を同一ギルド内の解決へ寄せ、他テナントへの誤送信経路を塞ぐ。

      `utils/notify.py` の `resolve_notice_channel_id` の docstring が明文化している約束——

      > **チャンネルの解決（get_channel）はしない。** 呼び出し側が
      > `guild.get_channel` で**同じギルド内に限定して**引くこと
      > （bot 全体のキャッシュから引くと他テナントへ流れる）。

      ——が、通知の主要経路で守られていない。`bot.py` の `_log_channel_for` は
      G4-11 でこの検査を入れたのに、`cogs/` 側は入っていない。

      `config.for_guild()` は環境変数の値を**全ギルドの GuildConfig に配る**
      （`config.py` の `for_guild`）。レガシー運用（`GUILD_ID` 指定 + 旧 `.env` に
      `DEFAULT_TASK_CHANNEL_ID` 等が残っている）のまま2サーバー目を迎えたインスタンスでは、
      **B サーバーの通知が A サーバーのチャンネルに出る**。

      - **置換対象**（`bot.get_channel(` → 同一ギルド内の解決へ）

        | 箇所 | チャンネル ID の出どころ | 危険度 |
        |---|---|---|
        | `cogs/reminders.py` `_task_channel` | `gconf.default_task_channel_id`（**env フォールバック**） | 高 |
        | `cogs/reminders.py` `_today_channel` | `gconf.today_channel_id`（**env フォールバック**） | 高 |
        | `cogs/schedule.py` `create` の投稿先 | `gconf.default_schedule_channel_id`（**env フォールバック**） | 高 |
        | `cogs/progress.py` `push_project_tasks` | `gconf.default_task_channel_id`（**env フォールバック**） | 高 |
        | `cogs/reminders.py` `_purge_one` | `gconf.bot_log_channel_id`（**env フォールバック**） | 中 |
        | `cogs/reminders.py` `_alert_milestones` | `resolve_default_channel_id`（ギルド別 settings） | 低 |
        | `cogs/reminders.py` セクション通知・バケット通知 | `teams.channel_id`（ギルド別） | 低 |
        | `cogs/schedule.py` `schedule["channel_id"]` を引く5箇所 | 予定行（ギルド別） | 低 |

        危険度「低」も**同じ形に揃える**。個別に判断させると次の追加でまた分かれる。

      - **除外（触らない）**:
        - `bot.py` の `_log_channel_for` / `_fetch_legacy_log_channel` — 既に検査済み。G4-11 の成果物
        - `cogs/schedule.py` の raw リアクション処理2箇所（`payload.channel_id`）—
          `payload.guild_id` を検査済みで、未キャッシュ時の `fetch_channel` フォールバックが要る。
          `guild.get_channel_or_thread` に寄せられるなら寄せてよいが、**フォールバックを消さない**
      - **変更ファイル（推定）**: `cogs/reminders.py`, `cogs/progress.py`, `cogs/schedule.py`,
        `tests/test_channel_scope.py`(新規)
      - **受入**:
        - 上表の「除外」以外のすべてで、チャンネル解決が `guild.get_channel`（または
          `utils.notify.guild_channel`）経由になっている
        - **ギルドが解決できないときは送らない。** 「たぶんこれだろう」でフォールバックしない
          （`bot.py` `_log_channel_for` と同じ判断。誤送信よりログが出ないほうがましである）
        - 2ギルドが同じ `channel_id` を指している状況で、A のチャンネルへ B の通知が出ないことを
          テストで固定する
        - **再混入の回帰テスト**: `cogs/` `services/` の**実行コード**（コメント・文字列リテラルを除く）に
          `bot.get_channel(` が現れたら落ちる。`tests/test_intents.py` の走査方式
          （`_iter_source_files` / `_code_only`）をそのまま流用する。除外は許可リストで明示
      - **検証**: 新規テストと既存の `tests/test_multi_tenant.py` / `tests/test_reminders_resilience.py` が緑
      - **注意**: ADR 0008（`guild_id` を型で封じる / 規律ではなく構造で守る）の延長線上にある。
        新しい ADR は不要だが、**許可リストに何を入れたかは完了ログに書く**

---

- [x] **H1-2** `/schedule create` が投稿に失敗したとき、ゾンビ投票を残さない。

      `create` は `create_schedule` / `add_option` を**投稿より先**に実行し、
      `channel.send()` を try/except していない（ボタン式・リアクション式の両方）。
      同じ Cog の `/schedule confirm` の告知は `except (Forbidden, HTTPException)` しているので、
      **一番古いコードパスだけが規約から取り残されている**。

      いま起きること:

      1. 予定と候補が DB に入る
      2. 送信が `Forbidden` → 利用者には「予期せぬエラーが発生しました」だけ
      3. `message_id` の無い予定が残り、5分後の自動締切ループが拾う
      4. 締切サマリーの投稿もまた失敗する

      - **変更ファイル（推定）**: `cogs/schedule.py`, `tests/test_schedule_create_failure.py`(新規)
      - **受入**:
        - **DB へ書く前に**、投稿先チャンネルへの `send_messages` / `embed_links` を
          `channel.permissions_for(guild.me)` で検査する。不足していれば**何も作らずに**
          「このチャンネルに投稿できません（不足: …）」と、不足している権限名を挙げて返す
        - それでも送信に失敗した場合（競合・API 障害）は `Forbidden` / `HTTPException` を捕捉し、
          `soft_delete_schedule` で予定を畳んでからエラーを返す
        - 畳んだ予定が自動締切（`list_due_schedules`）・自動催促（`list_reminder_candidates`）・
          `/schedule list`（`list_open_schedules`）のいずれにも出ないことをテストで固定する
        - リアクション式の `add_reaction` の失敗は**投票自体を無効にしない**。
          ログに残して続行する（絵文字が1つ付かないだけで投票は成立する）
        - `/schedule create` が成功 Embed を返したときは、**全候補に `message_id` が入っている**
      - **検証**: 送信が `Forbidden` を投げるスタブで、(a) DB に予定が残らない or 畳まれている
        (b) 利用者に権限不足が伝わる (c) 5分ループが拾わない、の3点
      - **注意**: **新しい repository メソッドを足さない。** `soft_delete_schedule` は
        `deleted_flag` と同時に `closed_flag` も立てるので、既存の条件式だけで
        3つのループがすべて止まる（メソッドの docstring に明記されている）

---

- [x] **H1-3** 設定キーをホワイトリスト化し、大会日を `/setup` から設定できるようにする。

      `/settings_set` は**任意のキーに任意の値**をそのまま保存する。
      ホワイトリストも値の検証も `setting_key` のオートコンプリートも無い。

      そして `COMPETITION_DATE` は `/setup` にもウィザードにも無く、
      **この生コマンドだけが設定手段**（`cogs/help.py` の `collect_setup_status` がそう案内している）。

      - `2026/07/25` と入力 → 保存は成功表示 → `milestone_service.parse_date` が `None` を返す
      - `/countdown` は「大会日が未設定です」と言い続ける
      - **週次マイルストーン警告も永久に飛ばない**
      - `COMPETITON_DATE`（タイポ）でも同じく成功表示で無言に死ぬ

      看板機能2つ（大会からの逆算・遅延警告）が、設定ミスに気づけない形でぶら下がっている。
      `IMPROVEMENT_REPORT.md` の P1-18 がタスク表に載らないまま残ったもの。

      - **変更ファイル（推定）**: `config.py`（または `utils/settings_spec.py` 新規）,
        `cogs/settings.py`, `cogs/setup_wizard.py`, `cogs/help.py`,
        `docs/OPERATION.md`, `docs/GUIDE.md`, `tests/test_settings_spec.py`(新規)
      - **受入**:
        - 設定キーの仕様（キー / 種別 / 検証 / 人間向けの説明）を **1箇所** に定義する。
          `config.for_guild()` が読むキーを**すべて**含むこと
          （チャンネル系7・ロール系5・数値系3・真偽系2・列挙1・日付1・文字列1）
        - `/settings_set` は**未知のキーを拒否**し、綴りの近い候補を提示する
        - `/settings_set` は**種別に合わない値を拒否**する。最低限:
          - `COMPETITION_DATE`: `2026-07-25` は成功 / `2026/07/25`・`7月25日`・空文字 はエラー
          - `*_CHANNEL_ID` / `*_ROLE_ID`: 数字以外はエラー
          - `WEEKLY_DIGEST_WEEKDAY`: 0〜6 以外はエラー
          - `SCHEDULE_UI_STYLE`: `buttons` / `reaction` 以外はエラー
          - `DATA_RETENTION_DAYS` / `LAYER_SESSION_*_MINUTES`: 整数以外はエラー
        - `setting_key` にオートコンプリートを付ける（説明文を候補名に含める）
        - `/setup` に「大会日を設定」ボタン（Modal 1枚）を足し、**同じ検証**を通す
        - `/setup-status` の大会日の hint を `/setup` に変える（生コマンドを案内しない）
        - **内部マーカーはコマンドから設定できない**が、`SettingsRepository.set()` の
          直接呼び出しは従来どおり通ること（`AUTO_SETUP_COMPLETED_AT`・`SETUP_VERSION`・
          `GUILD_NAME`・`GUILD_COMMANDS_CLEARED_AT` を起動時セットアップが書けなくなると
          全ギルドが壊れる）
      - **検証**: 検証関数の単体テスト（キー × 値の表）＋
        `/settings_set` が未知キー・不正値を拒否することのコマンドレベルのテスト
      - **注意**:
        - **拒否はコマンド層だけ。** repository 層で弾くと `bot.py` の
          `_ensure_guild_setup` が死んで全ギルドが起動できなくなる
        - 既存ギルドの `settings` に既に入っている不正値は**移行しない**
          （ADR 0024: 既定値で既存データを動かさない）。読み出し側は今までどおり
          「不正値は既定へ落として例外を投げない」を維持する
        - この表で**唯一 ADR を足す可能性がある**タスク。「設定キーはホワイトリストで、
          コマンド層で弾く」は新しい判断なので、実装したら ADR 草案を完了ログに書く

---

- [ ] **H1-4** 未回答リマインドの文面を、そのサーバーの投票 UI に合わせる。

      `notify_unanswered` の DM 本文が

      > 投票チャンネルでリアクションをお願いします。

      で固定されている。既定の UI は `buttons`（`DEFAULT_SCHEDULE_UI_STYLE = "buttons"`）で、
      **ボタン式のボードに付けたリアクションは投票として扱われない**
      （`_handle_reaction` が `ui_style == "buttons"` を明示的に無視する）。

      催促された人が言われたとおりにして、票が入らない。

      - **変更ファイル（推定）**: `cogs/schedule.py`, `tests/test_schedule_remind_text.py`(新規)
      - **受入**:
        - 本文が `schedule["ui_style"]` で分岐する
          - `buttons` → 「投票ボードのボタンから回答してください」の主旨
          - `reaction` → 「リアクションで回答してください」の主旨
        - `ui_style` が欠けている・未知の値のときは**既定（buttons）の文面**にする
          （例外を投げない。1件の壊れた行で催促が止まらないようにする）
        - `/schedule remind`（手動）と締切前の自動催促の**両方**が同じ文面になる
          （同じ関数を通るので自然に満たされるが、テストで固定する）
        - 文面を組む部分を純粋関数として切り出し、DB も Discord も触らずにテストできるようにする
      - **検証**: ui_style が `buttons` / `reaction` / 欠損 の3通りで期待した語が入ることをテスト
      - **注意**: **ジャンプリンクはここでは足さない。** 対象が全通知（未回答催促・
        確定日程・積層の押し忘れ・在庫・班別タスク）に広がるので、別タスクにする。
        同じ文字列を2回触ることになるが、「一度に触るのは1論点まで」を優先する

---

- [ ] **H1-5** 過去の締切・過去の候補日・締切より前の候補を弾く。

      `/schedule create deadline:2025-07-02`（去年の日付。打ち間違いで最も多い形）が通る。
      作成成功の緑 Embed が出て、5分以内に自動締切され、0票で終わる。
      `parse_deadline` にも `create` にも「未来かどうか」の検証が無い。

      - **変更ファイル（推定）**: `services/schedule_service.py`, `cogs/schedule.py`,
        `tests/test_schedule_time_validation.py`(新規)
      - **受入**:
        - `/schedule create` が次を**すべて**拒否する（拒否時は **DB に何も書かない**）
          - 締切 <= 現在
          - 候補日時 <= 現在（**どの候補が問題か**を文面に出す）
          - 候補日時 < 締切（投票が終わる前に予定日が来る）
        - `/schedule edit-deadline` も「新しい締切 <= 現在」を拒否する
        - 判定は `services/schedule_service.py` の**純粋関数**に置く。
          現在時刻を引数で受け取り、DB も Discord も触らない
        - エラー文面は「何が」「どう駄目か」「どう直すか」を含む
          （`utils/embeds.error_embed` の既存の作法に揃える）
      - **検証**: 純粋関数の表駆動テスト（正常 / 過去の締切 / 過去の候補 / 締切より前の候補 /
        締切と同時刻の候補＝許可）＋ コマンドレベルで「拒否したら予定が作られない」
      - **注意**:
        - `parse_datetime` の短縮形（`07-03`）は既に「過去なら翌年」へ送る。
          **完全な日付（`YYYY-MM-DD`）を勝手に翌年へ送らない。** 利用者の意図を推測せず、
          エラーにして打ち直させる（`2025-07-02` を `2026-07-02` に読み替えると、
          本当に過去のデータを入れたい場合に手段が無くなる）
        - 締切と候補が**同時刻**の場合は許可する（「締切＝集合時刻」の運用があるため）
        - この変更で通らなくなる入力を完了ログに列挙する

---

## 完了ログ

<!--
各タスクの完了時に、次の形式で追記する。

### H1-N: <タイトル>（YYYY-MM-DD / ブランチ fix/h1-n）

**完了内容**
- <ファイル>: <何を、なぜ>

**設計判断**
- <仮決めした解釈 / 却下した案とその理由 / ADR 草案が要るか>

**ゲートの判定**
- acm-plan-reviewer: APPROVE（N 周目）
- acm-diff-auditor: CLEAN
- acm-test-adversary: EFFECTIVE（実測表）

**次タスクへの申し送り**
- <次の担当が知らないと困ること>
-->

### H1-1: `bot.get_channel()` を同一ギルド内の解決へ寄せ、他テナントへの誤送信経路を塞ぐ（2026-09-24 / ブランチ fix/h1-1）

**完了内容**
- `utils/notify.py`:
  - `guild_channel(guild, channel_id)` を `guild.get_channel` → `guild.get_channel_or_thread` の**直接呼び出し**に変えた。
    スレッドも解決し、`send` を持たないもの（カテゴリ等）と数字でない ID は None。
    `get_channel` へのフォールバックも、メソッドが無いときに黙って None を返す枝も持たない（本番の `discord.Guild` は必ず持つので、そうした枝はフェイクだけを「チャンネルが引けない」理由で緑にする穴になる）
  - `guild_channel_by_id(bot, guild_id, channel_id)` を新設。`bot.get_guild` → `guild_channel`。
    ギルドが見えない・そのギルドに無いときは None（送らない）＋ `log.debug`（`bot.py` `_log_channel_for` と同じ）
  - `resolve_notice_channel_id` の docstring の指示を `guild_channel` / `guild_channel_by_id` へ直した（従来は `guild.get_channel` を指示しており、スレッドを解決しない）
- `cogs/reminders.py`: `_task_channel` / `_today_channel` / `_purge_one` / `_alert_milestones` / `push_section_tasks`（班チャンネル）/
  `_dispatch_todoist_tasks`（班バケット）を `guild_channel_by_id` へ。`Reminders._guild_channel` は `guild_channel` への委譲にした（中身が同一になったので実装を1つにする）。
  `_alert_milestones` の運用者ログの文言を「設定されていないか、このサーバーのチャンネルではないため」に
- `cogs/progress.py`: `push_project_tasks` を `guild_channel_by_id` へ
- `cogs/schedule.py`: `create` の既定投稿先を `guild_channel(interaction.guild, …)` へ（引けなければ既存のエラー。`interaction.channel` へは落とさない）。
  `_do_delete` / `edit_deadline` / `_refresh_vote_board` を `guild_channel_by_id` へ、
  `notify_unanswered` / `finalize_schedule` は取得済みの `guild` で `guild_channel` へ
- `tests/test_channel_scope.py`（新規・28件）: 静的走査＋走査の語彙の自己テスト、ヘルパ単体、
  2ギルドが同じ `channel_id` を指す実挙動テスト（`_task_channel` / `_today_channel`×2 / `/schedule create` / `push_project_tasks` / `_purge_one` / `_alert_milestones` / `finalize_schedule` / `notify_unanswered` の DM 不可フォールバック）。
  フェイク bot の `get_channel` / `fetch_channel` は他ギルドのチャンネルを返す「罠」。各テストに「同じ設定で自ギルドからなら届く」対照を入れた
- 既存テストのフェイク更新（`bot.get_channel` だけで引いていたものを `get_guild` → `get_channel_or_thread` で引ける形へ。原則アサーションは変えていない）:
  `test_confirm_view` / `test_data_purge` / `test_layer_session_alert` / `test_milestones` / `test_parent_chain` / `test_progress_notify` /
  `test_reminders_resilience` / `test_schedule_confirm` / `test_schedule_delete` / `test_schedule_notify` / `test_schedule_unanswered` /
  `test_schedule_vote_buttons` / `test_stock` / `test_weekly_digest`
- `docs/OPERATION.md`: `/schedule create` の `channel` 既定値に「そのサーバーのチャンネルに限る」
- `docs/SETUP.md`: チャンネル構成の表の直後に「`.env` のチャンネル ID はそのチャンネルがあるサーバーでだけ使われる。2つ目以降のサーバーは `/setup` で設定する」

**許可リスト（受入基準: 何を入れたか）**

`tests/test_channel_scope.py` の `ALLOWED_BOT_WIDE_LOOKUPS`。**(ファイル, 囲っている関数名, API) → 出現数**で固定している。

| ファイル | 関数 | API | 数 | 理由 |
|---|---|---|---|---|
| `cogs/schedule.py` | `_remove_other_reactions` | `get_channel` | 1 | raw リアクション処理。呼び出し元の `_handle_reaction` が `payload.guild_id` を検査済みで、`payload.channel_id` は Discord がそのギルドのイベントとして渡す値。未キャッシュ時の `fetch_channel` フォールバックが要る（H1-1 の「除外」） |
| 同上 | `_remove_other_reactions` | `fetch_channel` | 1 | 同上 |
| 同上 | `_refresh_option_message` | `get_channel` | 1 | 同上 |
| 同上 | `_refresh_option_message` | `fetch_channel` | 1 | 同上 |

`bot.py` の `_log_channel_for` / `_fetch_legacy_log_channel` は走査対象（`cogs/` `services/` `utils/`）の外なので許可リストには無い（G4-11 で検査済み）。

**設計判断**
- 走査の範囲と語彙を受入基準より広げた（仮決め）: 範囲に `utils/` を含め（ヘルパ自体への混入を拾う）、
  受け手を `bot` / `_bot` / `client`、API を `get_channel` / `fetch_channel` / `get_partial_messageable` にし、括弧を要求しない（参照も拾う）。
  正規表現そのものを弱める変更は `test_scanner_catches_bot_wide_lookups` / `test_scanner_ignores_guild_scoped_lookups` が検出する
- `guild.get_channel` ではなく `get_channel_or_thread` に寄せた: 置換前の `bot.get_channel` はスレッドも解決していた。
  `guild.get_channel` へ寄せるとスレッド内で作った予定の通知が黙って消える
- `/schedule create` は既定投稿先が引けないとき `interaction.channel` へ落とさない（現行も「設定済みだが引けない」ときはエラー。「たぶんこれだろう」で投稿しない）
- `_today_channel` の分岐構造（ラベル通知先が設定されていればそれだけを引く）は変えていない
- 却下: `config.for_guild()` が env の値を全ギルドへ配る挙動そのものを直す案。根本原因ではあるが、単一ギルドのレガシー運用の後方互換に触る別論点
- ADR 草案は不要: ADR 0008（規律ではなく構造で守る）・0023（届かないことは運用者に見える形で）・0018（退出後の削除）に沿う

**既存の挙動が変わる点**（コマンドの入力として通らなくなるものは無い。変わるのは送信先の解決だけ）
- 設定値（env フォールバック含む）が**他ギルドのチャンネル**を指している通知: 他ギルドへ誤送信されていた → 送らない（運用者ログがある経路では残る）
- `/schedule create` の既定投稿先が他ギルドのチャンネル: 他ギルドへ投票が投稿されていた → 「投稿先チャンネルが特定できません。channel を指定してください。」（何も作らない）
- ギルドが bot のキャッシュに無い: `bot.get_channel` で引ければ送れていた → 送らない
- `guild_channel` の既存の呼び出し元（`cogs/inventory.py` の在庫アラート・`Reminders._notify_low_stock` の在庫の閾値割れ通知）:
  スレッドを指す ID で送れるようになった（従来は None）。カテゴリ等を指していると従来は `.send` で AttributeError → None として「送信先が無い」経路へ
- 予定行の `channel_id` が数字でない壊れた行: `int()` で ValueError → None（送らない）
- **旧実装で他ギルドへ投稿されてしまった既存の予定**（`channel_id` が他ギルドのチャンネルを指す行）: 修正後はボード更新・締切サマリー投稿・
  `/schedule delete` での投票メッセージ削除（失敗件数として表示）・DM 不可時のフォールバックが行われない。
  漏れたメッセージは A 側での手動削除になる。DB は書き換えない（移行しない）ので巻き戻せる
- 単一ギルドのレガシー運用では挙動は変わらない

**アサーション・前提を変えた既存テスト**
- `test_data_purge.py::test_fake_bot_shape_matches_usage`: 「`bot.get_channel(1) is None`」→「`bot.get_guild(1) is None`」（偽 bot の形の検査。実装が `get_guild` を呼ぶようになったため）
- `test_milestones.py::test_progress_key_wins_over_setup_key`: 「もう一方のチャンネル」の置き場所を G2 → G1 のギルドへ（bot 全体解決の時代は置き場所が無関係だった。同じサーバーに両方のチャンネルが実在する状況でキーの優先度を見る）
- `test_schedule_delete.py` の `_Channel` に `send` を足した（`guild_channel` は送れないチャンネルを除くため。本物のテキストチャンネルは必ず持つ）

**`bot.get_channel` を残したフェイク**: `test_schedule_delete.py` / `test_schedule_vote_buttons.py`。
リアクション系テストは正常な経路では早期 return（削除済み・ボタン式）で `_remove_other_reactions` に届かないが、
ガードを外した変異がアサーションまで進めるよう作者が静かに終わるチャンネルを渡しているため残した。
この2ファイルの delete / ボード更新のテストは、実装が `bot.get_channel` へ差し戻されても**それ自体では検出しない**（そこは静的走査が守る）。

**ゲートの判定**
- acm-plan-reviewer: REVISE（1周目: R1〜R7 = `guild_channel` の getattr 枝を消す / フェイクを grep で全件洗う / 偽 bot の形の検査の更新 / create テストで `interaction.channel` を送信可能にする / 対照の追加 / 公開ドキュメント / 旧バグで作られた予定行の列挙）→ APPROVE（2周目）
- acm-diff-auditor: FINDINGS（1周目: `resolve_notice_channel_id` の docstring が `guild.get_channel` を指示）→ CLEAN（2周目）
- acm-test-adversary: EFFECTIVE（1周目: 69変異）→ EFFECTIVE（2周目: 同じ69変異で1周目と完全一致）

実測表:

| 戻した実装 | 赤くなったテスト（件数） | 捕まえたテスト |
|---|---|---|
| `_task_channel` を `self.bot.get_channel` へ | 2 | 静的走査＋実挙動 |
| `_today_channel` を同上 | 3 | 静的走査＋実挙動（env 2通り） |
| `_purge_one` を同上 | 2 | 静的走査＋実挙動 |
| `_alert_milestones` を同上 | 9 | 静的走査＋実挙動＋`test_milestones` |
| `push_section_tasks` / `_dispatch_todoist_tasks` を同上 | 1 / 1 | 静的走査のみ |
| `push_project_tasks` を同上 | 7 | 静的走査＋実挙動＋`test_parent_chain` / `test_progress_notify` |
| `/schedule create` の既定投稿先を同上 | 2 | 静的走査＋実挙動 |
| `/schedule create` で `or interaction.channel` へ落とす | 1 | 実挙動（`g2_here` に投稿されない） |
| `finalize_schedule` / `notify_unanswered` を同上 | 2 / 10 | 静的走査＋実挙動（＋既存） |
| `_do_delete` / `edit_deadline` / `_refresh_vote_board` を同上 | 2 / 1 / 1 | 静的走査（`_do_delete` は `test_confirm_view` も） |
| `guild_channel` を `guild.get_channel` へ | 35 | `resolves_threads`・`does_not_fall_back` ほか |
| `guild_channel` に getattr の枝 / `get_channel` フォールバック | 1 / 1 | `does_not_fall_back_to_get_channel` |
| `guild_channel` の send フィルタを外す | 1 | `rejects_unsendable_channels` |
| `guild_channel_by_id` でギルドが見えないとき全ギルドを探す | 1 | `returns_none_when_guild_is_not_cached` |
| `guild_channel_by_id` でギルドに無ければ bot 全体から引く（getattr で走査を回避） | 9 | 実挙動8件＋`test_progress_notify` |
| 走査の正規表現を弱める（`\(` を要求 / client・`_bot`・fetch・partial を外す） | 1〜5 | `scanner_catches_bot_wide_lookups` |
| 走査の正規表現を広げる（先頭の `\b` を外す等） | 1〜4 | `scanner_ignores_guild_scoped_lookups` |
| 許可リストを腐らせる（除外を寄せる / 除外関数に2つ目を足す） | 1〜2 | `allowlisted_call_sites_still_exist` |
| 対照: `guild_channel` / `guild_channel_by_id` を常に None | 40 / 23 | 各実挙動テストの「自ギルドへは届く」行 |
| **素通り**: `if guild is None` の節を消す | 0 | 等価変異（`guild_channel(None, …)` も None。差は debug ログだけ） |
| **素通り**: 静的走査だけが守る5経路を `getattr(self.bot, "get_channel")` で書く | 0 | 走査の語彙を回避する書き方。申し送りに記載 |
| **素通り**: 許可リストの照合をファイル単位へ緩める | 0 | テスト自体の書き換え（メタテスト無し）。申し送りに記載 |

1周目 69 変異（本番57＋対照8＋走査の語彙4）。2周目（docstring 修正後）も同じ 69 変異を再実行し、落ちたテスト ID の集合まで1周目と一致。
復元は毎回 MD5 照合（`git stash` / `checkout` / `restore` は不使用）。

**次タスクへの申し送り**
- H1-2: `/schedule create` に `permissions_for(guild.me)` の検査を足すと、`test_channel_scope.py::test_schedule_create_does_not_post_to_another_guild` の後半（G1 から投稿する側）が落ちる。`_Channel` に `permissions_for`、`_Guild` に `me` を足すこと。投稿先の解決（`guild_channel(interaction.guild, …)`）は権限検査より前にある
- H1-4: `notify_unanswered` の文面はチャンネル解決（`guild_channel(guild, …)`）の直前にある
- H1-5: `test_channel_scope.py` の create テストは締切 `2099-01-01 00:00`・候補 `2099-01-02 10:00`（過去日・候補 < 締切の拒否に巻き込まれない）
- 静的走査だけが守っている5経路（`push_section_tasks` / `_dispatch_todoist_tasks` の班バケット / `_do_delete` / `edit_deadline` / `_refresh_vote_board`）は、
  `getattr(self.bot, "get_channel")` のような走査を回避する書き方には気づけない（test-adversary 実測）。塞ぐなら `test_channel_scope.py` の罠 `_Bot` で2ギルドのテストを1本ずつ足す
- 許可リストの照合をファイル単位へ緩める変更（テスト自体の書き換え）は検出できない（メタテスト無し）
- `cogs/welcome.py` の `guild.get_channel` は同一ギルド内だがスレッドを解決しない（H1-1 の対象外のまま）
- `config.for_guild()` が env の値を全ギルドへ配る挙動は残っている（今回は送る側で塞いだ）

### H1-2: `/schedule create` が投稿に失敗したとき、ゾンビ投票を残さない（2026-09-24 / ブランチ fix/h1-2）

**完了内容**
- `utils/notify.py`: `missing_send_permission(channel, member)` を追加（純粋関数）。確認順は「チャンネルを見る」→「メッセージを送信」
  （スレッドでは「スレッドでメッセージを送信」）→「埋め込みリンク」で、**最初に欠けている1つ**の表示名を返す
- `cogs/schedule.py` の `create`:
  - **DB へ書く前に** `missing_send_permission(target_channel, interaction.guild.me)` を検査。不足なら何も作らず
    「このチャンネル（#…）に投稿できません（不足: …）」＋必要な権限の一覧を返す（`MISSING_PERMISSIONS`）。
    スレッドの親が未キャッシュで権限を計算できないとき（`discord.ClientException`）も作らない（`PERMISSION_UNKNOWN`）
  - 投稿部分（ボタン式・リアクション式）を try で囲み、`Forbidden` / `HTTPException` なら `_abort_create`:
    ① `soft_delete_schedule` で畳む（締切も立つ。ADR 0037）② 投稿済みのメッセージを候補行の `message_id` から辿って削除
    （`NotFound` は無視、消せなかった件数は利用者に伝える）③ 理由つきのエラー（`POST_FAILED`）
  - 通信層の例外（`OSError` 等。`HTTPException` に包まれない）は、畳んで投稿済みメッセージを消してから上げ直す
    （後始末の失敗は握って元の例外を隠さない）
  - リアクション式の `add_reaction` は `_add_vote_reactions` に切り出し、**どんな例外でも投票を無効にしない**。
    1回失敗したら以降は付けず、成功 Embed に注記を出す
  - 成功の通知（followup）は try の外に置く（通知の送信失敗で正常な予定を畳まない）
- `tests/test_schedule_create_failure.py`（新規・33件）: 純粋関数の表、事前検査（不足権限3種・スレッド・権限を計算できない）、
  送信失敗で畳む（Forbidden / HTTPException × buttons / reaction。健全な予定を同じ経路で作る対照つきで、自動締切・自動催促・
  開催中一覧の3つを見る）、部分投稿の後始末、後始末の失敗、通信層の例外、順序（畳むのが先）、リアクション失敗、成功時の message_id
- 既存テストのフェイク（`test_channel_scope` / `test_schedule_notify` / `test_schedule_vote_buttons`）に `guild.me` と
  `permissions_for`（Bot 自身の権限でなければ AssertionError・`discord.Permissions.all_channel()` を返す）を追加。アサーションは変えていない
- `docs/GUIDE.md`（困ったときの表に3行）・`docs/SETUP.md`（トラブルシューティングに1行）・`docs/OPERATION.md`（§5 のエラーコード3行）・
  ルート `README.md`（スレッドを投稿先にするときの権限の注記）

**設計判断**
- **受入基準の読み替え**: 「不足している権限名を挙げて」は「**最初に欠けている1つ**を挙げ、必要な権限の一覧を添える」にした。
  discord.py の `permissions_for` は権限を連鎖して落とす（送信できないと埋め込みリンクも False、見られないと全部 False）ので、
  後段が本当に拒否されているのかは区別できない。断定しない（開発ノートの判断軸「分からないものを数字にしない」）
- `guild.me` が None の枝は持たない: スラッシュコマンドでは discord.py が Interaction の組み立て時に Bot 自身を補う。
  万一 None でも AttributeError は DB に書く前に出るのでゾンビは残らない（H1-1 の `guild_channel` と同じ判断）
- 送信失敗の後始末は**畳むのが先**。削除で想定外の例外が出ても、message_id の無い開催中の予定を残さない
- 投稿済みメッセージは候補行の `message_id` から辿る（`channel.send` の直後に必ず `set_option_message` が走る）。
  `/schedule delete` と同じ辿り方で、ボタン式・リアクション式の両方を取りこぼさない
- 新しい repository メソッドは足していない（`soft_delete_schedule` をそのまま使う）。スキーマ変更なし
- 招待の権限（`INVITE_PERMISSIONS`）は増やしていない（ADR 0017）

**既存の挙動が変わる点 / 通らなくなる入力**
- Bot が投稿先で「チャンネルを見る」「メッセージを送信」（スレッドでは「スレッドでメッセージを送信」）「埋め込みリンク」の
  どれかを持たない `/schedule create`: 以前は予定と候補が DB に入り、送信の Forbidden で「予期せぬエラー」、`message_id` の無い予定が
  5分ごとの自動締切・催促に拾われ続けた → 何も作らず、不足権限名つきのエラー
- 権限はあるのに送信が失敗: 以前は同じくゾンビ → 論理削除（締切も立つ）してエラー。投稿済みのボードは消す。
  取り消した予定は `/schedule restore` の候補に出る（戻しても締切済みとして戻る。ADR 0037 の既存の挙動）
- リアクション式で Bot が「リアクションを追加」を持たない: 以前は `add_reaction` の Forbidden で「予期せぬエラー」 → 投票は成立し、成功 Embed に注記
- **旧バグで作られた既存のゾンビ予定（`message_id` が NULL で `closed_flag = 0` の行）は移行しない**（既存データを動かさない）

**ゲートの判定**
- acm-plan-reviewer: REVISE（1周目: `guild.me` の None 枝が本番で到達しない／フェイクが「誰の権限か」を見ていない／
  `permissions_for` の連鎖を無視した表／後始末の順序と利用者への通知／投稿済みメッセージの収集が規律頼み）→ APPROVE（2周目）
- acm-diff-auditor: FINDINGS（1周目: 通信層の例外でゾンビが残る・順序がテストで固定されていない・注記の断定・§5 のエラーコード・GUIDE の文言）
  → FINDINGS（2周目: `except Exception` で畳むようにしたため `add_reaction` の通信層の例外で投票が取り消される）→ CLEAN（3周目）→ CLEAN（test-adversary の差し戻し後の再監査）
- acm-test-adversary: INEFFECTIVE（成功通知の送信を try の中へ入れる変異・案内から理由を消す変異・NotFound を失敗に数える変異・
  後始末の失敗で元の例外を隠す変異が素通り）→ EFFECTIVE（テストを4本足して再実測）

実測表（最終）:

| 戻した実装 | 赤くなったテスト（件数） |
|---|---|
| 事前検査を消す／DB 書き込みの後ろへ移す | 5 / 5（拒否系。schedules の行数を見る） |
| `embed_links` を検査から外す／スレッドでも `send_messages` を見る／逆向き | 3 / 3 / 1 |
| `permissions_for` に実行者を渡す | 31（フェイクが Bot 自身以外で AssertionError） |
| 確認順を変える／全部を返す | 3〜7 / 4 |
| `ClientException` を握らない／握って素通しする | 1 / 1 |
| `soft_delete_schedule` を呼ばない／`close_schedule` で代用 | 9 / 9（`deleted_flag` のアサーション） |
| 畳む前に削除する | 1（順序テスト） |
| 後始末の削除をしない／件数を伝えない／NotFound を失敗に数える | 4 / 1 / 1 |
| `set_option_message` を呼ばない | 6 |
| 成功通知を try の中へ入れる | 1 |
| `_add_vote_reactions` の変異6種（HTTP 系だけ握る・上げ直す・付け続ける・注記なし 等） | 1〜3 |
| `Forbidden` だけを捕まえる | 2 |
| `except Exception` 節を消す／畳まずに raise／後始末しない／後始末の失敗を握らない | 3 / 3 / 2 / 1 |
| 案内から「理由」を消す | 4 |
| **素通り**: `set_option_message` を `add_reaction` の後ろへずらす | 0（等価変異。`_add_vote_reactions` が例外をすべて握るので観測できる差が無い。退行して上げ直すようになれば別の変異で捕まる） |

**次タスクへの申し送り**
- H1-3: `/schedule create` のテストのフェイクは `guild.me` と `permissions_for` を持つ（`test_channel_scope` / `test_schedule_notify` /
  `test_schedule_vote_buttons` / `test_schedule_create_failure`）。`/schedule create` を呼ぶテストを足すなら同じ形にする
- H1-4: 未回答リマインドの文面は `notify_unanswered` の中（H1-1 で `guild_channel(guild, …)` にしたチャンネル解決の直前）
- H1-5: `test_schedule_create_failure.py` の締切は `2099-01-01 00:00`、候補は締切の翌日以降。`test_schedule_vote_buttons.py` /
  `test_schedule_notify.py` の既存の create テストは締切が `2026-09-20` 等（過去日）なので、H1-5 で過去日を拒否すると落ちる
- 事前検査は `add_reactions` / `read_message_history` を見ない（受入基準どおり。欠けていても投票は成立する）

### H1-3: 設定キーをホワイトリスト化し、大会日を `/setup` から設定できるようにする（2026-09-24 / ブランチ fix/h1-3）

**進め方の記録**: 手順書（HARDENING_LOOP_PROMPT.md §B）は H1-3 の前にプランモードで人の確認を挟むよう書いているが、
ユーザーから「残りの H1 を全て最後まで回して」と明示の指示があったので、acm-plan-reviewer のゲート（3周）で代えた。

**完了内容**
- `utils/settings_spec.py`（新規・純粋モジュール）: ギルド別設定の仕様表 `SETTING_SPECS`（`config.for_guild()` が読む 24 キー +
  `PROGRESS_DEFAULT_CHANNEL_ID`）を**1箇所**に定義。種別は `SettingKind`（Enum）、説明（オートコンプリートの候補名・エラー文）、
  範囲・選択肢・上限、環境変数へフォールバックするか（`env`）。内部キー（`INTERNAL_KEYS`）・専用コマンドのキー（`TZ` / `DB_PATH` → `/set_common`）・
  廃止キーも持つ。`normalize_setting`（検証して保存形を返す）・`suggest_keys`（綴りの近い候補）・`setting_key_choices`（オートコンプリート）・
  `env_fallback`（仕様表のキーでだけ環境変数を引く）
- `cogs/settings.py`:
  - `/settings_set`: 仕様表に無いキー・種別に合わない値を**保存せずに**理由を返す（綴りの近いキーを案内）。キーは前後の空白を除いて大文字化し、値は保存形で書く
  - `/settings_get`: 表示するのは仕様表のキーの値（DB に無ければ、Bot が実際に環境変数から読んでいるキーについてだけ環境変数の値）と、
    内部キー・`TZ`・`DB_PATH` の DB の値だけ。それ以外のキーは表示しない
  - `/settings_delete`: 入力したキーをそのまま照合する（大文字化・空白除去しない）。内部キーは削除できない
  - `setting_key` のオートコンプリート（set / get は仕様表から説明つき、delete はこのサーバーに保存済みのキー名だけ・内部キーは出さない・管理者だけ）
- `cogs/setup_wizard.py`: 「大会日を設定」ボタン＋`CompetitionDateModal`。`save_setting` の中で `normalize_setting` を通す
  （/setup からの書き込みはすべて同じ関門）。Modal の初期値は検証を通る値か Bot が読めている値だけ（読めない値は placeholder に 100 字以内で）。
  `send_modal` の失敗を捕まえて本人に伝える
- `cogs/help.py`（`/setup-status`）: 大会日は「入っているか」ではなく「日付として読めるか」（`parse_date`）で判定。案内は `/setup`。読めない値は 20 字で切り詰めて表示
- `cogs/progress.py`: `/countdown` の案内（`COMPETITION_DATE_HELP`）を `/setup` へ
- テスト: `tests/test_settings_spec.py`（新規）・`tests/test_settings_commands.py`（新規）・`tests/test_setup_wizard.py`・`tests/test_help.py`・`tests/test_milestones.py`
- `docs/OPERATION.md`（設定コマンドの表・受け付ける値の表・大会日・値の範囲）・`docs/GUIDE.md`（/setup の手順・大会日の更新）

**コミットの分け方（1タスク1コミットの例外）**: 1つ目のコミット（`/settings_get` の表示範囲の修正＋仕様表＋そのテスト）を独立させた。
H1-1 / H1-2 に依存せず main へ単独で入れられるようにするため（origin/main へ cherry-pick してフルセット 1491 passed / 18 skipped を確認済み）。
2つ目のコミットで残りを入れた。

**設計判断**
- **設定キーはホワイトリスト（仕様表 1 箇所）で定義し、コマンド層で弾く。** `SettingsRepository.set()` では弾かない
  （起動時セットアップが内部マーカーを書くので、repository 層で弾くと全ギルドが起動できなくなる）→ 下の ADR 草案
- 表の内訳（チャンネル7・ロール5・数値3・真偽2・列挙1・日付1・文字列1 = 20）より実際のキーが多かった（絵文字 ID 3 と `WEEKLY_DIGEST_WEEKDAY`）。
  受入基準の「`for_guild()` が読むキーを**すべて**含む」を優先して 24 個とも入れた。内訳の数字は目安として読んだ
- 検証を通った値は読み出し側で必ず読める形で保存する（数字は ASCII のみ、Discord の ID は 1〜2**63−1 を `str(int)`、日付は厳密な `YYYY-MM-DD`、
  真偽は `1`/`0`、列挙は小文字）。範囲: `DATA_RETENTION_DAYS` 0〜36500（これを超えると退出時の削除予定の計算が失敗する）、
  `LAYER_SESSION_*` 0〜10080、`WEEKLY_DIGEST_WEEKDAY` 0〜6
- `PRIMARY/SECONDARY_TEAM_ROLE_IDS` は `for_guild` が読むので仕様表に入れたが、説明を「【旧方式】…（/team-role を推奨）」にした
- 既存ギルドの不正値は**移行しない**（既存データを動かさない）。読み出し側は無変更で「不正値は既定へ落として例外を投げない」を維持。
  代わりに `/setup-status` が読めない大会日を ❌ で知らせる
- ダッシュボードの設定 API（`dashboard/routers/settings.py`）は別の許可リストを持ったまま（統合は後回し）。食い違いを検出する構造テストだけ足した

**既存の挙動が変わる点 / 通らなくなる入力**
- `/settings_set` で通らなくなる入力:
  - 仕様表に無いキー（綴り違いの `COMPETITON_DATE`・任意の自作キー） — 以前は成功表示で保存され、どこからも読まれなかった
  - 内部キー（`AUTO_SETUP_COMPLETED_AT` `AUTO_SETUP_DONE` `SETUP_VERSION` `SETUP_AT` `GUILD_NAME` `GUILD_COMMANDS_CLEARED_AT`） — 以前は上書きできた
  - `TZ` / `DB_PATH`（`/set_common` へ案内。`/set_common` は無変更）、`TODOIST_` / `SHEET_` で始まる廃止キー
  - 種別に合わない値: `COMPETITION_DATE` の `2026/07/25`・`7月25日`・`2026-7-25`・`2026-02-30`・空、`*_CHANNEL_ID` / `*_ROLE_ID` / 絵文字の `#general`・全角数字・`0`・2**63 以上、
    `WEEKLY_DIGEST_WEEKDAY` の 7・−1、`SCHEDULE_UI_STYLE` の `button`、`DATA_RETENTION_DAYS` の `3.5`・−1・36501、`LAYER_SESSION_*` の 10081、
    `WELCOME_ENABLED` の `たぶん`、`CLUB_NAME` の空・51 字以上、`LEADER_ROLE_IDS` の数字でない要素、`*_TEAM_ROLE_IDS` の `:` の無い組・空の班キー・空
- `/settings_set` の保存形が変わる: 小文字・空白付きのキーは大文字のキーとして保存（以前は別の行になって読まれなかった）。真偽は `1`/`0`、列挙は小文字、ID は数字だけ
- `/settings_get`: 仕様表・内部キー・`TZ`/`DB_PATH`・`TODOIST_` 以外のキーは表示しない（DB に保存済みの旧 `SHEET_*` 等も。`/settings_list` では見え、`/settings_delete` で消せる）。
  環境変数の値を見せるのは Bot が実際に環境変数から読んでいる15キーだけ
- `/settings_delete`: 内部キーは削除できない（完全一致。以前の `/settings_set` が作った小文字のゴミ行は従来どおり消せる）
- `/setup-status`: 読めない大会日は ✅ ではなく ❌（「読めません」）
- アサーションを変えた既存テスト: `test_help.py`（大会日の hint が `COMPETITION_DATE` を含む → `/setup` を含み `/settings_set` を含まない）、
  `test_milestones.py`（`COMPETITION_DATE_HELP` に `/setup`）

**ゲートの判定**
- acm-plan-reviewer: REVISE（1周目: `/settings_get` の件の重さと公開の扱い・秘密情報のテストが禁止リストでも緑・構造テストが直書きしか見ない・
  検証を通った値が読めない穴・Modal だけの検証・仕様表の書き忘れ・ダッシュボードの別定義・移行のテスト・通らなくなる入力の漏れ）
  → REVISE（2周目: `/settings_delete` の大文字化で正しい行を消す・移行のテストが番号付きマイグレーションを走らせない・Modal の長い初期値・小文字キーの扱いの食い違い・拒否の確認）
  → APPROVE（3周目）
- acm-diff-auditor: FINDINGS（1周目: 内部マーカーを削除できる・補完に権限の確認が無い・Modal が読める値を「読めない」と表示・OPERATION の表示範囲・
  環境変数を読まないキーまで「環境変数から取得」と表示）→ CLEAN（2周目）
- acm-test-adversary: INEFFECTIVE（1周目: 整数の非 ASCII 数字・班→ロール対応の混在と空・`send_modal` の失敗のテストが無い）→ INEFFECTIVE（2周目: 内部キーの拒否を大文字化して判定する変異が素通り）→ EFFECTIVE（3周目）

実測表（3周目・79変異。件数は対象5ファイルで赤くなったテスト）:

| 戻した実装 | 赤くなったテスト（件数） |
|---|---|
| `/settings_get` の未知キーで環境変数を表示する（元の実装）／別名経由で同じことをする | 6 / 5 |
| `env_fallback` が仕様表を通さず入力のまま環境変数を引く | 6 |
| 禁止リスト方式にする（`/settings_get` 側／`env_fallback` 側） | 8 / 2（仕様表に無い任意の名前で落ちる） |
| 内部キー・TZ・DB_PATH で環境変数へ落ちる | 4 |
| `spec.env` の判定を消す／`_ENV_BACKED_KEYS` を1つ増やす・減らす | 6 / 1〜4 |
| `/settings_set` のキーの検証を消す／入力のまま保存／候補を出さない／内部キーを受け付ける | 5 / 1 / 2 / 4 |
| 拒否を repository 層に置く（受入基準で禁止された形） | 10 |
| 値の検証を消す／検証はするが生の値を保存 | 9 / 4 |
| ID を `isdigit()` で判定／上限・下限を外す／整数を `isdigit` 系で判定 | 2 / 1 / 3 |
| 日付の正規表現を外す／寛容な `strptime` にする | 2 / 2 |
| 整数の範囲チェックを外す（全部・上限・下限） | 6 / 4 / 2 |
| 真偽・列挙・text・role_list・role_map・メンション剥がしの各検証を緩める | 1〜4 |
| 未知の kind を素通しする／仕様表の必須項目を消す | 1 / 6〜9 |
| `/settings_delete` を大文字化・strip して照合／内部キーの拒否を消す／大文字化して判定 | 1〜3 / 3 / 1 |
| 補完の check を外す／内部キーの除外を外す／候補名に値を含める | 1 / 1 / 1 |
| `save_setting` の検証を外す（Modal 側だけにする）／生の値を保存 | 1 / 2 |
| Modal の初期値に不正値／placeholder を切り詰めない／読める値のフォールバックを外す | 1 / 1 / 3 |
| `send_modal` の失敗の捕捉を消す／EXTRA_SETUP_KEYS から COMPETITION_DATE を外す | 1 / 12 |
| `/setup-status` を `bool(value)` 判定・`/settings_set` 案内に戻す／切り詰めを外す | 1〜2 / 1 |
| `COMPETITION_DATE_HELP` を `/settings_set` 案内に戻す | 1 |
| for_guild に仕様表に無いキーの読み取りを足す（直書き／組み立てた名前） | 2 / 1（実行時の記録テストが拾う） |
| cogs にキー形の定数を足す | 1（全数分類テスト） |
| **素通り**: 補完のギルド外ガードを外す | 0（等価変異。ギルド ID が None なら DB の行に一致しない） |
| **素通り**: アンダースコアの無いキーを足す | 0（既知の限界。申し送りに記載） |

1周目の素通り（整数の非 ASCII 数字・班→ロール対応の混在と空・`send_modal` の失敗）と2周目の素通り（内部キーの拒否を大文字化）は、テストを足して赤になった。

**ADR 草案（公開: `docs/adr/0010-settings-key-whitelist-at-command-layer.md` の候補）**

- **文脈**: `/settings_set` は任意のキーに任意の値を保存し、成功と表示していた。読み出し側（`config.for_guild()` 等）は
  「不正値は既定へ落として例外を投げない」ので、綴り違いのキーや読めない値は黙って捨てられ、大会日からの逆算や
  遅延警告が設定ミスに気づけない形で止まった。設定キーの一覧はコードの複数箇所（`config.for_guild`・`/setup`・
  ダッシュボード）に部分的に散っていた
- **選択肢**: (A) repository 層（`SettingsRepository.set()`）で弾く (B) コマンド層で弾き、仕様表を1箇所に置く
  (C) 禁止リスト（危ないキーだけ拒否） (D) 何もせず、読み出し側の警告を増やす
- **決定**: (B)。ギルド別設定のキーは `utils/settings_spec.py` の仕様表（キー・種別・検証・説明・環境変数へ落ちるか）に1箇所で定義し、
  `/settings_set` `/settings_get` `/setup` はこの表に無いキー・種別に合わない値を受け付けない。`SettingsRepository.set()` は何でも書ける
- **理由**: 起動時セットアップは内部マーカー（`AUTO_SETUP_COMPLETED_AT` 等）を repository 経由で書くので、repository 層で弾くと
  全ギルドが起動できなくなる。仕様表を1箇所にし、`for_guild` が実際に問い合わせるキー・コード中のキー形の定数・ダッシュボードの定義との
  食い違いを構造テストで落とすことで、キーを足して仕様表を忘れる形を塞ぐ（規律ではなく構造で守る）
- **却下した案**: (A) 上の理由。(C) 新しい秘密のキーや廃止キーが増えるたびに漏れる（外部へ出すものはホワイトリストで定義する、と同じ考え）。
  (D) 保存の時点で止めないと、利用者は成功表示を信じて気づかない
- **影響範囲**: `/settings_set` で通らなくなる入力がある（完了ログに列挙）。既存の不正値は移行しない（読み出し側は無変更）。
  `/set_channel` 等の別コマンドとダッシュボードはまだ仕様表の検証を通らない
- **覆す条件**: 設定の書き込み口がコマンド以外（ダッシュボード・外部 API）に広がり、コマンド層だけでは守れなくなったとき。
  そのときは repository 層に「仕様表にあるキーだけ受け付ける書き込み口」と「内部マーカー専用の書き込み口」を分けて持たせる
- **根拠**: HARDENING_TASKS.md H1-3、`tests/test_settings_spec.py` の構造テスト

**次タスクへの申し送り**
- `/set_channel`（数字を検証しない）・`/set_role`・`/set_common`・ダッシュボードの設定 PATCH（`isdigit()` で判定）は仕様表の検証を通らない。
  同じ「成功と表示して黙って読まれない」型なので、仕様表の `normalize_setting` へ寄せる別タスクにする
- ダッシュボードの許可リストは仕様表と別定義のまま（食い違いは `test_dashboard_settings_agree_with_spec` が検出する）
- `/countdown` は、値が入っていても読めないとき「大会日: 未設定」と表示する（`/setup-status` では気づける）
- 構造テスト（キー形の定数の全数分類）は、アンダースコアを含まない名前（`TZ` 等）を拾わない
- 移行のテストは「H1 の基準（スキーマ v24）より後に足すマイグレーションは何度走っても同じ結果になる」ことを前提にしている。冪等でないものを足すと落ちる
- `/set_common DB_PATH` / `TZ` は `GUILD_ID` を指定した旧運用のサーバーでだけ効く
- 1つ目のコミットは本番への先行投入を想定している。関連する判断事項は利用者に別途報告済み
