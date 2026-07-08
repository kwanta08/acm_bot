# 運用マニュアル

鳥人間サークル 統合運営 Discord Bot の日常運用ガイドです。
全コマンド・権限・自動ジョブ・トラブル対応をまとめています。

---

## 1. 権限レベル（仕様 9）

| レベル | 対象 | 判定 |
|---|---|---|
| L1 | 一般メンバー | 既定（誰でも） |
| L2 | 班長 | `LEADER_ROLE_IDS` のロールを持つ |
| L3 | 幹部 | `EXEC_ROLE_ID` のロールを持つ |
| L4 | Bot 管理者 | `ADMIN_ROLE_ID` のロール、またはサーバー管理者権限/オーナー |

上位レベルは下位の権限をすべて含みます（L4 は L3/L2/L1 を内包）。

---

## 2. コマンド一覧

### Core
| コマンド | 権限 | 説明 |
|---|---|---|
| `/ping` | L1 | 応答確認 |
| `/health` | L1 | Bot・各連携サービスの状態表示 |

### Schedule（日程調整・出欠）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/schedule create` | L2 | 日程調整を作成（候補日時は `;` 区切り） |
| `/schedule list` | L1 | 開催中一覧 |
| `/schedule status <id>` | L1 | 投票の詳細表示 |
| `/schedule close <id>` | L2 | 手動締切 |
| `/schedule remind <id>` | L2 | 未回答者へ再通知 |
| `/schedule delete <id>` | L3 | 投票削除 |

**投票方法**: 候補日ごとに投稿されるメッセージへ ✅(参加) / ❌(不参加) / ❓(未定) でリアクション。
1候補につき1状態のみ。別の状態を押すと前の状態は自動で外れます。

### Tasks（タスク・Todoist 連携）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/task add` | L1 | タスク作成（Todoist へ反映） |
| `/task list [mine]` | L1 | 一覧（`mine:true` で自分の担当のみ） |
| `/task done <id>` | L1 | 完了（Todoist 側も close） |
| `/task delete <id>` | L2 | 削除 |
| `/task assign <id> <user>` | L2 | 担当者変更 |
| `/task priority <id> <1-4>` | L1 | 優先度変更 |
| `/task overdue` | L1 | 期限超過一覧 |
| `/task team <班>` | L1 | 班別一覧 |
| `/task sections` | L2 | Todoist のセクション一覧と班との紐付け状況を表示 |
| `/task link-section <班> <section_id>` | L3 | Todoist セクションを班に紐付け |
| `/task unlink-section <section_id>` | L3 | セクションの紐付けを解除 |
| `/task push` | L2 | セクション別タスクを各班チャンネルへ手動プッシュ |
| `/task sync` | L4 | Todoist 同期・ラベル整備 |
| `/today task <タスク名>` | L1 | 完全一致で「今日やること」ラベル付与 |
| `/today id <Todoist ID>` | L1 | 同名タスク複数時、ID 指定で確定 |

### Members（班・技能）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/member register <user> [班]` | L2 | メンバー登録 |
| `/member profile [user]` | L1 | プロフィール表示（省略時は自分） |
| `/member assign-team <user> <班>` | L2 | 主所属班を設定 |
| `/member set-channel <班> <channel>` | L3 | 班の通知先チャンネルを設定（タスクの班別通知に使用） |
| `/member set-leader <user> <bool>` | L3 | 班長フラグ設定 |
| `/member skill add <技能> [user]` | L1 | 技能タグ追加 |
| `/member skill remove <技能> [user]` | L1 | 技能タグ削除 |
| `/member support [班] [技能]` | L2 | 支援候補検索（班横断作業向け） |

### Reports（集計・出力）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/report weekly` | L2 | 週次サマリー |
| `/report export-tasks` | L2 | タスク一覧 CSV 出力 |
| `/report audit [limit]` | L3 | 通知・監査ログ表示 |
| `/report attendance-rate` | L2 | 出欠率一覧 |

### Sheets（Google Sheets 同期）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/sheets sync-all` | L3 | 全シート一括同期 |
| `/sheets sync-tasks` | L2 | タスクシートのみ |
| `/sheets sync-members` | L2 | メンバーシートのみ |
| `/sheets sync-attendance` | L2 | 出欠シートのみ |
| `/sheets status` | L1 | 連携状態 |
| `/sheets url` | L1 | スプレッドシート URL |

