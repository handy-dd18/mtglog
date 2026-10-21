// 取得ページ（design 2章）。マイクと画面共有の音声を取得し、WebSocket で文字起こしサーバーへ送る。
import {
  CHANNELS, SPEAKER_LABELS, SAMPLE_RATE, SendBuffer, backoffDelay, buildFrame, chooseInitialMic, defaultOutputInfo,
  fmtOffset, insertIndex,
} from "./lib.js";

const $ = (id) => document.getElementById(id);
const MIC_KEY = "mtglog-mic";

let config = { language: "ja", headset_keywords: [], save_audio: false };
let devices = [];
let ctx = null;
let sink = null;
const channels = { [CHANNELS.me]: null, [CHANNELS.remote]: null };
let displayStream = null;

// 記録の状態: idle / recording / reconnecting / stopping
let state = "idle";
let ws = null;
let sessionId = null;
let origin = null; // 記録の原点（AudioContext の標本位置）
let buffer = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let stopRequested = false;
let startedAt = 0;
let wallStart = null; // 記録の原点の壁時計（サーバーの再起動後に引き継ぐ）
let continuing = null; // サーバーが記録を失ったときに引き継ぐ情報
const segments = [];

// ---- 表示 ----
function setState(next) {
  state = next;
  const labels = { idle: "停止中", recording: "記録中", reconnecting: "再接続中", stopping: "停止処理中" };
  $("status").textContent = labels[next];
  $("status").className = next;
  $("btnStart").disabled = next !== "idle";
  $("btnStop").disabled = next === "idle" || next === "stopping";
  $("btnReshare").hidden = next === "idle";
  document.title = next === "recording" ? "● 記録中 - MTGログ" : "MTGログ";
}

function notice(id, text, level = "warn") {
  let el = document.querySelector(`#notices [data-id="${id}"]`);
  if (!text) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement("div");
    el.dataset.id = id;
    $("notices").append(el);
  }
  el.className = level;
  el.textContent = text;
}

function addSegment(seg) {
  const i = insertIndex(segments, seg.start);
  segments.splice(i, 0, seg);
  const li = document.createElement("li");
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = fmtOffset(seg.start);
  const sp = document.createElement("span");
  sp.className = `sp ${seg.speaker}`;
  sp.textContent = SPEAKER_LABELS[seg.speaker] ?? seg.speaker;
  const tx = document.createElement("span");
  tx.textContent = seg.text;
  li.append(t, sp, tx);
  const list = $("transcript");
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
  list.insertBefore(li, list.children[i] ?? null);
  $("emptyNote").hidden = true;
  if (atBottom) list.scrollTop = list.scrollHeight;
}

setInterval(() => {
  $("elapsed").textContent = state === "idle" ? "" : fmtOffset((performance.now() - startedAt) / 1000);
  if (buffer) {
    const behind = Math.max(buffer.bufferedSeconds(0), buffer.bufferedSeconds(1));
    notice("buffer", state === "reconnecting" ? `未送信の音声: ${behind.toFixed(0)} 秒（最大 60 秒まで保持します）` : "", "warn");
  }
}, 500);

// ---- デバイス（REQ-1） ----
async function loadConfig() {
  try {
    config = await (await fetch("/api/config")).json();
  } catch {
    notice("config", "サーバーから設定を取得できません。サーバーが起動しているか確かめてください", "ng");
  }
  $("language").value = config.language;
  $("saveAudio").checked = !!config.save_audio;
  $("engine").textContent = config.engine ?? "-";
  if (config.warning) notice("engine", config.warning, "warn");
}

