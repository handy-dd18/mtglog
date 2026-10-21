"""T0.1 検証用サーバー。

取得ページを配信し、WebSocket で受けた音声フレームのバイト数と受信時刻、
ページから届いたイベントを results/ に記録する。文字起こしはしない。

音声フレームの形式は design.md 4.1 と同じ:
    [0] uint8 チャンネル (0: 自分, 1: 相手)
    [1:4] 予約
    [4:12] uint64 LE 先頭標本の位置
    [12:] int16 LE 標本
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"
HEADER = struct.Struct("<B3xQ")
CHANNEL_NAMES = {0: "me", 1: "remote"}

app = FastAPI()


@dataclass
class ChannelStats:
    frames: int = 0
    samples: int = 0
    bytes: int = 0
    next_pos: int | None = None
    gaps: int = 0
    gap_samples: int = 0
    overlaps: int = 0
    last_arrival: float | None = None
    max_interval_ms: float = 0.0

    def add(self, pos: int, n: int, size: int, now: float) -> None:
        self.frames += 1
        self.samples += n
        self.bytes += size
        if self.next_pos is not None:
            if pos > self.next_pos:
                self.gaps += 1
                self.gap_samples += pos - self.next_pos
            elif pos < self.next_pos:
                self.overlaps += 1
        self.next_pos = max(self.next_pos or 0, pos + n)
        if self.last_arrival is not None:
            self.max_interval_ms = max(self.max_interval_ms, (now - self.last_arrival) * 1000)
        self.last_arrival = now

    def summary(self) -> dict:
        return {
            "frames": self.frames,
            "seconds": round(self.samples / 16000, 2),
            "bytes": self.bytes,
            "next_pos": self.next_pos,
            "gaps": self.gaps,
            "gap_seconds": round(self.gap_samples / 16000, 3),
            "overlaps": self.overlaps,
            "max_interval_ms": round(self.max_interval_ms, 1),
        }


@dataclass
class Run:
    path: Path
    started: float = field(default_factory=time.time)
    channels: dict[int, ChannelStats] = field(default_factory=dict)

    def log(self, record: dict) -> None:
        record = {"t": round(time.time() - self.started, 3), **record}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        return {CHANNEL_NAMES.get(ch, str(ch)): s.summary() for ch, s in sorted(self.channels.items())}


def new_run() -> Run:
    RESULTS_DIR.mkdir(exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Run(path=RESULTS_DIR / f"{name}.jsonl")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    run = new_run()
    run.log({"event": "connected", "client": ws.client.host if ws.client else None})

    async def report() -> None:
        while True:
            await asyncio.sleep(1)
            await ws.send_json({"type": "stats", "elapsed": round(time.time() - run.started, 1), "channels": run.summary()})

    reporter = asyncio.create_task(report())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                if len(data) < HEADER.size:
                    run.log({"event": "bad_frame", "size": len(data)})
                    continue
                ch, pos = HEADER.unpack_from(data)
                n = (len(data) - HEADER.size) // 2
                run.channels.setdefault(ch, ChannelStats()).add(pos, n, len(data), time.time())
            elif (text := msg.get("text")) is not None:
                run.log({"event": "page", **json.loads(text)})
    except WebSocketDisconnect:
        pass
    finally:
        reporter.cancel()
        run.log({"event": "disconnected", "summary": run.summary()})


@app.post("/api/report")
async def save_report(request: Request) -> JSONResponse:
    """ページで「結果を保存」を押したときの検証結果（V1〜V7 の記入内容と環境情報）を保存する。"""
    RESULTS_DIR.mkdir(exist_ok=True)
    body = await request.json()
    path = RESULTS_DIR / f"report-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"saved": str(path.relative_to(HERE))})


app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="T0.1 検証用サーバー")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"検証用ページ: http://localhost:{args.port}/  （Windows の Edge で開く）", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
