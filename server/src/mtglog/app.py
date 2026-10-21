"""FastAPI のアプリと、記録 1 回分の状態管理（design 3.1, 4.2）。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Config
from .frames import CHANNEL_NAMES, ChannelAssembler, parse_frame
from .segmenter import Segment, Segmenter, SegmenterParams
from .silence import SilenceMonitor
from .transcriber import Engine, Job, TranscriberWorker
from .writer import SPEAKER_LABELS, Writer, fmt_offset, new_output_dir

log = logging.getLogger(__name__)

WEB_DIR = Path(os.environ.get("MTGLOG_WEB_DIR") or Path(__file__).resolve().parents[3] / "web")
ACK_INTERVAL_S = 2.0


class Session:
    def __init__(self, cfg: Config, worker: TranscriberWorker, vad_factory: Callable, options: dict) -> None:
        self.id = uuid.uuid4().hex
        self.cfg = cfg
        self.worker = worker
        self.loop = asyncio.get_running_loop()
        now = datetime.now().astimezone()
        self.out_dir = new_output_dir(Path(cfg.output_dir), now)
        # サーバーの再起動などで以前の記録を引き継ぐ場合（design 4.2）は、時刻の原点と受信位置を引き継ぐ
        cont = options.get("continue") or {}
        wall_start = datetime.fromisoformat(cont["wall_start"]) if cont.get("wall_start") else now
        start_pos = cont.get("received") or {}
        save_audio = bool(options.get("save_audio", cfg.save_audio))
        self.language = options.get("language") or cfg.language
        self.wall_start = wall_start
        self.writer = Writer(
            self.out_dir,
            wall_start,
            save_audio,
            meta={
                "devices": options.get("devices"),
                "language": self.language,
                "engine": worker.engine.description,
                "save_audio": save_audio,
                "continued_from": cont.get("previous_session"),
            },
        )
        params = SegmenterParams.from_config(cfg)
        starts = {ch: int(start_pos.get(name, 0)) for ch, name in CHANNEL_NAMES.items()}
        self.assemblers = {ch: ChannelAssembler(starts[ch]) for ch in CHANNEL_NAMES}
        self.segmenters = {ch: Segmenter(vad_factory(), params, start=starts[ch]) for ch in CHANNEL_NAMES}
        self.silence = SilenceMonitor(cfg.silence_warning_sec, origin=max(starts.values()))
        self.ws: WebSocket | None = None
        self.disconnected_at: float | None = None
        self.pending = 0
        self.stopping = False
        self.done = asyncio.Event()

    def received(self) -> dict[str, int]:
        return {CHANNEL_NAMES[ch]: a.received for ch, a in self.assemblers.items()}

    def feed(self, data: bytes) -> None:
        if self.stopping:
            return
        frame = parse_frame(data)
        new = self.assemblers[frame.channel].push(frame.pos, frame.samples)
        if len(new) == 0:
            return
        self.writer.write_audio(frame.channel, new)
        for seg in self.segmenters[frame.channel].push(new):
            self._enqueue(frame.channel, seg)
        self._check_silence()

    def now(self) -> int:
        return max(a.received for a in self.assemblers.values())

    def device_changed(self) -> None:
        self.silence.device_changed(self.now())
        self._check_silence()

    def _check_silence(self) -> None:
        last_voice = {ch: s.last_voice for ch, s in self.segmenters.items()}
        for msg in self.silence.update(self.now(), last_voice):
            self.send(msg)

    def _enqueue(self, channel: int, seg: Segment) -> None:
        self.pending += 1
        self.worker.submit(Job(self.id, channel, seg, self._on_result_threadsafe, language=self.language))

    def _on_result_threadsafe(self, job: Job, text: str | None) -> None:
        self.loop.call_soon_threadsafe(self._on_result, job, text)

    def _on_result(self, job: Job, text: str | None) -> None:
        self.pending -= 1
        if text:
            speaker = CHANNEL_NAMES[job.channel]
            rec = self.writer.make_record(job.segment.start_s, job.segment.end_s, speaker, text)
            self.writer.add(rec)
            print(f"[{fmt_offset(rec['start'])}] {SPEAKER_LABELS[speaker]}: {text}", flush=True)
            self.send({"type": "segment", **rec})
        if self.stopping and self.pending == 0:
            self._finalize()

    def send(self, msg: dict) -> None:
        if self.ws is not None:
            asyncio.ensure_future(_safe_send(self.ws, msg))

    async def stop(self) -> None:
        """記録を停止する。進行中の発話を確定し、文字起こしが終わるのを待ってから出力を確定する。"""
        if not self.stopping:
            self.stopping = True
            for ch, seg_r in self.segmenters.items():
                for seg in seg_r.flush():
                    self._enqueue(ch, seg)
            if self.pending == 0:
                self._finalize()
        await self.done.wait()

    def _finalize(self) -> None:
        if self.done.is_set():
            return
        gaps = {CHANNEL_NAMES[ch]: round(a.gap_samples / 16000, 2) for ch, a in self.assemblers.items()}
        self.writer.close(gap_seconds=gaps)
        self.done.set()
        print(f"記録を終了しました: {self.out_dir}", flush=True)


async def _safe_send(ws: WebSocket, msg: dict) -> None:
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps(msg, ensure_ascii=False))


class SessionManager:
    def __init__(self, cfg: Config, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.clock = clock
        self.sessions: dict[str, Session] = {}

    def add(self, s: Session) -> None:
        self.sessions[s.id] = s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    async def remove(self, s: Session) -> None:
        await s.stop()
        self.sessions.pop(s.id, None)

    async def expire(self) -> None:
        """切断されたまま期限を過ぎた記録を停止する（design 4.2）。"""
        now = self.clock()
        for s in list(self.sessions.values()):
            if s.ws is None and s.disconnected_at is not None and now - s.disconnected_at >= self.cfg.resume_timeout_s:
                log.warning("再接続がないため記録を停止します: %s", s.id)
                await self.remove(s)

    async def stop_all(self) -> None:
        for s in list(self.sessions.values()):
            await self.remove(s)


def create_app(
    cfg: Config,
    engine: Engine,
    vad_factory: Callable,
    engine_warning: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    manager = SessionManager(cfg, clock)
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["worker"] = TranscriberWorker(engine, cfg.language)

        async def expire_loop() -> None:
            while True:
                await asyncio.sleep(15)
                await manager.expire()

        task = asyncio.create_task(expire_loop())
        try:
            yield
        finally:
            task.cancel()
            await manager.stop_all()
            state["worker"].close()

    app = FastAPI(lifespan=lifespan)
    app.state.manager = manager

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        return JSONResponse(
            {
                "language": cfg.language,
                "headset_keywords": cfg.headset_keywords,
                "save_audio": cfg.save_audio,
                "engine": engine.description,
                "warning": engine_warning,
            }
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        session: Session | None = None
        if engine_warning:
            await _safe_send(ws, {"type": "status", "level": "warning", "message": engine_warning})

        async def ack_loop() -> None:
            while True:
                await asyncio.sleep(ACK_INTERVAL_S)
                if session is not None and not session.stopping:
                    await ws.send_text(json.dumps({"type": "ack", "received": session.received()}))

        acker = asyncio.create_task(ack_loop())
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if (data := msg.get("bytes")) is not None:
                    if session is None:
                        continue
                    try:
                        session.feed(data)
                    except ValueError as e:
                        log.warning("フレームを捨てました: %s", e)
                    continue
                req = json.loads(msg.get("text") or "{}")
                kind = req.get("type")
                if kind == "start":
                    if session is not None:
                        await manager.remove(session)
                    opts = {
                        "save_audio": req.get("save_audio"),
                        "devices": req.get("devices"),
                        "language": req.get("language"),
                        "continue": req.get("continue"),
                    }
                    if opts["save_audio"] is None:
                        opts["save_audio"] = cfg.save_audio
                    session = Session(cfg, state["worker"], vad_factory, opts)
                    session.ws = ws
                    manager.add(session)
                    print(f"記録を開始しました: {session.out_dir}", flush=True)
                    await ws.send_text(json.dumps(_started(session)))
                elif kind == "resume":
                    found = manager.get(req.get("session_id", ""))
                    if found is None or found.stopping:
                        await ws.send_text(json.dumps({"type": "error", "code": "unknown_session"}))
                        continue
                    session = found
                    session.ws = ws
                    session.disconnected_at = None
                    log.info("記録を再開しました: %s", session.id)
                    await ws.send_text(json.dumps(_started(session)))
                    for m in session.silence.active_messages():
                        await _safe_send(ws, m)
                elif kind == "device_changed":
                    if session is not None:
                        session.device_changed()
                elif kind == "stop":
                    if session is None:
                        continue
                    s, session = session, None
                    await manager.remove(s)
                    await ws.send_text(
                        json.dumps({"type": "stopped", "output_dir": str(s.out_dir), "segments": len(s.writer.records)})
                    )
        except WebSocketDisconnect:
            pass
        finally:
            acker.cancel()
            if session is not None and session.ws is ws:
                session.ws = None
                session.disconnected_at = manager.clock()
                log.warning("ページとの接続が切れました。%d 秒間再接続を待ちます", cfg.resume_timeout_s)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _started(s: Session) -> dict:
    return {
        "type": "started",
        "session_id": s.id,
        "received": s.received(),
        "output_dir": str(s.out_dir),
        "wall_start": s.wall_start.isoformat(timespec="milliseconds"),
    }
