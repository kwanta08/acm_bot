# 運用マニュアル

鳥人間サークル 統合運営 Discord Bot の日常運用ガイドです。
全コマンド・権限・自動ジョブ・トラブル対応をまとめています。

> **導入するサークルの方へ**: まず [取扱説明書 GUIDE.md](GUIDE.md) をご覧ください。
> 導入手順・場面別の使い方・困ったときの対処を、コマンドの網羅ではなく
> 「やりたいこと」から引ける形でまとめています。
> 本書は網羅的なリファレンスと、Bot をホストする運営者向けの内容です。

---

## 1. 権限レベル（仕様 9）

| レベル | 対象 | 判定 |
|---|---|---|
| L1 | 一般メンバー | 既定（誰でも） |
| L2 | 班長 | `LEADER_ROLE_IDS` のロールを持つ |
| L3 | 幹部 | `EXEC_ROLE_ID` のロールを持つ |
| L4 | Bot 管理者 | `ADMIN_ROLE_ID` のロール、またはサーバー管理者権限/オーナー |

上位レベルは下位の権限をすべて含みます（L4 は L3/L2/L1 を内包）。

L1〜L4 は内部表記です。Discord 上のメッセージには「一般メンバー / 班長 / 幹部 /
Bot管理者」というラベルで出ます（`utils/permissions.LEVEL_LABELS`）。
権限が足りないときは、そのサーバーで実行できるロールを依頼先として併記します。

---

## 2. コマンド一覧

### Core
| コマンド | 権限 | 説明 |
|---|---|---|
| `/ping` | L1 | 応答確認 |
| `/health` | L1 | Bot・各連携サービスの状態表示 |
| `/help [command]` | L1 | コマンド一覧をカテゴリから探す。`command:` で個別の説明・引数・必要権限 |
| `/setup-status` | L1 | 初期設定（タスク通知/ログチャンネル・管理者ロール・班長ロール・班・桁・大会日）の未完了項目を表示。新入生オンボーディングが ON のときは案内チャンネルも検査する |

### Settings（サーバー設定）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/setup` | L4 | 初期設定ウィザード（対話形式）。班の一括作成、サークル名、**新入生オンボーディングの ON/OFF**（既定 OFF）も行う |
| `/settings_list` | L4 | 設定値の一覧 |
| `/settings_get key:` | L4 | 設定値の取得 |
| `/settings_set key: value:` | L4 | 設定値の保存（`COMPETITION_DATE` `DATA_RETENTION_DAYS` など） |
| `/settings_delete key:` | L4 | 設定値の削除 |
| `/set_channel` | L4 | 通知チャンネルの設定 |
| `/set_role` | L4 | ロール（幹部・管理者・班長）の設定。`action:add` / `action:remove` で班長ロールを1つだけ足す / 外せる（重複は保存時に除去） |
| `/set_common` | L4 | 共通設定 |

### Data（エクスポート・削除）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/data export` | L4 または Manage Server | このサーバーの主要14テーブルを ZIP（CSV 群）で受け取る。サーバーIDと認証情報は含まれない |
| `/data delete` | L4 または Manage Server | データ削除を申告する。確認のためサーバー名の入力が必要。最後のバックアップが添付される |
| `/data delete-cancel` | L4 または Manage Server | 予約済みの削除を取り消す |

### Season（年度替わり）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/season list` | L1 | 年度の一覧と現役／卒業の人数 |
| `/season new name:` | L4 | 現在の年度を終了して新しい年度を開始する |
| `/season rollover name:` | L4 | 年度切り替えウィザード。卒業者の仕分け・班長フラグの全リセット・年度スナップショットの添付 |

### Schedule（日程調整・出欠）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/schedule create` | L2 | 日程調整を作成（候補日時は `;` 区切り） |
| `/schedule list` | L1 | 開催中一覧 |
| `/schedule status <id>` | L1 | 投票の詳細表示 |
| `/schedule close <id>` | L2 | 手動締切 |
| `/schedule remind <id>` | L2 | 未回答者へ再通知 |
| `/schedule edit-deadline <id>` | L2 | 開催中の日程調整の締切を変更 |
| `/schedule list-closed` | L1 | 締切済みの一覧 |
| `/schedule delete <id>` | L3 | 投票を削除（投票メッセージも削除し締切扱いにする。**票データは残る**） |
| `/schedule restore <id>` | L3 | 削除した投票を戻す（締切済みとして戻り、自動の催促・自動締切は走らない） |
| `/schedule confirm <id> <option>` | L2 | 投票の結果として確定した日程を登録し、対象ロールへ告知する |
| `/schedule unconfirm <id>` | L2 | 確定日程を取り消す（取り消しも告知する） |
| `/schedule emoji set` | L4 | 出欠リアクションにサーバーのカスタム絵文字を設定（ステータス選択 + 絵文字名のオートコンプリート） |
| `/schedule emoji show` | L4 | 現在の絵文字設定を表示 |
| `/schedule emoji reset` | L4 | 絵文字設定を既定（✅❓❌）に戻す（ステータス指定可・省略で全部） |

**投票方法**: 既定では全候補が1つの**投票ボード**にまとまって投稿されます。
候補は下部のボタンとして横に並び（Discord モバイルは Embed の inline field を
縦積みにするため、横並びはボタン行で実現しています）、本文に候補ごとの
人数集計が1行ずつ出ます。名前の一覧は候補ボタンを押した詳細と締切後の
集計サマリーで確認できます。回答は候補ボタン → 自分にだけ見えるステータス選択
（✅参加 / ❓未定 / ❌不参加 / 回答を取り消す）。
1候補につき1状態のみ。選び直すと前の状態は上書きされます。
候補が 26 件以上ある場合はボードが複数メッセージに分かれます。

サーバー別設定 `SCHEDULE_UI_STYLE`（`/settings_set` で変更。
`buttons` = 投票ボード（既定） / `reaction` = 従来の
「候補日ごとに1メッセージ＋リアクション」）で方式を選べます。
変更は**この後に作成する日程調整から**適用され、投稿済みの投票は
作成時の方式のまま動き続けます。

