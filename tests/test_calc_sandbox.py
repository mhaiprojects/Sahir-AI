"""Sandboxed Python execution (bwrap)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from agent.calc_sandbox import (
    SandboxError,
    SandboxUnavailable,
    run_bwrap_isolated_script,
    run_sandboxed_python,
    validate_sandbox_code,
)

BWRAP = shutil.which("bwrap")
pytestmark = pytest.mark.skipif(not BWRAP, reason="bwrap not available on host")

PROBE_SUBCLASS = (
    "o=[c for c in ().__class__.__base__.__subclasses__() "
    "if c.__name__=='_wrap_close'][0].__init__.__globals__; print(o['listdir']('/'))"
)
PROBE_GETATTR = "getattr(__builtins__,'__imp'+'ort__')('os').listdir('/dev')"

_HOST_DEV_NOISE = ("vcs", "dri", "kfd", "nvme", "sd")


def _bwrap_raw_python(code: str) -> tuple[int, str, str]:
    """Host-only: run code in bwrap with isolated interpreter (no calc runner)."""
    r = run_bwrap_isolated_script(code)
    return int(r["returncode"]), r["stdout"], r["stderr"]


def test_sandbox_computes():
    code = "import math\nprint(math.sqrt(144))"
    out = run_sandboxed_python(code)
    assert "12" in out["stdout"]
    assert out["runtime_s"] < 9.0


def test_rejects_disallowed_import():
    with pytest.raises(SandboxError, match="not allowed"):
        run_sandboxed_python("import os\nprint(1)")


def test_ast_rejects_subclass_probe():
    with pytest.raises(SandboxError, match="attribute access"):
        validate_sandbox_code(PROBE_SUBCLASS)


def test_ast_rejects_getattr_probe():
    with pytest.raises(SandboxError, match="getattr|attribute access"):
        validate_sandbox_code(PROBE_GETATTR)


def test_runner_rejects_subclass_probe():
    with pytest.raises(SandboxError):
        run_sandboxed_python(PROBE_SUBCLASS)


def test_runner_rejects_getattr_probe():
    with pytest.raises(SandboxError):
        run_sandboxed_python(PROBE_GETATTR)


def test_infinite_loop_times_out():
    with pytest.raises(SandboxError, match=r"timeout|exit 15"):
        run_sandboxed_python("while True:\n    pass")


def test_output_flood_capped():
    code = "while True:\n    print('x' * 1000)\n"
    started = time.monotonic()
    with pytest.raises(SandboxError, match="output limit"):
        run_sandboxed_python(code)
    assert time.monotonic() - started < 2.0


def test_refuses_missing_bwrap(monkeypatch):
    monkeypatch.setattr("agent.calc_sandbox._bwrap_path", lambda: None)
    with pytest.raises(SandboxUnavailable):
        run_sandboxed_python("print(1)", bwrap=None)


def test_sandbox_dev_is_minimal():
    code = "import os\nprint('\\n'.join(sorted(os.listdir('/dev'))))"
    rc, out, err = _bwrap_raw_python(code)
    assert rc == 0, err
    entries = {line.strip() for line in out.splitlines() if line.strip()}
    assert "null" in entries or "zero" in entries
    for noise in _HOST_DEV_NOISE:
        assert not any(e.startswith(noise) for e in entries), f"host device leaked: {noise!r} in {entries}"


def test_sandbox_no_home_or_repo():
    repo = str(Path(__file__).resolve().parents[1])
    code = (
        "import os\n"
        "print('HOME', os.path.isdir('/home'))\n"
        f"print('REPO', os.path.isdir({repo!r}))\n"
    )
    rc, out, _ = _bwrap_raw_python(code)
    assert rc == 0
    assert "HOME False" in out
    assert "REPO False" in out


def test_sandbox_network_fails():
    code = (
        "import socket\n"
        "s=socket.socket()\n"
        "s.settimeout(0.2)\n"
        "s.connect(('127.0.0.1', 9))\n"
    )
    r = run_bwrap_isolated_script(code)
    assert r["returncode"] != 0
    assert r["runtime_s"] < 12.0


def test_import_os_via_runner_blocked():
    with pytest.raises(SandboxError):
        run_sandboxed_python("import os\nprint(os.listdir('/'))", validate_ast=False)