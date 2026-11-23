"""Design worker validation and routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.design_worker import DesignWorker, heuristic_design_intent
from agent.mermaid_validate import validate_mermaid
from agent.worker_message_meta import worker_message_meta_from_tasks
from tests.conftest import FakeLLM


_VALID = """---
title: Request flow
---
flowchart TD
    A[Start] --> B[Stop]
"""


def test_validate_mermaid_ok():
    v = validate_mermaid(_VALID)
    assert v.ok
    assert v.diagram_type == "flowchart"


def test_validate_rejects_empty():
    assert not validate_mermaid("").ok


def test_heuristic_design_positive():
    assert heuristic_design_intent("draw a flowchart of the login process")
    assert heuristic_design_intent("sequence diagram of payment API")


def test_heuristic_design_negative_code():
    assert not heuristic_design_intent("refactor the python module for auth")
    assert not heuristic_design_intent("implement unit tests in pytest")


def test_worker_stores_message_meta():
    llm = FakeLLM(reply=_VALID)

    def repair(*a, **k):
        return _VALID

    w = DesignWorker(llm)
    w._generate = lambda text: _VALID  # type: ignore[method-assign]
    w._repair = repair  # type: ignore[method-assign]
    task = MagicMock()
    task.description = "draw flowchart"
    task.context = {"query": "draw flowchart"}
    out = w.run(task)
    meta = out["message_meta"]
    assert meta["kind"] == "diagram"
    assert "flowchart" in meta["mermaid"]
    assert "diagram" in meta["tts_text"].lower()
    assert meta["diagram_steps"] >= 1


def test_worker_message_meta_from_tasks():
    tasks = [
        {
            "result": {
                "output": {"message_meta": {"kind": "diagram", "mermaid": _VALID}},
            }
        }
    ]
    assert worker_message_meta_from_tasks(tasks)["kind"] == "diagram"