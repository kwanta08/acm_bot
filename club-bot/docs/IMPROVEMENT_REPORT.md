# club-bot 改善提案レポート

作成日: 2026-08-20 / 対象: `acm_bot` リポジトリ（`club-bot/` 配下 約32,000行 / 89スラッシュコマンド / 18テーブル / FastAPI ダッシュボード）

`docs/FEATURE_TASKS.md`（F0〜F6）と `docs/PUBLIC_RELEASE_TASKS.md` はいずれも未完了タスクが実質ゼロ（残るは【人間タスク】の P0-5 のみ）です。本レポートは**その先**を扱います。

---

## 総評

設計の骨格は良好です。`guild_id` スコープを型と DI で強制する仕組み（`dashboard/security.py` の `GuildScope`）、`message_content` インテントを持たない判断とその回帰テスト、Sheets 依存を撤去して再混入をテストで検出する体制、`/data delete` の二段階確認と取消、退出後30日の猶予 — 公開マルチテナント bot として**押さえるべき所を先に押さえてある**プロジェクトです。

一方で、機能を高速に足し込んできた結果、**「作ったが繋がっていない」箇所**が複数あります。設定キーの綴りが2系統に分裂して通知が届かない、監査ログを書いているのに読む手段が無い、ダッシュボードの API が画面から呼ばれていない、デプロイがダッシュボードを再起動しない、など。利用者から見ると「設定したのに動かない」「エラーも出ない」という最も原因究明が難しい形で表面化します。

本レポートは以下の3層に分けています。

| 層 | 内容 | 件数 |
|---|---|---|
| **P0** | 壊れている / 危ない — 放置すると事故か沈黙障害になる | 8件 |
| **P1** | 使いやすさ — 利用者が日常的に不便を感じる | 12件 |
| **P2** | 新機能 — 入れると効果が大きい未実装機能 | 12件 |

指摘はすべてソースを確認済みです。行番号は本レポート作成時点のものです。

---

# P0. 先に直すべきもの

## P0-1. `/schedule list-closed` は締切済みが26件を超えた時点で恒久的に壊れる

**根拠**: `cogs/schedule.py:384-389`

```python
for s in schedules:                      # 上限なし
    embed.add_field(name=..., value=..., inline=False)
```

Discord の Embed fields は25個が上限です。同じ無制限ループが `cogs/schedule.py:338`（`/schedule list`）、`cogs/layer_tracking.py:201`（`/layer status`）にもあります。一方 `cogs/tasks.py:801` や `cogs/progress.py:262` では `[:25]` で正しく切っており、**実装者が制限を知っていることは明らか**なので単純な漏れです。

**影響**: 週1回の日程調整を半年やると締切済みが26件を超え、その瞬間から `/schedule list-closed` は「予期せぬエラーが発生しました。時間をおいて再試行してください」（`bot.py:415`）としか返さなくなります。時間をおいても永久に直りません。

**改善**: 当面は `[:25]` + 残件フッター。本筋は `list_closed_schedules` に `limit` を足し、ページング View を付ける。
**工数**: S

---

## P0-2. 週次マイルストーン警告が、`/setup` しかしていないサーバーには**永久に届かない**（設定キーの綴り違い）

**根拠**: 進捗の通知先を表す設定キーが**2種類**存在します。

```
services/progress_sync_service.py:51   SETTINGS_DEFAULT_CHANNEL_KEY = "PROGRESS_DEFAULT_CHANNEL_ID"
dashboard/routers/settings.py:44                                     "PROGRESS_DEFAULT_CHANNEL_ID"
--- 別物 ---
cogs/setup_wizard.py:39                ("DEFAULT_PROGRESS_CHANNEL_ID", "進捗チャンネル")
cogs/settings.py:230                    Choice(name="進捗", value="DEFAULT_PROGRESS_CHANNEL_ID")
config.py:183 / 301 / 382               DEFAULT_PROGRESS_CHANNEL_ID
```

`cogs/reminders.py:293-298` の週次マイルストーン警告は前者しか読まず、無ければフォールバックせずに `log.info` だけ出して終わります。`/setup` が書くのは後者です。

**影響**: F4-3 の目玉機能「遅れているマイルストーンの週次アラート」が、`/settings_set PROGRESS_DEFAULT_CHANNEL_ID` を手打ちした人以外には**一度も届きません**。`#bot-log` にも痕跡が残らないので、届いていないこと自体に気づけません。さらにダッシュボードは前者を、Discord の `/settings` は後者を編集させるため、同じ「進捗の通知先」を2画面で別々に設定させています。

**改善**: (a) 即応: `_alert_milestones` に `gconf.default_task_channel_id` フォールバックを追加し、それも無ければ `bot.log_to_channel` で理由を出す。(b) 本筋: `DEFAULT_PROGRESS_CHANNEL_ID` に一本化してマイグレーション（015）で旧キーを移す。
**工数**: S（a）/ M（b）

---

## P0-3. `/setup-status` が「使われない設定」だけをチェックし、通知の生命線を見ていない

**根拠**: `cogs/help.py:222-234` がチェックするのは 通知チャンネル(=`DEFAULT_ANNOUNCE_CHANNEL_ID`) / ログ / 管理者ロール / 班 / 桁 の5項目。

ところが `DEFAULT_ANNOUNCE_CHANNEL_ID` は、`grep` した限り**送信に一度も使われていません**（`config.py` で読み込み、`cogs/settings.py`・`setup_wizard.py`・`help.py:225` の判定に出るだけ）。`DEFAULT_PROGRESS_CHANNEL_ID` も同様に cogs/services から一度も読まれません。

逆に、実際に通知が飛ぶ `DEFAULT_TASK_CHANNEL_ID`（`cogs/reminders.py:586-590`）と、L2 判定の唯一の根拠である `LEADER_ROLE_IDS`（`utils/permissions.py:74-75`）は**チェック対象外**です。

**影響**: 管理者が `/setup` を完走 → 「すべての項目が設定済みです」と表示 → しかし毎朝のタスク通知は送信先が無く、`#bot-log` に一行出るか（それも未設定なら）完全に無音で捨てられる。「設定したのに通知が来ない」の主因です。

**改善**: `collect_setup_status()` の項目を実効設定（TASK / BOT_LOG / ADMIN / LEADER / 班 / 大会日）に差し替える。死に設定は「使う」か「消す」かを決める。
**工数**: S

---

## P0-4. 班長（L2）を `/setup` から設定できず、班長が何もできない

