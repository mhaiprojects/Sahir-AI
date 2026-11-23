"""Guard orchestrator / supervisor / Tier-2 prompt size (item 21)."""

from __future__ import annotations

from agent.clickup import ClickUpWorker
from agent.orchestrator import Orchestrator
from agent.prompt_metrics import (
    ORCH_TOKEN_CEILING,
    SUPERVISOR_TOKEN_CEILING,
    T2_TOKEN_CEILING,
    orchestrator_bundle,
    supervisor_bundle,
    tier2_bundle,
)
from agent.workers import CodeWorker, ResearchWorker
from config import Settings


class _StubDispatcher:
    def __init__(self) -> None:
        self.workers = {w.type: w for w in (ResearchWorker, CodeWorker, ClickUpWorker)}

    def catalog(self) -> list[dict[str, str]]:
        return [{"type": w.type, "description": w.description} for w in self.workers.values()]


def test_orchestrator_prompt_under_ceiling():
    orch = Orchestrator(Settings(), _StubDispatcher())
    b = orchestrator_bundle(orch)
    assert b["estimate_tokens"] <= ORCH_TOKEN_CEILING
    assert b["system_chars"] < 600
    assert b["tools_chars"] < 1400


def test_supervisor_prompt_under_ceiling():
    b = supervisor_bundle()
    assert b["estimate_tokens"] <= SUPERVISOR_TOKEN_CEILING


def test_tier2_prompt_under_ceiling():
    b = tier2_bundle()
    assert b["estimate_tokens"] <= T2_TOKEN_CEILING