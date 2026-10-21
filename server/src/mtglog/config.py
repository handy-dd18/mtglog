"""設定の読み込み（design 3.5）。

既定値 → config.toml → コマンドライン引数 の順に上書きする。
"""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_HEADSET_KEYWORDS = ["Headset", "ヘッドセット", "Headphone", "ヘッドホン", "Jabra", "Poly", "Logi"]


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    language: str = "ja"
    # model_dir の下の変換済みモデルのディレクトリ名（またはパス）。scripts/setup_models.sh で準備する（design 5.3）
    model: str = "whisper-large-v3-turbo"
    compute_type: str = "int8_float16"
    device: str = "auto"  # auto / cuda / cpu
    cpu_model: str = "whisper-small"
    cpu_compute_type: str = "int8"
    model_dir: str = "./models"
    headset_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_HEADSET_KEYWORDS))
    save_audio: bool = True
    output_dir: str = "./output"
    # 発話区間の切り出し（design 3.2）
    vad_start_threshold: float = 0.5
    vad_end_threshold: float = 0.35
    vad_min_silence_ms: int = 600
    vad_pad_ms: int = 200
    vad_max_segment_ms: int = 20000
    vad_min_segment_ms: int = 250
    # 切断後に記録を保持する時間（design 4.2）
    resume_timeout_s: int = 600
    # 無音の警告を出すまでの時間。0 で無効（design 3.6）
    silence_warning_sec: int = 120


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    cfg = Config()
    if path is not None:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        _apply(cfg, data, source=str(path))
    if overrides:
        _apply(cfg, {k: v for k, v in overrides.items() if v is not None}, source="引数")
    return cfg


def _apply(cfg: Config, data: dict, source: str) -> None:
    known = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"{source}: 未知の設定項目 {key!r}")
        setattr(cfg, key, value)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Config の各項目を同名の引数として追加する。既定値は None にし、指定されたものだけを上書きに使う。"""
    parser.add_argument("--config", help="設定ファイル（TOML）")
    for f in fields(Config):
        flag = "--" + f.name.replace("_", "-")
        if f.type == "bool":
            parser.add_argument(flag, dest=f.name, action=argparse.BooleanOptionalAction, default=None)
        elif f.type == "list[str]":
            parser.add_argument(flag, dest=f.name, nargs="+", default=None)
        else:
            conv = {"int": int, "float": float}.get(f.type, str)
            parser.add_argument(flag, dest=f.name, type=conv, default=None)


def from_args(args: argparse.Namespace) -> Config:
    overrides = {f.name: getattr(args, f.name, None) for f in fields(Config)}
    return load_config(args.config, overrides)