**根拠**: `cogs/setup_wizard.py:44-47` のロール設定は `ADMIN_ROLE_ID` と `EXEC_ROLE_ID` の2つだけ。L2 の判定は `LEADER_ROLE_IDS` のみ（`utils/permissions.py:74-75`）。

`/member set-leader` と `/member setup ... is_leader:True` が書くのは `members.is_leader` 列で、**Discord 側の権限判定には一切使われません**（使うのはダッシュボードの認可のみ、`dashboard/security.py:81-82`）。

**影響**: `/setup` を完走し `/member setup is_leader:True` まで済ませても、班長は `/schedule create` `/task assign` `/layer keta-add` `/progress add` が全部「権限がありません」。しかも `/setup-status` は「すべて設定済み」と言う。GUIDE.md:105 が開発者モードでロールIDをコピーする手順を載せている事実自体が、この詰まりの発生率を示しています。

**改善**: `ROLE_SETTINGS` に `LEADER_ROLE_IDS` を追加（`RoleSelect(max_values=5)` で複数選択、追記ではなく上書き保存）。`/setup-status` にも項目追加。
**工数**: M

---

## P0-5. 権限昇格の経路がある — ダッシュボードで班のロールIDを書き換えて bot にロールを付けさせられる

**根拠**:

```python
# repositories/table_repository.py:137-139  ← L2 で編集可能
_c("leader_role_id",    "班長ロールID",   "text", editable=True),
_c("member_role_id",    "班員ロールID",   "text", editable=True),
_c("secondary_role_id", "副所属ロールID", "text", editable=True),
```

編集に必要なのは `EditorGuild` = `Level.L2`（`dashboard/security.py:134`, `:81-82` の `members.is_leader`）。

一方、Discord 側で同じ列を書く `/team-role` は**管理者限定**（`cogs/teams.py:212` の `@app_commands.check(is_admin)`）です。ダッシュボードだけ緩い。

そして `cogs/members.py:83-113` の `_sync_roles` が `teams.member_role_id` をそのまま `add_roles()` に使い、これを叩く `/member assign-team` は L2（`cogs/members.py:201`）です。

**成立条件と経路**: 班長が (1) ダッシュボードで自班の `member_role_id` を `ADMIN_ROLE_ID` の値に書き換え、(2) `/member assign-team user:自分 team:自班` を実行すると、bot 権限で管理者ロールが付与され L4 に昇格します。前提は「bot が Manage Roles を持ち、対象ロールが bot ロールより下」で、運用ロールでは通常成立します。加えて `dashboard/routers/settings.py:65-82` の設定読み取りは L1 で通るため、`ADMIN_ROLE_ID` の値は誰でも API から取得できます。

補足として `members.is_leader` も `editable=True`（`table_repository.py:120`）なので、L2 は他人をダッシュボード L2 に昇格させられます。

**影響**: 学生運営で班長は毎年入れ替わります。悪意が無くても「ロールIDの列があるから入れてみた」で事故ります。監査ログ（`dashboard.update`）には残りますが、それを Discord から読む手段はありません（→ P0-6）。

**改善**: `leader_role_id` / `member_role_id` / `secondary_role_id` を `editable=False` にして `/team-role` に一本化する（数行）。`is_leader` の編集も L4 限定に。設定読み取りのロールID系は L4 のときだけ値を返す。
**工数**: S

---

## P0-6. `audit_log` を読む手段が存在しない（`/report audit` は別テーブルを見ている）

**根拠**: `cogs/reports.py:38` が `RemindersLogRepository` を使い、`:178` で `reminders_log` を読んでいます。説明文は「直近の通知・監査ログ」。

`AuditLogRepository.list_recent`（`repositories/audit_log_repository.py:38`）を呼ぶコードは、bot 側には**1箇所も存在しません**（ダッシュボードのみ）。

しかし `audit_log` には `/setup`、班マスタ変更（`cogs/teams.py:92,139,240`）、Todoist 登録、年度替わり（`cogs/season.py:290`）、データ削除予約（`cogs/data.py:261`）、**ダッシュボードからのセル編集**（`dashboard/routers/tables.py:230`）が記録され続けています。

さらに `/data export` の対象は `TABLES` の7テーブルのみ（`repositories/table_repository.py:84-215`）で、`audit_log` / `seasons` / `progress_milestones` / `layer_ketas` / `skill_tags` / `settings` は**持ち出せません**。

**影響**: 「誰が班を消したか」「ダッシュボードで誰が進捗率を書き換えたか」を調べる手段がゼロ。P0-5 の事故が起きても事後追跡できません。加えて `/data export` と `/season rollover` のスナップショットを「全データ」と説明している（GUIDE.md:428,467 / `cogs/season.py:261,281`）のは実装と食い違っています。

**改善**: (a) `/report changes`（L3）で `audit_log` を表示。既存の `/report audit` は `/report notifications` に改名。(b) `TABLES` に読み取り専用の TableSpec を追加すれば export とダッシュボードに同時に効きます。(c) 当面は文言を「主要7テーブル」に修正。
**工数**: S〜M

---

## P0-7. デプロイ CI がダッシュボードを一切更新しない

**根拠**: `.github/workflows/deploy.yml:24-30` の全内容が `git pull` → `pip install -r requirements.txt` → `systemctl restart club-bot.service` のみ。`dashboard/requirements.txt` の install も `club-bot-dashboard.service` の restart もありません。

にもかかわらず `deploy/club-bot-dashboard.service:9-11` は「`.github/workflows/deploy.yml` も同じ前提でデプロイします」と書いています。

**影響**: main に push してもダッシュボードは旧コードのまま動き続けます。さらに bot 側の再起動でスキーマだけが新版に移行されるため（`utils/db.py:722`）、**旧コードのダッシュボードが新スキーマを触る**状態が任意の期間続きます。現状は追加のみのマイグレーションなので致命傷にはなっていませんが、列の rename が入れば即座に壊れます。

**改善**:
```yaml
./venv/bin/python -m pip install --no-input -q -r requirements.txt -r dashboard/requirements.txt
sudo systemctl restart club-bot.service club-bot-dashboard.service
curl -fsS http://127.0.0.1:8000/healthz
```
（sudoers にダッシュボードのユニットが許可されているか要確認）
**工数**: S

---

## P0-8. PostgreSQL 本番でダッシュボードのセル編集が全滅している可能性が高い【要検証】

**根拠**: `dashboard/routers/tables.py:237-241` は `row_id: str` で受け、`repositories/table_repository.py:410-417` がそのままバインド値にします。`utils/db.py:827-839` の `_prepare()` は `?` → `$n` の置換のみで型変換をしません。

