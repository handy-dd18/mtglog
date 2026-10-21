"""文字起こしワーカー（design 3.3）。

モデルを 1 つだけ読み込み、両チャンネルの発話区間を 1 つのキューで先着順に処理する。
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .segmenter import Segment

log = logging.getLogger(__name__)


class Engine(Protocol):
    description: str

    def transcribe(self, audio: np.ndarray, language: str) -> str: ...


def preload_cuda_libs() -> None:
    """pip で入れた CUDA ライブラリ（nvidia-cublas-cu12 など）を先に読み込む。

    CTranslate2 は libcublas などを名前で dlopen する。LD_LIBRARY_PATH を起動前に設定しなくても
    見つかるように、同じライブラリを RTLD_GLOBAL で読み込んでおく。見つからなければ何もしない
    （Docker のイメージのようにシステムに入っている場合）。
    """
    try:
        import nvidia
    except ImportError:
        return
    order = ["libcublasLt.so", "libcublas.so", "libcudnn"]
    for base in nvidia.__path__:
        libs = sorted(Path(base).glob("*/lib/*.so*"))
        for prefix in order:
            for lib in libs:
                if lib.name.startswith(prefix):
                    try:
                        ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    except OSError as e:
                        log.debug("読み込めないライブラリ %s: %s", lib, e)


class WhisperEngine:
    def __init__(self, model: str, device: str, compute_type: str, download_root: str) -> None:
        from faster_whisper import WhisperModel

        kwargs = {"device": device, "compute_type": compute_type, "download_root": download_root}
        try:
            # 取得済みのモデルがあれば外部へ接続しない（REQ-6.1）
            self.model = WhisperModel(model, local_files_only=True, **kwargs)
        except Exception:  # noqa: BLE001 - 未取得の場合の例外の型はモデルの取得元によって異なる
            log.info("モデル %s を取得します（初回のみ外部へ接続します）", model)
            self.model = WhisperModel(model, **kwargs)
        self.description = f"{model} / {device} / {compute_type}"

    def transcribe(self, audio: np.ndarray, language: str) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language=language,
            vad_filter=False,
            beam_size=1,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return "".join(s.text for s in segments).strip()


def load_engine(cfg) -> tuple[Engine, str | None]:
    """設定に従ってモデルを読み込む。GPU が使えなければ CPU 用の設定に切り替え、警告文を返す（REQ-3.5）。"""
    if cfg.device in ("auto", "cuda"):
        preload_cuda_libs()
        try:
            engine = WhisperEngine(cfg.model, "cuda", cfg.compute_type, cfg.model_dir)
            engine.transcribe(np.zeros(16000, dtype=np.float32), cfg.language)  # 実行時のライブラリ不足をここで検出する
            return engine, None
        except Exception as e:
            if cfg.device == "cuda":
                raise
            warning = f"GPU で文字起こしできないため CPU で動かします（{cfg.cpu_model}）: {e}"
            log.warning(warning)
    else:
        warning = None
    return WhisperEngine(cfg.cpu_model, "cpu", cfg.cpu_compute_type, cfg.model_dir), warning


@dataclass
class Job:
    session_id: str
    channel: int
    segment: Segment
    callback: Callable[[Job, str | None], None]
    language: str | None = None
    enqueued_at: float = field(default_factory=time.monotonic)
    wait_s: float = 0.0
    proc_s: float = 0.0


class TranscriberWorker:
    def __init__(self, engine: Engine, language: str) -> None:
        self.engine = engine
        self.language = language
        self.queue: queue.Queue[Job | None] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="transcriber", daemon=True)
        self.thread.start()

    def submit(self, job: Job) -> None:
        self.queue.put(job)

    def close(self) -> None:
        self.queue.put(None)
        self.thread.join(timeout=30)

    def _run(self) -> None:
        while (job := self.queue.get()) is not None:
            start = time.monotonic()
            job.wait_s = start - job.enqueued_at
            text: str | None
            try:
                text = self.engine.transcribe(job.segment.audio, job.language or self.language)
            except Exception:
                log.exception("文字起こしに失敗しました")
                text = None
            job.proc_s = time.monotonic() - start
            log.info(
                "segment ch=%d %.1f-%.1fs wait=%.2fs proc=%.2fs",
                job.channel,
                job.segment.start_s,
                job.segment.end_s,
                job.wait_s,
                job.proc_s,
            )
            try:
                job.callback(job, text)
            except Exception:
                log.exception("結果の処理に失敗しました")
