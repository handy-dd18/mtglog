"""音声フレームの解釈と、チャンネルごとの連続音声への組み立て（design 4.1）。"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

HEADER = struct.Struct("<B3xQ")
SAMPLE_RATE = 16000
CHANNEL_NAMES = {0: "me", 1: "remote"}


@dataclass(frozen=True)
class Frame:
    channel: int
    pos: int
    samples: np.ndarray  # int16


def parse_frame(data: bytes) -> Frame:
    if len(data) < HEADER.size or (len(data) - HEADER.size) % 2:
        raise ValueError(f"不正なフレーム長: {len(data)}")
    channel, pos = HEADER.unpack_from(data)
    if channel not in CHANNEL_NAMES:
        raise ValueError(f"不正なチャンネル: {channel}")
    samples = np.frombuffer(data, dtype="<i2", offset=HEADER.size).astype(np.int16)
    return Frame(channel, pos, samples)


def build_frame(channel: int, pos: int, samples: np.ndarray) -> bytes:
    """テスト用クライアントのためのフレーム組み立て。"""
    return HEADER.pack(channel, pos) + np.asarray(samples, dtype="<i2").tobytes()


class ChannelAssembler:
    """標本位置つきのフレームを、位置 0 から連続した音声に組み立てる。

    1 本の WebSocket の中では順序が保たれ、再接続後はページが受信済み位置の続きから送り直す。
    そのため、位置が飛んだ区間は無音で埋めて先へ進め、受信済みの位置より前の標本は重複として捨てる。
    """

    def __init__(self, start: int = 0) -> None:
        self.received = start  # 次に期待する標本位置
        self.gap_samples = 0
        self.dropped_samples = 0
        self._first = True

    def push(self, pos: int, samples: np.ndarray) -> np.ndarray:
        """新しく確定した連続音声（int16）を返す。"""
        end = pos + len(samples)
        if end <= self.received:
            self.dropped_samples += len(samples)
            return samples[:0]
        parts = []
        if pos > self.received:
            gap = pos - self.received
            if not self._first:  # 記録開始直後の 1 フレーム未満のずれは欠落に数えない
                self.gap_samples += gap
            parts.append(np.zeros(gap, dtype=np.int16))
        elif pos < self.received:
            cut = self.received - pos
            self.dropped_samples += cut
            samples = samples[cut:]
        parts.append(samples)
        self.received = end
        self._first = False
        return parts[0] if len(parts) == 1 else np.concatenate(parts)