主キーが BIGINT の表は members / tasks / teams / schedule_votes / layer_records / progress の6つ（TEXT は schedules のみ）。asyncpg は厳格な型チェックをするため `DataError` → 500 になるはずです。SQLite は型親和性で通ってしまい、テストは SQLite のみ（`tests/test_dashboard_edit.py:39-53`）なので CI では検出されません。

**注記**: 手元に PostgreSQL が無いため実測していません。**まず `CLUB_TEST_PG_DSN=... pytest tests/test_dashboard_edit.py` を PG で1回回して確認してください。** 5分で白黒つきます。

**影響**: もし成立していれば、本番構成で「セルをクリックして編集」がほぼ全滅し、画面には「エラーが発生しました (500)」としか出ません。

**改善**: `TableSpec` に `pk_type` を持たせ、`get_row` / `update_row` の入口で `int()` 変換（失敗は404）。CI に postgres service を足して PG でも回す。
**工数**: S（修正）/ M（CI 整備込み）

---

# P1. 使いやすさの改善

## P1-1. 権限エラーが「L2 以上が必要」だけで、誰に頼めばいいか分からない

`utils/permissions.py:110-115` のメッセージは `この操作には L2 以上の権限が必要です。` のみ。L1〜L4 の意味（一般 / 班長 / 幹部 / Bot管理者）は**ソースのコメントにしか存在せず**、Discord のどの出力にも凡例がありません。

`gconf` には `admin_role_id` / `exec_role_id` / `leader_role_ids` が揃っている（`permissions.py:69-77`）ので、**ロールメンションを出すだけで解決します**。

```
この操作は**班長以上**が実行できます（あなたは一般メンバー）。
依頼先: @班長 @幹部
```

全89コマンド共通の入口を1関数直すだけで全体が良くなる、費用対効果が最も高い改善です。**工数 S**

---

## P1-2. 破壊的操作の大半に確認ステップが無く、Undo も無い

確認が**ある**のは `/data delete`（Modal でサーバー名入力 + ZIP 添付 + `/data delete-cancel`）、`/season rollover`（`RolloverView`）、`/team-remove`（所属者がいる場合のみ `confirm:True`）の3つ。

確認が**無い**もの:

- `cogs/progress.py:1001-1011` `/progress remove` — 引数を確定した瞬間に**配下ごと**消える。「主翼」を選ぶとリブ・桁・フィルムまで一括削除され、実行後に「合計47件を削除しました」と告げられる。復旧手段なし
- `cogs/schedule.py:446-473` `/schedule delete` — Discord の投票メッセージを全削除した後 DB を CASCADE 削除。票データ完全消失
- `cogs/season.py:170-197` `/season new` — 現年度を即終了。`rollover` にはある確認もスナップショットも無い

**改善**: `season.py:59-127` の `RolloverView` を汎用 `ConfirmView` に切り出して4箇所に適用。`/schedule delete` は論理削除にすれば Undo が成立します（`/team-remove` `/skill-remove` `/layer keta-remove` は既に論理削除方式なので方針統一にもなる）。**工数 M**

---

## P1-3. 投票ID・タスクIDを手で写させている

`cogs/schedule.py:347,393,416,441,482` の5コマンドすべてが素の `schedule_id: str`。`cogs/tasks.py:280,311,342,369` の `task_id: int` も同様。

一方、進捗ノードは `cogs/progress.py:1482-1491` で10箇所に階層字下げ付きオートコンプリートを一括登録し、内部ID を一度も見せない作りになっています。**同じ物差しを schedule と task にも当てるだけ**です。

`/schedule list` の ephemeral から `sch_a1b2c3` をコピーして貼り直す作業は、スマホでは相当な苦痛で、1文字ミスると「指定 ID の投票が見つかりません」しか返りません。**工数 S**

---

## P1-4. `/schedule remind` が何もしていないのに「再通知しました」と成功表示する

`cogs/schedule.py:670-678` の `notify_unanswered` は `target_role_id` が無いと `return 0`。呼び出し側（`:430-438`）はそれを緑の成功 Embed で「対象: 0 名」と表示します。

`target_role` は `/schedule create` の**任意**パラメータで、GUIDE.md:182 は「特定の班だけに聞くとき」と説明しているため、全体向けの投票では普通に省略されます。その投票では締切1時間前の自動催促（`reminders.py:157-164`）も無音のまま `status="success"` を記録し、二重送信防止フラグが立つので**後から対象ロールを付けても永久に再送されません**。

**改善**: 0 の理由を区別し、対象ロール未設定なら明示的にエラーで返す。`_log_reminder` の status を `skipped` に。**工数 S**

---

## P1-5. 日程調整を作っても誰にも通知が行かない

`cogs/schedule.py:194` は `target_channel.send(embed=embed)` のみで、`content` にロールメンションを付けません。`target_role` は催促時にしか使われず、自動催促の窓は締切1時間前だけ（`reminders.py:143-150`）。

結果、投票は静かにチャンネルへ落ち、全員が気づかないまま締切1時間前に一斉 DM が飛びます。「日程調整、いつの間に立ってたの？」が常態化します。

**改善**: 作成時に `content=f"{target_role.mention} ..."` を付ける（1行）。加えて催促を「24時間前」と「1時間前」の2段階に。**工数 S / M**

---

## P1-6. タスクを割り当てられた本人に何の通知も届かない

`cogs/tasks.py:198-262` の `_finalize_add_task` は実行者に ephemeral を返すだけ。`assignee` は DB に保存されるのみで DM もメンションも送りません。`/task assign` も同様。

担当者が知るのは、班チャンネルが設定済みかつ期限が7日以内になった朝の通知（`reminders.py:193`）を待つときだけ。期限が2週間先のタスクは誰にも認識されないまま放置されます。

**改善**: 担当者へ DM、`Forbidden` なら班チャンネルへメンション（`schedule.py:689-701` に同じ方針の実装が既にある）。**工数 S**

---

## P1-7. 誰でも他人のタスクを完了・削除でき、他人に技能タグを付けられる

- `cogs/tasks.py:279-292` `/task done` は `@require(Level.L1)` で、担当者・作成者との照合が**一切ありません**。ID を打ち間違えると他班の重要タスクが完了扱いになり Todoist からも消えます（`:296`）。tasks.py は `AuditLogRepository` を使っていないので記録も残りません
- `cogs/members.py:442-486` `/member skill add|remove` は L1 でありながら `user: discord.Member` を受け、他人に技能タグを付け外しできます

**改善**: 「自分の担当 or 作成者 or L2 以上」の条件を入れ、拒否時は担当者をメンションして案内する。技能タグの他人指定は L2 以上に限定。**工数 S**

