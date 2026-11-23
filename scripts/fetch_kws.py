#!/usr/bin/env python3
"""Download sherpa-onnx English KWS zipformer (gigaspeech ~16 MB) into models/kws/."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_ROOT = ROOT / "models" / "kws"
ARCHIVE_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2"
DOWNLOAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    + ARCHIVE_NAME
)
ARCHIVE_SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"
EXTRACT_DIR_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"

REQUIRED_FILES = (
    "tokens.txt",
    "bpe.model",
    "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
    "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "keywords.txt",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def model_dir() -> Path:
    return MODEL_ROOT / EXTRACT_DIR_NAME


def model_ready() -> bool:
    d = model_dir()
    return d.is_dir() and all((d / name).is_file() for name in REQUIRED_FILES)


def fetch(*, force: bool = False) -> Path:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    if model_ready() and not force:
        print(f"KWS model already present at {model_dir()}")
        return model_dir()

    archive = MODEL_ROOT / ARCHIVE_NAME
    if not archive.is_file() or force:
        print(f"Downloading {ARCHIVE_NAME} …")
        tmp = Path(tempfile.mkdtemp(prefix="kws-dl-"))
        try:
            part = tmp / ARCHIVE_NAME
            urllib.request.urlretrieve(DOWNLOAD_URL, part)  # noqa: S310
            digest = _sha256(part)
            if digest != ARCHIVE_SHA256:
                raise RuntimeError(
                    f"SHA-256 mismatch for {ARCHIVE_NAME}: got {digest}, expected {ARCHIVE_SHA256}"
                )
            shutil.move(str(part), archive)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("Extracting …")
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(path=MODEL_ROOT, filter="data")

    if not model_ready():
        raise RuntimeError(f"Extracted KWS model incomplete under {model_dir()}")
    print(f"KWS ready at {model_dir()}")
    return model_dir()


def main() -> int:
    force = "--force" in sys.argv
    try:
        fetch(force=force)
        return 0
    except OSError as exc:
        print(f"fetch_kws failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())