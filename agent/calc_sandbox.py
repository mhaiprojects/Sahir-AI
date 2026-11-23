"""Bubblewrap-isolated Python execution for the calc worker."""

from __future__ import annotations

import ast
import os
import re
import select
import shutil
import subprocess
import sys
import sysconfig
import textwrap
import time
from pathlib import Path
from typing import Any

_ALLOWED_IMPORTS = frozenset({
    "math", "statistics", "fractions", "decimal", "datetime", "itertools", "functools", "collections",
})
_MAX_OUT = 16_384
_WALL_TIMEOUT = 10.0
_CPU_SOFT = 5

_BANNED_NAMES = frozenset({
    "open", "eval", "exec", "compile", "__import__", "socket",
    "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir",
    "__builtins__", "breakpoint", "input", "help", "memoryview", "type", "object",
    "bytes", "bytearray", "super", "classmethod", "staticmethod", "property",
    "importlib", "builtins", "sys", "os", "subprocess", "pathlib", "ctypes",
})

_DYNAMIC_NAME_RE = re.compile(
    r"(?i)(import|getattr|setattr|globals|builtins|subclass|__.*__)"
)


class SandboxError(RuntimeError):
    pass


class SandboxUnavailable(SandboxError):
    pass


def _bwrap_path() -> str | None:
    return shutil.which("bwrap")


def _check_name(name: str) -> None:
    if not name:
        raise SandboxError("empty name not allowed")
    if name.startswith("_"):
        raise SandboxError(f"name {name!r} not allowed")
    if name in _BANNED_NAMES:
        raise SandboxError(f"name {name!r} not allowed")