---

## P1-8. ボタン・メニューが期限切れになっても画面上は押せるままで、押すと無言で失敗する

`cogs/progress.py:478-480` ほか5箇所の `on_timeout` は `item.disabled = True` するだけで、`message.edit(view=self)` を呼びません。View の disabled は**メッセージを編集して初めて反映**されます。`cogs/season.py` の `RolloverView` には `on_timeout` すらありません。

最も痛いのは `/season rollover` で、卒業者を選んでいる最中に5分経つと「確定する」が無反応になり、選択内容が全部消えます。`/task add` のセクション選択は2分（`tasks.py:36`）で、タスク名を考えている間に切れます。

**改善**: `TimeoutAwareView` 基底クラスを1つ作り、`view.message = await interaction.original_response()` を保持して timeout 時に「時間切れです。もう一度実行してください」へ差し替える。`rollover` は timeout を 300→900 に。**工数 M**

---

## P1-9. 一覧の打ち切りが利用者に伝わらない / 表示が内部名のまま

打ち切りを告知している良い例（`tasks.py:813`, `progress.py:275`）がある一方、`teams.py:181`（`/team-list` は26件目以降が消える）、`progress.py:1416`（マイルストーン51件目以降）、`members.py:536`（`/member support` の26人目以降）、`reports.py:206`（**`/report attendance-rate` は26件目以降が集計から落ちるので数字自体が誤りになる**）は無言で切っています。

また `/progress edit` の変更内容表示は DB のカラム名そのままで、画面に `- manual_progress: None` `- parent_id: n_3f9a01b2c4` と出ます（`progress.py:991-996`）。`/report audit` の対象も生の18桁ユーザーIDのまま（`reports.py:186`）で、`discord_name_cache` があるのに使っていません。

**改善**: `help.py:135-147` の `_join_within`（制限内に収めて残件数を示す処理）が既に正しく書けているので、`utils/embeds.py` へ移して全 Cog から使う。表示名マップを追加。**工数 S**

---

## P1-10. `/progress edit` に変な進捗率を入れると、成功と表示されつつ進捗が消える

`services/progress_tree.py:82-107` の `parse_progress` は解釈不能なら `None` を返し、`cogs/progress.py:977-979` はそれをそのまま `manual_progress` に代入します。

`/progress edit node:主桁 progress:半分` で**既存の進捗率が消え**、それでも緑の成功 Embed が出ます。

**改善**: `parse_progress` の仕様（移行スクリプト用）は変えず、コマンド側で `None` を弾いて「`0.5` `50%` `50` の形式で指定してください」と返す。**工数 S**

---

## P1-11. Todoist 側の失敗を握りつぶすため、Discord と Todoist が静かに食い違う

`cogs/tasks.py:293-299` / `:324-330` / `:572-573` はいずれも `except TodoistError: pass` で、`log.warning` すら出しません。

`/task done` は必ず「完了にしました」と返るのに Todoist 側は未完了のまま残り、翌朝の通知に出続けます。利用者からは「完了にしたのに毎朝催促される」としか見えません。

**改善**: ローカル完了は維持しつつ、成功メッセージに同期結果を明記する（`⚠️ Todoist 側の完了に失敗しました。Todoist 上で直接完了にしてください`）。**工数 S**

---

## P1-12. ダッシュボードの実用上の弱点（まとめ）

| 項目 | 現状 | 根拠 | 改善 | 工数 |
|---|---|---|---|---|
| ページング | API には `limit/offset` があるのに**フロントが一度も送らない**。201行目以降は到達不能で「3000件中200件を表示」と出るだけ | `static/app.js:365`, `table_repository.py:22` | ツールバーに前後ページボタン | S |
| 検索・絞り込み・ソート | **存在しない**。並び順は `TableSpec.order_by` 固定 | `app.js` 全420行に該当実装なし | 列ヘッダのソートはホワイトリスト方式で（現行の SQL 組み立て方針を維持） | M |
| CSV が500行で無言の切り捨て | 全件を読む `list_all_rows` が**実装済みなのに使われていない**。シート絞り込みも無視 | `routers/tables.py:209` vs `table_repository.py:395` | `list_all_rows` に差し替え | S |
| セル編集が blur で確定 | `blur` → 保存。**モバイルには Escape が無く取り消し不能** | `app.js:178` | blur をキャンセルにし、確定は Enter と ✓ ボタンのみ | S |
| 設定画面が無い | `/settings` API も `/summary` API も**画面から一度も呼ばれない**（デッドコード） | `app.js` の fetch は5箇所のみ | 「設定」タブを追加、または API を削除 | M |
| `GUILD_NAME` を保存しても反映されない | ダッシュボードは `GUILD_NAME` を「サークル名」として書くが、週次サマリーが読むのは `CLUB_NAME` | `routers/settings.py:38` vs `config.py:319` | キーを `CLUB_NAME` に統一 | S |
| 値の検証がサーバー側に無い | 型チェックはクライアントの `parseInput` のみ。`progress.parent_id` に存在しないIDを入れると**そのノードと子孫が進捗表から静かに消える** | `table_repository.py:427-438` | 列型に沿った正規化と 400 応答、`parent_id` の実在・循環チェック | M |
| エラーで画面全体が消える | `showError` が `#app` をまるごと置換。再試行ボタン無し。セッション切れ時もログインリンクが出ない | `app.js:44-46, 368-370` | 401 で `renderLoginPrompt()`、エラーはグリッド内に留める | S |
| キーボード操作不可 | `td` に `tabindex` も keydown も無く、編集はマウス/タッチ必須 | `app.js:137-140` | `tabindex="0"` + Enter/Space | S |
| モバイル未対応 | メディアクエリは `prefers-color-scheme` のみ。sticky ヘッダも `.grid-wrap` に高さ制限が無く機能していない | `style.css:13, 145-159` | `max-height:70vh` + 狭幅レイアウト | S〜M |
| セッションが7日固定 | 所属ギルドと `manage_guild` を Cookie に焼き込む。**退会・降格が最大7日反映されず、失効させる手段も無い** | `auth.py:189-197`, `config.py:20` | 既定を24時間に短縮＋期限切れ時の再検証 | S〜M |
| ログが捨てられている | `setup_logging()` の呼び出しは `bot.py:439` のみ。ダッシュボードのルートロガーは WARNING のまま | `dashboard/main.py:33-35` | `create_app()` で `setup_logging()` を呼ぶ（ログファイル名は要分離） | S |
| 同時起動でマイグレーションがレースする | bot と dashboard が両方 `connect()` → `_migrate()` を呼ぶ。排他制御なし | `bot.py:120`, `dashboard/db.py:30` | PG では `pg_advisory_lock` で囲む（数行） | S |

