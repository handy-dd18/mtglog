import json
import wave
from datetime import datetime

import numpy as np

from mtglog.writer import Writer, new_output_dir


def test_output_dir_unique(tmp_path):
    now = datetime(2026, 10, 21, 10, 0, 0)
    a = new_output_dir(tmp_path, now)
    b = new_output_dir(tmp_path, now)
    assert a.name == "20261021-100000" and b.name == "20261021-100000-2"


def test_append_and_finalize(tmp_path):
    start = datetime(2026, 10, 21, 10, 0, 0).astimezone()
    w = Writer(tmp_path, start, save_audio=False, meta={"language": "ja"})
    w.add(w.make_record(5.0, 6.0, "remote", "二番目"))
    # 追記の直後にファイルへ反映されている（REQ-4.2）
    assert len((tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert "二番目" in (tmp_path / "transcript.md").read_text(encoding="utf-8")
    w.add(w.make_record(1.5, 2.0, "me", "一番目"))
    lines = (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    assert rec == {
        "start": 1.5,
        "end": 2.0,
        "speaker": "me",
        "text": "一番目",
        "wall_start": start.replace(microsecond=500000, second=1).isoformat(timespec="milliseconds"),
    }
    w.close()
    md = (tmp_path / "transcript.md").read_text(encoding="utf-8").splitlines()
    body = [line for line in md if line.startswith("- ")]
    assert body == ["- `00:00:01` **自分**: 一番目", "- `00:00:05` **相手**: 二番目"]  # 停止時に開始時刻順
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["segments"] == 2 and "ended_at" in meta and meta["language"] == "ja"
    assert not (tmp_path / "me.wav").exists()


def test_wav(tmp_path):
    w = Writer(tmp_path, datetime.now().astimezone(), save_audio=True, meta={})
    w.write_audio(0, np.arange(1600, dtype=np.int16))
    w.write_audio(1, np.zeros(3200, dtype=np.int16))
    w.close()
    for name, n in (("me", 1600), ("remote", 3200)):
        with wave.open(str(tmp_path / f"{name}.wav")) as r:
            assert (r.getframerate(), r.getnchannels(), r.getsampwidth(), r.getnframes()) == (16000, 1, 2, n)
