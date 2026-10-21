// 入力を 16 ビット整数に変換し、FRAME 標本ごとに先頭標本の位置（currentFrame）を添えて渡す。
const FRAME = 1600; // 16 kHz で 100 ミリ秒

class Framer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(FRAME);
    this.filled = 0;
    this.startPos = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const n = input[0].length;
    for (let i = 0; i < n; i++) {
      if (this.filled === 0) this.startPos = currentFrame + i;
      let v = 0;
      for (let c = 0; c < input.length; c++) v += input[c][i];
      v /= input.length;
      v = Math.max(-1, Math.min(1, v));
      this.buf[this.filled++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.filled === FRAME) {
        this.port.postMessage({ pos: this.startPos, samples: this.buf }, [this.buf.buffer]);
        this.buf = new Int16Array(FRAME);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("framer", Framer);