**セキュリティ面で問題なしと確認したもの**（誇張を避けるため明記）: CSRF（`same_site="lax"` + CORS 未設定 + `frame-ancestors 'none'`）、XSS（`innerHTML` 使用ゼロ、`textContent` のみ）、SQL インジェクション（テーブル名・列名はホワイトリスト、リクエスト由来は必ずバインド）、guild_id スコープの迂回、CSV インジェクション（`csv_safe` でエスケープ済み）、オープンリダイレクト、秘密情報の露出。

---

## P1-13. 通知が08:30に集中し、超過タスクは毎晩永久に再送される

同時刻に発火するループが4系統あります（`reminders.py:193` の3ジョブ + `:242` の週次 + `progress.py:803`）。うち `reminders.py:428-568`（Todoist セクション別）と `progress.py:809-871`（Todoist プロジェクト別）は**同じタスクを別の切り口で二重投稿しえます**。

再送抑止も非対称です。マイルストーンは `reminders.py:280` で ISO週キーにより週1回に絞られている一方、`daily_night`（21:00 の超過タスク）と 7日以内通知には重複判定が無く、放置された超過タスクは**毎晩永久に**同じ内容が流れます。

そして**通知をオフにする設定キーが存在しません**（`NOTIFY_*` / `REMINDER_*` の grep はゼロ件）。班チャンネルを外しても既定チャンネルにフォールバックするので逃げ場がありません。

**改善**: (a) 超過通知は週1回か「新たに超過したもの」だけに、(b) 7日以内は初回と前日のみ、(c) `REMINDER_DISABLED_TYPES` を settings に用意して `/setup` から切れるように。**工数 M**

---

## P1-14. 空状態の作り込みがバラバラ

次の一手を示している良い例（`progress.py:75-78` の「`/progress add` で機体を追加してください」、`teams.py:165-169`、`season.py:147-155`）がある一方、`/task list`（`tasks.py:794`）、`/schedule list`（`schedule.py:331`）、`/layer keta-list`（`layer_tracking.py:101`）は「ありません」で終わります。

特に `/report weekly` は新規サーバーで「未完了 0 / 超過 0 / 投票 0」と表示され、**健全に運用できている状態と見分けが付きません**。幹部が最初に叩くコマンドなので、ここでゼロ埋めの表を見せると導入判断そのものを損ないます。

**改善**: 空状態 Embed をユーティリティ化し「状態の説明 + 次の1コマンド」を必ず含める規約に。**工数 S**

---

## P1-15. コマンド命名が3種類混在している

- アンダースコア: `/settings_list` `/set_channel` `/set_role`（7個）
- ハイフンのトップレベル: `/team-add` `/skill-add` `/todoist-setup` `/setup-status`（11個）
- グループ: `/member assign-team` `/layer keta-add`

語順も `/team-add`（名詞→動詞）と `/set_channel`（動詞→名詞）が同居。特に問題なのは `/skill-add`（管理者用マスタ）と `/member skill add`（自分にタグ付与）が**別物なのに前方一致で混ざる**ことです。

また `/set_channel` / `/set_role` は ID を文字列で受け取り `<#...>` を手で剥がしていますが（`settings.py:247-248`）、`cogs/members.py:392` は `channel: discord.TextChannel`、`cogs/teams.py:216` は `role: discord.Role` と正しく型で受けています。**同一コードベースに2つの流儀が併存**しています。

**改善**: 型で受ける形に統一するのは即効性が高く安全（S）。命名統一はグループへ寄せる方向で、旧名→新名の対応表を `/help` に1リリース分載せる（M）。

---

## P1-16. 一覧系が ephemeral 固定で共有できず、逆に `/progress view` は必ず公開される

公開応答は89コマンド中6つだけ。`/task list` `/report weekly` `/schedule list` `/layer status` などはすべて `ephemeral=True` 固定で、班ミーティングで画面共有できずスクリーンショットを貼る運用になります。

逆に `/progress view` は必ず公開投稿され、しかも所有者ガード（`progress.py:430-438`）により**他人には見えるのに操作できない置物**が残ります。

**改善**: 一覧系に共通の `public: bool = False` 引数を追加。`/progress view` は既定を ephemeral に反転し、`public:True` のときだけ公開かつガードを外す。**工数 M**

---

## P1-17. `/set_role` の班長ロールは追加専用で、1つだけ外せない

`cogs/settings.py:288-295` は常に `f"{current},{role_id}"` で追記し、重複チェックもありません。1つ外すには `/settings_delete LEADER_ROLE_IDS` で**全部消して全部入れ直す**しかなく、その間は全班長が L1 に降格します。

年度替わりでも `/season rollover` がリセットするのは `members.is_leader` のみ（`services/season_service.py:71`）で、実効的な L2 の源である `LEADER_ROLE_IDS` には触れません。**毎年ロールIDが積み上がり、3年目には誰が班長なのか設定から読めなくなります。**

**改善**: `action: add|remove` を追加。`rollover` の結果 Embed に「班長ロールの見直し」チェックリストを添える。**工数 M**

---

## P1-18. 大会日の設定手段が生の設定キーの手打ちだけで、書式ミスが無言で握りつぶされる

`cogs/progress.py:169-173` は未設定時に `/settings_set` で `COMPETITION_DATE` を登録せよと案内しますが、`/settings_set` にはキーのホワイトリストも値の検証もありません（`settings.py:167-191`）。

`2027/07/25` と入れると保存は成功し `/settings_list` にも出るのに、`/countdown` は「大会日: 未設定」と表示します。しかも `/setup-status` は大会日をチェックしません。

**改善**: `/progress competition-date` のような専用コマンド（Modal + 書式検証）を作り、設定キー名を利用者に晒さない。`/settings_set` に既知キーのオートコンプリートと `*_DATE` `*_CHANNEL_ID` の形式検証を入れる。**工数 M**

---

## P1-19. 招待直後の案内が、ほとんどのサーバーで誰にも届かない

`bot.py:331-336` の「次のステップ: `/setup` を実行してください」は `log_to_channel(..., guild_id=...)` 経由で送られますが、`bot.py:363-370` は `BOT_LOG_CHANNEL_ID` が未設定なら**無言で破棄**します。

そして `bot.py:69-76` の `INVITE_PERMISSIONS` は `manage_channels` を含まないため、README 記載の招待URLで入れた場合 `#bot-log` は自動作成されません。招待者への DM もシステムチャンネルへの投稿もフォールバックも実装がありません。