async function refreshDevices() {
  try {
    if (!devices.some((d) => d.label)) {
      // マイクの利用を許可されるまでデバイス名は返らない
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
    }
    devices = await navigator.mediaDevices.enumerateDevices();
  } catch (e) {
    notice("devices", `マイクを使えません（${e.name}）。Edge のアドレスバー左のアイコンからマイクを「許可」にしてください`, "ng");
    return;
  }
  notice("devices", "");
  const sel = $("micSelect");
  const current = sel.value;
  let saved = null;
  try { saved = localStorage.getItem(MIC_KEY); } catch { /* 保存できない環境でも続ける */ }
  const chosen = current && devices.some((d) => d.deviceId === current) ? current : chooseInitialMic(devices, config.headset_keywords, saved);
  sel.innerHTML = "";
  for (const d of devices.filter((x) => x.kind === "audioinput")) sel.add(new Option(d.label || d.deviceId, d.deviceId));
  if (chosen) sel.value = chosen;

  const out = defaultOutputInfo(devices, config.headset_keywords);
  $("outputName").textContent = out ? out.name : "不明";
  $("outputName").className = out?.isHeadset ? "ok" : "warn";
  notice("output", out && !out.isHeadset
    ? `既定の再生デバイス（${out.name}）がヘッドセットではない可能性があります。相手の声が取れないことがあります`
    : "", "warn");
}

$("micSelect").addEventListener("change", () => {
  try { localStorage.setItem(MIC_KEY, $("micSelect").value); } catch { /* 無視 */ }
  $("btnSwitchMic").hidden = state === "idle";
});
navigator.mediaDevices.addEventListener("devicechange", () => refreshDevices());
$("btnRefresh").onclick = () => refreshDevices();

// ---- 音声グラフ（design 2.4） ----
async function ensureContext() {
  if (ctx) {
    if (ctx.state === "suspended") await ctx.resume();
    return;
  }
  ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  await ctx.audioWorklet.addModule("worklet.js");
  sink = ctx.createGain();
  sink.gain.value = 0; // 取得した音は再生しない
  sink.connect(ctx.destination);
  requestAnimationFrame(drawMeters);
}

function attach(ch, track) {
  detach(ch);
  const source = ctx.createMediaStreamSource(new MediaStream([track]));
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  const node = new AudioWorkletNode(ctx, "framer", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  node.port.onmessage = ({ data }) => onFrame(ch, data.pos, data.samples);
  source.connect(analyser);
  source.connect(node);
  node.connect(sink);
  channels[ch] = { track, source, analyser, node, data: new Float32Array(analyser.fftSize) };
  track.addEventListener("ended", () => onTrackEnded(ch, track));
}

function detach(ch) {
  const c = channels[ch];
  if (!c) return;
  c.node.port.onmessage = null;
  c.source.disconnect();
  c.node.disconnect();
  channels[ch] = null;
}

function drawMeters() {
  for (const [ch, el] of [[CHANNELS.me, $("meterMe")], [CHANNELS.remote, $("meterRemote")]]) {
    const c = channels[ch];
    if (!c) { el.value = 0; continue; }
    c.analyser.getFloatTimeDomainData(c.data);
    let sum = 0;
    for (const v of c.data) sum += v * v;
    el.value = Math.min(1, Math.sqrt(sum / c.data.length) * 4);
  }
  requestAnimationFrame(drawMeters);
}

function onTrackEnded(ch, track) {
  if (channels[ch]?.track !== track || state === "idle") return;
  detach(ch);
  if (ch === CHANNELS.me) {
    // REQ-1.5: 記録は止めず、マイクを選び直せるようにする
    notice("mic", "マイクが使えなくなりました。記録は続いています。マイクを選び直して「このマイクに切り替える」を押してください", "ng");
    $("btnSwitchMic").hidden = false;
  } else {
    notice("share", "相手の音声の共有が止まりました。記録は続いています。「相手の音声を共有し直す」を押してください", "ng");
  }
}

// ---- 取得（design 2.3） ----
async function openDisplay() {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: true,
    audio: { suppressLocalAudioPlayback: false },
    systemAudio: "include",
    windowAudio: "system",
    monitorTypeSurfaces: "include",
    selfBrowserSurface: "exclude",
    surfaceSwitching: "exclude",
  });
  const audio = stream.getAudioTracks()[0];
  if (!audio) {
    stream.getTracks().forEach((t) => t.stop());
    const surface = stream.getVideoTracks()[0]?.getSettings().displaySurface;
    throw new Error(`音声が共有されませんでした${surface ? `（選ばれた共有対象: ${surface}）` : ""}。` +
      "「画面全体」を選び、「システム オーディオも共有する」をオンにして、もう一度やり直してください");
  }
  // 映像は使わないが、止めると音声も止まる可能性があるため負荷を下げて保持する（V7 の結果で見直す）
  const video = stream.getVideoTracks()[0];
  video?.applyConstraints({ frameRate: 1, width: 320 }).catch(() => {});
  return stream;
}

