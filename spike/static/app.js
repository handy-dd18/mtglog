// T0.1 検証ページ。音声の取得・メーター表示・サーバーへの送信と、検証結果の記入を行う。
const $ = (id) => document.getElementById(id);
const CH = { me: 0, remote: 1 };
const VERDICTS = [
  ["V1", "Edge で画面共有（画面全体＋システム音声）とマイクが使えるか"],
  ["V3", "ウィンドウ共有で Teams のウィンドウの音だけが取れるか（通知音などが混ざらないか）"],
  ["V4", "Edge で共有を続けたまま Teams の会議を 30 分続けて、会議に影響がないか"],
  ["V5", "タブを背景・最小化にしても取得と送信が続くか（欠落がないか）"],
  ["V7", "映像トラックを止めても音声トラックが続くか"],
];

const logLines = [];
let ctx = null;
let sink = null;
let ws = null;
let sending = false;
let origin = 0;
let lastStats = null;
let devicesInfo = null;
let hiddenSince = document.hidden ? performance.now() : null;
let hiddenTotalMs = 0;
const channels = { [CH.me]: null, [CH.remote]: null };
let displayStream = null;

function log(name, detail = {}) {
  const line = `${new Date().toLocaleTimeString()} ${name} ${JSON.stringify(detail)}`;
  logLines.push(line);
  $("log").textContent = logLines.slice(-200).join("\n");
  $("log").scrollTop = $("log").scrollHeight;
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ name, detail }));
}

// ---- 環境 ----
function showEnv() {
  const sc = navigator.mediaDevices?.getSupportedConstraints?.() ?? {};
  const pick = ["suppressLocalAudioPlayback", "restrictOwnAudio", "displaySurface", "echoCancellation", "noiseSuppression", "autoGainControl"];
  const env = {
    userAgent: navigator.userAgent,
    brands: navigator.userAgentData?.brands?.map((b) => `${b.brand} ${b.version}`) ?? null,
    isSecureContext: window.isSecureContext,
    getDisplayMedia: typeof navigator.mediaDevices?.getDisplayMedia === "function",
    supportedConstraints: Object.fromEntries(pick.map((k) => [k, !!sc[k]])),
  };
  $("env").textContent = JSON.stringify(env, null, 2);
  return env;
}
const env = showEnv();

async function listDevices() {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    const all = await navigator.mediaDevices.enumerateDevices();
    devicesInfo = all.filter((d) => d.kind !== "videoinput").map((d) => ({ kind: d.kind, label: d.label, deviceId: d.deviceId.slice(0, 12) }));
    const lines = devicesInfo.map((d) => `${d.kind === "audioinput" ? "入力" : "出力"}  ${d.label}${d.deviceId === "default" ? "  ← 既定" : ""}`);
    $("devices").textContent = lines.join("\n") || "（なし）";
    $("devices").className = "";
    const sel = $("micSelect");
    sel.innerHTML = "";
    for (const d of all.filter((d) => d.kind === "audioinput")) {
      sel.add(new Option(d.label || d.deviceId, d.deviceId));
    }
    log("devices", { count: devicesInfo.length, defaultOutput: devicesInfo.find((d) => d.kind === "audiooutput" && d.deviceId === "default")?.label ?? null });
  } catch (e) {
    $("devices").textContent = `失敗: ${e.name}: ${e.message}`;
    $("devices").className = "ng";
    log("devices_error", { name: e.name, message: e.message });
  }
}

// ---- 音声グラフ ----
async function ensureCtx() {
  if (ctx) return;
  ctx = new AudioContext({ sampleRate: 16000 });
  await ctx.audioWorklet.addModule("framer-worklet.js");
  sink = ctx.createGain();
  sink.gain.value = 0; // 取得した音を再生しない
  sink.connect(ctx.destination);
  log("audio_context", { sampleRate: ctx.sampleRate, state: ctx.state });
  requestAnimationFrame(drawMeters);
}