### LayerTracking（桁巻き積層記録）
| コマンド | 権限 | 説明 |
|---|---|---|
| `/layer start <桁名> <層番号>` | L1 | 積層開始を記録 |
| `/layer end` | L1 | 進行中セッションを終了し桁別シートへ追記 |
| `/layer status` | L1 | 進行中の作業一覧 |

桁名はセレクトメニューから選択（タイプミス防止）。桁の追加・変更は
`config.py` の `LAYER_KETA_CHOICES` を編集します。

---

## 3. 典型的な運用フロー

### 木曜部会の出欠を取る
```
/schedule create
  title: 第N回部会
  options: 2026-07-03 18:30
  deadline: 2026-07-03 12:00
  target_role: @全員    （任意。指定すると未回答者通知の対象になる）
  channel: #出欠管理     （任意。未指定なら DEFAULT_SCHEDULE_CHANNEL_ID）
```
→ 締切1時間前に未回答者へ自動 DM。締切を過ぎると自動でクローズし結果要約を投稿。

### タスクを登録して追いかける
```
/task add title:翼リブ加工 due:2026-07-05 18:00 team:翼 assignee:@担当 priority:3
```
→ 毎朝 08:00 に「7日以内の期限タスク」を通知。
→ 毎晩 21:00 に「期限超過タスク」を警告。
→ 当日やるものは `/today task:翼リブ加工` でラベル付与し、毎朝 08:00 に一覧通知。

### タスク通知を班ごとのチャンネルに振り分ける
朝（08:00 の7日以内期限）と夜（21:00 の期限超過）のタスク通知は、
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
- 毎朝 08:00 の定期通知に含まれます（自動）。
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
→ 桁名と同名のシートに「層番号・作業者・開始・終了・作業時間(分)」が1行追記される。
→ シートが無ければヘッダー付きで自動作成。

### 班をまたいだ支援者を探す
```
/member support skill:CFRP積層        ← CFRP積層ができる人を検索
/member support team:電装 skill:はんだ ← 電装班ではんだができる人
```

---

## 4. 自動ジョブ（仕様 11.5.1）

| ジョブ | タイミング | 内容 |
|---|---|---|
| 締切前催促 | 締切1時間前（5分間隔で判定） | 未回答者へ DM、不可ならチャンネルでメンション |
| 自動締切 | 5分ごと | 締切超過の投票を終了し結果要約を投稿 |
| 7日以内期限通知 | 毎日 08:00 | 今日〜7日以内が期限の未完了タスク |
| Todoist セクション別通知 | 毎日 08:00 | 班と紐付けたセクションのタスク（期限7日以内+超過）を各班チャンネルへ |
| 今日やること通知 | 毎日 08:00 | 「今日やること」ラベル付きタスク |
| 超過通知 | 毎日 21:00 | 期限切れの未完了タスク |
| Sheets 定期同期 | 毎日 04:00 | タスク・メンバー・出欠シートを同期 |

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

- **状態確認**: `/health` で SQLite・Todoist・Sheets の有効/無効と遅延を確認。
- **ログ**: さくらのVPS（systemd常駐）では `journalctl -u club-bot -f`、ファイルは `logs/bot.log`。
- **監査**: `/report audit` で直近の通知履歴と失敗理由を確認。
- **バックアップ**: `data/club.db` を日次でコピー。週1で `/report export-tasks`。
- **桁の増減**: `config.py` の `LAYER_KETA_CHOICES` を編集 → Bot 再起動で選択肢に反映。
- **班・技能の増減**: `config.py` の `INITIAL_TEAMS` / `SKILL_TAGS` を編集 → 再起動。

---

## 7. よくある質問

**Q. Todoist や Sheets を使わない運用はできますか？**
A. はい。`.env` で該当トークン/ID を空のままにすれば、その機能だけ無効化されて
ほかは通常どおり動きます。`/health` で「無効」と表示されます。

**Q. Bot を再起動すると進行中の投票や桁巻き作業は消えますか？**
A. 消えません。投票はリアクションの raw イベントで再処理され、桁巻きセッションは
SQLite から復元されるため、再起動後も `/layer end` を実行できます。

**Q. 桁名を間違えて開始してしまいました。**
A. 一度 `/layer end` で終了（シートに記録されます）し、正しい桁名で `/layer start`
し直してください。誤記録の行はスプレッドシート上で削除できます。
