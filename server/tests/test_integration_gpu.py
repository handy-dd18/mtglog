"""実モデル（faster-whisper）と Silero VAD を使う結合テスト（T2.2, T2.4）。

モデルの読み込みに時間がかかるため、MTGLOG_GPU_TESTS=1 のときだけ実行する。
テスト音声 tests/data/ja_*.wav は Windows の音声合成（Microsoft Haruka）で作った。
"""

import json
import os
import statistics
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mtglog.app import create_app
from mtglog.config import Config
from mtglog.frames import build_frame
from mtglog.segmenter import Segmenter
from mtglog.transcriber import load_engine
from mtglog.vad import SileroVAD

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(os.environ.get("MTGLOG_GPU_TESTS") != "1", reason="MTGLOG_GPU_TESTS=1 のときだけ実行"),
]

DATA = Path(__file__).parent / "data"
MODEL_DIR = str(Path(__file__).resolve().parents[2] / "models")
SR = 16000


def load_wav(name: str) -> np.ndarray:
    with wave.open(str(DATA / name)) as r:
        assert r.getframerate() == SR and r.getnchannels() == 1
        return np.frombuffer(r.readframes(r.getnframes()), "<i2").astype(np.float32) / 32768


@pytest.fixture(scope="module")
def engine():
    eng, _warning = load_engine(Config(model_dir=MODEL_DIR))
    return eng


def test_silero_segments_speech():
    audio = np.concatenate([np.zeros(SR), load_wav("ja_me.wav"), np.zeros(2 * SR)]).astype(np.float32)
    seg = Segmenter(SileroVAD())
    out = []
    for i in range(0, len(audio), 1600):
        out += seg.push(audio[i : i + 1600])
    out += seg.flush()
    assert 1 <= len(out) <= 3
    assert out[0].start_s == pytest.approx(1.0, abs=0.5)


def test_engine_transcribes_japanese(engine):
    text = engine.transcribe(load_wav("ja_me.wav"), "ja")
    assert "会議" in text and "リリース" in text


def test_realtime_pipeline_latency(engine, tmp_path):
    """2 チャンネルの音声を実時間で流し、発話の終わりから segment が届くまでの遅延を測る（REQ-3.3）。"""
    me, remote = load_wav("ja_me.wav"), load_wav("ja_remote.wav")
    total = int(SR * (1 + len(me) / SR + 1 + len(remote) / SR + 3))
    ch = {0: np.zeros(total, np.float32), 1: np.zeros(total, np.float32)}
    me_at, remote_at = SR, SR + len(me) + SR
    ch[0][me_at : me_at + len(me)] = me
    ch[1][remote_at : remote_at + len(remote)] = remote
    speech_end = {"me": (me_at + len(me)) / SR, "remote": (remote_at + len(remote)) / SR}

    cfg = Config(output_dir=str(tmp_path / "out"), model_dir=MODEL_DIR)
    app = create_app(cfg, engine, SileroVAD)
    latencies, texts = {}, {}
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws.receive_text())["type"] == "started"
        t0 = time.monotonic()
        import threading

        def reader():
            while True:
                msg = json.loads(ws.receive_text())
                if msg["type"] == "segment":
                    latencies[msg["speaker"]] = time.monotonic() - t0 - speech_end[msg["speaker"]]
                    texts[msg["speaker"]] = texts.get(msg["speaker"], "") + msg["text"]
                if msg["type"] == "stopped":
                    return

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        for i in range(0, total, 1600):
            # 実時間に合わせて 100 ミリ秒ごとに送る
            delay = t0 + i / SR - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            for k in (0, 1):
                ws.send_bytes(build_frame(k, i, (ch[k][i : i + 1600] * 32767).astype(np.int16)))
        ws.send_text(json.dumps({"type": "stop"}))
        th.join(timeout=30)

    print("latency:", latencies, "texts:", texts)
    assert set(texts) == {"me", "remote"}
    assert "会議" in texts["me"] and "金曜日" in texts["remote"]
    assert statistics.median(latencies.values()) <= 5.0
