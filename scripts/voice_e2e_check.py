#!/usr/bin/env python3
"""Automated voice path checks: Piper TTS → /ws/voice PCM stream, PTT transcribe, barge-in.

Default: starts a throwaway ``bot.py --web`` on a free port with a temp agent DB (like
``scripts/test_web.py``). Use ``--use-running`` to hit the server from``.env`` (e.g.
``https://127.0.0.1:8765``). When reachable, also runs against ``https://192.168.1.220:8765``.

Auth: bearer token from ``WEB_AUTH_TOKEN_FILE`` / env when ``WEB_AUTH_ENABLED`` is on.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.voice_pcm_utils import (  # noqa: E402
    pad_utterance_pcm,
    pcm16_stream_chunks,
    pcm16_to_wav,
    wav_bytes_to_pcm16_mono,
)

DEFAULT_UTTERANCES = (
    "switch to the supervisor",
    "Supervisor, please describe every active chat in this project in one detailed sentence each",
    "what is two plus two",
    "stop",
)
PARTIAL_MIN_SAMPLES = 24_000  # ~1.5 s speech @ 16 kHz
BARGE_INTERRUPT_TEXT = "stop talking"
BARGE_PROMPT_TEXT = (
    "Supervisor, list every chat in this project with a full sentence about each one, "
    "including titles and what we last discussed."
)
PTT_PHRASE = "what is two plus two"
LAN_URL = "https://192.168.1.220:8765"
CHUNK_SLEEP_S = 0.064  # ~realtime for 1024-sample frames


def _load_test_web():
    spec = importlib.util.spec_from_file_location(
        "sahir_test_web", ROOT / "scripts" / "test_web.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _word_overlap(expected: str, actual: str) -> float:
    want = _words(expected)
    if not want:
        return 1.0 if not actual.strip() else 0.0
    return len(want & _words(actual)) / len(want)


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _url_reachable(base_url: str, timeout: float = 4.0) -> bool:
    url = base_url.rstrip("/") + "/api/health"
    try:
        with urlopen(Request(url), timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.status == 200
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


@dataclass
class TurnTiming:
    label: str
    expected: str
    t_pcm_done: float | None = None
    t_first_partial: float | None = None
    t_transcript: float | None = None
    t_reply: float | None = None
    t_first_audio: float | None = None
    transcript: str = ""
    reply_text: str = ""
    route_tier: Any = None
    route_label: str = ""
    reply_action: str = ""
    partial_count: int = 0
    errors: list[str] = field(default_factory=list)

    def ms_after_pcm(self, t: float | None) -> str:
        if t is None or self.t_pcm_done is None:
            return "—"
        return f"{int((t - self.t_pcm_done) * 1000)}"


async def _voice_turn(
    ws_url: str,
    pcm: bytes,
    *,
    token: str | None,
    conversation_id: int | None = None,
    barge_pcm: bytes | None = None,
    timeout: float = 600.0,
) -> tuple[list[dict[str, Any]], list[bytes]]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    events: list[dict[str, Any]] = []
    wavs: list[bytes] = []
    t0 = time.monotonic()
    scheme = "wss" if ws_url.startswith("wss") else "ws"
    ssl_arg = _ssl_ctx() if scheme == "wss" else None

    async with websockets.connect(
        ws_url,
        ssl=ssl_arg,
        additional_headers=headers,
        max_size=2**24,
        open_timeout=30,
    ) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        events.append({**first, "_t": time.monotonic() - t0})
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "conversation_id": conversation_id,
                    "mode": "agent",
                    "tts": True,
                }
            )
        )
        second = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        events.append({**second, "_t": time.monotonic() - t0})

        pcm_done_t: float | None = None
        sent_barge = False
        finished = asyncio.Event()
        saw_final = False
        saw_reply = False

        async def _inject_barge() -> None:
            if not barge_pcm:
                return
            for chunk in pcm16_stream_chunks(barge_pcm):
                await ws.send(chunk)
                await asyncio.sleep(CHUNK_SLEEP_S)

        async def _sender() -> None:
            nonlocal pcm_done_t
            for chunk in pcm16_stream_chunks(pcm):
                await ws.send(chunk)
                await asyncio.sleep(CHUNK_SLEEP_S)
            pcm_done_t = time.monotonic() - t0

        async def _maybe_barge() -> None:
            nonlocal sent_barge
            if barge_pcm and not sent_barge:
                sent_barge = True
                await _inject_barge()

        async def _receiver() -> None:
            while not finished.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    events.append({"type": "timeout", "_t": time.monotonic() - t0})
                    finished.set()
                    break
                if isinstance(msg, bytes):
                    wavs.append(msg)
                    await _maybe_barge()
                    continue
                ev = json.loads(msg)
                ev["_t"] = time.monotonic() - t0
                if pcm_done_t is not None:
                    ev["_t_pcm_done"] = pcm_done_t
                events.append(ev)
                kind = ev.get("type")
                if kind in ("audio",) or (
                    kind == "status" and ev.get("state") == "speaking"
                ):
                    await _maybe_barge()
                if kind == "transcript" and not ev.get("discarded") and str(
                    ev.get("text") or ""
                ).strip():
                    saw_final = True
                if kind == "reply":
                    saw_reply = True
                    if ev.get("action") in (
                        "stop_speech",
                        "cancel_request",
                        "pause_listening",
                    ):
                        finished.set()
                        break
                if kind == "error":
                    finished.set()
                    break
                if (
                    kind == "tts_done"
                    and (not barge_pcm or sent_barge)
                    and pcm_done_t is not None
                    and saw_reply
                ):
                    finished.set()
                    break
                if kind == "tts_done" and (not barge_pcm or sent_barge) and saw_final and saw_reply:
                    finished.set()
                    break

        await asyncio.gather(_sender(), _receiver())
    return events, wavs


async def _voice_text_turn(
    ws_url: str,
    text: str,
    *,
    token: str | None,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    """Route+audio via ``text`` + ``as_voice`` (used for short stop commands)."""
    headers = {"Authorization": f"Bearer {token}"} if token else None
    ssl_arg = _ssl_ctx() if ws_url.startswith("wss") else None
    events: list[dict[str, Any]] = []
    t0 = time.monotonic()

    async with websockets.connect(
        ws_url,
        ssl=ssl_arg,
        additional_headers=headers,
        max_size=2**24,
        open_timeout=30,
    ) as ws:
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "conversation_id": conversation_id,
                    "mode": "agent",
                    "tts": True,
                }
            )
        )
        await ws.recv()
        await ws.send(json.dumps({"type": "text", "text": text, "as_voice": True}))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
            if isinstance(msg, bytes):
                continue
            ev = json.loads(msg)
            ev["_t"] = time.monotonic() - t0
            events.append(ev)
            if ev.get("type") == "reply":
                break
            if ev.get("type") == "tts_done":
                break
            if ev.get("type") == "error":
                break
    return events


async def _barge_in_test(
    ws_url: str,
    *,
    token: str | None,
    conversation_id: int | None,
    tw,
) -> list[str]:
    """Long supervisor answer via text inject; stream barge PCM on first TTS chunk."""
    headers = {"Authorization": f"Bearer {token}"} if token else None
    ssl_arg = _ssl_ctx() if ws_url.startswith("wss") else None
    errors: list[str] = []
    barge_wav = _synthesize_wav(tw, BARGE_INTERRUPT_TEXT)
    barge_speech, _ = wav_bytes_to_pcm16_mono(barge_wav)
    barge_pcm = pad_utterance_pcm(barge_speech, lead_silence_s=0.05, trail_silence_s=0.6)
    types: list[str] = []

    async with websockets.connect(
        ws_url,
        ssl=ssl_arg,
        additional_headers=headers,
        max_size=2**24,
        open_timeout=30,
    ) as ws:
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "conversation_id": conversation_id,
                    "mode": "agent",
                    "tts": True,
                }
            )
        )
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "text",
                    "text": BARGE_PROMPT_TEXT,
                    "as_voice": True,
                }
            )
        )
        sent_barge = False

        async def _stream_barge() -> None:
            for chunk in pcm16_stream_chunks(barge_pcm):
                await ws.send(chunk)
                await asyncio.sleep(CHUNK_SLEEP_S)

        def _kick_barge() -> None:
            nonlocal sent_barge
            if sent_barge:
                return
            sent_barge = True
            asyncio.create_task(_stream_barge())

        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(30.0, deadline - time.monotonic()))
            if isinstance(msg, bytes):
                _kick_barge()
                continue
            ev = json.loads(msg)
            types.append(ev.get("type", ""))
            if ev.get("type") in ("audio", "reply"):
                _kick_barge()
            elif ev.get("type") == "status" and ev.get("state") == "speaking":
                _kick_barge()
            if ev.get("type") == "barge_in":
                break
            if ev.get("type") == "tts_done" and sent_barge:
                break
            if ev.get("type") == "error":
                errors.append(f"barge-in: {ev.get('error')}")
                break

    if "barge_in" not in types:
        errors.append("barge-in: no barge_in event (long TTS + inject)")
    else:
        print("barge-in: ok (text-inject long TTS)")
    return errors


def _timing_from_events(label: str, expected: str, events: list[dict]) -> TurnTiming:
    timing = TurnTiming(label=label, expected=expected)
    timing.partial_count = sum(1 for e in events if e.get("type") == "transcript_partial")
    t_pcm = next((e.get("_t_pcm_done") for e in events if "_t_pcm_done" in e), None)
    timing.t_pcm_done = t_pcm
    for e in events:
        t = e.get("_t")
        kind = e.get("type")
        if kind == "transcript_partial" and timing.t_first_partial is None and t is not None:
            timing.t_first_partial = t
        elif kind == "transcript" and not e.get("discarded") and str(e.get("text") or "").strip():
            timing.t_transcript = t
            timing.transcript = str(e.get("text") or "")
        elif kind == "reply":
            timing.t_reply = t
            timing.reply_text = str(e.get("text") or "")
            timing.reply_action = str(e.get("action") or "")
            timing.route_tier = e.get("route_tier")
            det = e.get("action_details") or {}
            timing.route_label = str(
                det.get("route_label")
                or det.get("route_command")
                or det.get("label")
                or e.get("action")
                or ""
            )
        elif kind == "audio" and timing.t_first_audio is None:
            timing.t_first_audio = t
    return timing


def _assert_turn(
    timing: TurnTiming,
    *,
    require_partials: bool = True,
    speech_samples: int = 0,
    events: list[dict] | None = None,
) -> None:
    evs = events or []
    saw_speech = any(e.get("type") == "speech_start" for e in evs)
    need_partials = require_partials and speech_samples >= PARTIAL_MIN_SAMPLES
    if need_partials and timing.partial_count < 1:
        timing.errors.append("no transcript_partial events (expected for >1.5s utterance)")
    stop_like = timing.expected.strip().lower() == "stop"
    overlap = _word_overlap(timing.expected, timing.transcript)
    if not stop_like and overlap < 0.45:
        timing.errors.append(f"transcript mismatch ({overlap:.0%}): {timing.transcript!r}")
    if not stop_like and not timing.t_transcript:
        timing.errors.append("no final transcript event")
    if not timing.t_reply:
        timing.errors.append("no reply event")
    elif (
        not stop_like
        and timing.route_tier is None
        and not timing.route_label
    ):
        timing.errors.append("reply missing route_tier/label")


def _print_timing_table(rows: list[TurnTiming], base: str) -> None:
    print(f"\n=== Voice timing ({base}) ===")
    hdr = (
        "utterance",
        "partials",
        "pcm→final ms",
        "pcm→route ms",
        "pcm→reply ms",
        "pcm→audio ms",
        "tier",
        "label",
    )
    print(" | ".join(hdr))
    print(" | ".join("---" for _ in hdr))
    for r in rows:
        print(
            " | ".join(
                [
                    r.label[:40],
                    str(r.partial_count),
                    r.ms_after_pcm(r.t_transcript),
                    r.ms_after_pcm(r.t_reply),
                    r.ms_after_pcm(r.t_reply),
                    r.ms_after_pcm(r.t_first_audio),
                    str(r.route_tier if r.route_tier is not None else "—"),
                    (r.route_label or "—")[:24],
                ]
            )
        )
        if r.errors:
            print(f"  errors: {', '.join(r.errors)}")


def _synthesize_wav(tw, text: str) -> bytes:
    status, body = tw._request(
        "POST",
        "/api/speak",
        data=json.dumps({"message": text, "route": False}).encode(),
        headers={"Content-Type": "application/json"},
        timeout=180.0,
    )
    if status != 200 or not isinstance(body, bytes) or body[:4] != b"RIFF":
        raise RuntimeError(f"/api/speak failed: {status}")
    return body


def _post_ptt(tw, wav: bytes) -> dict[str, Any]:
    status, payload = tw._post_audio(
        "/api/transcribe?mode=ptt",
        "speech.wav",
        "audio/wav",
        wav,
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"PTT transcribe failed: {status} {payload}")
    return payload


async def _run_suite(
    base_url: str,
    token: str | None,
    tw,
    *,
    include_barge: bool,
) -> tuple[list[TurnTiming], list[str]]:
    errors: list[str] = []
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws/voice"
    rows: list[TurnTiming] = []
    conversation_id: int | None = None

    for phrase in DEFAULT_UTTERANCES:
        wav = _synthesize_wav(tw, phrase)
        speech, _ = wav_bytes_to_pcm16_mono(wav)
        pcm = pad_utterance_pcm(speech)
        speech_samples = len(speech) // 2
        events, _ = await _voice_turn(
            ws_url, pcm, token=token, conversation_id=conversation_id
        )
        timing = _timing_from_events(phrase, phrase, events)
        _assert_turn(
            timing,
            require_partials=True,
            speech_samples=speech_samples,
            events=events,
        )
        if phrase.strip().lower() == "stop":
            if timing.route_tier != 0:
                timing.errors.append(
                    f"stop must be tier 0, got tier {timing.route_tier} "
                    f"({timing.route_label}) transcript={timing.transcript!r}"
                )
            if timing.reply_action not in ("stop_speech", "cancel_request"):
                timing.errors.append(
                    f"stop reply action must be stop_speech, got {timing.reply_action!r} "
                    f"transcript={timing.transcript!r}"
                )
        if timing.errors:
            errors.extend(f"{phrase}: {e}" for e in timing.errors)
        rows.append(timing)
        for ev in events:
            if ev.get("type") == "reply" and ev.get("conversation_id"):
                conversation_id = int(ev["conversation_id"])
                break

    if include_barge:
        errors.extend(
            await _barge_in_test(ws_url, token=token, conversation_id=conversation_id, tw=tw)
        )

    ptt_wav = _synthesize_wav(tw, PTT_PHRASE)
    try:
        ptt = _post_ptt(tw, ptt_wav)
        overlap = _word_overlap(PTT_PHRASE, ptt.get("transcript", ""))
        if overlap < 0.45:
            errors.append(f"PTT transcript mismatch ({overlap:.0%}): {ptt.get('transcript')!r}")
        if not ptt.get("reply"):
            errors.append("PTT missing reply")
        print(
            f"PTT ({base_url}): transcript={ptt.get('transcript')!r} "
            f"action={ptt.get('action')!r} overlap={overlap:.0%}"
        )
    except Exception as exc:
        errors.append(f"PTT: {exc}")

    _print_timing_table(rows, base_url)
    return rows, errors


def _markdown_baseline(base: str, rows: list[TurnTiming], errors: list[str]) -> str:
    lines = [
        f"### Automated baseline ({base})",
        f"_Generated by `scripts/voice_e2e_check.py` (throwaway server unless `--use-running`)._",
        "",
        "| utterance | partials | pcm→final (ms) | pcm→reply (ms) | pcm→audio (ms) | tier |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r.label} | {r.partial_count} | {r.ms_after_pcm(r.t_transcript)} | "
            f"{r.ms_after_pcm(r.t_reply)} | {r.ms_after_pcm(r.t_first_audio)} | {r.route_tier} |"
        )
    if errors:
        lines.extend(["", "**Errors:**"] + [f"- {e}" for e in errors])
    else:
        lines.extend(["", "_No automated errors._"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice E2E automated checks")
    parser.add_argument(
        "--use-running",
        action="store_true",
        help="Use server from .env; does not start a throwaway instance",
    )
    parser.add_argument("--base-url", default="", help="Override primary base URL")
    parser.add_argument("--skip-lan", action="store_true", help="Do not probe LAN URL")
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=ROOT / "docs" / "testing" / "manual-voice-checklist.md",
        help="Update Automated baseline section in this checklist (first base only)",
    )
    args = parser.parse_args(argv)

    tw = _load_test_web()
    proc = None
    temp_dir = None
    all_errors: list[str] = []
    baseline_md = ""
    exit_code = 1

    try:
        if args.use_running:
            if args.base_url:
                tw._set_base_url(args.base_url)
            primary = tw._base_url()
        else:
            proc, temp_dir, primary = tw._start_throwaway_server()
            tw._set_base_url(primary)

        token = tw._resolve_bearer_token()
        bases = [primary]
        lan_ok = (
            not args.skip_lan
            and args.use_running
            and _url_reachable(LAN_URL)
        )
        if lan_ok:
            bases.append(LAN_URL)
        elif not args.skip_lan and args.use_running:
            print(f"LAN URL not reachable (skipped): {LAN_URL}")
        elif not args.use_running:
            print("LAN URL skipped (throwaway server is localhost only; use --use-running)")

        first_rows: list[TurnTiming] = []
        for i, base in enumerate(bases):
            if i > 0:
                tw._set_base_url(base)
            print(f"\n--- Testing {base} ---")
            rows, errs = asyncio.run(_run_suite(base, token, tw, include_barge=(i == 0)))
            if i == 0:
                first_rows = rows
                baseline_md = _markdown_baseline(base, rows, errs)
            all_errors.extend(f"[{base}] {e}" for e in errs)

        if args.write_baseline and baseline_md:
            path: Path = args.write_baseline
            path.parent.mkdir(parents=True, exist_ok=True)
            marker = "<!-- voice-e2e-baseline -->"
            if path.is_file():
                text = path.read_text()
                if marker in text:
                    pre, post = text.split(marker, 1)
                    end = "<!-- /voice-e2e-baseline -->"
                    if end in post:
                        _, tail = post.split(end, 1)
                        text = pre + marker + "\n" + baseline_md + "\n" + end + tail
                    else:
                        text = pre + marker + "\n" + baseline_md + "\n"
                else:
                    text = text.rstrip() + f"\n\n{marker}\n{baseline_md}\n<!-- /voice-e2e-baseline -->\n"
            else:
                text = (
                    "# Manual voice checklist\n\n"
                    f"{marker}\n{baseline_md}\n<!-- /voice-e2e-baseline -->\n"
                )
            path.write_text(text)

        print(f"\nSummary: {len(all_errors)} error(s)")
        for e in all_errors:
            print(" -", e)
        exit_code = 1 if all_errors else 0
    finally:
        if proc is not None and temp_dir is not None:
            tw._stop_throwaway_server(proc, temp_dir)

    sys.stderr.write(f"voice_e2e_check exit_code={exit_code}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())