**リアクション絵文字のカスタマイズ**: 管理者が `/schedule emoji set` で
サーバー固有のカスタム絵文字（アニメーション絵文字も可）に変更できます。
設定はサーバーごとに保存され、**この後に作成する日程調整から**適用されます
（ボタンの絵文字・リアクションのどちらにも使われます。
投稿済みの投票メッセージは変わりません）。
未設定のステータスは既定絵文字のまま。設定した絵文字がサーバーから
削除されていた場合は自動で既定絵文字にフォールバックし、ログに記録されます。

### Tasks（タスク・Todoist）
**タスクの正本は Todoist だけです。** bot はローカル DB にタスクを持たず、
`/task` の各コマンドは Todoist API を直接呼びます。`<id>` は Todoist の
タスク ID で、オートコンプリートから選べます（手で写す必要はありません）。

**Todoist 未設定のサーバーでは `/task` は使えません。** 「管理者が
`/todoist-setup` で登録してください」と案内して終わります。

| コマンド | 権限 | 説明 |
|---|---|---|
| `/task add` | L1 | タスク作成（Todoist へ登録） |
| `/task list` | L1 | 未完了タスクの一覧 |
| `/task done <id>` | L1 | 完了（Todoist 側で close） |
| `/task delete <id>` | L2 | 削除（Todoist 側でも削除） |
| `/task priority <id> <1-4>` | L1 | 優先度変更 |
| `/task overdue` | L1 | 期限超過一覧 |
| `/task team <班>` | L1 | 班に紐付いた Todoist セクションのタスク一覧 |
| `/task sections` | L2 | Todoist のセクション一覧と班との紐付け状況を表示 |
| `/task link-section <班> <section_id>` | L3 | Todoist セクションを班に紐付け |
| `/task unlink-section <section_id>` | L3 | セクションの紐付けを解除 |
| `/task unlink-team-sections <班>` | L3 | 指定した班に紐付いたセクションをまとめて解除 |
| `/task push` | L2 | セクション別タスクを各班チャンネルへ手動プッシュ |
| `/task sync` | L4 | Todoist 同期・ラベル整備 |
| `/today task <タスク名>` | L1 | 完全一致で「今日やること」ラベル付与 |
| `/today id <Todoist ID>` | L1 | 同名タスク複数時、ID 指定で確定 |

Todoist 連携はサーバーごとの登録制です。未登録のサーバーでは Todoist 関連
コマンドは「管理者が `/todoist-setup` で登録してください」と案内します。

**担当者の割り当てはありません。** Todoist には「どの Discord ユーザーが
担当か」という概念がないため、`/task assign` と担当者への DM 通知、
`/me` のタスク欄は廃止しました（スキーマ v22）。班への割り当ては
「Todoist セクション ↔ 班」の紐付けで行います。

### Todoist 管理（トークン登録）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/todoist-setup` | L4 | このサーバーの Todoist API トークンを登録（引数なし。表示されるボタンからフォーム（Modal）を開いて入力。上書き可） |
| `/todoist-status` | L4 | 連携状態を表示（登録有無・プロジェクト・ラベル名・復号可否。トークン本体は表示されません） |
| `/todoist-remove` | L4 | このサーバーの Todoist 設定を削除 |

- トークンはコマンドの引数では受け取りません（オプション値は Discord の
  履歴に残るため）。`/todoist-setup` を実行すると管理者にだけ見える
  メッセージとボタンが表示され、ボタンから開くフォーム（Modal）の
  入力欄で受け取ります。Modal の入力値はチャンネル履歴に残りません。
- トークンは Fernet で暗号化して DB（`todoist_configs` テーブル）に保存され、
  平文では保存されません。暗号鍵 `ENCRYPTION_KEY` は `.env` のみに保持します。
- 応答はすべて実行者のみに表示されます（ephemeral）。

### データのエクスポート（CSV）

| 方法 | 権限 | 説明 |
|---|---|---|
| `/report export-tasks` | L2 | タスク一覧を CSV ファイルとして Discord に投稿 |
| Web ダッシュボードの「CSV をダウンロード」 | L1 | 表示中の表をそのまま CSV で保存（7テーブルすべて対応） |

Google Sheets へのエクスポート連携（旧 `/set_sheet` `/sheet_sync`）は
**撤去しました**。CSV は Excel / Google スプレッドシートのどちらでも
そのまま開けます（BOM 付き UTF-8）。

ダウンロードは監査ログに `dashboard.export` として記録されます。

### Progress（機体進捗管理）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/progress view` | L1 | 進捗をドリルダウン表示（機体 → パーツ → 部品 → …。各階層でテキストの進捗バー付き） |
| `/progress history [node] [days]` | L1 | 進捗の推移（テキストのスパークライン）と直近7日の伸び。20分ごとの同期が1日1件記録したものを読む |
| `/progress add` | L2 | ノードを追加（`parent` 省略で機体＝最上位。担当・状態・進捗率も同時指定可） |
| `/progress edit` | L2 | 名前・担当・状態・進捗率・親を変更（進捗率を入れるとソースは手入力に戻る） |
| `/progress remove` | L2 | ノードを配下ごと削除 |
| `/progress spar-link` | L2 | 桁名と目標層数を進捗ノードへ紐付け（`/layer` の記録から進捗率を自動計算） |
| `/progress setup` | サーバー管理 または L2 | Todoist プロジェクトを進捗ツリーに紐付けるウィザード（プロジェクト選択 → 紐付け先選択 → 通知先選択。手入力・再起動なし）。導入直後で班長ロールが未設定でも、Discord の「サーバー管理」権限があれば実行できる |
| `/progress sync` | L4 | Todoist 同期＋桁巻き反映＋再集計を即時実行（通常は20分ごとに自動実行） |

#### 重量管理（機体重量はグラム固定）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/weight set node: actual: [target:]` | L2 | ノードの実測重量（と目標重量）を記録 |
| `/weight view [node]` | L1 | 集計重量・目標との差・実測入力率を表示（省略時は機体全体） |
| `/weight top` | L1 | 目標超過の大きい順に並べる（減量の着手先） |

