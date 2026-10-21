import numpy as np
import pytest

SR = 16000


def tone(seconds: float, amp: float = 0.3, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def energy_prob(window: np.ndarray) -> float:
    """テスト用の VAD: 音量が一定以上なら発話とみなす。"""
    return 1.0 if float(np.sqrt(np.mean(window**2))) > 0.05 else 0.0


class FakeEngine:
    description = "fake"

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = []

    def transcribe(self, audio, language):
        import time

        if self.delay:
            time.sleep(self.delay)
        self.calls.append((len(audio), language))
        return f"len={len(audio)}"


@pytest.fixture
def fake_engine():
    return FakeEngine()
