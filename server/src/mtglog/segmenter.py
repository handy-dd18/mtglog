"""チャンネルごとの発話区間の切り出し（design 3.2）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .frames import SAMPLE_RATE
from .vad import WINDOW


@dataclass(frozen=True)
class Segment:
    start: int  # 記録の原点からの標本位置
    end: int
    audio: np.ndarray  # float32, [-1, 1]

    @property
    def start_s(self) -> float:
        return self.start / SAMPLE_RATE

    @property
    def end_s(self) -> float:
        return self.end / SAMPLE_RATE


@dataclass
class SegmenterParams:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    min_silence_ms: int = 600
    pad_ms: int = 200
    max_segment_ms: int = 20000
    min_segment_ms: int = 250

    @classmethod
    def from_config(cls, cfg) -> SegmenterParams:
        return cls(
            start_threshold=cfg.vad_start_threshold,
            end_threshold=cfg.vad_end_threshold,
            min_silence_ms=cfg.vad_min_silence_ms,
            pad_ms=cfg.vad_pad_ms,
            max_segment_ms=cfg.vad_max_segment_ms,
            min_segment_ms=cfg.vad_min_segment_ms,
        )


def _ms(ms: int) -> int:
    return ms * SAMPLE_RATE // 1000


class Segmenter:
    """連続音声を 512 標本の窓ごとに評価し、確定した発話区間を返す。

    prob_fn は窓（float32 の 512 標本）を受け取って発話確率を返す関数。実運用では SileroVAD、
    テストでは音量から確率を作る関数を渡す。
    """

    def __init__(
        self, prob_fn: Callable[[np.ndarray], float], params: SegmenterParams | None = None, start: int = 0
    ) -> None:
        self.prob_fn = prob_fn
        self.p = params or SegmenterParams()
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_start = start  # buf[0] の標本位置
        self.eval_pos = start  # 次に評価する窓の先頭位置
        self.in_speech = False
        self.seg_start = 0
        self.speech_start = 0
        self.silence_run = 0
        self.probs: list[tuple[int, float]] = []

    @property
    def buf_end(self) -> int:
        return self.buf_start + len(self.buf)

    def push(self, samples: np.ndarray) -> list[Segment]:
        if samples.dtype == np.int16:
            samples = samples.astype(np.float32) / 32768.0
        self.buf = np.concatenate([self.buf, samples.astype(np.float32, copy=False)])
        out: list[Segment] = []
        while self.eval_pos + WINDOW <= self.buf_end:
            i = self.eval_pos - self.buf_start
            prob = self.prob_fn(self.buf[i : i + WINDOW])
            self._step(self.eval_pos, prob, out)
            self.eval_pos += WINDOW
        self._trim()
        return out

    def flush(self) -> list[Segment]:
        """記録の停止時に、進行中の発話を区間として確定する。"""
        out: list[Segment] = []
        if self.in_speech and self.buf_end - self.speech_start >= _ms(self.p.min_segment_ms):
            self._emit(self.seg_start, self.buf_end, out)
        self.in_speech = False
        self._trim()
        return out

    def _step(self, pos: int, prob: float, out: list[Segment]) -> None:
        p = self.p
        if not self.in_speech:
            if prob >= p.start_threshold:
                self.in_speech = True
                self.seg_start = max(self.buf_start, pos - _ms(p.pad_ms))
                self.speech_start = pos
                self.silence_run = 0
                self.probs = [(pos, prob)]
            return

        self.probs.append((pos, prob))
        self.silence_run = self.silence_run + WINDOW if prob < p.end_threshold else 0
        if self.silence_run >= _ms(p.min_silence_ms):
            speech_end = pos + WINDOW - self.silence_run
            # 最小長は余白を除いた発話部分で判定する（短い物音を捨てる）
            if speech_end - self.speech_start >= _ms(p.min_segment_ms):
                self._emit(self.seg_start, speech_end + _ms(p.pad_ms), out)
            self.in_speech = False
        elif pos + WINDOW - self.seg_start >= _ms(p.max_segment_ms):
            # 区間の後半で発話確率が最も低い窓の位置で区切る
            half = self.seg_start + _ms(p.max_segment_ms) // 2
            _, cut = min((pr, q) for q, pr in self.probs if q >= half)
            if cut <= self.seg_start:
                cut = pos + WINDOW
            self._emit(self.seg_start, cut, out)
            self.seg_start = self.speech_start = cut
            self.probs = [(q, pr) for q, pr in self.probs if q >= cut]

    def _emit(self, start: int, end: int, out: list[Segment]) -> None:
        end = min(end, self.buf_end)
        if end <= start:
            return
        a = self.buf[start - self.buf_start : end - self.buf_start].copy()
        out.append(Segment(start, end, a))

    def _trim(self) -> None:
        keep_from = self.seg_start if self.in_speech else self.eval_pos - _ms(self.p.pad_ms)
        keep_from = max(self.buf_start, keep_from)
        if keep_from > self.buf_start:
            self.buf = self.buf[keep_from - self.buf_start :]
            self.buf_start = keep_from
