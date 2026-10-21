// 取得ページの純粋関数（DOM に依存しない）。node --test で単体テストする。

export const SAMPLE_RATE = 16000;
export const FRAME = 1600; // 100 ミリ秒
export const HEADER_BYTES = 12;
export const CHANNELS = { me: 0, remote: 1 };
export const CHANNEL_NAMES = ["me", "remote"];
export const SPEAKER_LABELS = { me: "自分", remote: "相手" };

/** 32 ビット浮動小数点の標本を 16 ビット整数に変換する（範囲外はクリップ）。 */
export function floatToInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? Math.round(v * 0x8000) : Math.round(v * 0x7fff);
  }
  return out;
}

/** design 4.1 の音声フレームを組み立てる。 */
export function buildFrame(channel, pos, samples) {
  const buf = new ArrayBuffer(HEADER_BYTES + samples.length * 2);
  const dv = new DataView(buf);
  dv.setUint8(0, channel);
  dv.setBigUint64(4, BigInt(pos), true);
  for (let i = 0; i < samples.length; i++) dv.setInt16(HEADER_BYTES + i * 2, samples[i], true);
  return buf;
}

export function isHeadsetLabel(label, keywords) {
  const l = (label ?? "").toLowerCase();
  return keywords.some((k) => k && l.includes(k.toLowerCase()));
}

/** 「既定 - 〈名前〉」「Default - 〈名前〉」のような接頭辞を除いた実デバイス名。 */
export function stripDefaultPrefix(label) {
  return (label ?? "").replace(/^(既定|規定|通信|Default|Communications)\s*-\s*/i, "");
}

/**
 * マイクの初期選択（REQ-1.2, 1.4）。
 * 1. 前回選んだデバイスが一覧にあればそれ
 * 2. 名前がヘッドセットを示す語を含む最初のデバイス（「既定」「通信」の別名は除く）
 * 3. 既定のデバイス
 */
export function chooseInitialMic(devices, keywords, savedId) {
  const inputs = devices.filter((d) => d.kind === "audioinput");
  if (inputs.length === 0) return null;
  if (savedId && inputs.some((d) => d.deviceId === savedId)) return savedId;
  const real = inputs.filter((d) => d.deviceId !== "default" && d.deviceId !== "communications");
  const headset = real.find((d) => isHeadsetLabel(d.label, keywords));
  if (headset) return headset.deviceId;
  return (inputs.find((d) => d.deviceId === "default") ?? inputs[0]).deviceId;
}

/** 既定の再生デバイスの名前と、ヘッドセットらしいかどうか（REQ-1.3）。 */
export function defaultOutputInfo(devices, keywords) {
  const d = devices.find((x) => x.kind === "audiooutput" && x.deviceId === "default");
  if (!d) return null;
  const name = stripDefaultPrefix(d.label);
  return { label: d.label, name, isHeadset: isHeadsetLabel(name, keywords) };
}

/** 時間を「2 分」「90 秒」のように表す。 */
export function fmtDuration(seconds) {
  return seconds % 60 === 0 ? `${seconds / 60} 分` : `${seconds} 秒`;
}

/** 無音の警告の文（REQ-2.7, 2.8 / design 2.6）。 */
export function silenceMessage(kind, seconds) {
  const d = fmtDuration(seconds);
  if (kind === "remote") {
    return `相手の音声が ${d}間届いていません。Teams / Zoom のスピーカーと Windows の既定の再生デバイスが同じか、`
      + "画面共有で「システム オーディオも共有する」をオンにしたかを確かめてください。";
  }
  return `自分と相手のどちらの音声も ${d}間届いていません。マイクが音を拾っているか、スピーカーの設定が合っているかを`
    + "確かめてください。会議が終わっていれば、記録を停止してください。";
}

/** 再接続の待ち時間: 1 秒、2 秒、4 秒、以後 4 秒（design 2.5）。 */
export function backoffDelay(attempt) {
  return [1000, 2000, 4000][Math.min(attempt, 2)];
}

export function fmtOffset(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}

/** 開始時刻順を保って結果を挿入する位置。 */
export function insertIndex(list, start) {
  let i = list.length;
  while (i > 0 && list[i - 1].start > start) i--;
  return i;
}

/**
 * 送信済みだがサーバーの受領確認がない音声フレームを、チャンネルごとに保持する（REQ-5.3）。
 * 上限を超えた分は古い順に捨て、捨てた区間を dropped に記録する。
 */
export class SendBuffer {
  constructor(maxSamples = 60 * SAMPLE_RATE) {
    this.maxSamples = maxSamples;
    this.frames = { 0: [], 1: [] };
    this.sizes = { 0: 0, 1: 0 };
    this.acked = { me: 0, remote: 0 }; // サーバーが受信済みと確認した位置
    this.dropped = [];
  }

  push(channel, pos, samples) {
    const list = this.frames[channel];
    list.push({ pos, samples });
    this.sizes[channel] += samples.length;
    while (this.sizes[channel] > this.maxSamples) {
      const old = list.shift();
      this.sizes[channel] -= old.samples.length;
      this._recordDrop(channel, old.pos, old.pos + old.samples.length);
    }
  }

  _recordDrop(channel, from, to) {
    const last = this.dropped.at(-1);
    if (last && last.channel === channel && last.to === from) last.to = to;
    else this.dropped.push({ channel, from, to });
  }

  /** received: { me: 標本位置, remote: 標本位置 }。この位置までのフレームを消す。 */
  ack(received) {
    for (const [name, ch] of Object.entries(CHANNELS)) {
      const r = received?.[name];
      if (r === undefined) continue;
      this.acked[name] = Math.max(this.acked[name], r);
      const list = this.frames[ch];
      while (list.length && list[0].pos + list[0].samples.length <= r) {
        this.sizes[ch] -= list.shift().samples.length;
      }
    }
  }

  /** 再接続後に送り直すフレーム（受領確認済みの分を除いた残り全部）。 */
  pending() {
    return [0, 1].flatMap((ch) => this.frames[ch].map((f) => ({ channel: ch, ...f })));
  }

  bufferedSeconds(channel) {
    return this.sizes[channel] / SAMPLE_RATE;
  }
}
