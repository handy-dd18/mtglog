"""結果の書き出し（design 3.4）。"""

from __future__ import annotations

import json
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .frames import CHANNEL_NAMES, SAMPLE_RATE

SPEAKER_LABELS = {"me": "自分", "remote": "相手"}


def new_output_dir(root: Path, now: datetime) -> Path:
    base = root / now.strftime("%Y%m%d-%H%M%S")
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    path.mkdir(parents=True)
    return path


def fmt_offset(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def md_line(rec: dict) -> str:
    return f"- `{fmt_offset(rec['start'])}` **{SPEAKER_LABELS.get(rec['speaker'], rec['speaker'])}**: {rec['text']}\n"


class Writer:
    def __init__(self, out_dir: Path, wall_start: datetime, save_audio: bool, meta: dict) -> None:
        self.dir = out_dir
        self.wall_start = wall_start
        self.records: list[dict] = []
        self.meta = {"started_at": wall_start.isoformat(timespec="seconds"), **meta}
        self._write_meta()
        self.jsonl = (out_dir / "transcript.jsonl").open("a", encoding="utf-8")
        self.md = (out_dir / "transcript.md").open("a", encoding="utf-8")
        self.md.write(self._md_header())
        self.md.flush()
        self.wavs: dict[int, wave.Wave_write] = {}
        if save_audio:
            for ch, name in CHANNEL_NAMES.items():
                w = wave.open(str(out_dir / f"{name}.wav"), "wb")  # noqa: SIM115 - 記録中は開いたままにし close() で閉じる
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                self.wavs[ch] = w

    def _md_header(self) -> str:
        return f"# 文字起こし {self.wall_start:%Y-%m-%d %H:%M}\n\n"

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def make_record(self, start: float, end: float, speaker: str, text: str) -> dict:
        wall = self.wall_start + timedelta(seconds=start)
        return {
            "start": round(start, 2),
            "end": round(end, 2),
            "speaker": speaker,
            "text": text,
            "wall_start": wall.isoformat(timespec="milliseconds"),
        }

    def add(self, rec: dict) -> None:
        """発話が確定するたびに追記する（REQ-4.2）。"""
        self.records.append(rec)
        self.jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.jsonl.flush()
        self.md.write(md_line(rec))
        self.md.flush()

    def write_audio(self, channel: int, samples: np.ndarray) -> None:
        if w := self.wavs.get(channel):
            w.writeframes(samples.astype("<i2").tobytes())

    def close(self, **meta) -> None:
        """停止時に Markdown を開始時刻順に書き直し、メタ情報を確定する。"""
        for w in self.wavs.values():
            w.close()
        self.jsonl.close()
        self.md.close()
        ordered = sorted(self.records, key=lambda r: (r["start"], r["speaker"]))
        (self.dir / "transcript.md").write_text(
            self._md_header() + "".join(md_line(r) for r in ordered), encoding="utf-8"
        )
        self.meta.update(meta)
        self.meta["ended_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.meta["segments"] = len(self.records)
        self._write_meta()
