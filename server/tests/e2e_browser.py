"""ヘッドレス Chromium で取得ページを動かすスモークテスト（手動実行用）。

偽のマイクと偽の画面共有（ブラウザーのテスト用フラグ）で、ページ → WebSocket → サーバー → 結果表示の
流れが動くことを確かめる。実デバイスでの確認（tasks.md の手動の完了条件）の代わりにはならない。

    uv run --with playwright python tests/e2e_browser.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from conftest import FakeEngine, energy_prob

from mtglog.app import create_app
from mtglog.config import Config

PORT = 8799


def start_server(out: Path) -> tuple[uvicorn.Server, threading.Thread]:
    cfg = Config(port=PORT, output_dir=str(out))
    app = create_app(cfg, FakeEngine(), lambda: energy_prob)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    return server, th


def main() -> int:
    out = Path(tempfile.mkdtemp())
    server, th = start_server(out)

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--auto-select-desktop-capture-source=Entire screen",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        # サーバーを止めている間の接続失敗は想定どおりなので除く
        page.on(
            "console",
            lambda m: m.type == "error" and "ERR_CONNECTION_REFUSED" not in m.text and errors.append(m.text),
        )
        page.goto(f"http://localhost:{PORT}/")
        page.wait_for_function(
            "document.querySelector('#micSelect').options.length > 0 && document.querySelector('#micSelect').value !== ''"
        )
        print("mic options:", page.eval_on_selector_all("#micSelect option", "els => els.map(e => e.textContent)"))
        print("output:", page.text_content("#outputName"))
        page.click("#btnStart")
        time.sleep(1)
        print("status:", page.text_content("#status"), "| notices:", page.text_content("#notices"))
        page.wait_for_selector("#status.recording", timeout=10000)
        time.sleep(6)

        # 記録中にサーバーを止めて 3 秒後に起動し直す（T3.3）
        server.should_exit = True
        th.join(timeout=20)
        page.wait_for_selector("#status.reconnecting", timeout=10000)
        print("server stopped; status:", page.text_content("#status"))
        time.sleep(3)
        server, th = start_server(out)
        page.wait_for_selector("#status.recording", timeout=20000)
        print("reconnected; notices:", page.text_content("#notices"))
        time.sleep(6)
        page.click("#btnStop")
        page.wait_for_selector("#status.idle", timeout=20000)
        print("result:", page.text_content("#outputDir"))
        n = page.eval_on_selector_all("#transcript li", "els => els.length")
        print("segments shown:", n)
        browser.close()
    server.should_exit = True

    th.join(timeout=20)
    dirs = sorted(out.iterdir())
    ok = len(dirs) == 2
    for d in dirs:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        lines = [json.loads(x) for x in (d / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
        print(d.name, "continued_from:", meta.get("continued_from"), "gap:", meta.get("gap_seconds"))
        print("  segments:", [(r["speaker"], r["start"], r["end"]) for r in lines])
        ok = ok and len(lines) > 0
    ok = ok and json.loads((dirs[1] / "meta.json").read_text(encoding="utf-8")).get("continued_from") is not None
    print("page errors:", errors)
    return 0 if ok and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
