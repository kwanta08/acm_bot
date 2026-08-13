#!/usr/bin/env bash
# acm_bot ループ検証: ruff + pytest をまとめて実行し、失敗だけを圧縮表示する。
# club-bot/ で実行するのが基本（リポジトリルートから叩いても club-bot に降りる）。
#
#   bash scripts/loop_check.sh              # フル
#   bash scripts/loop_check.sh --fast       # 前回失敗分のみ・最初の失敗で停止
#   bash scripts/loop_check.sh -k progress  # 追加引数はそのまま pytest へ

set -uo pipefail

# --- club-bot に移動 -------------------------------------------------------
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for cand in "$PWD" "$PWD/club-bot" "$here/.." "$here/../club-bot"; do
  if [ -f "$cand/pyproject.toml" ] && [ -d "$cand/tests" ]; then
    cd "$cand" || exit 1
    found=1
    break
  fi
done
if [ -z "${found:-}" ]; then
  echo "ERROR: club-bot ディレクトリが見つかりません（tests/ と pyproject.toml のある場所で実行してください）" >&2
  exit 2
fi

# --- python を決める -------------------------------------------------------
if [ -x "venv/Scripts/python.exe" ]; then PY="venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ];      then PY="venv/bin/python"
elif command -v python3 >/dev/null; then PY="python3"
else                                     PY="python"
fi

FAST=0
PYTEST_ARGS=()
for a in "$@"; do
  case "$a" in
    --fast) FAST=1 ;;
    *) PYTEST_ARGS+=("$a") ;;
  esac
done
[ "$FAST" = "1" ] && PYTEST_ARGS+=("-x" "--lf")

echo "== ruff check =================================================="
"$PY" -m ruff check .
RUFF=$?
[ $RUFF -eq 0 ] && echo "ruff: OK"

echo
echo "== pytest ======================================================"
# set -u 下で空配列を展開しても落ちないようにする（bash 4.3 以前対策）
"$PY" -m pytest tests/ -q --no-header -r fE ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
PYT=$?

echo
echo "== summary ====================================================="
echo "ruff   : $([ $RUFF -eq 0 ] && echo PASS || echo FAIL)"
echo "pytest : $([ $PYT  -eq 0 ] && echo PASS || echo FAIL)"
if [ $RUFF -ne 0 ] || [ $PYT -ne 0 ]; then
  echo ">> まだ完了ではありません。失敗を分類 → 修正 → 再実行してください。"
  exit 1
fi
echo ">> 全パス。AGENTS.md の完了前チェックへ進んでください。"