function attach(ch, track) {
  detach(ch);
  const source = ctx.createMediaStreamSource(new MediaStream([track]));
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  const node = new AudioWorkletNode(ctx, "framer", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  node.port.onmessage = ({ data }) => sendFrame(ch, data.pos, data.samples);
  source.connect(analyser);
  source.connect(node);
  node.connect(sink);
  channels[ch] = { track, source, analyser, node, buf: new Float32Array(analyser.fftSize) };
  for (const ev of ["ended", "mute", "unmute"]) {
    track.addEventListener(ev, () => {
      log(`track_${ev}`, { channel: ch, kind: track.kind, label: track.label });
      updateStates();
    });
  }
  updateStates();
}

function detach(ch) {
  const c = channels[ch];
  if (!c) return;
  c.source.disconnect();
  c.node.disconnect();
  c.node.port.onmessage = null;
  channels[ch] = null;
}

function drawMeters() {
  for (const [ch, meter] of [[CH.me, $("micMeter")], [CH.remote, $("dispMeter")]]) {
    const c = channels[ch];
    if (!c) { meter.value = 0; continue; }
    c.analyser.getFloatTimeDomainData(c.buf);
    let sum = 0;
    for (const v of c.buf) sum += v * v;
    meter.value = Math.min(1, Math.sqrt(sum / c.buf.length) * 4);
  }
  requestAnimationFrame(drawMeters);
}

function trackState(c) {
  if (!c) return ["未取得", "muted"];
  const t = c.track;
  const s = `${t.readyState}${t.muted ? "（muted）" : ""}  ${t.label}`;
  return [s, t.readyState === "live" && !t.muted ? "ok" : "ng"];
}

function updateStates() {
  for (const [ch, el] of [[CH.me, $("micState")], [CH.remote, $("dispState")]]) {
    const [text, cls] = trackState(channels[ch]);
    el.textContent = text;
    el.className = cls;
  }
  if (displayStream) {
    const v = displayStream.getVideoTracks()[0];
    const a = displayStream.getAudioTracks()[0];
    $("dispInfo").textContent = JSON.stringify({
      video: v ? { readyState: v.readyState, label: v.label, settings: v.getSettings() } : null,
      audio: a ? { readyState: a.readyState, label: a.label, settings: a.getSettings() } : null,
    }, null, 2);
    $("dispInfo").className = "";
  }
}

// ---- 取得 ----
async function openMic() {
  try {
    await ensureCtx();
    const proc = $("micProc").checked;
    const id = $("micSelect").value;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: id === "default" ? undefined : { exact: id }, echoCancellation: proc, noiseSuppression: proc, autoGainControl: proc },
    });
    channels[CH.me]?.track.stop();
    attach(CH.me, stream.getAudioTracks()[0]);
    log("mic_open", { label: stream.getAudioTracks()[0].label, processing: proc });
  } catch (e) {
    $("micState").textContent = `失敗: ${e.name}: ${e.message}`;
    $("micState").className = "ng";
    log("mic_error", { name: e.name, message: e.message });
  }
}

async function share(mode) {
  const options = mode === "screen"
    ? { video: true, audio: { suppressLocalAudioPlayback: false }, systemAudio: "include", windowAudio: "system", monitorTypeSurfaces: "include", selfBrowserSurface: "exclude" }
    : { video: { displaySurface: "window" }, audio: { suppressLocalAudioPlayback: false }, windowAudio: "window", systemAudio: "include", monitorTypeSurfaces: "exclude", selfBrowserSurface: "exclude" };
  try {
    await ensureCtx();
    const stream = await navigator.mediaDevices.getDisplayMedia(options);
    displayStream?.getTracks().forEach((t) => t.stop());
    displayStream = stream;
    const v = stream.getVideoTracks()[0];
    const a = stream.getAudioTracks()[0];
    log("share", { mode, displaySurface: v?.getSettings().displaySurface ?? null, videoLabel: v?.label ?? null, audio: a ? a.label : null });
    if (v) v.addEventListener("ended", () => { log("video_ended", { mode }); updateStates(); });
    if (!a) {
      detach(CH.remote);
      updateStates();
      const surface = v?.getSettings().displaySurface ?? "不明";
      $("dispState").textContent = `音声トラックがありません（選ばれた共有対象: ${surface}）。` +
        (mode === "screen" ? "「画面全体」タブを選び、「システム オーディオも共有する」をオンにして選び直してください" : "選択画面で音声共有のスイッチをオンにして選び直してください");
      $("dispState").className = "ng";
      return;
    }
    attach(CH.remote, a);
    const surface = v?.getSettings().displaySurface;
    if (mode === "screen" && surface !== "monitor") {
      log("surface_mismatch", { expected: "monitor", actual: surface ?? null });
      $("dispState").textContent += `  ※「画面全体」ではなく「${surface}」が選ばれています`;
      $("dispState").className = "warn";
    }
  } catch (e) {
    $("dispState").textContent = `失敗: ${e.name}: ${e.message}`;
    $("dispState").className = "ng";
    log("share_error", { mode, name: e.name, message: e.message });
  }
}

function stopVideo() {
  const v = displayStream?.getVideoTracks()[0];
  if (!v) return;
  v.stop();
  log("video_stopped_by_user", {});
  updateStates();
  // 音声トラックが続いているかを 1 秒後と 5 秒後に記録する
  for (const ms of [1000, 5000]) {
    setTimeout(() => {
      const a = displayStream?.getAudioTracks()[0];
      log("audio_after_video_stop", { afterMs: ms, readyState: a?.readyState ?? null, muted: a?.muted ?? null, level: Number($("dispMeter").value.toFixed(3)) });
      updateStates();
    }, ms);
  }
}

// ---- 送信 ----
function sendFrame(ch, pos, samples) {
  if (!sending || !ws || ws.readyState !== WebSocket.OPEN) return;
  const rel = pos - origin;
  if (rel < 0) return;
  const buf = new ArrayBuffer(12 + samples.byteLength);
  const dv = new DataView(buf);
  dv.setUint8(0, ch);
  dv.setBigUint64(4, BigInt(rel), true);
  new Int16Array(buf, 12).set(samples);
  ws.send(buf);
}