集計規則は「そのノードに実測が入っていればそれを採用、無ければ子の合計を積み上げる」。
未計測は 0g ではなく「未計測」として扱い、実測入力率で確度を示します。

#### 大会からの逆算
| コマンド | 権限 | 説明 |
|---|---|---|
| `/milestone add node: name: due:` | L2 | ノードに期限（マイルストーン）を設定 |
| `/milestone remove node: name:` | L2 | マイルストーンを削除 |
| `/milestone list` | L1 | 登録済みのマイルストーンを期限順に表示 |
| `/countdown` | L1 | 大会までの残り日数と、マイルストーンごとの必要ペース・実績ペース・遅延判定 |

大会日はギルド別設定 `COMPETITION_DATE`（`YYYY-MM-DD`）に登録します（既定値なし）。
遅れているマイルストーンがある週は、月曜 8:30 に自動通知されます（無い週は通知しません）。

機体製作の進捗を **DB（`progress_nodes` テーブル）を正本** として管理し、
Discord からドリルダウンで確認できる機能です。深さに制限はなく、
親子関係だけで機体 → パーツ → 部品 → サブタスク…と自由に階層化できます。

**Google スプレッドシートもサービスアカウントも不要**です
（`GOOGLE_CREDENTIALS_PATH` は `/set_sheet` `/sheet_sync` のエクスポート連携と、
旧シートからの移行スクリプトでのみ使います）。
進捗データは**サーバーごとに完全に独立**します。

ノードを指定する引数（`node` / `parent` / `keta`）はオートコンプリート付きで、
ツリーが字下げ表示されるため ID を覚える必要はありません。

**ノードの持つ項目**

| 項目 | 内容 | 更新する主体 |
|---|---|---|
| 名前 / 担当者 / 状態 | 状態は「未着手/製作中/完了」推奨 | 人間（`/progress add` `/progress edit`） |
| 進捗率 | 葉ノードのみ有効（`0.5` / `50%` どちらの書式でも可） | 人間 or 同期処理 |
| 集計進捗率 | 葉＝進捗率、親＝子の平均を再帰計算（表示時に毎回計算） | bot（自動） |
| ソース | `manual` / `todoist` / `spar_winding` | bot |

**ソースによる保護** — `source=manual` のノードは同期処理が**絶対に上書きしません**。
手動管理・Todoist 管理・桁巻き管理のパーツを同じツリーに混在できます。
循環参照や親 ID の誤りがあると、該当ノードをスキップして `#bot-log` に通知します
（クラッシュ・無限ループはしません）。

**桁巻き連携** — `/layer` の記録がそのまま進捗になります

```
1. /layer keta-add で桁名を登録（済みならスキップ）
2. /progress add で桁に対応する葉ノードを作成
3. /progress spar-link keta:<桁名> node:<ノード> target_layers:<目標層数>
4. 以降、/layer end で積層を記録するたびに
   進捗率 = 完了層数 ÷ 目標層数 が自動更新される（ソース=spar_winding）
   ※ 同じ層を巻き直しても1層として数えます
```

**Todoist 連携（任意）** — メンバーの作業は Todoist の操作だけで完結します

```
1. /todoist-setup で Todoist トークンを登録（済みならスキップ）
2. 班長（またはサーバー管理権限を持つ人）が /progress setup を実行:
   ① Todoist プロジェクトをメニューから選択（ID の手入力なし）
   ② 紐付け先ノード（機体/パーツ or 新規パーツとして追加）を選択
   ③ タスク通知の送信先（専用チャンネルをピッカーで指定 or 共通）を選択
3. 以降20分ごとに自動同期:
   - プロジェクトのタスク・サブタスク階層が td_<TodoistタスクID> のノードとして
     対応ノードの配下へ自動追加される
   - Todoist でタスクを完了チェック → 進捗率 100%・状態=完了 に自動更新
4. 毎朝 08:30 に各プロジェクトの期限タスク（7日以内・超過）を、
   ②で選んだチャンネルへ通知
   - サブタスクには「親タスク」行（例: 主翼 > 第2リブ > 桁受け加工）が付き、
     どの部品・工程のタスクかが通知だけで分かる。直近の親が Todoist の
     タスクなら、その親タスクへのリンクになる。トップレベルのタスクには付かない
```

通知先は「紐付け時に選んだチャンネル → `PROGRESS_DEFAULT_CHANNEL_ID`（settings）→
ギルドのタスクチャンネル」の順に解決されます。共通の送信先を変えるには
`/settings_set PROGRESS_DEFAULT_CHANNEL_ID <チャンネルID>` を実行してください。

**旧・中央スプレッドシートからの移行**

```bash
# dry-run（既定。DB を変更せず件数と警告を確認）
venv/bin/python scripts/migrate_progress_sheet_to_db.py     --guild-id <サーバーID> --spreadsheet-id <シートID>

# 実行
venv/bin/python scripts/migrate_progress_sheet_to_db.py     --guild-id <サーバーID> --spreadsheet-id <シートID> --apply
```

- 移行には `gspread` / `google-auth` と `GOOGLE_CREDENTIALS_PATH` が必要です
  （シートを読むだけで書き込みはしません）
- 旧構成で**1枚のシートを複数サーバーで共有していた場合**は、サーバーごとに
  実行してください。移行後の進捗ツリーはサーバーごとに独立して更新されます
- 何度実行しても行は重複しません（node_id をキーに upsert）。
  やり直したい場合は `--replace` を付けます
- 進捗バーは Embed 内のテキスト（`████░░░░`）です。画像を生成しないため
  CJK フォントの導入は不要になりました。より詳細なグラフは
  Web ダッシュボードの「機体進捗」タブで表示されます（8章）

