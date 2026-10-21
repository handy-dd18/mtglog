"""無音の警告の判定（T5.1）。"""

import json

import numpy as np
from conftest import FakeEngine, energy_prob, silence, tone
from fastapi.testclient import TestClient
from test_app import frames, recv_until

from mtglog.app import create_app
from mtglog.config import Config
from mtglog.segmenter import Segmenter
from mtglog.silence import SilenceMonitor

SR = 16000
ME, REMOTE = 0, 1


def sec(s: float) -> int:
    return int(s * SR)


def kinds(msgs):
    return [(m["kind"], m["active"]) for m in msgs]


def test_remote_warns_after_limit_from_start():
    m = SilenceMonitor(10)
    voice = {ME: sec(5), REMOTE: None}
    assert m.update(sec(9.9), voice) == []
    assert kinds(m.update(sec(10), voice)) == [("remote", True)]
    assert m.update(sec(12), voice) == []  # 状態が変わらなければ送らない


def test_remote_clears_on_voice_and_stays_off_until_device_change():
    m = SilenceMonitor(10)
    m.update(sec(11), {ME: sec(10.5), REMOTE: None})
    assert m.active["remote"]
    assert kinds(m.update(sec(12), {ME: sec(10.5), REMOTE: sec(12)})) == [("remote", False)]
    # 相手の発話を検出した後は、相手が長く黙っても出さない
    assert m.update(sec(100), {ME: sec(99), REMOTE: sec(12)}) == []
    m.device_changed(sec(100))
    assert m.update(sec(109), {ME: sec(108), REMOTE: sec(12)}) == []
    assert kinds(m.update(sec(110), {ME: sec(109), REMOTE: sec(12)})) == [("remote", True)]


def test_device_change_resets_active_remote_warning():
    m = SilenceMonitor(10)
    m.update(sec(10), {ME: sec(9), REMOTE: None})
    m.device_changed(sec(10))
    assert kinds(m.update(sec(10), {ME: sec(9), REMOTE: None})) == [("remote", False)]


def test_both_warns_when_nobody_speaks_and_clears_on_any_voice():
    m = SilenceMonitor(10)
    # 相手の発話を検出した後なので remote は出ない。両方の無音だけを見る
    assert m.update(sec(5), {ME: None, REMOTE: sec(5)}) == []
    assert m.update(sec(14.9), {ME: None, REMOTE: sec(5)}) == []
    assert kinds(m.update(sec(15), {ME: None, REMOTE: sec(5)})) == [("both", True)]
    assert kinds(m.update(sec(16), {ME: sec(16), REMOTE: sec(5)})) == [("both", False)]


def test_both_and_remote_can_be_active_together():
    m = SilenceMonitor(10)
    assert kinds(m.update(sec(10), {ME: None, REMOTE: None})) == [("remote", True), ("both", True)]
    assert m.active_messages() == [
        {"type": "silence", "kind": "remote", "active": True, "seconds": 10},
        {"type": "silence", "kind": "both", "active": True, "seconds": 10},
    ]


def test_origin_is_used_for_continued_session():
    m = SilenceMonitor(10, origin=sec(100))
    assert m.update(sec(109), {ME: None, REMOTE: None}) == []
    assert len(m.update(sec(110), {ME: None, REMOTE: None})) == 2


def test_zero_disables():
    m = SilenceMonitor(0)
    assert m.update(sec(1000), {ME: None, REMOTE: None}) == []


def test_segmenter_records_last_voice():
    s = Segmenter(energy_prob)
    s.push(np.concatenate([silence(1), tone(0.5), silence(1)]))
    assert s.last_voice is not None
    assert sec(1.4) <= s.last_voice <= sec(1.6)


def _app(tmp_path, limit):
    cfg = Config(output_dir=str(tmp_path / "out"), silence_warning_sec=limit)
    return create_app(cfg, FakeEngine(), lambda: energy_prob)


def test_websocket_sends_silence_messages(tmp_path):
    # 自分だけが話し、相手の音声が無音のまま 3 秒続くと remote が出る。片方しか発話がなくても時間は進む
    with TestClient(_app(tmp_path, 3)) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        recv_until(ws, "started")
        for f in frames(ME, np.concatenate([tone(1), silence(0.5), tone(1), silence(0.5), tone(1)])):
            ws.send_bytes(f)
        msg = recv_until(ws, "silence")
        assert (msg["kind"], msg["active"], msg["seconds"]) == ("remote", True, 3)
        # 相手が話すと消える
        for f in frames(REMOTE, np.concatenate([silence(3.5), tone(0.5)])):
            ws.send_bytes(f)
        msg = recv_until(ws, "silence")
        assert (msg["kind"], msg["active"]) == ("remote", False)
        ws.send_text(json.dumps({"type": "stop"}))
        recv_until(ws, "stopped")


def test_websocket_device_changed_rearms_remote(tmp_path):
    with TestClient(_app(tmp_path, 2)) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        recv_until(ws, "started")
        for f in frames(REMOTE, tone(1)):
            ws.send_bytes(f)
        for f in frames(ME, np.concatenate([tone(1), tone(1.5)])):
            ws.send_bytes(f)
        ws.send_text(json.dumps({"type": "device_changed"}))
        for f in frames(ME, tone(2.5), start=sec(2.5)):
            ws.send_bytes(f)
        msg = recv_until(ws, "silence")
        assert (msg["kind"], msg["active"]) == ("remote", True)
        ws.send_text(json.dumps({"type": "stop"}))
        recv_until(ws, "stopped")
