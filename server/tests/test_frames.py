import numpy as np
import pytest

from mtglog.frames import ChannelAssembler, build_frame, parse_frame


def s(n, v=1):
    return np.full(n, v, dtype=np.int16)


def test_parse_roundtrip():
    f = parse_frame(build_frame(1, 123456789012, np.arange(5, dtype=np.int16)))
    assert (f.channel, f.pos) == (1, 123456789012)
    assert f.samples.tolist() == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("data", [b"\x00" * 5, b"\x00" * 13, b"\x05" + b"\x00" * 11])
def test_parse_invalid(data):
    with pytest.raises(ValueError):
        parse_frame(data)


def test_contiguous():
    a = ChannelAssembler()
    out = [a.push(0, s(1600)), a.push(1600, s(1600, 2))]
    assert np.concatenate(out).tolist() == [1] * 1600 + [2] * 1600
    assert a.received == 3200 and a.gap_samples == 0


def test_gap_filled_with_silence():
    a = ChannelAssembler()
    a.push(0, s(1600))
    out = a.push(3200, s(1600, 3))
    assert out.tolist() == [0] * 1600 + [3] * 1600
    assert a.received == 4800 and a.gap_samples == 1600


def test_duplicate_dropped():
    a = ChannelAssembler()
    a.push(0, s(1600))
    a.push(1600, s(1600))
    assert len(a.push(1600, s(1600))) == 0
    assert a.received == 3200 and a.dropped_samples == 1600


def test_partial_overlap_trimmed():
    a = ChannelAssembler()
    a.push(0, s(1600))
    out = a.push(800, s(1600, 4))
    assert out.tolist() == [4] * 800
    assert a.received == 2400


def test_reordered_later_frame_first():
    # 後のフレームが先に届いた場合、間は無音で埋めて進み、遅れて届いたフレームは捨てる
    a = ChannelAssembler()
    a.push(0, s(1600))
    a.push(3200, s(1600, 3))
    assert len(a.push(1600, s(1600, 2))) == 0
    assert a.received == 4800