### Members（班・メンバー）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/member register <user> [班]` | L2 | メンバー登録 |
| `/member profile [user]` | L1 | プロフィール表示（省略時は自分） |
| `/member assign-team <user> <班>` | L2 | 主所属班を設定 |
| `/member assign-sub-team <user> <班>` | L2 | 副所属班を追加・削除 |
| `/member setup <user>` | L3 | 主所属班・副所属班・班長を一括設定 |
| `/member set-channel <班> <channel>` | L3 | 班の通知先チャンネルを設定（タスクの班別通知に使用） |
| `/member set-leader <user> <bool>` | L3 | 班長フラグ設定 |
| `/member support <班>` | L2 | 支援候補検索（班横断作業向け）。既定は現役のみ。`include_alumni:True` で卒業者も含む |

班の選択肢は、そのギルドの DB（teams テーブル）から入力途中の文字列で
絞り込んで表示されます（autocomplete）。班がまだ登録されていないギルドでは、
先に管理者が `/team-add` で登録してください。

### Teams（班マスタ管理）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/team-add slug:<識別子> name:<表示名>` | L4 | 班を追加。識別子は半角英小文字・数字・`-`・`_`（32文字以内）。同じ識別子の再登録で表示名更新・再有効化 |
| `/team-remove slug:<識別子> [confirm]` | L4 | 班を無効化（論理削除）。主所属メンバーがいる場合は `confirm:True` が必要 |
| `/team-list` | L4 | 班の一覧（有効/無効・所属人数・ロール・通知ch） |
| `/team-role team:<識別子> role:<ロール> [role_type]` | L4 | 班と Discord ロールを紐付け。`role_type` は `primary`（主所属、既定）・`secondary`（副所属）・`leader`（班長。`/team-list` の表示用で自動付与も権限付与もしない） |

**新しいサーバーを追加した場合**: 班は空の状態で始まります。
管理者（L4: Bot管理者ロール・サーバーオーナー・Discord管理者権限のいずれか）が
`/setup` の「班を一括作成」（班と対応ロールをまとめて作成）か
`/team-add` で登録してください。
個別の班ロールは Discord 側で作成したロールを `/team-role` で紐付けます。

`role_type:leader` で設定する班長ロールは `/team-list` に表示するための情報です。
**班長の権限（L2）は settings の `LEADER_ROLE_IDS` が唯一の根拠**なので、
権限を与えるときは `/setup` の「班長ロール」、または
`/set_role role_type:リーダー`（`action:remove` で1つだけ外せる）を使ってください。

使用例:
```
/team-add slug:wing name:翼
/team-role team:wing role:@翼班 role_type:primary
/team-role team:wing role:@翼班支援 role_type:secondary
/team-role team:wing role:@翼班長 role_type:leader
```

### Reports（集計・出力）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/report weekly` | L2 | 週次サマリー |
| `/report export-tasks` | L2 | タスク一覧 CSV 出力 |
| `/report weekly [public]` | L2 | 週次サマリー。`public:true` でチャンネルへ公開投稿（既定は自分にだけ表示） |
| `/report notifications [limit]` | L3 | bot が送った通知の記録（`reminders_log`）を表示 |
| `/report changes [limit] [actor]` | L3 | 設定・マスタ変更の操作ログ（`audit_log`）を表示。実行者は表示名に解決 |
| `/report attendance-rate` | L2 | 投票ごとの出欠率一覧 |
| `/report member-attendance [months]` | L2 | **メンバー別**の回答率・ok率・連続未回答数（回答率の低い順・ephemeral 固定） |

### Me（個人サマリー）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/me [user]` | L1 | 自分の未回答の投票・今月の積層・担当中の進捗ノードをまとめて表示（ephemeral）。`user` の指定は L2 以上 |

新しいテーブルは使いません（既存の集計を合成しているだけ）。
タスクは出しません（正本の Todoist に Discord ユーザー単位の担当が無いため）。
担当中の進捗ノードは `progress_nodes.assignee`（自由記述の名前）を、
Discord の表示名と `members.display_name` の両方で照合します。

---

### Inventory（資材・消耗品の在庫）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/stock list` | L1 | 在庫一覧（閾値を割っている品目を強調） |
| `/stock use <品目> <数量> [用途]` | L1 | 消費の記録。閾値を割ったらその場で1回告知 |
| `/stock add <品目> <数量> [単位] [メモ]` | L2 | 品目の登録・入庫 |
| `/stock set-threshold <品目> <閾値>` | L2 | 発注アラートの閾値（負の値で解除。`0` は「尽きたら知らせる」） |
| `/stock remove <品目>` | L2 | 品目の無効化（増減の履歴は残る） |

**品目の初期値は入っていません**（何を管理するかはサークルごとに違うため）。
`/stock add` で登録すると、以降は候補から選べます。

閾値を割ると (1) その場で1回、告知チャンネルへ通知し、
(2) 割れたままなら毎朝 08:30 の通知にも含まれます。
**閾値未設定の品目は判定しません**（在庫0でも通知は飛びません）。

---

### LayerTracking（桁巻き積層記録）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/layer start <桁名> <層番号>` | L1 | 積層開始を記録 |
| `/layer end` | L1 | 進行中セッションを終了し DB に記録 |
| `/layer cancel` | L1 | 進行中セッションを**記録を残さずに**取り消す（打ち間違え・押し忘れ用） |
| `/layer status` | L1 | 進行中の作業一覧 |
| `/layer stats [桁名] [期間]` | L1 | 積層記録を桁別・作業者別に集計（層数・時間・1層あたり平均・最終作業日） |
| `/layer keta-add <桁名>` | L2 | 桁名を登録 |
| `/layer keta-remove <桁名>` | L2 | 桁名を無効化 |
| `/layer keta-list` | L1 | 登録済みの桁名一覧 |

桁名はセレクトメニューから選択（タイプミス防止）。桁の追加・変更は
`/layer keta-add` / `/layer keta-remove` で行います。

**押し忘れの検知**（5分ごとの定期処理）:

| ギルド別設定 | 既定 | 動き |
|---|---|---|
| `LAYER_SESSION_ALERT_MINUTES` | 240 | 経過がこの分数を超えたら本人へ DM で1回だけ催促 |
| `LAYER_SESSION_AUTO_CANCEL_MINUTES` | 720 | 経過がこの分数を超えたら自動で `/layer cancel` 相当（記録は残らない）し、本人へ DM |