function toggleSend() {
  if (sending) {
    sending = false;
    ws?.close();
    $("btnSend").textContent = "送信を開始";
    return;
  }
  if (!ctx) {
    $("wsState").textContent = "先にマイクか画面共有を開いてください";
    $("wsState").className = "warn";
    return;
  }
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    origin = Math.round(ctx.currentTime * ctx.sampleRate);
    sending = true;
    $("wsState").textContent = "送信中";
    $("wsState").className = "ok";
    $("btnSend").textContent = "送信を停止";
    log("send_start", { hidden: document.hidden, me: !!channels[CH.me], remote: !!channels[CH.remote] });
  };
  ws.onclose = () => {
    sending = false;
    $("wsState").textContent = "未接続";
    $("wsState").className = "muted";
    $("btnSend").textContent = "送信を開始";
  };
  ws.onerror = () => log("ws_error", {});
  ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.type !== "stats") return;
    lastStats = msg;
    $("elapsed").textContent = msg.elapsed;
    const names = { me: "自分", remote: "相手" };
    const rows = Object.entries(msg.channels).map(([k, s]) =>
      `<tr><td>${names[k] ?? k}</td><td>${s.seconds}</td><td class="${s.gaps ? "ng" : "ok"}">${s.gaps} / ${s.gap_seconds}</td><td>${s.overlaps}</td><td class="${s.max_interval_ms > 1000 ? "warn" : ""}">${s.max_interval_ms} ms</td></tr>`);
    $("stats").innerHTML = rows.join("") || '<tr><td colspan="5" class="muted">まだ受信していません</td></tr>';
  };
}

// ---- 背景タブの記録（V5） ----
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    hiddenSince = performance.now();
  } else if (hiddenSince !== null) {
    hiddenTotalMs += performance.now() - hiddenSince;
    hiddenSince = null;
    $("hiddenTotal").textContent = (hiddenTotalMs / 1000).toFixed(0);
  }
  log("visibility", { hidden: document.hidden, ctxState: ctx?.state ?? null });
});
navigator.mediaDevices?.addEventListener?.("devicechange", () => log("devicechange", {}));

// ---- 検証結果 ----
function loadSaved() {
  try { return JSON.parse(localStorage.getItem("mtglog-spike-verdicts") ?? "{}"); } catch { return {}; }
}
function renderVerdicts() {
  const saved = loadSaved();
  $("verdicts").innerHTML = "<tr><th>ID</th><th>確かめること</th><th>結果</th><th>メモ</th></tr>" + VERDICTS.map(([id, text]) => `
    <tr><td>${id}</td><td>${text}</td>
      <td><select data-id="${id}" data-f="result">${["未確認", "可", "一部可", "不可"].map((v) => `<option${saved[id]?.result === v ? " selected" : ""}>${v}</option>`).join("")}</select></td>
      <td><input type="text" data-id="${id}" data-f="memo" size="30"></td></tr>`).join("");
  for (const el of $("verdicts").querySelectorAll("input[data-f=memo]")) el.value = saved[el.dataset.id]?.memo ?? "";
  $("verdicts").addEventListener("change", () => {
    try { localStorage.setItem("mtglog-spike-verdicts", JSON.stringify(collectVerdicts())); } catch { /* 保存できなくても続ける */ }
  });
}
function collectVerdicts() {
  const out = {};
  for (const el of $("verdicts").querySelectorAll("[data-id]")) {
    (out[el.dataset.id] ??= {})[el.dataset.f] = el.value;
  }
  return out;
}
async function saveReport() {
  if (!$("machine").value) {
    $("saveState").textContent = "検証した端末を選んでください";
    $("saveState").className = "warn";
    return;
  }
  const body = {
    savedAt: new Date().toISOString(),
    machine: $("machine").value,
    verdicts: collectVerdicts(),
    env,
    devices: devicesInfo,
    audioContextSampleRate: ctx?.sampleRate ?? null,
    display: $("dispInfo").textContent,
    lastStats,
    hiddenTotalSec: Math.round(hiddenTotalMs / 1000),
    log: logLines,
  };
  try {
    const res = await fetch("/api/report", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await res.json();
    $("saveState").textContent = `保存しました: ${j.saved}`;
    $("saveState").className = "ok";
  } catch (e) {
    $("saveState").textContent = `保存に失敗しました: ${e.message}`;
    $("saveState").className = "ng";
  }
}

try { $("machine").value = localStorage.getItem("mtglog-spike-machine") ?? ""; } catch { /* 読めなくても続ける */ }
$("machine").onchange = () => { try { localStorage.setItem("mtglog-spike-machine", $("machine").value); } catch { /* 保存できなくても続ける */ } };
$("btnDevices").onclick = listDevices;
$("btnMic").onclick = openMic;
$("btnScreen").onclick = () => share("screen");
$("btnWindow").onclick = () => share("window");
$("btnStopVideo").onclick = stopVideo;
$("btnSend").onclick = toggleSend;
$("btnSave").onclick = saveReport;
renderVerdicts();
