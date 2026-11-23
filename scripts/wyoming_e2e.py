#!/usr/bin/env python3
"""E2E: Wyoming describe, TTS round-trip STT, handle on/off (temp DB)."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _word_overlap(expected: str, actual: str) -> float:
    a = {w.lower() for w in expected.split() if w.strip()}
    b = {w.lower() for w in actual.split() if w.strip()}
    if not a:
        return 1.0 if not b else 0.0
    return len(a & b) / len(a)


async def _describe(uri: str) -> dict:
    from wyoming.client import AsyncTcpClient
    from wyoming.info import Describe, Info

    out: dict = {}
    async with AsyncTcpClient.from_uri(uri) as client:
        await client.write_event(Describe().event())
        while True:
            ev = await client.read_event()
            if ev is None:
                raise RuntimeError("disconnect on describe")
            if Info.is_type(ev.type):
                info = Info.from_event(ev)
                out["asr"] = [p.name for p in info.asr]
                out["tts"] = [p.name for p in info.tts]
                out["handle_programs"] = [p.name for p in info.handle]
                return out
    return out


async def _run_client(uri: str, *, handle_env: bool, test_handle: bool = True) -> dict:
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncTcpClient
    from wyoming.handle import Handled, NotHandled
    from wyoming.info import Describe, Info
    from wyoming.tts import Synthesize

    phrase = "The quick brown fox jumps over the lazy dog."
    out: dict = {"handle": handle_env}

    out.update(await _describe(uri))

    async with AsyncTcpClient.from_uri(uri) as client:
        await client.write_event(Synthesize(text=phrase).event())
        pcm_chunks: list[bytes] = []
        rate = 16000
        while True:
            ev = await client.read_event()
            if ev is None:
                raise RuntimeError("disconnect on synthesize")
            if AudioStart.is_type(ev.type):
                rate = int(ev.data["rate"])
            elif AudioChunk.is_type(ev.type):
                chunk = AudioChunk.from_event(ev)
                pcm_chunks.append(chunk.audio)
            elif AudioStop.is_type(ev.type):
                break

        async with AsyncTcpClient.from_uri(uri) as asr_client:
            await asr_client.write_event(Transcribe(language="en").event())
            await asr_client.write_event(AudioStart(rate=rate, width=2, channels=1).event())
            for blob in pcm_chunks:
                await asr_client.write_event(
                    AudioChunk(rate=rate, width=2, channels=1, audio=blob).event()
                )
            await asr_client.write_event(AudioStop().event())
            transcript = ""
            while True:
                ev = await asr_client.read_event()
                if ev is None:
                    break
                if Transcript.is_type(ev.type):
                    transcript = Transcript.from_event(ev).text
                    break
        out["transcript"] = transcript
        out["overlap"] = _word_overlap(phrase, transcript)

    if test_handle:
        from wyoming.error import Error

        async with AsyncTcpClient.from_uri(uri) as hclient:
            await hclient.write_event(Transcript(text="Hello Wyoming", language="en").event())
            for _ in range(30):
                ev = await asyncio.wait_for(hclient.read_event(), timeout=30.0)
                if ev is None:
                    break
                if Handled.is_type(ev.type):
                    out["handled"] = Handled.from_event(ev).text
                    break
                if NotHandled.is_type(ev.type):
                    out["handled"] = None
                    out["not_handled"] = NotHandled.from_event(ev).text
                    break
                if Error.is_type(ev.type):
                    out["handle_error"] = Error.from_event(ev).text
                    break

    return out


def main() -> int:
    port = _free_port()
    uri = f"tcp://127.0.0.1:{port}"
    tmp = tempfile.TemporaryDirectory(prefix="sahir-wyoming-e2e-")
    db = Path(tmp.name) / "agent.db"
    env = os.environ.copy()
    env["WYOMING_URI"] = uri
    env["WYOMING_HANDLE_ENABLED"] = "false"
    env["AGENT_DB_PATH"] = str(db)

    py = ROOT / "venv" / "bin" / "python"
    proc = subprocess.Popen(
        [str(py), "-m", "wyoming_server", "--uri", uri],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(1.5)
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode(errors="replace")
            print("Wyoming server failed to start:", err[:500])
            return 1

        out_off = asyncio.run(_run_client(uri, handle_env=False))
        assert "sahir-asr" in out_off.get("asr", []), out_off
        assert out_off.get("handle_programs") == [], out_off
        assert out_off.get("not_handled"), out_off

        overlap = float(out_off.get("overlap", 0))
        if overlap < 0.5:
            print(f"WARN: STT overlap {overlap:.0%} (transcript={out_off.get('transcript')!r})")

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)

        env["WYOMING_HANDLE_ENABLED"] = "true"
        proc2 = subprocess.Popen(
            [str(py), str(ROOT / "scripts" / "_wyoming_e2e_server.py"), "--uri", uri],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(2.0)
            out_on = asyncio.run(_run_client(uri, handle_env=True, test_handle=True))
            assert out_on.get("handle_programs"), out_on
            assert out_on.get("handled") or out_on.get("not_handled"), out_on
            assert out_on.get("handled"), f"expected handled text, got {out_on!r}"
        finally:
            proc2.terminate()
            try:
                proc2.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc2.kill()
                proc2.wait(timeout=3)

        print(
            "Wyoming e2e OK:",
            f"overlap={overlap:.0%}",
            f"handle_off={bool(out_off.get('not_handled'))}",
            f"handle_on={out_on.get('handled')!r}",
            f"handle_on_programs={out_on.get('handle_programs')}",
        )
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())