どちらも `/settings_set` で変更でき、**`0` を設定するとその機能だけ無効**になります。
`/layer end` を押し忘れると 1200 分といった作業時間が記録され、
完了層数が増えて `/progress` の進捗率まで水増しされるため、既定で有効にしています。

---

## 3. 典型的な運用フロー

### 定例ミーティングの出欠を取る
```
/schedule create
  title: 第N回定例会
  options: 2026-07-03 18:30
  deadline: 2026-07-03 12:00
  target_role: @全員    （任意。指定するとそのロールが未回答者通知の対象になる。未指定なら名簿の現役メンバーが対象）
  channel: #出欠管理     （任意。未指定なら DEFAULT_SCHEDULE_CHANNEL_ID）
```
→ 締切1時間前に未回答者へ自動 DM。締切を過ぎると自動でクローズし結果要約を投稿。

### タスクを登録して追いかける
```
/task add title:翼リブ加工 due:2026-07-05 18:00 team:翼 assignee:@担当 priority:3
```
→ 毎朝 08:30 に「7日以内の期限タスク」を通知。
→ 毎晩 21:00 に「期限超過タスク」を警告。
→ 当日やるものは `/today task:翼リブ加工` でラベル付与し、毎朝 08:30 に一覧通知。

### タスク通知を班ごとのチャンネルに振り分ける
朝（08:30 の7日以内期限）と夜（21:00 の期限超過）のタスク通知は、
タスクの「班（team）」ごとに各班のチャンネルへ自動で振り分けられます。
使うには、先に各班の通知先チャンネルを登録します。

```
/member set-channel team:翼 channel:#翼班
/member set-channel team:CFRP channel:#cfrp班
…（各班分を設定）
```

**振り分けのルール**
- 班にチャンネルが設定済み → その班のタスクはそのチャンネルに届く（タイトル末尾に「｜○○班」が付く）。
- 班チャンネルが未設定、または班未割当（team なし）のタスク → 従来どおり共通チャンネル（`DEFAULT_TASK_CHANNEL_ID`）にまとめて届く。
- `set-channel` を一度も使わなければ、従来どおり全部が共通チャンネルに届きます（既存運用に影響なし）。

> メモ: 日程調整（`/schedule`）の通知はこの振り分けの対象外です（従来どおり `channel` 指定または日程用チャンネル）。

### Todoist セクションを班ごとに管理して通知する
Todoist 側の「セクション」を班と紐付けると、そのセクションのタスクを
対応する班の Discord チャンネルへまとめて通知できます。

**紐付けの手順**
```
1. /task sections            ← Todoist のセクション一覧と section_id を確認
2. /task link-section team:翼 section_id:1234567890   ← 班とセクションを紐付け（L3）
   …（各班分を紐付け）
```
- `section_id` は `/task sections` の一覧に表示されます。班との紐付けは幹部（L3）のみ実行できます。
- 一度紐付けると、以降 `/task add` で同じ班を指定して作ったタスクは
  Todoist 側でも自動でそのセクションに入ります。

**通知のタイミングと範囲**
- 毎朝 08:30 の定期通知に含まれます（自動）。
- `/task push` でいつでも手動プッシュできます（L2）。
- 通知対象は **期限が 7 日以内 + 期限超過** のタスク。期限なし・8 日以降先のものは対象外。
- 各タスクは期限順に並び、超過したものには「（超過）」と付きます。

**振り分けのルール**
- 班にチャンネルが設定済（`/member set-channel`）→ その班のチャンネルに届きます。
- 班チャンネルが未設定 → 共通チャンネル（`DEFAULT_TASK_CHANNEL_ID`）に班名付きで届きます。

### 桁巻き作業を記録する
```
/layer start keta:主翼前桁 layer_num:3   ← 作業開始時
（積層作業）
/layer end                               ← 作業終了時（自動で作業時間を計算しシートへ追記）
```
→ 桁名・層番号・作業者・開始・終了・作業時間(分) が DB（layer_records テーブル）に
  1行記録される。

### 班をまたいだ支援者を探す
```
/member support team:電装                        ← 電装班の現役メンバー
/member support team:電装 include_alumni:True    ← 卒業生も含めて探す
```

---

## 4. 自動ジョブ（仕様 11.5.1）

| ジョブ | タイミング | 内容 |
|---|---|---|
| 締切前催促 | 締切1時間前（5分間隔で判定） | 未回答者へ DM、不可ならチャンネルでメンション |
| 自動締切 | 5分ごと | 締切超過の投票を終了し結果要約を投稿 |
| 確定日程リマインド（前日） | 毎日 20:00 | `/schedule confirm` で確定した翌日の予定を投稿チャンネルへ通知 |
| 確定日程リマインド（当日） | 毎日 08:30 | 同じく当日の予定を通知。確定が無い日は何も送らない |
| 7日以内期限通知 | 毎日 08:30 | 今日〜7日以内が期限の未完了タスク |
| Todoist セクション別通知 | 毎日 08:30 | 班と紐付けたセクションのタスク（期限7日以内+超過）を各班チャンネルへ |
| 今日やること通知 | 毎日 08:30 | 「今日やること」ラベル付きタスク |
| 遅延マイルストーン通知 | 毎週月曜 08:30 | 期限に遅れている節目のみ。**遅れが無い週は送らない**（ADR 0023） |
| データ削除の実行 | 毎日 04:00 | 退出後の保持期間満了、または `/data delete` を申告したサーバーのデータを全テーブルから削除。結果は `#bot-log` へ（退出済みサーバーは通知先が解決できないため送られない） |
| 超過通知 | 毎日 21:00 | 期限切れの未完了タスク |
| 進捗同期 | 20分ごと | 全サーバーの Todoist 取り込み・桁巻き反映・進捗の再集計（`/progress sync` で即時実行） |
| 進捗プロジェクト通知 | 毎日 08:30 | 紐付け済みプロジェクトの期限タスク（7日以内・超過）。サブタスクには親タスクのパンくず（例: 主翼 > 第2リブ）を添える |

