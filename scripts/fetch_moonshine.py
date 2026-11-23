#!/usr/bin/env python3
"""Download sherpa-onnx Moonshine tiny en int8 (~100 MB) into models/moonshine/."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_ROOT = ROOT / "models" / "moonshine"
ARCHIVE_NAME = "sherpa-onnx-moonshine-tiny-en-int8.tar.bz2"
DOWNLOAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    + ARCHIVE_NAME
)
# SHA-256 of the release archive (sherpa-onnx asr-models tag).
ARCHIVE_SHA256 = "d5fe6ec4334fef36255b2a4010412cad4c007e33103fec62fb5d17cad88086f2"

REQUIRED_FILES = (
    "preprocess.onnx",
    "encode.int8.onnx",
    "uncached_decode.int8.onnx",
    "cached_decode.int8.onnx",
    "tokens.txt",
)

EXTRACT_DIR_NAME = "sherpa-onnx-moonshine-tiny-en-int8"


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
        print(f"Moonshine model already present at {model_dir()}")
        return model_dir()

    archive = MODEL_ROOT / ARCHIVE_NAME
    if not archive.is_file() or force:
        print(f"Downloading {ARCHIVE_NAME} …")
        tmp = Path(tempfile.mkdtemp(prefix="moonshine-dl-"))
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
        raise RuntimeError(f"Extracted model incomplete under {model_dir()}")
    print(f"Moonshine ready at {model_dir()}")
    return model_dir()


def main() -> int:
    force = "--force" in sys.argv
    try:
        fetch(force=force)
        return 0
    except OSError as exc:
        print(f"fetch_moonshine failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())