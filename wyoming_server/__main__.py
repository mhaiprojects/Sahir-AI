"""Run the optional Sahir Wyoming protocol server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from config import Settings
from wyoming.server import AsyncTcpServer

from wyoming_server.config import WyomingSettings, parse_tcp_bind, validate_bind
from wyoming_server.handler import handler_factory
from wyoming_server.services import WyomingServices


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sahir Wyoming STT/TTS (and optional handle) server")
    p.add_argument(
        "--uri",
        default=None,
        help="tcp:// host:port (default from WYOMING_URI or tcp://127.0.0.1:10300)",
    )
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def run_services(services: WyomingServices, *, wy: WyomingSettings | None = None, uri: str | None = None) -> int:
    """Start the Wyoming TCP server with a pre-built ``WyomingServices`` instance."""
    wy = wy or services.wyoming_settings
    if uri:
        wy = WyomingSettings(
            uri=uri.strip(),
            allow_lan=wy.allow_lan,
            handle_enabled=wy.handle_enabled,
            max_audio_seconds=wy.max_audio_seconds,
            project_name=wy.project_name,
            conversation_title=wy.conversation_title,
        )
        services.wyoming_settings = wy
    host, port = validate_bind(wy)
    logger = logging.getLogger("wyoming_server")
    logger.info(
        "Wyoming server on %s:%s (handle=%s, max_audio=%.0fs)",
        host,
        port,
        wy.handle_enabled,
        wy.max_audio_seconds,
    )

    async def _run() -> None:
        server = AsyncTcpServer(host, port)
        await server.run(handler_factory(services))

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    wy = WyomingSettings.from_env()
    if args.uri:
        wy.uri = args.uri.strip()
    app_settings = Settings.from_env()
    services = WyomingServices(app_settings=app_settings, wyoming_settings=wy)
    return run_services(services, wy=wy, uri=args.uri)


if __name__ == "__main__":
    raise SystemExit(main())