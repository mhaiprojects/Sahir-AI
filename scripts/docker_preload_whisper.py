#!/usr/bin/env python3
"""Pre-download faster-whisper models during Docker build or first start."""

from __future__ import annotations

import os
import sys

from faster_whisper import WhisperModel


def main() -> int:
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    live = os.getenv("WHISPER_LIVE_MODEL", "tiny")
    final = os.getenv("WHISPER_FINAL_MODEL", "base")
    seen: set[str] = set()
    for name in (live, final):
        if name in seen:
            continue
        seen.add(name)
        print(f"Loading Whisper model {name!r} ({device}, {compute})...", flush=True)
        WhisperModel(name, device=device, compute_type=compute)
    print("Whisper preload complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
