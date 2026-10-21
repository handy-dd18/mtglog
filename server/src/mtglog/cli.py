"""コマンドライン: `mtglog serve`。"""

from __future__ import annotations

import argparse
import logging

from . import config as config_mod


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mtglog", description="会議音声のローカル文字起こし")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="文字起こしサーバーを起動する")
    config_mod.add_arguments(serve)
    serve.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("faster_whisper",):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = config_mod.from_args(args)
    if args.command == "serve":
        serve_cmd(cfg)


def serve_cmd(cfg: config_mod.Config) -> None:
    import uvicorn

    from .app import create_app
    from .transcriber import ModelNotFoundError, load_engine
    from .vad import SileroVAD

    print("モデルを読み込んでいます…", flush=True)
    try:
        engine, warning = load_engine(cfg)
    except ModelNotFoundError as e:
        raise SystemExit(f"エラー: {e}") from None
    print(f"モデル: {engine.description}", flush=True)
    if warning:
        print(f"警告: {warning}", flush=True)
    app = create_app(cfg, engine, SileroVAD, engine_warning=warning)
    print(f"\n取得ページ: http://localhost:{cfg.port}/  （Windows の Edge で開く）\n停止するには Ctrl+C\n", flush=True)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