通知失敗時（仕様 11.5.2）: DM 失敗→チャンネル通知へフォールバック、
API 障害→`#bot-log` に記録、送信履歴を保存し多重送信を防止。

---

## 5. エラーメッセージ（仕様 14.2）

| コード | 意味 | 対処 |
|---|---|---|
| `INVALID_DATETIME` | 日時形式が不正 | `YYYY-MM-DD HH:MM`（例 `2026-07-03 19:00`）で再入力 |
| `ROLE_NOT_FOUND` | 対象ロールが不正 | ロールを指定し直す |
| `TODOIST_API_FAILED` | Todoist API 失敗 | 時間をおいて再試行。継続時は `/health` で状態確認 |
| `DM_FORBIDDEN` | DM 送信不可 | 対象者の DM 設定。自動でチャンネル通知へ切替 |
| `MESSAGE_NOT_FOUND` | 投票メッセージが削除済み | 対象の投票を作り直す |
| `PERMISSION_DENIED` | 権限不足 | 必要な権限レベルを持つ人に依頼 |

---

## 6. 日常の保守

- **状態確認**: `/health` で DB（PostgreSQL / SQLite）・Todoist（ギルド別）・暗号鍵の状態と遅延を確認。
- **ログ**: さくらのVPS（systemd常駐）では `journalctl -u club-bot -f`、ファイルは `logs/bot.log`。
**週次ダイジェストの自動投稿**（既定 OFF）:

| ギルド別設定 | 既定 | 動き |
|---|---|---|
| `WEEKLY_DIGEST_ENABLED` | `0`（OFF） | `1` にすると、`/report weekly` と同じ内容を朝 08:30 に公開チャンネルへ投稿 |
| `WEEKLY_DIGEST_WEEKDAY` | `0`（月曜） | 0=月 〜 6=日。範囲外の値は既定に戻ります |

`/settings_set` で変更します。投稿先は「お知らせチャンネル →（無ければ）
進捗チャンネル → タスク通知チャンネル」の順です。
集計対象が1件も無い週は**何も送りません**（0件のダイジェストは送らない）。

遅延マイルストーンの警告（遅れがある週だけ届く）とは**別の通知**です。
ダイジェストに「遅延はありません」の類は入れていません。

- **監査**: `/report notifications` で直近の通知履歴と失敗理由を、`/report changes` で設定・マスタ変更の操作ログを確認。
- **日次バックアップ**: 本番（PostgreSQL）は `pg_dump -Fc` を日次で取得（ADR 0006。
  SQLite は開発・テスト用なので、開発環境では `data/club.db` をコピー）。週1で `/report export-tasks`。
- **再起動前のバックアップ**: bot を再起動する前にも必ず `pg_dump -Fc` を取る。 起動時にスキーマ
  マイグレーションが自動適用され、**down（巻き戻し）は用意していない**:

  ```bash
  pg_dump -Fc "$DATABASE_URL" -f "backup-$(date +%Y%m%d-%H%M).dump"
  sudo systemctl restart club-bot.service
  ```

  暗号鍵 `ENCRYPTION_KEY` は DB とは**別の場所**に保管する（次章）。
- **桁の増減**: `/layer keta-add` / `/layer keta-remove` で管理（DB に保存。再起動不要）。
- **班の増減**: `/team-add` `/team-remove` で管理
  （DB に保存。再起動不要）。班ロールの紐付けは `/team-role`。

---

## 7. 暗号鍵（ENCRYPTION_KEY）の管理

### 7.1 影響範囲 — 鍵は1本で全サーバーを保護している

**本Botは単一の `ENCRYPTION_KEY` で、参加している全サーバー（全テナント）の
Todoist API トークンを暗号化しています。** サーバーごとに鍵は分かれていません
（`utils/crypto.py` は環境変数から読んだ鍵1本で Fernet を構成し、
プロセス内にキャッシュします）。

この構成の帰結を先に押さえてください。

| 事象 | 影響範囲 |
|---|---|
| 鍵を**紛失**した | **全サーバー**の Todoist トークンが復号不能になり、全サーバーで `/todoist-setup` の再登録が必要（1サーバーだけの問題では済まない） |
| 鍵が**漏洩**した | DB のバックアップを併せて取得された場合、**全サーバー**の Todoist トークンが平文で復元され、各サークルの Todoist のタスクを読み書きされうる |
| 鍵を**変更**した | 変更前に暗号化されたトークンはすべて復号できなくなる（後述のローテーション手順が必要） |

補足:

- 鍵が未設定・不正でも Bot 自体は起動して動作を継続します。
  トークンの登録・利用だけが安全に拒否されます（起動ログに `ERROR` が出ます）
- 鍵の状態は `/health` で確認できます
- 鍵・平文トークン・暗号文はログにも例外メッセージにも出力しません（`utils/crypto.py`）
- 鍵は DB や `settings` テーブルには保存しません。**環境変数（`.env`）のみ**が正本です

### 7.2 バックアップ方針

1. **DB とは別の場所に保管する。** 鍵と暗号化済み DB を同じバックアップ先に置くと、
   その1箇所が漏れただけで暗号化の意味が失われます
   （例: DB バックアップは VPS のスナップショット、鍵はパスワードマネージャ）
2. **2箇所以上に保管する。** 単一箇所だと紛失＝全サーバーへの波及になります
   （例: パスワードマネージャ + オフラインの紙／暗号化 USB）
3. **リポジトリへ入れない。** `.env` と `.env.*` は `.gitignore` 済み
   （追跡されるのは `.env.example` のみ）。値をコミットしないこと
4. **VPS 上のファイル権限を絞る。** `chmod 600 ~/club-bot/.env`、
   所有者は Bot 実行ユーザーのみ
5. **鍵を運用者間で共有する場合**は、Discord・メール・チャットの本文へ貼らず、
   パスワードマネージャの共有機能を使う
