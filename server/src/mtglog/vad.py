"""Silero VAD を 512 標本ずつ逐次評価する。

モデルは faster-whisper に同梱されている ONNX ファイルを使う。入力は直前の窓の末尾 64 標本を
前置した 576 標本で、内部状態 h, c を窓ごとに引き継ぐ。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

WINDOW = 512
CONTEXT = 64


def _model_path() -> Path:
    import faster_whisper

    assets = Path(faster_whisper.__file__).parent / "assets"
    found = sorted(assets.glob("silero_vad*.onnx"))
    if not found:
        raise FileNotFoundError(f"Silero VAD のモデルが見つかりません: {assets}")
    return found[-1]


class SileroVAD:
    def __init__(self) -> None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            str(_model_path()), providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, window: np.ndarray) -> float:
        """float32 の 512 標本を受け取り、発話確率を返す。"""
        x = np.concatenate([self.context, window.astype(np.float32)])[None, :]
        out, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = x[0, -CONTEXT:]
        return float(out[0])
