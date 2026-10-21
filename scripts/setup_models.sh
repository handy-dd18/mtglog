#!/usr/bin/env bash
# OpenAI 公式の Whisper モデルを取得して、faster-whisper 用の形式に変換する（design 5.3）。
# 準備済みなら何もしない。scripts/start.sh と Docker のエントリーポイントから自動で呼ばれる。
# 使い方: scripts/setup_models.sh [モデルの置き場所（既定: models）]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS="$(mkdir -p "${1:-$ROOT/models}" && cd "${1:-$ROOT/models}" && pwd)"
SCRIPT="$ROOT/scripts/setup_models.py"

if uv run -q --no-project --python 3.12 python "$SCRIPT" check "$MODELS" >/dev/null; then
  exit 0
fi

echo "文字起こしモデルを準備します（初回のみ。数分かかり、一時的に約 4 GB の空きが必要です）"
uv run -q --no-project --python 3.12 python "$SCRIPT" check "$MODELS" || true

# 取得したファイルと変換用のツールは、すべてこの一時ディレクトリに置き、最後に消す
TMP="$MODELS/.setup-tmp"
rm -rf "$TMP"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT
export UV_CACHE_DIR="$TMP/uv-cache" HF_HOME="$TMP/hf" HF_HUB_DISABLE_TELEMETRY=1

# 変換ツールは、サーバーと同じ版の ctranslate2 を使う
CT2_VERSION="$(awk '/^name = "ctranslate2"$/ { getline; gsub(/[^0-9.]/, ""); print; exit }' "$ROOT/server/uv.lock")"
uv venv -q --python 3.12 "$TMP/venv"
VIRTUAL_ENV="$TMP/venv" uv pip install -q --torch-backend cpu \
  "ctranslate2==$CT2_VERSION" "transformers>=4.40,<5" accelerate huggingface_hub torch

"$TMP/venv/bin/python" "$SCRIPT" convert "$MODELS" "$TMP"
echo "文字起こしモデルの準備が終わりました: $MODELS"
