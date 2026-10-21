import assert from "node:assert/strict";
import { test } from "node:test";
import {
  SendBuffer, backoffDelay, buildFrame, chooseInitialMic, defaultOutputInfo, floatToInt16, fmtOffset, insertIndex,
  isHeadsetLabel, stripDefaultPrefix,
} from "../lib.js";

const KW = ["Headset", "ヘッドセット", "Jabra"];
const devices = [
  { kind: "audioinput", deviceId: "default", label: "既定 - マイク (Realtek(R) Audio)" },
  { kind: "audioinput", deviceId: "communications", label: "通信 - ヘッドセット マイク (Jabra Evolve2)" },
  { kind: "audioinput", deviceId: "aaa", label: "マイク (Realtek(R) Audio)" },
  { kind: "audioinput", deviceId: "bbb", label: "ヘッドセット マイク (Jabra Evolve2)" },
  { kind: "audiooutput", deviceId: "default", label: "既定 - ヘッドホン (Jabra Evolve2)" },
];

test("floatToInt16: 変換とクリップ", () => {
  assert.deepEqual([...floatToInt16(new Float32Array([0, 1, -1, 0.5, 2, -3]))], [0, 32767, -32768, 16384, 32767, -32768]);
});

test("buildFrame: ヘッダーと標本", () => {
  const buf = buildFrame(1, 2 ** 40 + 5, new Int16Array([1, -2]));
  const dv = new DataView(buf);
  assert.equal(buf.byteLength, 16);
  assert.equal(dv.getUint8(0), 1);
  assert.equal(dv.getUint8(1) + dv.getUint8(2) + dv.getUint8(3), 0);
  assert.equal(dv.getBigUint64(4, true), BigInt(2 ** 40 + 5));
  assert.equal(dv.getInt16(12, true), 1);
  assert.equal(dv.getInt16(14, true), -2);
});

test("isHeadsetLabel / stripDefaultPrefix", () => {
  assert.ok(isHeadsetLabel("Logitech USB HEADSET", ["headset"]));
  assert.ok(!isHeadsetLabel("スピーカー (Realtek)", KW));
  assert.equal(stripDefaultPrefix("既定 - スピーカー (Realtek)"), "スピーカー (Realtek)");
  assert.equal(stripDefaultPrefix("Default - Speakers"), "Speakers");
});

test("chooseInitialMic: 前回の選択を優先", () => {
  assert.equal(chooseInitialMic(devices, KW, "aaa"), "aaa");
});

test("chooseInitialMic: 前回の選択が一覧にないときはヘッドセット（別名を除く）", () => {
  assert.equal(chooseInitialMic(devices, KW, "gone"), "bbb");
});

test("chooseInitialMic: ヘッドセットがなければ既定", () => {
  assert.equal(chooseInitialMic(devices.filter((d) => !d.label.includes("Jabra")), KW, null), "default");
});

test("chooseInitialMic: 入力デバイスがない", () => {
  assert.equal(chooseInitialMic([], KW, null), null);
});

test("defaultOutputInfo: ヘッドセットかどうか", () => {
  assert.deepEqual(defaultOutputInfo(devices, KW), { label: "既定 - ヘッドホン (Jabra Evolve2)", name: "ヘッドホン (Jabra Evolve2)", isHeadset: true });
  const speaker = [{ kind: "audiooutput", deviceId: "default", label: "既定 - スピーカー (Realtek(R) Audio)" }];
  assert.equal(defaultOutputInfo(speaker, KW).isHeadset, false);
  assert.equal(defaultOutputInfo([], KW), null);
});

test("backoffDelay / fmtOffset / insertIndex", () => {
  assert.deepEqual([0, 1, 2, 3, 10].map(backoffDelay), [1000, 2000, 4000, 4000, 4000]);
  assert.equal(fmtOffset(3725.9), "01:02:05");
  const list = [{ start: 1 }, { start: 3 }, { start: 5 }];
  assert.equal(insertIndex(list, 4), 2);
  assert.equal(insertIndex(list, 6), 3);
  assert.equal(insertIndex(list, 0), 0);
});

test("SendBuffer: ack で受領済みのフレームを消す", () => {
  const b = new SendBuffer();
  for (let i = 0; i < 5; i++) b.push(0, i * 1600, new Int16Array(1600));
  b.push(1, 0, new Int16Array(1600));
  b.ack({ me: 3200, remote: 0 });
  assert.deepEqual(b.pending().map((f) => [f.channel, f.pos]), [[0, 3200], [0, 4800], [0, 6400], [1, 0]]);
  b.ack({ me: 4000 }); // フレームの途中までの受領確認では消さない
  assert.equal(b.pending().filter((f) => f.channel === 0).length, 3);
});

test("SendBuffer: 60 秒を超えたら古い順に捨てて記録する", () => {
  const b = new SendBuffer(4800); // 0.3 秒分
  for (let i = 0; i < 5; i++) b.push(1, i * 1600, new Int16Array(1600));
  assert.deepEqual(b.pending().map((f) => f.pos), [3200, 4800, 6400]);
  assert.deepEqual(b.dropped, [{ channel: 1, from: 0, to: 3200 }]);
  assert.equal(b.bufferedSeconds(1), 0.3);
});

test("SendBuffer: 受領確認済みの位置を覚える（サーバー再起動時の引き継ぎに使う）", () => {
  const b = new SendBuffer();
  b.ack({ me: 3200, remote: 1600 });
  b.ack({ me: 1600 }); // 古い確認で巻き戻らない
  assert.deepEqual(b.acked, { me: 3200, remote: 1600 });
});
