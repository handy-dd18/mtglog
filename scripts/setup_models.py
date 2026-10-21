"""OpenAI 公式の Whisper モデルを取得し、faster-whisper 用の形式（CTranslate2）に変換する（design 5.3）。

scripts/setup_models.sh から呼ぶ。`check` は標準ライブラリだけで動き、`convert` は変換用の使い捨ての環境で動く。

    python setup_models.py check <models_dir>
    python setup_models.py convert <models_dir> <tmp_dir>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 版は固定する。上げるときは design 5.3 の表も同じコミットで変える
MODELS = [
    {
        "name": "whisper-large-v3-turbo",
        "repo": "openai/whisper-large-v3-turbo",
        "revision": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
        "quantization": "int8_float16",
    },
    {
        "name": "whisper-small",
        "repo": "openai/whisper-small",
        "revision": "973afd24965f72e36ca33b3055d56a652f456b4d",
        "quantization": "int8",
    },
]

# 変換に要るファイルだけを取得する（同じ重みの別形式 pytorch_model.bin などは取得しない）
FILES = [
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "normalizer.json",
    "added_tokens.json",
    "special_tokens_map.json",
]
# large-v3 系は入力の形式（メル周波数の数）を preprocessor_config.json から読む
COPY_FILES = ["tokenizer.json", "preprocessor_config.json"]


def is_ready(models_dir: Path, m: dict) -> bool:
    source = models_dir / m["name"] / "SOURCE.json"
    if not (models_dir / m["name"] / "model.bin").is_file() or not source.is_file():
        return False
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return data.get("repo") == m["repo"] and data.get("revision") == m["revision"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def convert(models_dir: Path, tmp: Path, m: dict) -> None:
    import ctranslate2
    from ctranslate2.converters import TransformersConverter
    from huggingface_hub import snapshot_download

    print(f"{m['repo']} を取得しています…", flush=True)
    src = snapshot_download(m["repo"], revision=m["revision"], allow_patterns=FILES, local_dir=tmp / "src" / m["name"])
    print(f"{m['name']} に変換しています（{m['quantization']}）…", flush=True)
    out = tmp / "out" / m["name"]
    # float16 で読み込み、変換中のメモリを抑える
    converter = TransformersConverter(src, copy_files=COPY_FILES, load_as_float16=True, low_cpu_mem_usage=True)
    converter.convert(str(out), quantization=m["quantization"], force=True)

    files = {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()}
    source = {
        "repo": m["repo"],
        "revision": m["revision"],
        "quantization": m["quantization"],
        "converter": f"ctranslate2 {ctranslate2.__version__}",
        "converted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sha256": files,
    }
    (out / "SOURCE.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dest = models_dir / m["name"]
    if dest.exists():
        shutil.rmtree(dest)
    out.rename(dest)  # 一時ディレクトリは models の下にあるので、同じファイルシステム内の移動になる
    shutil.rmtree(tmp / "src" / m["name"], ignore_errors=True)
    print(f"準備しました: {dest}", flush=True)


def main(argv: list[str]) -> int:
    cmd, models_dir = argv[0], Path(argv[1]).resolve()
    missing = [m for m in MODELS if not is_ready(models_dir, m)]
    if cmd == "check":
        for m in missing:
            print(f"未準備: {m['name']}（{m['repo']}）")
        return 1 if missing else 0
    if cmd == "convert":
        tmp = Path(argv[2]).resolve()
        for m in missing:
            convert(models_dir, tmp, m)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