async function openMic() {
  const id = $("micSelect").value;
  const proc = $("micProc").checked;
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      deviceId: id ? { exact: id } : undefined,
      echoCancellation: proc,
      noiseSuppression: proc,
      autoGainControl: proc,
    },
  });
  return stream.getAudioTracks()[0];
}

async function start() {
  notice("start", "");
  segments.length = 0;
  $("transcript").innerHTML = "";
  $("emptyNote").hidden = false;
  $("outputDir").textContent = "";
  $("outputDir").className = "muted";
  let micTrack = null;
  try {
    // getDisplayMedia はボタン操作の直後に呼ぶ必要があるので、先に呼ぶ
    displayStream = await openDisplay();
    micTrack = await openMic();
    await ensureContext();
  } catch (e) {
    displayStream?.getTracks().forEach((t) => t.stop());
    displayStream = null;
    micTrack?.stop();
    notice("start", e.name === "NotAllowedError" ? "画面共有またはマイクが許可されませんでした" : e.message, "ng");
    return;
  }
  attach(CHANNELS.remote, displayStream.getAudioTracks()[0]);
  attach(CHANNELS.me, micTrack);
  buffer = new SendBuffer();
  sessionId = null;
  origin = null;
  wallStart = null;
  continuing = null;
  stopRequested = false;
  startedAt = performance.now();
  setState("recording");
  connect();
}

async function reshare() {
  try {
    const stream = await openDisplay();
    displayStream?.getTracks().forEach((t) => t.stop());
    displayStream = stream;
    attach(CHANNELS.remote, stream.getAudioTracks()[0]);
    notice("share", "");
  } catch (e) {
    notice("share", e.name === "NotAllowedError" ? "画面共有が許可されませんでした" : e.message, "ng");
  }
}

async function switchMic() {
  try {
    const track = await openMic();
    channels[CHANNELS.me]?.track.stop();
    attach(CHANNELS.me, track);
    notice("mic", "");
    $("btnSwitchMic").hidden = true;
  } catch (e) {
    notice("mic", `マイクを開けません（${e.name}）`, "ng");
  }
}

function releaseCapture() {
  for (const ch of [CHANNELS.me, CHANNELS.remote]) {
    channels[ch]?.track.stop();
    detach(ch);
  }
  displayStream?.getTracks().forEach((t) => t.stop());
  displayStream = null;
}

// ---- 送信（design 2.4, 2.5, 4章） ----
function onFrame(ch, pos, samples) {
  if (origin === null || state === "idle" || state === "stopping") return;
  const rel = pos - origin;
  if (rel < 0) return;
  buffer.push(ch, rel, samples);
  if (ws?.readyState === WebSocket.OPEN && sessionId && state === "recording") ws.send(buildFrame(ch, rel, samples));
}

function startMessage() {
  return {
    type: "start",
    language: $("language").value.trim() || config.language,
    save_audio: $("saveAudio").checked,
    devices: {
      mic: channels[CHANNELS.me]?.track.label ?? null,
      output: defaultOutputInfo(devices, config.headset_keywords)?.name ?? null,
      share: displayStream?.getVideoTracks()[0]?.getSettings().displaySurface ?? null,
    },
    continue: continuing,
  };
}