さらに `bot.py:209-227` は**権限不足で何も作らなかった場合でも `AUTO_SETUP_DONE` を立てる**ため、GUIDE.md:59-61 の「権限を付けてから再度招待してください」という復旧手順が効きません（settings は退出後30日残るため）。

**改善**: 案内を「bot-log →（無ければ）`guild.system_channel` →（無ければ）送信可能な最初のチャンネル」の順で送り、`/setup` `/setup-status` `/help` を明記。作成に成功したときだけマーカーを立てる。`/setup` に「不足しているものを今すぐ作る」ボタンを置く。**工数 S〜M**

---

## P1-20. GUIDE.md と実装の齟齬

| 箇所 | GUIDE の記述 | 実装 |
|---|---|---|
| GUIDE.md:364-365 | 毎朝 **08:00** にタスク通知 | `reminders.py:193` は **08:30** |
| GUIDE.md:360-368 の通知表 | 4件 | 実際は `weekly_milestone_alert`（月曜08:30）、`daily_purge`（04:00）、`progress.py:803` が抜けている。班チャンネルへの振り分けも未記載 |
| GUIDE.md:484-509 の早見表 | — | `/help` `/setup-status` `/countdown` `/weight` `/milestone` `/season` `/data` が**全部無い**。`/weight` は GUIDE 全体で0ヒット |
| GUIDE.md:428,467 | 「全データ」 | 実際は7テーブルのみ（→ P0-6） |

`tests/test_docs_commands.py:1-24` は `docs/OPERATION.md` だけを `bot.tree` と突き合わせています。**導入サークルが実際に読む GUIDE.md は検証対象外**なので、今後も同じ形で腐ります。

**改善**: 通知表と早見表を実装に合わせて更新し、`test_docs_commands.py` の検査対象に GUIDE.md の付録を追加。**工数 S**

---

# P2. 導入すると効果が大きい新機能

小さく作れて効くものから並べています。**上位5件はいずれも新規テーブル不要**で、既に DB に貯まっているのに見せていないデータを価値化するものです。

## P2-1. `/layer stats` — 積層記録の集計【S / 効果 大】

`layer_records` には「誰が・どの桁の・何層目を・何分」が全部溜まっているのに、**人間が読める形で出すコマンドがゼロ**です。`/progress` に出るのは率だけで、**時間の情報が完全に捨てられています**。

班長が知りたいのは「主桁1はあと何層か」「今週の投入工数」「1層あたり平均何分だから残り何日か」「特定の人に偏っていないか」。今はダッシュボードの生テーブルか CSV を自分で集計するしかありません。

- **乗る資産**: `layer_records`（`utils/db.py:256`）、目標層数は `progress_spar_links.target_layers`（`utils/db.py:404`）、名前解決は `discord_name_cache`、桁補完は既存の `_keta_autocomplete`
- **最小実装**: `/layer stats [keta] [period:今週|今月|全期間]` → 桁別（完了層数/目標・合計時間・1層平均分・最終作業日）と人別の2セクション

## P2-2. 週次ダイジェストの自動投稿【S / 効果 大】

`/report weekly` は L2 以上・ephemeral で、部員には「今週サークル全体で何が進んだか」が一切見えません。人力飛行機は数ヶ月続く長期戦で、**進んでいる実感が途切れると来なくなります**。

毎週月曜に「先週の積層 12層 / 34時間 / 参加9名 / 主桁 62%→68% / 完了タスク7件」が公開チャンネルに流れるだけで、来た人の労力が可視化され、来なかった人にも状況が伝わります。

- **乗る資産**: `reminders.py:242` の `weekly_milestone_alert`（曜日で絞る `tasks.loop`）を複製、二重送信防止は `reminders_log_repository.exists` の既存イディオム。集計元は `layer_records` / `tasks` / `schedule_votes` / `progress_tree.load_tree`。表示は `utils/progress_bar.py`
- **最小実装**: `services/digest_service.py`（純粋関数）+ ループ1本。`/report weekly` に `public` 引数を足して手動でも同じ Embed を投げられるように

## P2-3. `/layer cancel` と押し忘れ検知【S / 効果 中〜大】

`/layer start` したまま帰宅すると翌日の `/layer end` が「1200分」を記録し、しかも完了層数が増えるので**進捗率まで水増しされます**。打ち間違えて start した場合の取り消し手段も無く、必ずゴミ行が残ります。

- **乗る資産**: `layer_sessions.started_at`、`LayerTrackingService.list_active`（経過分を既に返す）、通知は `reminders.py:128` の5分ループに相乗り
- **最小実装**: (a) `/layer cancel`（記録を残さず破棄）、(b) 閾値超過で本人へ DM、(c) さらに超過で自動 cancel

## P2-4. `/me` — 個人サマリー【S / 効果 中〜大】

部員視点の入口がありません。自分のタスク・未回答の投票・自分の積層実績・担当ノードはそれぞれ別コマンドで、`/task list` は全体を返します。**新入生が「今日自分は何をすればいいか」を1コマンドで確認できません。**

- **乗る資産**: 全部既存クエリの合成。新規テーブル不要
- **最小実装**: `/me`（L1・ephemeral）＝ 未完了タスク上位5 / 未回答の投票 / 今月の積層 / 担当中のノード。`user` 引数（L2以上のみ）を後付けすれば班長の面談にも使える

## P2-5. `/schedule confirm` — 確定日程の登録と当日リマインド【S〜M / 効果 大】

`finalize_schedule`（`cogs/schedule.py:704`）は締切時に集計を投稿して終わりで、**「結局いつに決まったのか」がどこにも残りません**。誰かが手で「◯日にやります」と書き、見逃した人が来ない、が毎回起きます。前日・当日朝のリマインドもありません。

- **乗る資産**: `schedules` に `confirmed_option_id TEXT NULL` を1列（マイグレーション015）。リマインドは朝ループに1関数
- **最小実装**: `/schedule confirm schedule_id option_id`（L2）→ 確定保存 + 対象ロールへ告知。前日20時と当日朝に通知。`schedule_votes` の `status='ok'` から**参加表明者だけへ DM** も即座に出せる
- **発展**: `.ics` ファイルを添付すれば、外部依存ゼロでカレンダー連携の目的を果たせます

## P2-6. `/report member-attendance` — メンバー軸の出欠【S / 効果 中〜大】

`/report attendance-rate` は投票ごとの ok 率で、**「最近来ていない人」が特定できません**。サークル運営の最大の失敗は退部の予兆を見逃すことで、「3回連続で未回答」はほぼ確実な予兆です。

