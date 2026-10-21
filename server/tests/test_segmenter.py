import numpy as np
from conftest import SR, energy_prob, silence, tone

from mtglog.segmenter import Segmenter, SegmenterParams


def run(audio, chunk=1600, params=None):
    seg = Segmenter(energy_prob, params)
    out = []
    for i in range(0, len(audio), chunk):
        out += seg.push(audio[i : i + chunk])
    out += seg.flush()
    return out


def near(actual_samples, expected_s, tol_s=0.1):
    return abs(actual_samples / SR - expected_s) <= tol_s


def test_silence_only():
    assert run(silence(5)) == []


def test_short_noise_dropped():
    audio = np.concatenate([silence(1), tone(0.05), silence(2)])
    assert run(audio) == []


def test_single_utterance_boundaries():
    audio = np.concatenate([silence(1), tone(2), silence(2)])
    segs = run(audio)
    assert len(segs) == 1
    # 余白 200 ms を前後に含む
    assert near(segs[0].start, 0.8)
    assert near(segs[0].end, 3.2)
    assert len(segs[0].audio) == segs[0].end - segs[0].start


def test_two_utterances_split_by_silence():
    audio = np.concatenate([silence(0.5), tone(1), silence(1), tone(1.5), silence(1)])
    segs = run(audio)
    assert len(segs) == 2
    assert near(segs[0].start, 0.3) and near(segs[0].end, 1.7)
    assert near(segs[1].start, 2.3) and near(segs[1].end, 4.2)


def test_short_pause_not_split():
    # 600 ms 未満の間は 1 つの発話として扱う
    audio = np.concatenate([silence(0.5), tone(1), silence(0.3), tone(1), silence(1)])
    assert len(run(audio)) == 1


def test_long_speech_forced_split():
    audio = np.concatenate([silence(0.5), tone(25), silence(1)])
    segs = run(audio)
    assert len(segs) == 2
    assert all((s.end - s.start) / SR <= 20.0 + 0.1 for s in segs)
    assert segs[0].end == segs[1].start  # 連続していて欠けがない
    assert near(segs[1].end, 25.7)


def test_flush_emits_ongoing_speech():
    seg = Segmenter(energy_prob, SegmenterParams())
    out = seg.push(np.concatenate([silence(0.5), tone(1)]))
    assert out == []
    out = seg.flush()
    assert len(out) == 1 and near(out[0].end, 1.5)


def test_buffer_is_trimmed_during_silence():
    seg = Segmenter(energy_prob, SegmenterParams())
    for _ in range(100):
        seg.push(silence(1))
    assert len(seg.buf) <= SR  # 長い無音でも保持量が増え続けない
