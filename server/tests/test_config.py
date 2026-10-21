import argparse

import pytest

from mtglog.config import DEFAULT_HEADSET_KEYWORDS, add_arguments, from_args, load_config


def test_defaults():
    cfg = load_config()
    assert cfg.port == 8765
    assert cfg.language == "ja"
    assert cfg.model == "whisper-large-v3-turbo"
    assert cfg.compute_type == "int8_float16"
    assert cfg.headset_keywords == DEFAULT_HEADSET_KEYWORDS
    assert cfg.save_audio is True
    assert cfg.output_dir == "./output"


def test_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('port = 9000\nlanguage = "en"\nheadset_keywords = ["Foo"]\nsave_audio = false\n', encoding="utf-8")
    cfg = load_config(p)
    assert (cfg.port, cfg.language, cfg.headset_keywords, cfg.save_audio) == (9000, "en", ["Foo"], False)
    assert cfg.model == "whisper-large-v3-turbo"


def test_args_override_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('port = 9000\nlanguage = "en"\n', encoding="utf-8")
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args(["--config", str(p), "--port", "9100", "--no-save-audio", "--headset-keywords", "A", "B"])
    cfg = from_args(args)
    assert cfg.port == 9100  # 引数が優先
    assert cfg.language == "en"  # ファイルの値
    assert cfg.save_audio is False
    assert cfg.headset_keywords == ["A", "B"]


def test_unknown_key(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("nope = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)
