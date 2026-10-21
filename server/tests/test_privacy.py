"""外部通信がないことの確認（T4.2, REQ-6.1〜6.3）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
URL = re.compile(r"""(?:https?|wss?)://[^\s"'`)<>]+""")
LOCAL = ("localhost", "127.0.0.1", "${location.host}")


def test_web_page_loads_no_external_resources():
    """取得ページの HTML / JS / CSS に、localhost 以外の URL が含まれない（REQ-6.3）。"""
    found = []
    for p in (ROOT / "web").rglob("*"):
        if p.suffix not in {".html", ".js", ".css"} or "tests" in p.parts:
            continue
        for url in URL.findall(p.read_text(encoding="utf-8")):
            if not any(h in url for h in LOCAL):
                found.append(f"{p.relative_to(ROOT)}: {url}")
    assert found == []


def test_server_binds_localhost_by_default():
    from mtglog.config import Config

    assert Config().host == "127.0.0.1"  # REQ-6.2


def test_docker_publishes_only_to_localhost():
    compose = (ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8")
    ports = re.findall(r'-\s*"([^"]+:\d+:\d+)"', compose)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports)


def test_server_never_downloads_models():
    """サーバーは変換済みのモデルのディレクトリだけを読み、外部から取得しない（REQ-6.1、design 5.3）。"""
    src = (ROOT / "server" / "src" / "mtglog" / "transcriber.py").read_text(encoding="utf-8")
    assert "WhisperModel(str(path)" in src
    assert "download_root" not in src and "huggingface" not in src


def test_models_come_only_from_openai():
    """準備するモデルは OpenAI 公式のリポジトリだけ（specs/_steering/tech.md）。"""
    src = (ROOT / "scripts" / "setup_models.py").read_text(encoding="utf-8")
    repos = re.findall(r'"repo": "([^"]+)"', src)
    assert repos and all(r.startswith("openai/") for r in repos)
