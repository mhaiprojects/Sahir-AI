"""Lint Docker VPS PoC files (item 22)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.vps.yml"
DOCKERFILE = ROOT / "Dockerfile"

FORBIDDEN_HOST_PORTS = {8765, 11434}


def _host_port_bindings(compose: dict) -> list[int]:
    ports: list[int] = []
    for svc in (compose.get("services") or {}).values():
        for spec in svc.get("ports") or []:
            if isinstance(spec, int):
                ports.append(spec)
                continue
            parts = str(spec).split(":")
            if len(parts) >= 2 and parts[-2].isdigit():
                ports.append(int(parts[-2]))
            elif parts[0].isdigit():
                ports.append(int(parts[0]))
    return ports


def test_compose_vps_loads_and_labels():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert data.get("name") == "sahir-vps-poc"
    for svc in (data.get("services") or {}).values():
        labels = svc.get("labels") or {}
        assert labels.get("com.sahir.trial") == "jw12"


def test_compose_vps_no_forbidden_host_ports():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    bound = set(_host_port_bindings(data))
    assert not bound.intersection(FORBIDDEN_HOST_PORTS)
    assert 8768 in bound


def test_compose_vps_ollama_not_published():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    ollama = data["services"]["ollama"]
    assert "ports" not in ollama or not ollama.get("ports")


def test_dockerfile_has_minimal_and_full_targets():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM\s+.*\s+AS\s+minimal\s*$", text, re.M)
    assert re.search(r"^FROM\s+.*\s+AS\s+full\s*$", text, re.M)