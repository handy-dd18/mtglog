# 文字起こしサーバーの Docker イメージ（REQ-5.2）。CUDA 12 と cuDNN 9 を含む。
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MTGLOG_WEB_DIR=/app/web

WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv python install 3.12 && uv sync --frozen --no-dev --no-install-project
COPY server/src ./src
RUN uv sync --frozen --no-dev
COPY web /app/web
# 任意の UID で実行できるよう、読み取りと実行を全員に許可する
RUN chmod -R a+rX /app /opt/python
ENV UV_CACHE_DIR=/tmp/uv-cache HF_HOME=/tmp/hf

WORKDIR /app
EXPOSE 8765
# コンテナ内では 0.0.0.0 で待ち受け、ホスト側は compose.yaml で 127.0.0.1 にだけ公開する（REQ-6.2）
ENTRYPOINT ["uv", "run", "--project", "/app/server", "--no-sync", "mtglog", "serve", "--host", "0.0.0.0", "--model-dir", "/app/models", "--output-dir", "/app/output"]