6. **年1回、復旧テストを行う。** バックアップから取り出した鍵で
   `/health` の「暗号鍵（ENCRYPTION_KEY）」が ✅ になることを確認し、
   さらに `/task list` など Todoist を実際に呼ぶコマンドが動くことまで確認する

   > ⚠️ `/health` の ✅ は**鍵の形式が正しいこと**しか保証しません
   > （`crypto.is_encryption_ready()` は Fernet を構成できるかだけを見ます）。
   > 別の有効な鍵に差し替わっていても ✅ になるため、
   > 「既存の暗号文を復号できるか」は Todoist を呼ぶコマンドで確かめること。

鍵の生成:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

手元の鍵が DB の暗号文と一致するかの確認（値は出力しません）:

```bash
python -c "import os;from cryptography.fernet import Fernet;Fernet(os.environ['ENCRYPTION_KEY'].encode());print('key format OK')"
```

### 7.3 ローテーション方針

**いつ実施するか**

| 契機 | 緊急度 |
|---|---|
| 鍵の漏洩・漏洩の疑い（VPS への不正アクセス、`.env` の誤コミット、鍵を貼ったメッセージの流出） | 即時（7.4 へ） |
| 鍵を知る運用者の交代・引退 | 交代後すみやかに |
| 定期ローテーション | 年1回を目安 |

**現状の制約**: 再暗号化スクリプトは未提供です。したがって現在取れる手順は
「鍵を差し替え、各サーバーにトークンの再登録を依頼する」方式になります。

**手順A: 再登録方式（現行の推奨）**

1. 新しい鍵を生成する
2. 各サーバーの管理者へ事前告知する
   （「切替後に `/todoist-setup` の再実行が必要」「切替までの間に
   Todoist 連携が一時的に無効になる」）
3. `.env` の `ENCRYPTION_KEY` を差し替え、Bot を再起動する
   （`utils/crypto.py` は Fernet をプロセス内にキャッシュするため、
   **再起動しないと新しい鍵は反映されません**）
4. 復号不能になった古い暗号文を掃除する。放置しても復号エラーとして
   安全に失敗しますが、再登録を促すため削除を推奨します

   ```sql
   -- 影響: 全サーバーの Todoist 連携が「未登録」状態に戻る
   DELETE FROM todoist_configs;
   ```

5. 各サーバーの管理者に `/todoist-setup` の再実行を依頼する
   （`/health` の「Todoist（このサーバー）」が
   `⚪ 未登録（/todoist-setup）` になるため、各サーバーで状況を確認できる）
6. 旧鍵をバックアップ先から破棄する

**手順B: 再暗号化方式（将来の実装方針）**

無停止で移行する場合は、`cryptography` の `MultiFernet` を用いて
「旧鍵で復号 → 新鍵で暗号化 → `todoist_configs.api_token_encrypted` を UPDATE」
を行うスクリプトを用意します（`MultiFernet` は複数鍵での復号を許容するため、
移行期間中は新旧どちらの暗号文も読めます）。

この方式を採る場合は次を満たすこと。

- 実行前に DB のバックアップを取得する
- `guild_id` 単位で1件ずつ処理し、失敗した行はロールバックして記録する
- dry-run を既定とし、`--apply` で初めて書き込む（`scripts/` の既存スクリプトに合わせる）
- 鍵・平文トークンを標準出力にもログにも出さない

> 未実装です。実装する場合は `scripts/rotate_encryption_key.py` として追加してください。

### 7.4 漏洩時の緊急対応

**鍵のローテーションだけでは不十分です。** 漏洩した鍵で既に復号されたトークンは、
鍵を変えても Todoist 側で有効なままだからです。次の順に実施します。

1. **各サーバーの管理者へ、Todoist 側での API トークンの再発行（失効）を依頼する**
   （最優先。Todoist の設定 → 連携 → API トークンを再発行すると旧トークンは無効になる）
2. `DELETE FROM todoist_configs;` で保存済みの暗号文を削除する
3. 鍵をローテーションする（7.3 手順A の 1〜3）
4. 漏洩経路を塞ぐ（VPS のクレデンシャル更新、誤コミットの場合はリポジトリ履歴の扱いを判断）
5. `/report audit` と `logs/bot.log` で不審な操作の有無を確認する
6. [プライバシーポリシー](PRIVACY.md) に沿って、影響を受けたサーバーへ通知する

### 7.5 チェックリスト

- [ ] `ENCRYPTION_KEY` を DB バックアップとは別の場所に保管している
- [ ] 保管先が2箇所以上ある
- [ ] `.env` のファイル権限が `600` である
- [ ] 鍵をチャット・メール・リポジトリに貼っていない
- [ ] 年1回、バックアップからの復旧テストを実施している
- [ ] 運用者の交代時に鍵をローテーションする運用になっている

---

## 8. ダッシュボード（Web UI）の運用

bot とは**別プロセス**で動く FastAPI アプリ。Discord でログインし、
自分が所属するサーバーのデータだけを表形式で閲覧・編集できる。
セットアップの詳細は [`../dashboard/README.md`](../dashboard/README.md)。

### 8.1 構成

```
インターネット → Caddy(443/80) → uvicorn(127.0.0.1:8000) → PostgreSQL
                                   ↑ bot とは別プロセス（同じ DB を共有）
```

- ダッシュボードは **127.0.0.1 にのみバインド**し、公開は Caddy 経由に限る
- Caddy が Let's Encrypt から証明書を自動取得・自動更新する
- ダッシュボードに **Discord Bot トークンは不要**（OAuth2 のクライアント
  ID / シークレットのみ）

### 8.2 導入

**手順の詳細は [`DASHBOARD_SETUP.md`](DASHBOARD_SETUP.md) にあります。**
Discord Developer Portal の OAuth2 設定から、Caddy での HTTPS 公開、
systemd 常駐、公開前チェックリストまでを順に追える形でまとめています。

概要:

