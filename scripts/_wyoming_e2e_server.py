#!/usr/bin/env python3
"""Test-only Wyoming server: stub embedder + orchestrator client for scripts/wyoming_e2e.py."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class WyomingE2EEmbedder:
    model = "wyoming-e2e-embed"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 8


class WyomingE2EClient:
    def chat(self, model, messages, tools=None, options=None):
        return {"message": {"content": "Wyoming e2e stub reply.", "tool_calls": []}}


def main(argv: list[str] | None = None) -> int:
    from config import Settings
    from wyoming_server.__main__ import _parse_args, run_services
    from wyoming_server.config import WyomingSettings
    from wyoming_server.services import WyomingServices

    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    wy = WyomingSettings.from_env()
    wy.handle_enabled = True
    if args.uri:
        wy.uri = args.uri.strip()
    app_settings = Settings.from_env()
    services = WyomingServices(
        app_settings=app_settings,
        wyoming_settings=wy,
        embedder=WyomingE2EEmbedder(),
        orchestrator_client=WyomingE2EClient(),
    )
    return run_services(services, wy=wy, uri=args.uri)


if __name__ == "__main__":
    raise SystemExit(main())