"""WebSocket のプロトコルと記録の状態管理（T1.4）、サーバー全体の流れ（T2.4 の一部）。"""

import json
import time

import numpy as np
import pytest
from conftest import FakeEngine, energy_prob, silence, tone
from fastapi.testclient import TestClient

from mtglog.app import create_app
from mtglog.config import Config
from mtglog.frames import build_frame

FRAME = 1600


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def frames(channel, audio, start=0):
    pcm = (audio * 32767).astype(np.int16)
    for i in range(0, len(pcm), FRAME):
        yield build_frame(channel, start + i, pcm[i : i + FRAME])


def recv_until(ws, kind, limit=200):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"{kind} が届きません")


@pytest.fixture
def setup(tmp_path):
    cfg = Config(output_dir=str(tmp_path / "out"), resume_timeout_s=600)
    clock = Clock()
    engine = FakeEngine()
    app = create_app(cfg, engine, lambda: energy_prob, clock=clock)
    return cfg, app, clock, engine


def test_config_endpoint(setup):
    _, app, _, _ = setup
    with TestClient(app) as c:
        j = c.get("/api/config").json()
        assert j["language"] == "ja" and "Headset" in j["headset_keywords"]


def test_start_send_stop(setup, tmp_path):
    _cfg, app, _, engine = setup
    me = np.concatenate([silence(0.5), tone(1), silence(1)])
    remote = np.concatenate([silence(1), tone(1.5), silence(1)])
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "start", "language": "en", "devices": {"mic": "Headset"}}))
        started = recv_until(ws, "started")
        assert started["received"] == {"me": 0, "remote": 0}
        for f in frames(0, me):
            ws.send_bytes(f)
        for f in frames(1, remote):
            ws.send_bytes(f)
        ws.send_text(json.dumps({"type": "stop"}))
        segs = []
        while True:
            msg = json.loads(ws.receive_text())
            if msg["type"] == "segment":
                segs.append(msg)
            if msg["type"] == "stopped":
                break
    assert sorted(s["speaker"] for s in segs) == ["me", "remote"]
    assert {lang for _, lang in engine.calls} == {"en"}
    out = next((tmp_path / "out").iterdir())
    lines = (out / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    md = (out / "transcript.md").read_text(encoding="utf-8")
    assert "**自分**" in md and "**相手**" in md
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["devices"] == {"mic": "Headset"} and meta["segments"] == 2
    # 録音は既定で保存する（REQ-4.4）
    import wave

    for name, audio in (("me", me), ("remote", remote)):
        with wave.open(str(out / f"{name}.wav")) as r:
            assert r.getframerate() == 16000 and r.getnframes() == len(audio)


def test_disconnect_resume_continues(setup):
    _, app, _, _ = setup
    audio = np.concatenate([silence(0.5), tone(3), silence(1)])
    all_frames = list(frames(1, audio))
    half = len(all_frames) // 2
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            sid = recv_until(ws, "started")["session_id"]
            for f in all_frames[:half]:
                ws.send_bytes(f)
            ack = recv_until(ws, "ack")  # 2 秒ごとの受領確認
            assert ack["received"]["remote"] == half * FRAME
        # 切断後に再接続して続きから送る
        with c.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "resume", "session_id": sid}))
            started = recv_until(ws, "started")
            assert started["session_id"] == sid
            assert started["received"]["remote"] == half * FRAME
            # ページは受信済みの位置より後から送り直す。重複分が混ざっても捨てられる
            for f in all_frames[half - 2 :]:
                ws.send_bytes(f)
            ws.send_text(json.dumps({"type": "stop"}))
            segs = []
            while (msg := json.loads(ws.receive_text()))["type"] != "stopped":
                if msg["type"] == "segment":
                    segs.append(msg)
    assert len(segs) == 1
    assert segs[0]["start"] == pytest.approx(0.3, abs=0.1)
    assert segs[0]["end"] == pytest.approx(3.7, abs=0.1)


def test_resume_unknown_session(setup):
    _, app, _, _ = setup
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "resume", "session_id": "nope"}))
        assert recv_until(ws, "error")["code"] == "unknown_session"


def test_session_expires_after_timeout(setup):
    _, app, clock, _ = setup

    manager = app.state.manager
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            sid = recv_until(ws, "started")["session_id"]
        deadline = time.time() + 2
        while manager.get(sid).ws is not None and time.time() < deadline:
            time.sleep(0.01)
        clock.t += 599
        c.portal.call(manager.expire)
        assert manager.get(sid) is not None  # 期限前は保持
        clock.t += 1
        c.portal.call(manager.expire)
        assert manager.get(sid) is None  # 10 分で停止
        with c.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "resume", "session_id": sid}))
            assert recv_until(ws, "error")["code"] == "unknown_session"


def test_engine_warning_is_sent(tmp_path):
    cfg = Config(output_dir=str(tmp_path / "out"))
    app = create_app(cfg, FakeEngine(), lambda: energy_prob, engine_warning="GPU なし")
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        msg = json.loads(ws.receive_text())
        assert msg == {"type": "status", "level": "warning", "message": "GPU なし"}


def test_start_with_continue_keeps_timeline(setup, tmp_path):
    """サーバーの再起動で記録が失われたとき、ページは時刻の原点と受信位置を引き継いで新しい記録を始める。"""
    _, app, _, _ = setup
    offset = 16000 * 30  # 30 秒の位置から続ける
    audio = np.concatenate([silence(0.5), tone(1), silence(1)])
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "start",
                    "continue": {
                        "previous_session": "old",
                        "wall_start": "2026-10-21T10:00:00.000+09:00",
                        "received": {"me": offset, "remote": offset},
                    },
                }
            )
        )
        started = recv_until(ws, "started")
        assert started["received"] == {"me": offset, "remote": offset}
        assert started["wall_start"].startswith("2026-10-21T10:00:00")
        for f in frames(0, audio, start=offset):
            ws.send_bytes(f)
        ws.send_text(json.dumps({"type": "stop"}))
        seg = recv_until(ws, "segment")
    assert seg["start"] == pytest.approx(30.3, abs=0.1)
    assert seg["wall_start"].startswith("2026-10-21T10:00:30")
    out = next((tmp_path / "out").iterdir())
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["continued_from"] == "old" and meta["gap_seconds"] == {"me": 0.0, "remote": 0.0}
