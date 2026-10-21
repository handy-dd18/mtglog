#!/usr/bin/env bash
# コンテナの起動時に、モデルがなければ準備してからサーバーを起動する（design 5.2, 5.3）。
set -euo pipefail
/app/scripts/setup_models.sh /app/models
# コンテナ内では 0.0.0.0 で待ち受け、ホスト側は compose.yaml で 127.0.0.1 にだけ公開する（REQ-6.2）
exec uv run --project /app/server --no-sync mtglog serve --host 0.0.0.0 --model-dir /app/models --output-dir /app/output "$@"
