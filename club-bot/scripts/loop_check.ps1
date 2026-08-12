# acm_bot ループ検証: ruff + pytest をまとめて実行し、結果を要約する（PowerShell 版）。
# club-bot/ で実行するのが基本（リポジトリルートから叩いても club-bot に降りる）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\loop_check.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\loop_check.ps1 -Fast
#   powershell -ExecutionPolicy Bypass -File scripts\loop_check.ps1 -k progress
#
# -Fast 以外の引数はそのまま pytest へ渡す（-File 経由では配列パラメータが
# 壊れるため、名前付き -PytestArgs ではなく残余引数で受ける）。

param(
  [switch]$Fast,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Continue'

# --- club-bot に移動 -------------------------------------------------------
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cands = @($PWD.Path, (Join-Path $PWD.Path 'club-bot'), (Join-Path $here '..'), (Join-Path $here '..\club-bot'))
$root = $null
foreach ($c in $cands) {
  if ((Test-Path (Join-Path $c 'pyproject.toml')) -and (Test-Path (Join-Path $c 'tests'))) { $root = (Resolve-Path $c).Path; break }
}
if (-not $root) { Write-Error 'club-bot ディレクトリが見つかりません（tests/ と pyproject.toml のある場所で実行してください）'; exit 2 }
Set-Location $root

# --- python を決める -------------------------------------------------------
$py = if (Test-Path 'venv\Scripts\python.exe') { 'venv\Scripts\python.exe' } else { 'python' }

if ($Fast) { $PytestArgs += @('-x', '--lf') }

Write-Host '== ruff check =================================================='
& $py -m ruff check .
$ruff = $LASTEXITCODE
if ($ruff -eq 0) { Write-Host 'ruff: OK' }

Write-Host ''
Write-Host '== pytest ======================================================'
& $py -m pytest tests/ -q --no-header -r fE @PytestArgs
$pyt = $LASTEXITCODE

Write-Host ''
Write-Host '== summary ====================================================='
Write-Host ("ruff   : " + $(if ($ruff -eq 0) { 'PASS' } else { 'FAIL' }))
Write-Host ("pytest : " + $(if ($pyt  -eq 0) { 'PASS' } else { 'FAIL' }))
if ($ruff -ne 0 -or $pyt -ne 0) {
  Write-Host '>> まだ完了ではありません。失敗を分類 → 修正 → 再実行してください。'
  exit 1
}
Write-Host '>> 全パス。AGENTS.md の完了前チェックへ進んでください。'