def validate_sandbox_code(code: str) -> None:
    """AST defence-in-depth before bwrap."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SandboxError(f"invalid Python syntax: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise SandboxError("attribute access not allowed in sandbox snippet")
            base = node.value
            if not (isinstance(base, ast.Name) and base.id in _ALLOWED_IMPORTS):
                raise SandboxError("attribute access not allowed in sandbox snippet")
        if isinstance(node, ast.Name):
            _check_name(node.id)
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    raise SandboxError(f"import {root!r} not allowed")
                _check_name(root)
        if isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    raise SandboxError(f"import from {root!r} not allowed")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DYNAMIC_NAME_RE.search(node.value):
                raise SandboxError("suspicious string literal in sandbox snippet")
        if isinstance(node, ast.JoinedStr):
            raise SandboxError("f-strings not allowed in sandbox snippet")


def _base_interpreter() -> Path:
    raw = getattr(sys, "_base_executable", None) or sys.executable
    return Path(os.path.realpath(raw))


def _python_bind_args(py: Path) -> list[str]:
    """Read-only binds for isolated CPython (stdlib only, no venv site-packages)."""
    prefix = Path(sys.base_prefix).resolve()
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    stdlib = Path(sysconfig.get_path("stdlib", vars={"base": str(prefix), "platbase": str(prefix)}))
    if not stdlib.is_dir():
        stdlib = prefix / f"lib/python{ver}"
    platstdlib = Path(sysconfig.get_path("platstdlib", vars={"base": str(prefix), "platbase": str(prefix)}))
    binds: list[str] = []
    seen: set[str] = set()

    def add_ro(path: Path) -> None:
        if not path.is_dir():
            return
        # Use the configured mount path (do not resolve symlinks like /lib -> /usr/lib).
        key = str(path)
        if key not in seen:
            seen.add(key)
            binds.extend(["--ro-bind", key, key])

    add_ro(py.parent)
    add_ro(stdlib)
    if platstdlib.is_dir() and str(platstdlib) != str(stdlib):
        add_ro(platstdlib)
    dynload = stdlib / "lib-dynload"
    if dynload.is_dir():
        add_ro(dynload)
    # Dynamic linker + libc for the base interpreter only.
    for libdir in (Path("/lib"), Path("/lib64"), Path("/usr/lib"), Path("/usr/lib64")):
        if libdir.is_dir():
            add_ro(libdir)
    return binds


def _sandbox_runner_source(user_code: str) -> str:
    return textwrap.dedent(
        f"""
        import importlib
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, ({_CPU_SOFT}, {_CPU_SOFT + 1}))
        resource.setrlimit(resource.RLIMIT_AS, ({512 * 1024 * 1024}, {512 * 1024 * 1024}))
        resource.setrlimit(resource.RLIMIT_FSIZE, ({1024 * 1024}, {1024 * 1024}))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        except (ValueError, OSError):
            pass
        _ALLOWED = {{{", ".join(repr(x) for x in sorted(_ALLOWED_IMPORTS))}}}
        def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = (name or "").split(".")[0]
            if root not in _ALLOWED:
                raise ImportError(f"import {{root!r}} not allowed")
            return importlib.import_module(name, package=None)
        _pre = {{m: importlib.import_module(m) for m in _ALLOWED}}
        _builtins = {{
            "print": print, "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
            "len": len, "range": range, "int": int, "float": float, "bool": bool,
            "list": list, "tuple": tuple, "dict": dict, "set": set, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter, "sorted": sorted, "all": all, "any": any,
            "pow": pow, "divmod": divmod, "True": True, "False": False, "None": None,
            "__import__": _safe_import,
        }}
        _globals = {{"__name__": "__main__", "__builtins__": _builtins, **_pre}}
        exec(compile({user_code!r}, "<calc>", "exec"), _globals)
        """
    ).strip()


def _bwrap_argv(py: Path, script: str) -> list[str]:
    bwrap = _bwrap_path()
    if not bwrap:
        raise SandboxUnavailable("bubblewrap (bwrap) is not available; sandboxed calc refused")
    binds = _python_bind_args(py)
    return [
        bwrap,
        "--unshare-all",
        "--unshare-user",
        "--cap-drop",
        "ALL",
        "--die-with-parent",
        "--new-session",
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        *binds,
        "--chdir",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--setenv",
        "HOME",
        "/tmp",
        str(py),
        "-I",
        "-S",
        "-c",
        script,
    ]


def run_bwrap_isolated_script(script: str) -> dict[str, Any]:
    """Run arbitrary ``-c`` script inside bwrap (tests / probes only)."""
    py = _base_interpreter()
    cmd = _bwrap_argv(py, script)
    rc, out, err, runtime = _run_bwrap_process(cmd)
    return {
        "returncode": rc,
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
        "runtime_s": round(runtime, 3),
    }


def _kill_bwrap_process(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (OSError, ProcessLookupError):
        proc.kill()


def _append_capped(buf: bytearray, chunk: bytes, *, total_so_far: int) -> tuple[int, bool]:
    """Append up to remaining cap; return (new_total, exceeded)."""
    if not chunk:
        return total_so_far, False
    room = _MAX_OUT - total_so_far
    if room <= 0:
        return total_so_far, True
    if len(chunk) > room:
        buf.extend(chunk[:room])
        return total_so_far + room, True
    buf.extend(chunk)
    new_total = total_so_far + len(chunk)
    return new_total, new_total >= _MAX_OUT


def _drain_available(stream, buf: bytearray, total: int) -> tuple[int, bool]:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return total, False
        total, over = _append_capped(buf, chunk, total_so_far=total)
        if over:
            return total, True
    return total, False


def _run_bwrap_process(cmd: list[str]) -> tuple[int, bytes, bytes, float]:
    """Run bwrap command; stream stdout/stderr with a 16 KB combined cap."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    out_buf = bytearray()
    err_buf = bytearray()
    total = 0
    try:
        assert proc.stdout is not None and proc.stderr is not None
        while True:
            if time.monotonic() - t0 > _WALL_TIMEOUT:
                _kill_bwrap_process(proc)
                proc.wait(timeout=2)
                raise SandboxError("sandbox wall-clock timeout")
            if proc.poll() is not None:
                total, over = _drain_available(proc.stdout, out_buf, total)
                if over:
                    _kill_bwrap_process(proc)
                    proc.wait(timeout=2)
                    raise SandboxError("sandbox output limit exceeded")
                total, over = _drain_available(proc.stderr, err_buf, total)
                if over:
                    _kill_bwrap_process(proc)
                    proc.wait(timeout=2)
                    raise SandboxError("sandbox output limit exceeded")
                break
            rlist, _, _ = select.select([proc.stdout, proc.stderr], [], [], 0.05)
            for stream in rlist:
                chunk = stream.read(4096)
                if not chunk:
                    continue
                buf = out_buf if stream is proc.stdout else err_buf
                total, over = _append_capped(buf, chunk, total_so_far=total)
                if over:
                    _kill_bwrap_process(proc)
                    proc.wait(timeout=2)
                    raise SandboxError("sandbox output limit exceeded")
        proc.wait(timeout=2)
    finally:
        if proc.poll() is None:
            _kill_bwrap_process(proc)
            proc.wait(timeout=2)
    runtime = time.monotonic() - t0
    return proc.returncode or 0, bytes(out_buf), bytes(err_buf), runtime


def run_sandboxed_python(code: str, *, bwrap: str | None = None, validate_ast: bool = True) -> dict[str, Any]:
    """Run ``code`` in bwrap. Refuses when bwrap is missing (no unsandboxed fallback)."""
    if validate_ast:
        validate_sandbox_code(code)
    py = _base_interpreter()
    runner = _sandbox_runner_source(code)
    cmd = _bwrap_argv(py, runner)
    if bwrap is not None:
        cmd[0] = bwrap
    try:
        rc, out, err, runtime = _run_bwrap_process(cmd)
    except OSError as exc:
        raise SandboxError(f"sandbox spawn failed: {exc}") from exc
    stdout = out.decode(errors="replace").strip()
    stderr = err.decode(errors="replace").strip()
    if rc != 0:
        raise SandboxError(stderr or f"sandbox exit {rc}")
    return {"stdout": stdout, "stderr": stderr, "runtime_s": round(runtime, 3)}


# Alias for tests that introspect runner without duplicating policy.
_validate_imports = validate_sandbox_code