- **乗る資産**: `schedule_votes` と既存の `list_schedule_votes` / `list_voters_for_schedule`。新規テーブル不要
- **最小実装**: `/report member-attendance [months:3]`（L2以上・**ephemeral 固定**で晒しにならないように）→ メンバー別の回答率・ok率・連続未回答数を回答率の低い順に

## P2-7. `/poll` — 汎用投票【S / 効果 中】

他の公開 Discord bot ではまず必ずある機能。「機体の名前」「Tシャツのサイズ」「合宿の宿A/B」など日程以外の意思決定は年に何十回もあり、今は手でリアクションを付けて誰かが数えています。**`/schedule` のリアクション集計基盤がそっくり使えるのに日程専用に閉じています。**

- **乗る資産**: `services/schedule_service.py` の集計、`cogs/schedule.py:568` の raw reaction リスナ、締切ループ。`schedules` に `kind TEXT DEFAULT 'schedule'` を1列足すだけ
- **最小実装**: `/poll create title options:"A;B;C" [deadline] [multi]`

## P2-8. 新入生オンボーディング【S〜M / 効果 中〜大】

新歓期に30〜50人が一気に入りますが、`on_member_join` は名前キャッシュを更新するだけ（`cogs/name_cache.py:157`）。**bot は新入生の存在を知らず、`/member register` を幹部が1人ずつ手打ちしています**。結果、名簿に載らない人が出て班別通知も出欠催促も届きません。

- **乗る資産**: `/member setup`（`cogs/members.py:287`）が**班選択セレクト付きウィザードを既に持っている**のでUIはほぼ流用。`teams.member_role_id` でロール付与。新規テーブル不要
- **最小実装**: `on_member_join` → ようこそ Embed + 「班を選ぶ」ボタン → `members` 登録 + ロール付与。`/setup` に ON/OFF を1項目

## P2-9. `progress_snapshots` — 進捗の履歴とバーンダウン【M / 効果 大】

`services/milestone_service.py:9-14` が自ら書いている通り、履歴が無いため遅延判定のペースが「作成日〜最終更新日の平均」という粗い推定にしかならず、判定不能が多発します。そして**「先週から何%進んだか」が誰にも分かりません**。

- **乗る資産**: 書き込みフックは `cogs/progress.py:776` の20分同期ループ末尾。読み出しはダッシュボードの既存 SVG 描画に乗る。新規テーブル1つ（`progress_snapshots(guild_id, node_id, snapshot_date, aggregated, actual_weight_g)`）
- **最小実装**: 日次スナップショット → `/progress history [node] [days:60]` でテキストのスパークライン → `milestone_service` のペース算出に採用
- **相乗効果**: P2-2 の週次ダイジェストが「主桁 62%→68%」を言えるようになります

## P2-10. `/stock` — 資材・消耗品の在庫と発注アラート【M / 効果 大】

人力飛行機で最も痛い事故は「**プリプレグが無くて桁が巻けない**」「エポキシ／フィルム／離型剤が切れて作業日が丸ごと潰れる」。カーボンプリプレグは納期が数週間なので、切れてから気づくと工程が1ヶ月ずれます。しかも**発注判断は「残量が閾値を割った」という、まさに bot が自動で見張れる条件**です。

- **乗る資産**: マスタ管理は `layer_ketas`（有効フラグ・オートコンプリート付き）とほぼ同型。通知は朝ループに相乗り。新規テーブル2つ（`stock_items` / `stock_movements`）
- **最小実装**: `/stock list`（閾値割れを赤表示）/ `/stock add` / `/stock use`（L1・消費記録）/ `/stock set-threshold`。閾値割れで即通知、以降は朝のダイジェストに含める
- **発展**: `stock_movements` があれば次年度の**発注量見積もり**まで自然に伸ばせます

## P2-11. `/tool` — 工具・機材の貸出管理【M / 効果 中】

「ノギスが無い」「トルクレンチを誰が持ち帰ったか分からない」。共用工具は数が少なく高価で、行方不明が作業を止めます。`/layer start` → `/layer end` と**まったく同じ「開始→進行中→終了」モデル**なので既存コードの型どおりに書けます。P2-10 と同じ Cog にまとめれば実質 S 追加。

## P2-12. `/incident` — ヒヤリハット・事故報告【S〜M / 効果 中（起きたときは極大）】

人力飛行機は課外活動の中でも危険度が高く（工房での切削・溶剤・高所作業・機体運搬・琵琶湖でのテストフライト・長距離輸送）、大学から安全管理体制の提示を求められることもあります。今は事故もヒヤリハットも雑談に流れて消えます。**書きやすさが全て**で、Modal 1枚で出せて幹部に即通知される導線があるかどうかが報告数を決めます。

- **乗る資産**: Modal は `cogs/todoist_admin.py:39` に完成形あり。新規テーブル1つ
- **最小実装**: `/incident report`（L1）→ Modal（日時 / 場所 / 何が起きたか / けがの有無 / 再発防止案）→ 幹部へ通知。`/incident list`（L3）。**匿名フラグ**を持たせる（報告者IDはDBに持つが表示しない）と報告数が明確に増えます

## 補足: `/flight`（テストフライト記録）と `/feedback`（利用者→運営者の窓口 + コマンド利用統計）

いずれも M / 効果 中〜大。前者は TF の記録がスプレッドシートで散逸する問題に、後者は「導入サークルが困っても運営者に届く導線がゼロ」「どの機能が使われているか分からず次に何を作るべきか決められない」という**公開 bot の運営者側の必須機能**の欠落に効きます。

---

## 検討したが提案しなかったもの