function connect() {
  clearTimeout(reconnectTimer);
  const sock = new WebSocket(`ws://${location.host}/ws`);
  ws = sock;
  sock.onopen = () => {
    reconnectAttempt = 0;
    sock.send(JSON.stringify(sessionId ? { type: "resume", session_id: sessionId } : startMessage()));
  };
  sock.onmessage = ({ data }) => onMessage(JSON.parse(data));
  sock.onclose = () => {
    if (ws !== sock) return;
    if (state === "recording" || state === "reconnecting" || (state === "stopping" && sessionId)) {
      if (state === "recording") setState("reconnecting");
      const delay = backoffDelay(reconnectAttempt++);
      reconnectTimer = setTimeout(connect, delay);
    }
  };
}

function onMessage(msg) {
  switch (msg.type) {
    case "started": {
      if (origin === null) origin = Math.round(ctx.currentTime * ctx.sampleRate);
      const first = sessionId === null && continuing === null;
      sessionId = msg.session_id;
      wallStart = msg.wall_start;
      continuing = null;
      $("outputDir").textContent = `保存先: ${msg.output_dir}`;
      if (!first) {
        // 再接続: 受信済みの位置より後のフレームを送り直す
        buffer.ack(msg.received);
        for (const f of buffer.pending()) ws.send(buildFrame(f.channel, f.pos, f.samples));
        if (buffer.dropped.length) {
          const sec = buffer.dropped.reduce((a, d) => a + (d.to - d.from), 0) / SAMPLE_RATE;
          notice("dropped", `切断が長かったため、音声 ${sec.toFixed(0)} 秒分を送れませんでした`, "warn");
        }
      }
      if (stopRequested) {
        ws.send(JSON.stringify({ type: "stop" }));
      } else {
        setState("recording");
      }
      break;
    }
    case "ack":
      buffer?.ack(msg.received);
      break;
    case "segment":
      addSegment(msg);
      break;
    case "status":
      notice("server", msg.message, msg.level === "warning" ? "warn" : "ng");
      break;
    case "stopped":
      finish(`記録を終了しました。保存先: ${msg.output_dir}（${msg.segments} 件）`);
      break;
    case "error":
      if (msg.code === "unknown_session") {
        // サーバーの再起動などで記録が失われた。時刻の原点を引き継いで新しい記録として続け、未確認の音声を送り直す
        continuing = { previous_session: sessionId, wall_start: wallStart, received: { ...buffer.acked } };
        sessionId = null;
        notice("continued", "サーバーが記録を引き継げなかったため、新しい保存先で記録を続けています", "warn");
        ws.send(JSON.stringify(startMessage()));
      }
      break;
  }
}

function stop() {
  stopRequested = true;
  setState("stopping");
  releaseCapture();
  if (ws?.readyState === WebSocket.OPEN && sessionId) {
    // 未送信の音声を送り切ってから停止を伝える
    ws.send(JSON.stringify({ type: "stop" }));
  }
}

function finish(message, level = "ok") {
  releaseCapture();
  clearTimeout(reconnectTimer);
  const sock = ws;
  ws = null;
  sock?.close();
  sessionId = null;
  origin = null;
  continuing = null;
  buffer = null;
  setState("idle");
  $("outputDir").textContent = message;
  $("outputDir").className = level;
  notice("buffer", "");
  $("btnSwitchMic").hidden = true;
}

window.addEventListener("beforeunload", (e) => {
  if (state !== "idle") e.preventDefault();
});

$("btnStart").onclick = start;
$("btnStop").onclick = stop;
$("btnReshare").onclick = reshare;
$("btnSwitchMic").onclick = switchMic;

setState("idle");
await loadConfig();
await refreshDevices();
