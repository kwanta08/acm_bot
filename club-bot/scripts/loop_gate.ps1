# acm_bot Stop フック用ゲート（PowerShell 版）。
#
# ★このファイルは UTF-8 BOM 付きで保存すること。
#   Windows PowerShell 5.1 は BOM が無い .ps1 を CP932 として読むため、
#   日本語の文字列リテラルが壊れてパースエラーになる
#   （開発ノートの gotcha powershell-parse-error-on-japanese）。
#   既存の scripts\loop_check.ps1 も BOM 付き。
#
# エージェントが「終わりました」と止まろうとしたとき、作業が未完成なら
# exit 2 で終了をブロックして継続させる。
#
# 設置: .claude\settings.json の hooks.Stop から呼ぶ
#   "command": "powershell -NoProfile -ExecutionPolicy Bypass -File club-bot\\scripts\\loop_gate.ps1"
#
# 終了コード:
#   0 … 止まってよい（ゲート対象外 / 全部緑 / 打ち切り）
#   2 … 止めない。標準エラーの内容をエージェントに返して継続させる

$ErrorActionPreference = 'Continue'

# --- club-bot に移動（loop_check.ps1 と同じ探し方）-------------------------
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$cands = @($PWD.Path, (Join-Path $PWD.Path 'club-bot'), (Join-Path $here '..'), (Join-Path $here '..\club-bot'))
$root  = $null
foreach ($c in $cands) {
  if ((Test-Path (Join-Path $c 'pyproject.toml')) -and (Test-Path (Join-Path $c 'tests'))) {
    $root = (Resolve-Path $c).Path
    break
  }
}
if (-not $root) { exit 0 }
Set-Location $root

$py = if (Test-Path 'venv\Scripts\python.exe') { 'venv\Scripts\python.exe' } else { 'python' }

function Write-Err([string]$msg) { [Console]::Error.WriteLine($msg) }

# --- 1. コード変更が無いターンはゲートしない（会話・調査だけの応答）--------
& git diff --quiet -- .
$dirty = ($LASTEXITCODE -ne 0)
if (-not $dirty) {
  & git diff --cached --quiet -- .
  $dirty = ($LASTEXITCODE -ne 0)
}
if (-not $dirty) { exit 0 }

# --- 2. 無限ループ防止 -----------------------------------------------------
# 同じ作業で MAX 回までしかブロックしない。30 分空いたらカウントを捨てる。
$MAX   = 3
$state = Join-Path $env:TEMP 'acm_loop_gate.txt'
$now   = [int64]((Get-Date).ToUniversalTime() - (Get-Date '1970-01-01')).TotalSeconds
$count = 0
if (Test-Path $state) {
  $raw = (Get-Content $state -ErrorAction SilentlyContinue) -join ''
  if ($raw -match '^(\d+)\s+(\d+)$') {
    if (($now - [int64]$Matches[2]) -lt 1800) { $count = [int]$Matches[1] }
  }
}
if ($count -ge $MAX) {
  Write-Err "[loop_gate] $MAX 回ブロックしても緑になりませんでした。人間の判断が要ります。"
  Remove-Item $state -ErrorAction SilentlyContinue
  exit 0
}

function Block([string]$msg) {
  $next = $count + 1
  Set-Content -Path $state -Value "$next $now" -Encoding ASCII
  Write-Err "[loop_gate] まだ完了していません（$next / $MAX 回目のブロック）。"
  Write-Err $msg
  exit 2
}

# --- 3. ruff ---------------------------------------------------------------
$out  = & $py -m ruff check . 2>&1
$code = $LASTEXITCODE
if ($code -ne 0) {
  $tail = ($out | Select-Object -Last 30) -join [Environment]::NewLine
  Block "ruff が赤です。直してから終わってください:$([Environment]::NewLine)$tail"
}

# --- 4. pytest -------------------------------------------------------------
$out  = & $py -m pytest tests/ -q -rs 2>&1
$code = $LASTEXITCODE
$text = ($out | Out-String)
if ($code -ne 0) {
  $tail = ($out | Select-Object -Last 40) -join [Environment]::NewLine
  Block "pytest が赤です。直してから終わってください:$([Environment]::NewLine)$tail"
}

# --- 5. skip を緑と数えない ------------------------------------------------
# CLUB_TEST_PG_DSN を渡して回しているときに skip があるのは異常。
$skipped = 0
if ($text -match '(\d+)\s+skipped') { $skipped = [int]$Matches[1] }
if ($env:CLUB_TEST_PG_DSN -and $skipped -gt 0) {
  $reasons = (($out | Select-String -Pattern '^SKIPPED' | Select-Object -First 10) | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
  $nl = [Environment]::NewLine
  Block "CLUB_TEST_PG_DSN を設定しているのに $skipped 件 skip されています。${nl}skip は緑ではありません。理由を確認してください:${nl}$reasons"
}

# --- 6. 通過 ---------------------------------------------------------------
Remove-Item $state -ErrorAction SilentlyContinue
$summary = ''
if ($text -match '(?m)^(\d+ passed.*)$') { $summary = $Matches[1] }
Write-Err "[loop_gate] ruff / pytest 緑（$summary）"
exit 0
