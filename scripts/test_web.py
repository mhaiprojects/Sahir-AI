#!/usr/bin/env python3
"""Integration tests for the web API (no browser/mic required).

Includes a real TTS -> STT round trip: Piper synthesizes a phrase via
/api/speak, then faster-whisper transcribes it via /api/transcribe and
/api/transcribe/live. Also checks truncated / headerless WebM handling.

By default this script starts a throwaway ``python bot.py --web`` on a free
localhost port (override with ``TEST_WEB_THROWAWAY_PORT``), with ``AGENT_DB_PATH`` and
``WEB_DB_PATH`` under a temp directory, then stops only that process and
removes the temp dir — it does not write to the live ``data/agent.db``.

Opt-in to hit an already-running server (and delete conversations it creates)::

  python scripts/test_web.py --use-running
  TEST_WEB_USE_RUNNING=1 python scripts/test_web.py

When not using ``--use-running``, row counts in the live agent DB (if present)
are compared before and after; they must match.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent

_OVERRIDE_BASE_URL: str | None = None
CREATED_CONV_IDS: list[int] = []
_THROWAWAY_BEARER: str | None = None


def _set_base_url(url: str) -> None:
    global _OVERRIDE_BASE_URL
    _OVERRIDE_BASE_URL = url.rstrip("/")


def _default_base_url() -> str:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = os.getenv("WEB_PORT", "8765")
    use_ssl = bool(os.getenv("WEB_SSL_CERT", "").strip())
    scheme = "https" if use_ssl else "http"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"{scheme}://{host}:{port}"


def _base_url() -> str:
    return _OVERRIDE_BASE_URL or _default_base_url()


def _live_agent_db_path() -> Path:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    raw = os.getenv("AGENT_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "agent.db"


def _agent_db_row_counts(db_path: Path) -> dict[str, int]:
    if not db_path.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: dict[str, int] = {}
        for table in ("conversations", "messages", "memories"):
            try:
                out[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                continue
        return out
    finally:
        conn.close()


def _track_conversation_response(payload: dict | bytes) -> None:
    if not isinstance(payload, dict):
        return
    cid = payload.get("conversation_id")
    if cid is not None:
        CREATED_CONV_IDS.append(int(cid))


def _cleanup_created_conversations() -> None:
    seen: set[int] = set()
    for cid in CREATED_CONV_IDS:
        if cid in seen:
            continue
        seen.add(cid)
        status, body = _request("DELETE", f"/api/conversations/{cid}")
        if status != 200:
            print(f"warn: could not delete conversation {cid}: {status} {body}")


def _venv_python() -> Path:
    candidate = ROOT / "venv" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def _web_auth_enabled() -> bool:
    return os.getenv("WEB_AUTH_ENABLED", "true").lower() not in ("0", "false", "no")


def _resolve_bearer_token() -> str | None:
    if not _web_auth_enabled():
        return None
    if _THROWAWAY_BEARER:
        return _THROWAWAY_BEARER
    env = os.getenv("WEB_AUTH_TOKEN", "").strip()
    if env:
        return env
    from config import Settings
    from web.auth import load_web_auth_token

    return load_web_auth_token(Settings.from_env())


def _auth_headers() -> dict[str, str]:
    tok = _resolve_bearer_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 180.0) -> None:
    url = base_url.rstrip("/") + "/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(url), timeout=5.0) as resp:
                if resp.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy at {base_url}")


def _start_throwaway_server() -> tuple[subprocess.Popen[bytes], Path, str]:
    global _THROWAWAY_BEARER
    temp_dir = Path(tempfile.mkdtemp(prefix="sahir-test-web-"))
    port = int(os.getenv("TEST_WEB_THROWAWAY_PORT", "0") or "0")
    if port <= 0:
        port = _pick_free_port()
    env = os.environ.copy()
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(port)
    env["AGENT_DB_PATH"] = str(temp_dir / "agent.db")
    env["WEB_DB_PATH"] = str(temp_dir / "web.db")
    env["WEB_SSL_CERT"] = ""
    env["WEB_SSL_KEY"] = ""
    token_path = temp_dir / "web-auth-token.txt"
    token_path.write_text(secrets.token_urlsafe(32))
    env["WEB_AUTH_ENABLED"] = "true"
    env["WEB_AUTH_TOKEN_FILE"] = str(token_path)
    _THROWAWAY_BEARER = token_path.read_text().strip()
    proc = subprocess.Popen(
        [str(_venv_python()), str(ROOT / "bot.py"), "--web"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base)
    except RuntimeError:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        _stop_throwaway_server(proc, temp_dir)
        raise RuntimeError(f"throwaway server failed on {base}: {err[:500]}") from None
    return proc, temp_dir, base


def _stop_throwaway_server(proc: subprocess.Popen[bytes], temp_dir: Path) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sahir web API integration tests")
    parser.add_argument(
        "--use-running",
        action="store_true",
        help="Use the server from .env (WEB_PORT); delete conversations created during the run",
    )
    return parser.parse_args(argv)


def _request(
    method: str,
    path: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
    auth: bool = True,
) -> tuple[int, dict | bytes]:
    import ssl

    url = _base_url() + path
    req = Request(url, data=data, method=method)
    merged = {**(_auth_headers() if auth else {}), **(headers or {})}
    for key, value in merged.items():
        req.add_header(key, value)

    ctx = ssl.create_default_context()
    if url.startswith("https://"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(body.decode())
            return resp.status, body
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body}


def _silence_wav(seconds: float = 0.5, sample_rate: int = 16_000) -> bytes:
    n_frames = int(seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


PASSED: list[str] = []
ROUND_TRIP_PHRASE = "The quick brown fox jumps over the lazy dog."


def ok(*parts: object) -> None:
    PASSED.append(str(parts[0]))
    print(*parts)


def _multipart(field: str, filename: str, ctype: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----WebTestBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _post_audio(path: str, filename: str, ctype: str, payload: bytes) -> tuple[int, dict | bytes]:
    body, content_type = _multipart("audio", filename, ctype, payload)
    return _request(
        "POST",
        path,
        data=body,
        headers={"Content-Type": content_type},
        timeout=180.0,
    )


def _wav_to_webm(wav: bytes) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
         "-c:a", "libopus", "-f", "webm", "pipe:1"],
        input=wav,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode(errors="replace") or "ffmpeg failed")
    return proc.stdout


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _word_overlap(expected: str, actual: str) -> float:
    want = _words(expected)
    return len(want & _words(actual)) / max(1, len(want))


def run_extended_checks(errors: list[str], cursor: dict) -> None:
    """Checks for features added in the Nov 2026 resume (round trip, WebM, UI)."""
    status, health = _request("GET", "/api/health")
    if status == 200 and health.get("build_id"):
        ok("health build_id:", health["build_id"])
    else:
        errors.append(f"health.build_id missing: {status}")

    status, index_html = _request("GET", "/")
    if status == 200 and re.search(rb"app\.js\?v=\d{8}", index_html or b""):
        ok("index cache-bust: ok")
    else:
        errors.append("index.html missing app.js?v= cache-bust")

    if status == 200 and b"desktop-only-voice-note" in (index_html or b"") and b"Desktop app only" in (
        index_html or b""
    ):
        ok("index desktop-only voice labels: ok")
    else:
        errors.append("index.html missing desktop-only wake/PTT labels")

    status, app_js = _request("GET", "/static/app.js")
    missing = [
        name
        for name in (b"MIC_ENABLED_KEY", b"ROUTE_TOOLS_KEY", b"promptForMicPermission",
                     b"reloadForNewBuild", b"reportCursorCompletion", b"build_id")
        if name not in (app_js if isinstance(app_js, bytes) else b"")
    ]
    if status == 200 and not missing:
        ok("app.js UX features: ok")
    else:
        errors.append(f"app.js missing UX features: {missing}")

    if status == 200 and b"DESKTOP_ONLY_VOICE_NOTE" in (app_js if isinstance(app_js, bytes) else b""):
        ok("app.js desktop-only labels: ok")
    else:
        errors.append("app.js missing DESKTOP_ONLY_VOICE_NOTE")

    details = cursor.get("action_details", {}) if isinstance(cursor, dict) else {}
    if "exit_code" in details and "success" in details:
        ok("cursor completion details: ok (exit", details["exit_code"], ")")
    else:
        errors.append(f"cursor action_details lack exit_code/success: {details}")

    # Real TTS -> STT round trip
    status, speech = _request(
        "POST",
        "/api/speak",
        data=json.dumps({"message": ROUND_TRIP_PHRASE, "route": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not isinstance(speech, bytes) or speech[:4] != b"RIFF":
        errors.append(f"round trip speak failed: {status}")
        return
    ok("round trip speak:", len(speech), "bytes WAV")

    status, final = _post_audio(
        "/api/transcribe?route=false&phase=final&mode=ptt", "speech.wav", "audio/wav", speech
    )
    transcript = final.get("transcript", "") if isinstance(final, dict) else ""
    overlap = _word_overlap(ROUND_TRIP_PHRASE, transcript)
    if status == 200 and overlap >= 0.7:
        ok(f"round trip final STT: {overlap:.0%} words -> {transcript!r}")
    else:
        errors.append(f"round trip final STT failed: {status} overlap={overlap:.0%} {final}")

    status, live = _post_audio("/api/transcribe/live", "speech.wav", "audio/wav", speech)
    live_text = live.get("transcript", "") if isinstance(live, dict) else ""
    if status == 200 and _word_overlap(ROUND_TRIP_PHRASE, live_text) >= 0.5:
        ok(f"round trip live STT: {live_text!r}")
    else:
        errors.append(f"round trip live STT failed: {status} {live}")

    # WebM robustness
    try:
        webm = _wav_to_webm(speech)
    except Exception as exc:
        errors.append(f"could not build webm fixture: {exc}")
        return

    status, full_webm = _post_audio("/api/transcribe/live", "speech.webm", "audio/webm", webm)
    if status == 200 and _word_overlap(ROUND_TRIP_PHRASE, full_webm.get("transcript", "")) >= 0.5:
        ok("live webm STT:", repr(full_webm.get("transcript", "")))
    else:
        errors.append(f"live webm STT failed: {status} {full_webm}")

    status, half = _post_audio(
        "/api/transcribe/live", "half.webm", "audio/webm", webm[: len(webm) // 2]
    )
    if status == 200 and half.get("phase") == "live" and not half.get("error"):
        ok("live truncated webm (half) salvaged:", repr(half.get("transcript", "")))
    else:
        errors.append(f"live truncated webm (half) failed: {status} {half}")

    for label, chunk in (
        ("header-only", webm[:300]),
        ("headerless tail", webm[len(webm) // 2 :]),
    ):
        status, res = _post_audio("/api/transcribe/live", "chunk.webm", "audio/webm", chunk)
        if status == 200 and res.get("skipped") == "partial_audio":
            ok(f"live {label} webm: skipped partial_audio")
        else:
            errors.append(f"live {label} webm not skipped gracefully: {status} {res}")

    status, res = _post_audio(
        "/api/transcribe?route=false&mode=always", "chunk.webm", "audio/webm", webm[:300]
    )
    if status == 400 and "ws/voice" in str(res.get("detail", "")):
        ok("always-mode rejected (use /ws/voice)")
    else:
        errors.append(f"always-mode should be rejected: {status} {res}")

    status, res = _post_audio(
        "/api/transcribe?route=false&mode=ptt", "chunk.webm", "audio/webm", webm[:300]
    )
    if status == 400 and "incomplete" in str(res.get("detail", "")):
        ok("ptt truncated webm: friendly 400")
    else:
        errors.append(f"ptt truncated webm unexpected: {status} {res}")


def _run_auth_checks(errors: list[str]) -> None:
    if not _web_auth_enabled():
        return
    status, _ = _request("GET", "/api/config", auth=False)
    if status != 401:
        errors.append(f"auth: expected 401 without credentials, got {status}")
    else:
        ok("auth: protected API returns 401")
    status, _ = _request("GET", "/api/health", auth=False)
    if status != 200 or not isinstance(_, dict) or _.get("status") != "ok":
        errors.append(f"auth: public health unexpected: {status} {_}")
    else:
        ok("auth: public health ok")
    tok = _resolve_bearer_token()
    if not tok:
        errors.append("auth: no bearer token for integration tests")
        return
    status, login = _request(
        "POST",
        "/api/auth/login",
        data=json.dumps({"token": "not-the-token"}).encode(),
        headers={"Content-Type": "application/json"},
        auth=False,
    )
    if status != 401:
        errors.append(f"auth: bad login should be 401, got {status}")
    else:
        ok("auth: bad login rejected")


def run_all_tests(errors: list[str]) -> None:
    """Exercise the web API; append failures to errors."""
    _run_auth_checks(errors)

    status, health = _request("GET", "/api/health")
    if status != 200:
        errors.append(f"health failed: {status} {health}")
    elif health.get("auth_required"):
        ok("health: ok (minimal)")
    else:
        ok("health: ok")
        for key in ("ollama", "cursor_cli", "piper", "whisper"):
            if not health.get(key, {}).get("ok", False):
                errors.append(f"health.{key} not ok: {health.get(key)}")
        tx = health.get("transcription", {})
        if tx.get("conversation_silence_seconds") != 3.0:
            errors.append(f"unexpected silence seconds: {tx}")
        if not tx.get("live_stt_backend"):
            errors.append(f"transcription.live_stt_backend missing: {tx}")
        else:
            ok("transcription.live_stt_backend:", tx.get("live_stt_backend"))
        llm = health.get("llm", {})
        if llm.get("backend") not in ("ollama", "openai"):
            errors.append(f"health.llm missing backend: {llm}")
        else:
            ok("llm.backend:", llm.get("backend"))
        if not health.get("https_enabled"):
            print("warn: HTTPS not enabled (mobile mic needs it)")

    status, cfg = _request("GET", "/api/config")
    if status != 200 or cfg.get("conversation_silence_seconds") != 3.0:
        errors.append(f"config failed: {status} {cfg}")
    else:
        ok("config: ok")

    status, _ = _request("GET", "/")
    if status != 200:
        errors.append(f"index failed: {status}")
    else:
        ok("index: ok")

    status, static = _request("GET", "/static/app.js")
    if status != 200 or b"sendToCursor" not in static:
        errors.append("static app.js missing expected functions")
    else:
        ok("static: ok")

    status, md_js = _request("GET", "/static/markdown.js")
    if status != 200 or b"renderMarkdown" not in md_js:
        errors.append("static markdown.js missing")
    else:
        ok("markdown static: ok")

    chat_body = json.dumps({"message": "What is 2+2?", "route": False}).encode()
    status, chat = _request(
        "POST",
        "/api/chat",
        data=chat_body,
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not chat.get("reply") or not chat.get("log_id"):
        errors.append(f"chat failed: {status} {chat}")
    else:
        ok("chat:", chat["reply"][:60])
        _track_conversation_response(chat)

    history_body = json.dumps(
        {
            "message": "What did I just ask?",
            "route": False,
            "history": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": chat.get("reply", "4")},
            ],
        }
    ).encode()
    status, history_chat = _request(
        "POST",
        "/api/chat",
        data=history_body,
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not history_chat.get("reply"):
        errors.append(f"chat history failed: {status} {history_chat}")
    else:
        ok("chat history: ok")
        _track_conversation_response(history_chat)

    cursor_body = json.dumps(
        {
            "message": "List files in the project root only.",
            "history": [],
        }
    ).encode()
    status, cursor = _request(
        "POST",
        "/api/cursor",
        data=cursor_body,
        headers={"Content-Type": "application/json"},
        timeout=180.0,
    )
    if status != 200 or not cursor.get("reply") or cursor.get("action") != "run_cursor_agent":
        errors.append(f"cursor endpoint failed: {status} {cursor}")
    else:
        ok("cursor endpoint: ok")
        _track_conversation_response(cursor)

    status, logs = _request("GET", "/api/logs?limit=5")
    if status != 200 or not logs.get("logs"):
        errors.append(f"logs failed: {status} {logs}")
    else:
        newest = logs["logs"][0]
        if newest["id"] != cursor.get("log_id"):
            errors.append("logs not newest-first or cursor request not logged")
        elif len(logs["logs"]) > 1 and logs["logs"][0]["id"] < logs["logs"][1]["id"]:
            errors.append("logs not sorted newest-first")
        else:
            ok("logs: ok (newest first)")

    wav = _silence_wav()
    boundary = "----WebTestBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="silence.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav + f"\r\n--{boundary}--\r\n".encode()
    status, live = _request(
        "POST",
        "/api/transcribe/live",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status != 200 or live.get("phase") != "live":
        errors.append(f"live transcribe failed: {status} {live}")
    else:
        ok("live transcribe: ok")

    status, speak = _request(
        "POST",
        "/api/speak",
        data=json.dumps({"message": "Hello", "route": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not isinstance(speak, bytes) or speak[:4] != b"RIFF":
        errors.append(f"speak failed: {status}")
    else:
        ok("speak: ok")

    status, speak_md = _request(
        "POST",
        "/api/speak",
        data=json.dumps({"message": "**Bold** and `code`", "route": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not isinstance(speak_md, bytes) or speak_md[:4] != b"RIFF":
        errors.append(f"speak markdown failed: {status}")
    else:
        ok("speak markdown strip: ok")

    route_body = json.dumps({"message": "run uptime", "route": True}).encode()
    status, routed = _request(
        "POST",
        "/api/chat",
        data=route_body,
        headers={"Content-Type": "application/json"},
        timeout=180.0,
    )
    if status != 200:
        errors.append(f"routed chat failed: {status} {routed}")
    elif routed.get("action") not in {"run_command", "local_chat", "fallback_chat"}:
        errors.append(f"unexpected route action: {routed.get('action')}")
    else:
        ok("pc route:", routed.get("action"), routed.get("reply", "")[:60])
        _track_conversation_response(routed)

    status, cmds = _request("GET", "/api/commands")
    if status != 200 or not cmds.get("commands"):
        errors.append(f"commands list failed: {status} {cmds}")
    else:
        ok("commands:", len(cmds["commands"]), "defined")

    match_body = json.dumps({"text": "please stop talking now"}).encode()
    status, matched = _request(
        "POST",
        "/api/commands/match",
        data=match_body,
        headers={"Content-Type": "application/json"},
    )
    if status != 200 or not matched.get("matched") or matched.get("action") != "stop_speech":
        errors.append(f"command match failed: {status} {matched}")
    else:
        ok("command match: stop")

    run_extended_checks(errors, cursor if isinstance(cursor, dict) else {})


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    use_running = args.use_running or os.getenv("TEST_WEB_USE_RUNNING", "").strip() in {
        "1",
        "true",
        "yes",
    }
    live_db = _live_agent_db_path()
    before_counts = _agent_db_row_counts(live_db)

    proc: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    errors: list[str] = []

    try:
        if use_running:
            _set_base_url(_default_base_url())
            print("Mode: use-running (cleanup conversations created during test)")
        else:
            proc, temp_dir, base = _start_throwaway_server()
            _set_base_url(base)
            print("Mode: throwaway server (isolated temp databases)")

        print(f"Testing {_base_url()}")
        run_all_tests(errors)

        if use_running:
            _cleanup_created_conversations()
    except RuntimeError as exc:
        errors.append(str(exc))
    finally:
        if proc is not None and temp_dir is not None:
            _stop_throwaway_server(proc, temp_dir)

    after_counts = _agent_db_row_counts(live_db)
    if before_counts != after_counts:
        errors.append(
            f"live agent DB row counts changed: before={before_counts} after={after_counts} ({live_db})"
        )
    elif before_counts and not use_running:
        ok("live agent.db unchanged:", before_counts)

    print(f"\nSummary: {len(PASSED)} passed, {len(errors)} failed")
    if errors:
        print("\nFAILED:")
        for err in errors:
            print(" -", err)
        return 1

    print("\nAll web integration tests passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except URLError as exc:
        print(f"Could not reach server: {exc}", file=sys.stderr)
        print("By default this script starts its own server; use --use-running for an existing one.", file=sys.stderr)
        raise SystemExit(2) from exc
