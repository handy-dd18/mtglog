"""無音の警告の判定（design 3.6）。

時間は受信した音声の標本位置で数える。発話の有無は Segmenter が記録した「最後に発話を検出した位置」を使う。
"""

from __future__ import annotations

from .frames import CHANNEL_NAMES, SAMPLE_RATE

REMOTE = next(ch for ch, name in CHANNEL_NAMES.items() if name == "remote")


class SilenceMonitor:
    """`remote`（相手だけが無音、REQ-2.7）と `both`（両方が無音、REQ-2.8）の警告を判定する。"""

    def __init__(self, limit_sec: float, origin: int = 0) -> None:
        self.limit = int(limit_sec * SAMPLE_RATE)
        self.seconds = limit_sec
        self.origin = origin
        # remote の起点。相手の発話を検出したら None にし、次の device_changed まで判定しない
        self.remote_from: int | None = origin
        self.active = {"remote": False, "both": False}

    def device_changed(self, now: int) -> None:
        self.remote_from = now

    def update(self, now: int, last_voice: dict[int, int | None]) -> list[dict]:
        """現在位置と、チャンネルごとの最後に発話を検出した位置から判定し、状態が変わった警告を返す。"""
        if self.limit <= 0:
            return []
        remote_voice = last_voice.get(REMOTE)
        if self.remote_from is not None and remote_voice is not None and remote_voice > self.remote_from:
            self.remote_from = None
        remote = self.remote_from is not None and now - self.remote_from >= self.limit

        voices = [v for v in last_voice.values() if v is not None]
        both_from = max([self.origin, *voices])
        both = now - both_from >= self.limit

        changed = []
        for kind, value in (("remote", remote), ("both", both)):
            if self.active[kind] != value:
                self.active[kind] = value
                changed.append(self.message(kind))
        return changed

    def message(self, kind: str) -> dict:
        return {"type": "silence", "kind": kind, "active": self.active[kind], "seconds": self.seconds}

    def active_messages(self) -> list[dict]:
        return [self.message(kind) for kind, value in self.active.items() if value]
