#!/usr/bin/env bash
# WSL 上で文字起こしサーバーを起動する（REQ-5.1）。
# 使い方: scripts/start.sh [mtglog serve の引数...]
#   例: scripts/start.sh --no-save-audio
#       scripts/start.sh --config config.toml
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv が見つかりません。https://docs.astral.sh/uv/ の手順で入れてください" >&2
  exit 1
fi

EXTRA=()
if command -v nvidia-smi >/dev/null 2>&1; then
  EXTRA=(--extra cuda)  # GPU 用の CUDA ライブラリ
fi

ARGS=("$@")
if [[ -f "$ROOT/config.toml" ]] && [[ " ${ARGS[*]-} " != *" --config "* ]]; then
  ARGS=(--config "$ROOT/config.toml" "${ARGS[@]}")
fi

uv sync --project server "${EXTRA[@]}" -q
exec uv run --project server --no-sync mtglog serve --model-dir "$ROOT/models" --output-dir "$ROOT/output" "${ARGS[@]}"
