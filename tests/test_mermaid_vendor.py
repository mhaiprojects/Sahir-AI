"""Vendored Mermaid static asset (browser global build)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import web_server
from fastapi.testclient import TestClient

VENDOR = Path(__file__).resolve().parent.parent / "web" / "static" / "vendor" / "mermaid.min.js"


def test_mermaid_vendor_is_browser_global_build():
    text = VENDOR.read_text(encoding="utf-8", errors="replace")
    assert "globalThis.mermaid" in text or "window.mermaid" in text
    assert "__esbuild_esm_mermaid" in text
    assert text.strip().endswith("globalThis.mermaid = globalThis.__esbuild_esm_mermaid.default;")


def test_mermaid_vendor_sha_documented():
    digest = hashlib.sha256(VENDOR.read_bytes()).hexdigest()
    md = (VENDOR.parent / "mermaid.vendor.md").read_text(encoding="utf-8")
    assert digest in md


def test_static_route_serves_mermaid():
    client = TestClient(web_server.create_app())
    r = client.get("/static/vendor/mermaid.min.js")
    assert r.status_code == 200
    assert "javascript" in (r.headers.get("content-type") or "")
    assert b"globalThis.mermaid" in r.content[:4000] or b"globalThis.mermaid" in r.content[-500:]