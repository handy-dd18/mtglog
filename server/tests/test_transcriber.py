"""文字起こしワーカーの順序と、GPU が使えないときの切り替え（T2.2）。"""

import threading

import numpy as np
import pytest

from mtglog import transcriber
from mtglog.config import Config
from mtglog.segmenter import Segment
from mtglog.transcriber import Job, TranscriberWorker, load_engine


class RecordingEngine:
    description = "rec"

    def __init__(self):
        self.seen = []

    def transcribe(self, audio, language):
        self.seen.append((int(audio[0]), language))
        if audio[0] == 99:
            raise RuntimeError("boom")
        return f"t{int(audio[0])}"


def seg(v):
    return Segment(0, 1, np.full(10, v, dtype=np.float32))


def test_worker_processes_in_order_and_survives_errors():
    eng = RecordingEngine()
    w = TranscriberWorker(eng, "ja")
    results, done = [], threading.Event()

    def cb(job, text):
        results.append((job.channel, text))
        if len(results) == 4:
            done.set()

    w.submit(Job("s", 0, seg(1), cb))
    w.submit(Job("s", 1, seg(99), cb))  # 失敗しても後続を処理する
    w.submit(Job("s", 1, seg(2), cb, language="en"))
    w.submit(Job("s", 0, seg(3), cb))
    assert done.wait(5)
    w.close()
    assert results == [(0, "t1"), (1, None), (1, "t2"), (0, "t3")]
    assert eng.seen == [(1, "ja"), (99, "ja"), (2, "en"), (3, "ja")]


class FakeWhisper:
    def __init__(self, model, device, compute_type, model_dir):
        if device == "cuda":
            raise RuntimeError("CUDA driver not found")
        self.description = f"{model} / {device} / {compute_type}"

    def transcribe(self, audio, language):
        return ""


def test_load_engine_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(transcriber, "WhisperEngine", FakeWhisper)
    monkeypatch.setattr(transcriber, "preload_cuda_libs", lambda: None)
    engine, warning = load_engine(Config(device="auto"))
    assert engine.description == "whisper-small / cpu / int8"
    assert "CPU" in warning and "CUDA driver not found" in warning


def test_load_engine_cuda_only_raises(monkeypatch):
    monkeypatch.setattr(transcriber, "WhisperEngine", FakeWhisper)
    monkeypatch.setattr(transcriber, "preload_cuda_libs", lambda: None)
    with pytest.raises(RuntimeError):
        load_engine(Config(device="cuda"))


def test_load_engine_cpu_without_warning(monkeypatch):
    monkeypatch.setattr(transcriber, "WhisperEngine", FakeWhisper)
    engine, warning = load_engine(Config(device="cpu"))
    assert engine.description.startswith("whisper-small / cpu") and warning is None


def test_resolve_model_by_name_or_path(tmp_path):
    d = tmp_path / "whisper-small"
    d.mkdir()
    (d / "model.bin").write_bytes(b"")
    assert transcriber.resolve_model("whisper-small", str(tmp_path)) == d
    assert transcriber.resolve_model(str(d), "/nonexistent") == d


def test_missing_model_is_reported_without_download(tmp_path):
    with pytest.raises(transcriber.ModelNotFoundError, match="setup_models.sh"):
        transcriber.resolve_model("whisper-small", str(tmp_path))


def test_missing_model_is_not_hidden_by_cpu_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(transcriber, "preload_cuda_libs", lambda: None)
    with pytest.raises(transcriber.ModelNotFoundError):
        load_engine(Config(device="auto", model_dir=str(tmp_path)))
