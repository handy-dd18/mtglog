"""ヘッドレス Chromium で無音の警告の表示を確かめるスモークテスト（T5.2、手動実行用）。

「相手」チャンネルの VAD を常に 0 にし、警告までの時間を 3 秒にして記録する。警告が出ること、閉じられること、
閉じた後に再表示されないことを確かめる。

    uv run --with playwright python tests/e2e_silence.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from itertools import count
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from conftest import FakeEngine, energy_prob
from e2e_browser import BROWSER_ARGS

from mtglog.app import create_app
from mtglog.config import Config

PORT = 8798


def vad_factory():
    """Session は「自分」「相手」の順に VAD を作るので、2 つ目ごとに常に無音を返す VAD を渡す。"""
    n = next(calls)
    return energy_prob if n % 2 == 0 else (lambda _w: 0.0)


calls = count()


def main() -> int:
    cfg = Config(port=PORT, output_dir=tempfile.mkdtemp(), silence_warning_sec=3)
    app = create_app(cfg, FakeEngine(), vad_factory)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)

    errors: list[str] = []
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(args=BROWSER_ARGS)
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"http://localhost:{PORT}/")
        page.wait_for_function("document.querySelector('#micSelect').value !== ''")
        print("speaker:", page.text_content("#outputName"))
        page.click("#btnStart")
        page.wait_for_selector("#status.recording", timeout=10000)

        sel = '#notices [data-id="silence-remote"]'
        page.wait_for_selector(sel, timeout=10000)
        text = page.text_content(sel)
        print("warning:", text)
        ok = ok and "相手の音声が 3 秒間届いていません" in text

        page.click(f"{sel} button.close")
        time.sleep(3)
        reappeared = page.query_selector(sel) is not None
        print("reappeared after close:", reappeared)
        ok = ok and not reappeared

        page.click("#btnStop")
        page.wait_for_selector("#status.idle", timeout=20000)
        browser.close()
    server.should_exit = True
    th.join(timeout=20)
    print("page errors:", errors)
    return 0 if ok and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