```bash
venv/bin/pip install -r dashboard/requirements.txt   # 依存（bot とは分離）
nano ~/club-bot/dashboard.env                        # 設定（bot の .env とは別）
sudo cp deploy/club-bot-dashboard.service /etc/systemd/system/
sudo systemctl enable --now club-bot-dashboard       # 127.0.0.1:8000 で常駐
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile        # HTTPS 公開（要ドメイン）
```

Docker で動かす場合は `deploy/docker-compose.dashboard.yml`
（Caddy + ダッシュボードのセット）を使う。

**間違いやすい点**: Discord Developer Portal の OAuth2 > Redirects に
`https://<ドメイン>/auth/callback` を登録し、`DASHBOARD_REDIRECT_URI` と
**1文字も違わず一致**させること。

### 8.3 権限

| レベル | 判定 | できること |
|---|---|---|
| L4 | Discord の「サーバー管理」権限 | 表の編集 ＋ サーバー設定の変更 |
| L2 | `members.is_leader`（班長） | 表の編集 |
| L1 | サーバー参加者 | 閲覧のみ |

ロール ID による L3（幹部）判定は Bot トークンなしでは行えないため、
ダッシュボードでは 3 段階で扱う。

### 8.4 安全性の要点

- 表示・編集は**セッションで検証済みの `guild_id`** に固定される。
  他サーバーのデータは URL を書き換えても取得できない（403）
- 参照・編集できるテーブルと列はホワイトリスト方式
  （`repositories/table_repository.py`）。`settings` や
  `todoist_configs` は表グリッドの対象外
- **権限まわりの列は Web から編集できない**。`teams` のロール ID 3 列と
  `members.is_leader` は読み取り専用（`editable=False`）。
  `member_role_id` / `secondary_role_id` は `cogs/members._sync_roles()` がそのまま
  `add_roles()` に渡すため、書き換えられると bot の権限で任意のロールを
  付けさせる経路になる。`members.is_leader` はダッシュボードの L2 判定そのもの。
  変更は Discord の `/team-role`（L4。`role_type` は primary / secondary / leader）と
  `/member set-leader`（L3 以上）から行う
- ロール ID 設定の**実値は L4 にだけ返す**。参加者には「（設定済み）」と表示される
- 設定変更で触れるキーもホワイトリスト（トークン類は対象外）
- 編集は `audit_log` に必ず記録される（`/report audit` で確認できる）
- API ドキュメント（`/docs`・`/openapi.json`）は配信しない

### 8.5 設定変更の反映

ダッシュボードから `settings` を更新すると、PostgreSQL の
`NOTIFY clubbot_settings` により bot プロセスのギルド別設定キャッシュが
無効化される（再起動不要）。**SQLite 構成では伝播しない**ため、
ダッシュボードを併用する本番は PostgreSQL を使うこと。

### 8.6 監視・トラブル対応

- 死活: `curl -s https://<ドメイン>/healthz` → `{"status":"ok", "pool":{...}}`
  `pool.in_use` が `max_size` に張り付く場合は
  `DASHBOARD_DB_POOL_MAX_SIZE` を上げる
- ログの見方（D2-5 でアプリログを追加）:
  - **アプリの動作ログ（INFO 以上）**: `/home/ubuntu/club-bot/logs/dashboard.log`。
    `utils/logger.py` の RotatingFileHandler（5MB × 5世代）が書く。
    出力先はサービスの `Environment=LOG_DIR=` で指定（相対 `logs/` は
    `ProtectHome=read-only` で書けず再起動ループになるため必ず絶対パス）。
    レベルは `DASHBOARD_LOG_LEVEL`（例: `DEBUG` / `WARNING`）で上書きできる
  - **プロセスの標準出力（uvicorn のアクセスログ等）**: 同 `dashboard.out` /
    `dashboard.err`（systemd の append。dashboard.log とはファイルを分けてある —
    同じファイルに向けるとローテーションと追記 fd が競合する）
  - ほか: `journalctl -u club-bot-dashboard -f` / `/var/log/caddy/dashboard.log`
- 接続数の目安: bot(10) + ダッシュボード(10) + LISTEN 用(1) で、
  PostgreSQL の `max_connections`（既定 100）に十分収まる

| 症状 | 確認すること |
|---|---|
| ログインが 503 | `DASHBOARD_SECRET_KEY` / `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` / `DASHBOARD_REDIRECT_URI` が設定されているか |
| ログイン後にサーバーが1つも出ない | bot がそのサーバーに参加しているか（`guilds` 台帳に載っているか） |
| ログインしてもすぐログアウトされる | HTTPS 配信になっているか（`DASHBOARD_SECURE_COOKIE=0` はローカル専用） |
| 403「このサーバーのデータにはアクセスできません」 | 別サーバーの URL を開いていないか。所属し直した場合は再ログイン |
| 編集できない | 権限が L1（閲覧のみ）。サーバー管理権限か班長フラグが必要 |

---

## 9. よくある質問

**Q. Todoist を使わない運用はできますか？**
A. はい。Todoist は `/todoist-setup` で登録しなければ自動的に無効です。
その機能だけ OFF になり、ほかは通常どおり動きます。`/health` で状態を確認できます。

**Q. Google Sheets 連携はどうなりましたか？**
A. 常時同期は廃止されました。記録の正本は SQLite/PostgreSQL です
（任意のエクスポート連携 `/set_sheet` `/sheet_sync` は利用できます）。
旧 Sheets のデータは `scripts/migrate_sheets_to_db.py` で DB に取り込めます
（[`archive/NOCODB.md`](archive/NOCODB.md) 6章）。

**Q. Bot を再起動すると進行中の投票や桁巻き作業は消えますか？**
A. 消えません。投票ボードのボタンは永続 View（DynamicItem）なので再起動後も
押せます。リアクション式の投票も raw イベントで再処理されます。
桁巻きセッションは SQLite から復元されるため、再起動後も `/layer end` を
実行できます。

**Q. 桁名を間違えて開始してしまいました。**
A. 一度 `/layer end` で終了（DB に記録されます）し、正しい桁名で `/layer start`
し直してください。誤記録の行は DB を直接操作して削除できます。