| 案 | 見送りの理由 |
|---|---|
| 天候API連携で活動可否を自動判断 | 全テナント分のAPIキー・費用を運営者が負担する構造になる。何より **bot の判断が安全判断の根拠にされるのは責任上危険**。`milestone_service.py:9` の「嘘の予測を出さない」方針とも不整合。天候は P2 の飛行記録の**手入力欄**として残すのが妥当 |
| 大会エントリー書類の期限管理（専用機能） | `/task add`（期限付き）+ 朝の7日以内通知 + 夜の超過通知 + `/milestone add` で**ほぼ完全に賄えます**。専用テーブルを足すと「期限」が3系統に分裂して二重管理になる。GUIDE.md への運用例追記（工数ほぼゼロ）が正解 |
| Google カレンダー同期 | P3-2 で gspread / google-auth を**わざわざ撤去し、再混入を検出する回帰テストまで置いてある**ので明確な方針逆行。代わりに `.ics` 添付（標準ライブラリのみ・依存ゼロ）で同じ目的を果たせる → P2-5 の拡張として |
| 部費・立替精算・会計管理 | 金銭記録の正本を bot が持つと欠損時の責任が運営者個人に及ぶ。実際の入出金は銀行・大学の会計ルール側にあり、どうやっても二重記帳になる。`PRIVACY.md` / `TERMS.md` の責任範囲も一段重くなる。P2-10 の在庫から CSV を出して既存の会計手段に渡すのが現実的 |
| 機体設計値（翼幅・たわみ・CG位置）の汎用スペック管理 | 「設計値 vs 実測値」の型は **`/weight` で実装済み**。他の物理量へ広げると `progress_nodes` の列が際限なく増えるか汎用EAVになり、「グラム固定・単位設定は作らない」と割り切った既存の設計判断（`utils/db.py:372`）に真っ向から反する。個別の計測は飛行/試験記録の1レコードとして扱うのが素直 |
| 汎用リアクションロール | P2-8（新入生オンボーディング）に完全に包含されます。`teams.member_role_id` を使う限り別機能にする理由がありません |

---

# 推奨ロードマップ

## フェーズ G0: 沈黙障害を止める（1〜2日 / すべて S）

まずここだけやれば、「設定したのに動かない」の大半が消えます。

1. P0-1 `[:25]` を4箇所（`/schedule list-closed` `/schedule list` `/layer status` ほか）
2. P0-2 マイルストーン通知のフォールバック追加
3. P0-3 `/setup-status` の項目を実効設定に差し替え
4. P0-5 `teams` のロールID列を `editable=False`、`is_leader` を L4 限定
5. P0-7 `deploy.yml` にダッシュボードの install / restart / スモークテスト
6. P0-8 PG でダッシュボード編集テストを1回回す（白黒つける）

## フェーズ G1: 全89コマンドに効く共通改善（3〜5日）

7. P1-1 権限エラーにロールメンションと日本語ラベル（**費用対効果が最も高い**）
8. P1-2 `ConfirmView` を切り出して破壊的操作4箇所に適用
9. P1-3 `schedule_id` / `task_id` にオートコンプリート
10. P1-9 `_join_within` を `utils/embeds.py` へ移して打ち切り告知を統一
11. P1-4/5/6 通知の抜け（`remind` の嘘成功 / 作成時メンション / 担当者DM）
12. P1-8 `TimeoutAwareView` 基底クラス

## フェーズ G2: 溜まっているデータを見せる（1週間 / 新規テーブル不要）

13. P2-1 `/layer stats`
14. P2-4 `/me`
15. P2-2 週次ダイジェスト
16. P0-6 `/report changes`（`audit_log` 閲覧）+ export 対象の拡張
17. P2-6 `/report member-attendance`

## フェーズ G3: 導入と定着（2週間）

18. P0-4 `/setup` に `LEADER_ROLE_IDS`（+ P1-17 の add/remove）
19. P1-19 招待直後の案内のフォールバック
20. P2-8 新入生オンボーディング
21. P2-5 `/schedule confirm` + `.ics`
22. P1-20 GUIDE.md 更新と `test_docs_commands.py` の対象拡張

## フェーズ G4: ドメイン拡張（以降）

23. P2-9 `progress_snapshots`（P2-2 の質が跳ね上がる）
24. P2-10 `/stock` → P2-11 `/tool`（同一 Cog）
25. P2-12 `/incident`、`/flight`
26. P1-12 ダッシュボードのページング・検索・設定画面・モバイル対応

---

# 良くできている点

高く評価すべき設計判断を記録しておきます。**これらを壊さない前提で改善を進めてください。**

1. **`guild_id` スコープを型と DI で強制している** — `dashboard/security.py:38-59` の `GuildScope` は `require_guild_scope` からしか生成できず、ハンドラは生の `guild_id` を受け取れません。「間違った guild_id を渡すには意図的に迂回するコードを書く必要がある」という不変条件がコードで表現され、`tests/test_dashboard_scope.py` で検査されています。この規模のプロジェクトとしては非常に質の高い設計です。

2. **`/data delete` の二段階確認とバックアップ導線** — Modal でサーバー名を入力させ、一致しても即削除せず予約にとどめ、削除前 ZIP を添付し、`/data delete-cancel` で取り消せる。破壊的操作のお手本で、これを `ConfirmView` として他へ横展開すべきです（P1-2）。

3. **通知疲れを設計時点で意識している** — `reminders.py:259-264` の「**遅れが無いときは沈黙する**（毎週『問題ありません』を送ると通知が読まれなくなるため）」というコメントと、ISO週キーによる二重送信防止。この思想を全ジョブへ広げるのが P1-13 です。

4. **マルチテナントの障害遮断が全ループで徹底** — `reminders.py` の5ループすべてでギルド単位に `try/except` を張り、「1ギルドの失敗が全ギルドの自動通知を永久に止める」という理由までコメントに書いてある。公開 bot で最も壊れやすい箇所を正しく押さえています。

5. **N+1 を構造的に排除している** — `routers/tables.py:68-103` の `_name_maps` が列型ごとに1クエリだけ発行し、行ごとの解決をしません。各リポジトリの docstring にも「1クエリ。N+1 を作らない」と明記され、実装もその通りです。

6. **依存を増やさない判断** — matplotlib を捨ててテキスト進捗バーに（常駐37.5MBとCJKフォント依存を排除）、gspread / google-auth を撤去して回帰テストで再混入を検出。フォント環境や外部サービスに左右されない選択は、学生が代替わりしながら運用する前提で極めて堅実です。

7. **踏んだ罠が再発防止策とセットで残っている** — `deploy/club-bot-dashboard.service:53-56`（ReadWritePaths のディレクトリが無いと 226/NAMESPACE で再起動ループ）、`deploy/Caddyfile:66-72`、`utils/db.py:744-750`（インデックスは必ずマイグレーションの後）など。代替わりのある組織でこれは大きな資産です。

8. **`/help` の動的生成と権限バッジ** — コマンド一覧をハードコードせず `tree.walk_commands()` から生成し、権限が足りないコマンドも**隠さず** 🔒 バッジを付けて見せる。「この bot で何ができるか」を全員に伝えるうえで正しい判断です。

---

*本レポートの指摘はすべて `/tmp` に展開したソースを直接確認して作成しました。行番号は 2026-08-20 時点のものです。P0-8 のみ PostgreSQL 環境が無いため未検証で、検証手順を本文に記載しています。*
