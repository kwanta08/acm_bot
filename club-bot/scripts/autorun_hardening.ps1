# 堅牢化タスク（H1-1〜H1-5）を無人で連続実行するランナー。
#
# ★このファイルは UTF-8 BOM 付きで保存すること。
#   Windows PowerShell 5.1 は BOM が無い .ps1 を CP932 として読むため、
#   日本語の文字列リテラルが壊れてパースエラーになる
#   （既存の scripts\loop_check.ps1 / loop_gate.ps1 も BOM 付き）。
#
# 手順の正は docs/development/HARDENING_LOOP_PROMPT.md。
# 対象の表は docs/development/HARDENING_TASKS.md。
#
# 使い方（リポジトリルート、または club-bot/ から）:
#   powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1
#   powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1 -MaxIterations 2
#   powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1 -IncludeH13
#   powershell -ExecutionPolicy Bypass -File club-bot\scripts\autorun_hardening.ps1 -VaultPath D:\notes\projects\acm_bot
#
# ★git worktree の中でだけ回すこと。実行後は必ず git diff を人間が読む。
#   無人実行の結果をレビューなしでマージしない。

[CmdletBinding()]
param(
  # 1 回の実行で回すタスクの上限
  [int]$MaxIterations = 5,

  # H1-3（設定キーのホワイトリスト化）も対象に含める。
  # 既定では外してある。新しい構造を作るうえ既存の挙動を変えるので、
  # HARDENING_LOOP_PROMPT.md §B のとおりプランモードを挟むべきタスク。
  [switch]$IncludeH13,

  # 開発ノート（ADR / gotcha）のディレクトリ。指定すると --add-dir で読ませる。
  # リポジトリには含まれないので、無い環境では省略してよい。
  [string]$VaultPath = '',

  # 使うモデル
  [string]$Model = 'opus',

  # 実際には実行せず、流すコマンドだけ表示する
  [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# --- リポジトリルートを探す（loop_gate.ps1 と同じ探し方）-------------------
# claude はリポジトリルートで動かす。プロンプトが club-bot/... のパスを
# 参照しているため、club-bot/ の中から動かすと相対パスが解決できない。
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$cands = @($PWD.Path, (Join-Path $PWD.Path '..'), (Join-Path $here '..\..'), (Join-Path $here '..'))
$root  = $null
foreach ($c in $cands) {
  if (Test-Path (Join-Path $c 'club-bot\pyproject.toml')) {
    $root = (Resolve-Path $c).Path
    break
  }
}
if (-not $root) {
  Write-Error 'リポジトリルートが見つかりません（club-bot\pyproject.toml を探しています）。'
  exit 1
}
Set-Location $root
Write-Host "[autorun] リポジトリルート: $root"

# --- claude CLI の存在確認 -------------------------------------------------
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Error 'claude コマンドが見つかりません。Claude Code CLI を PATH に通してください。'
  exit 1
}

# --- main で回していないことを確認 -----------------------------------------
# AGENTS.md の絶対ルール1（main へ直接コミットしない）。
# 無人実行は git worktree の中でやる前提。
$branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
if ($LASTEXITCODE -ne 0) {
  Write-Error 'git リポジトリではありません。'
  exit 1
}
if ($branch -eq 'main' -or $branch -eq 'master') {
  Write-Error "現在のブランチが $branch です。作業用のブランチか git worktree で実行してください。"
  exit 1
}
Write-Host "[autorun] ブランチ: $branch"

# --- 未コミットの変更があるなら先に片付けさせる -----------------------------
& git diff --quiet -- .
$dirty = ($LASTEXITCODE -ne 0)
if ($dirty) {
  Write-Warning '未コミットの変更があります。前回のタスクが未完成の可能性があります。'
  Write-Warning '続けると別のタスクの差分が混ざります。git status を確認してください。'
}

# --- 対象タスク -------------------------------------------------------------
# 番号順。H1-3 は既定で外す（-IncludeH13 で入る）。
$tasks = @('H1-1', 'H1-2', 'H1-4', 'H1-5')
if ($IncludeH13) { $tasks = @('H1-1', 'H1-2', 'H1-3', 'H1-4', 'H1-5') }
if ($tasks.Count -gt $MaxIterations) { $tasks = $tasks[0..($MaxIterations - 1)] }

Write-Host ("[autorun] 対象: {0}（{1} 件）" -f ($tasks -join ', '), $tasks.Count)
if (-not $IncludeH13) {
  Write-Host '[autorun] H1-3 は対象外です（プランモードを挟むため）。-IncludeH13 で含められます。'
} elseif ($tasks -contains 'H1-3') {
  Write-Warning 'H1-3 を無人で回します。設定キーの仕様表という新しい構造を作るタスクなので、'
  Write-Warning '本来は HARDENING_LOOP_PROMPT.md §B のとおりプランモードを挟むべきものです。'
  Write-Warning '差分は特に注意して読んでください。'
}

# --- プロンプト本体 ---------------------------------------------------------
# docs/development/HARDENING_LOOP_PROMPT.md §A と同じ内容を、タスク ID を
# 明示する形にしたもの。番号を指定するのは、H1-3 を飛ばしても
# 「未完了で最も若い番号」が H1-3 に戻ってしまわないようにするため。
$promptTemplate = @'
/autonomous-dev-loop

club-bot/docs/development/HARDENING_TASKS.md の __TASK_ID__ を、plan から記録まで通しで実装して。

このセッションは自走前提です。**各ゲートで私に確認を取らず、判定に従って自分で進めてください。**
環境は Windows / PowerShell。作業ディレクトリは club-bot/ です。
コマンドは PowerShell の書式で書くこと（cp は Copy-Item の別名で /tmp は C:\tmp になる、
printf は無い、glob は展開されない）。

## 事前に読むもの（plan を書くより先に）

1. HARDENING_TASKS.md の「運用ルール」「全タスク共通の受入基準」「この表に固有の受入基準」と、
   __TASK_ID__ の受入基準・検証・注意
2. リポジトリルートの AGENTS.md（全文）
3. 開発ノートの decisions/_index.md と gotchas/_index.md（--add-dir されていれば）
4. __TASK_ID__ の「注意」に ADR 番号や gotcha 名が書いてあれば、その本文も

## フェーズ1 — plan

受入基準を1行ずつ書き出し、次を含む plan を作る。曖昧な点は最も素直な解釈で
仮決めして明記する（私に聞かない）。

- 触るファイルと、それぞれで何を変えるか
- 先に書く「失敗するテスト」の一覧と、それぞれが何を固定するか
- 既存の挙動が変わる箇所と、通らなくなる入力
- 触る ADR があれば番号と、覆すのか沿うのか
- やらないこと（隣接して見えるが今回の範囲外のもの）

## ゲート1 — plan の客観評価

実装に入る前に @"acm-plan-reviewer (agent)" を呼んで審査させる。
APPROVE なら実装へ。REVISE は直して再審査（2回まで。3回目も REVISE なら止まって報告）。
REJECT / NEEDS-HUMAN は実装せず、STOPPED: に続けて理由を出力して終了する。

## フェーズ2 — 実装

テスト先行 → 最小実装 → 検証 → 失敗を分類して自己修正、を全パスまで。
一度に触るのは1論点まで。無関係なリファクタを混ぜない。

  venv\Scripts\python.exe -m ruff check .
  venv\Scripts\python.exe -m pytest tests/ -q -rs

同じテストが3周直しても緑にならなければ STOPPED: に続けて理由を出力して終了する。

## ゲート2 — 実装後（2つ同時に立てる）

全テストが緑になったら @"acm-diff-auditor (agent)" と @"acm-test-adversary (agent)" を
同時に立てる。FINDINGS / INEFFECTIVE は直して両方もう一度（2回まで）。
NEEDS-HUMAN なら STOPPED: に続けて理由を出力して終了する。

## フェーズ3 — 完了処理

1. HARDENING_TASKS.md の __TASK_ID__ にチェックを入れ、完了ログへ
   「完了内容 / 設計判断 / ゲートの判定 / 次タスクへの申し送り」を追記する
2. 実装は fix/__TASK_BRANCH__ ブランチに1コミット。表とドキュメントの更新は docs: の別コミット
3. README.md / docs/GUIDE.md / docs/OPERATION.md と矛盾していれば同じイテレーションで直す
4. 開発ノートへ書く材料（ADR 草案 / gotcha 草案 / unfixed を外せる gotcha）を出力する
5. 「変更内容 / 検証 / 両ゲートの判定 / test-adversary の実測表 / 残課題」の形で報告する

## 制約

- push はしない。コミットはタスク単位で行ってよい
- __TASK_ID__ の範囲外のファイルを触らない
- skip を「緑」と数えない。pytest は必ず -rs 付きで回し、skip 理由を報告に書く
- スキーマを変えない。migrations/ にファイルを足さない
- 止まるときは必ず STOPPED: で始まる行を出力してから終了する
'@

# --- 実行 -------------------------------------------------------------------
$logDir = Join-Path $root 'club-bot\logs\autorun'
if (-not $DryRun -and -not (Test-Path $logDir)) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$done    = 0
$stopped = $null

foreach ($task in $tasks) {
  $branchName = $task.ToLower()
  $prompt = $promptTemplate.Replace('__TASK_ID__', $task).Replace('__TASK_BRANCH__', $branchName)

  Write-Host ''
  Write-Host ("=== {0} を開始します（{1}/{2}）===" -f $task, ($done + 1), $tasks.Count)

  $claudeArgs = @('-p', '--model', $Model, '--permission-mode', 'acceptEdits')
  if ($VaultPath -and (Test-Path $VaultPath)) {
    $claudeArgs += @('--add-dir', $VaultPath)
  } elseif ($VaultPath) {
    Write-Warning "-VaultPath が見つかりません: $VaultPath（--add-dir を省略します）"
  }
  $claudeArgs += $prompt

  if ($DryRun) {
    Write-Host "[dry-run] claude $($claudeArgs[0..4] -join ' ') <prompt: $task>"
    $done++
    continue
  }

  $stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
  $logPath = Join-Path $logDir "$stamp-$branchName.log"

  $output = & claude @claudeArgs 2>&1
  $code   = $LASTEXITCODE
  $text   = ($output | Out-String)
  Set-Content -Path $logPath -Value $text -Encoding UTF8
  Write-Host $text
  Write-Host "[autorun] ログ: $logPath"

  if ($code -ne 0) {
    $stopped = "$task で claude が異常終了しました（exit $code）。"
    break
  }
  if ($text -match '(?m)^\s*STOPPED:') {
    $line = ($output | Select-String -Pattern 'STOPPED:' | Select-Object -First 1)
    $stopped = "$task で自走が停止しました: $line"
    break
  }

  $done++
  Write-Host "=== $task を完了しました ==="
}

# --- まとめ -----------------------------------------------------------------
Write-Host ''
Write-Host "[autorun] 完了 $done 件 / 対象 $($tasks.Count) 件"
if ($stopped) {
  Write-Warning $stopped
  Write-Warning '残りは実行していません。ログを読んで判断してください。'
}
Write-Host '[autorun] 次にやること: git log --oneline と git diff を人間が読む。'
Write-Host '[autorun] レビューなしでマージしないこと。'

if ($stopped) { exit 1 }
exit 0
