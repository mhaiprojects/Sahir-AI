#!/usr/bin/env python3
"""End-to-end milestone checks for the local-first voice agent (real Ollama/Whisper/Piper).

Usage: python scripts/e2e_agent.py [--base https://127.0.0.1:8766] [--skip-cursor]

M1 voice loop      : Piper WAV → /ws/voice (streamed PCM) → VAD → Whisper → orchestrator → TTS → Whisper check
M2 delegation      : orchestrator emits a research task and receives its result
M3 real worker     : research worker summarizes a real ingested document; code worker runs cursor-agent
M4 ClickUp         : worker output → ClickUp actions (real API if CLICKUP_API_TOKEN set, else mocked HTTP)
M5 multi-worker    : research → clickup chain with history + previous_results preserved
Barge-in           : speech during a reply cancels it
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio_pipeline import SpeechPipeline  # noqa: E402
from config import Settings  # noqa: E402
from web_server import _resample  # noqa: E402

RESULTS: dict[str, tuple[bool, str]] = {}
NOTES = """# Project Falcon notes

Project Falcon is Alex's home-automation dashboard rebuild.
The beta deadline is the 12th of December and the public launch is planned for late January.
The frontend uses Svelte and the backend is a FastAPI service talking to Home Assistant over websockets.
Open risks: the Zigbee bridge drops devices after firmware updates, and the energy graphs are slow.
Owner for the Zigbee issue is Sam; owner for the energy graphs is Priya.
"""


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS[name] = (ok, detail)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def wav_to_pcm16(pipeline: SpeechPipeline, text: str) -> bytes:
    audio, sr = pipeline.synthesize_float(text)
    audio = _resample(audio, sr, 16000)
    pad = np.zeros(int(16000 * 1.2), dtype=np.float32)
    full = np.concatenate([np.zeros(8000, dtype=np.float32), audio, pad])
    return (np.clip(full, -1, 1) * 32767).astype(np.int16).tobytes()


def wav_bytes_to_text(pipeline: SpeechPipeline, wav: bytes) -> str:
    import io
    import wave

    with wave.open(io.BytesIO(wav)) as wf:
        sr = wf.getframerate()
        a = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return pipeline.transcribe_final(_resample(a, sr, 16000))


async def voice_turn(url: str, pcm: bytes, *, conversation_id=None, barge_pcm: bytes | None = None, timeout=600):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    events, wavs = [], []
    t0 = time.monotonic()
    async with websockets.connect(url, ssl=ctx if url.startswith("wss") else None, max_size=2**24) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "hello", "conversation_id": conversation_id, "mode": "agent", "tts": True}))
        await ws.recv()
        chunk = 1024 * 2  # 64 ms of PCM16
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i:i + chunk])
            await asyncio.sleep(0.016)  # ~4x realtime
        sent_barge = False
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(msg, bytes):
                wavs.append(msg)
                if barge_pcm and not sent_barge:
                    sent_barge = True
                    for i in range(0, len(barge_pcm), chunk):
                        await ws.send(barge_pcm[i:i + chunk])
                        await asyncio.sleep(0.016)
                continue
            ev = json.loads(msg)
            ev["_t"] = round(time.monotonic() - t0, 2)
            events.append(ev)
            if ev["type"] == "transcript" and ev.get("discarded"):
                break
            if ev["type"] == "tts_done" and not (barge_pcm and sent_barge and not any(e["type"] == "barge_in" for e in events)):
                if not barge_pcm or any(e["type"] == "barge_in" for e in events):
                    break
            if ev["type"] == "error":
                break
            if barge_pcm and sent_barge and ev["type"] == "transcript" and len([e for e in events if e["type"] == "transcript"]) >= 2:
                break
    return events, wavs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://127.0.0.1:8766")
    ap.add_argument("--skip-cursor", action="store_true")
    args = ap.parse_args()
    ws_url = args.base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/voice"
    http = httpx.Client(base_url=args.base, verify=False, timeout=600)
    settings = Settings.from_env()
    pipeline = SpeechPipeline(settings)

    # Seed local knowledge
    up = http.post("/api/rag/documents", files=[("files", ("project-falcon-notes.md", NOTES.encode(), "text/markdown"))]).json()
    record("rag_ingest", up["results"][0]["ok"], f"chunks={up['results'][0].get('document', {}).get('chunk_count')}")
    hits = http.post("/api/rag/search", json={"query": "When is the Falcon beta deadline?", "k": 2}).json()["results"]
    record("rag_retrieval", bool(hits) and hits[0]["filename"] == "project-falcon-notes.md", f"top score={hits[0]['score'] if hits else None}")

    # M1: plain voice loop
    q = "What is the capital of France? Answer in one short sentence."
    ev, wavs = asyncio.run(voice_turn(ws_url, wav_to_pcm16(pipeline, q)))
    tr = next((e for e in ev if e["type"] == "transcript"), {})
    rp = next((e for e in ev if e["type"] == "reply"), {})
    first_audio = next((e for e in ev if e["type"] == "audio"), {})
    heard = wav_bytes_to_text(pipeline, wavs[0]) if wavs else ""
    ok = "france" in tr.get("text", "").lower() and "paris" in rp.get("text", "").lower() and "paris" in heard.lower()
    record("M1_voice_loop", ok, f"transcript={tr.get('text')!r} reply={rp.get('text')!r} tts_heard={heard!r} "
           f"reply_at={rp.get('_t')}s first_audio_at={first_audio.get('_t')}s")
    conv_id = rp.get("conversation_id")

    # M2+M3: voice → orchestrator → research worker → voice
    q2 = "Summarize my notes about Project Falcon."
    ev, wavs = asyncio.run(voice_turn(ws_url, wav_to_pcm16(pipeline, q2), conversation_id=conv_id))
    rp = next((e for e in ev if e["type"] == "reply"), {})
    det = rp.get("action_details") or {}
    workers = det.get("workers") or []
    record("M2_orchestrator_delegates", rp.get("action") == "delegated" and "research" in workers,
           f"action={rp.get('action')} workers={workers} task_ids={det.get('task_ids')}")
    res = (det.get("results") or [{}])[0]
    heard = wav_bytes_to_text(pipeline, wavs[0]) if wavs else ""
    summary_ok = res.get("status") == "success" and ("december" in str(res.get("summary")).lower() or "zigbee" in str(res.get("summary")).lower())
    record("M3_research_worker_e2e", summary_ok and bool(wavs),
           f"status={res.get('status')} summary={str(res.get('summary'))[:160]!r} spoken={heard[:120]!r}")

    # Context across turns (follow-up resolved from history)
    r = http.post("/api/agent/chat", json={"message": "Who owns the Zigbee issue in that project?", "conversation_id": conv_id, "mode": "agent"}).json()
    record("context_followup", "sam" in r["reply"].lower(), f"reply={r['reply'][:160]!r} action={r['action']}")

    # Code worker (real cursor-agent)
    from agent.delegation import Task
    from agent.factory import build_agent_stack

    stack = build_agent_stack(settings)
    if not args.skip_cursor:
        cr = stack.dispatcher.dispatch(Task.create("code", "Do not modify any files. Reply with exactly the single word: ready",
                                                   context={"conversation_id": conv_id}))
        record("M3_code_worker_cursor", cr.ok and "ready" in str(cr.output).lower(),
               f"status={cr.status} duration={cr.duration}s output={str((cr.output or {}).get('text'))[:60]!r} errors={cr.errors}")

    # M4: ClickUp actions from worker output
    from agent.clickup import ClickUpClient, ClickUpSync, ClickUpWorker

    calls = []

    class Resp:
        def __init__(self, d):
            self.status_code, self._d, self.content, self.text = 200, d, b"{}", ""

        def json(self):
            return self._d

    def fake_transport(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url.split("/api/v2")[1]))
        if url.endswith("/team"):
            return Resp({"teams": [{"id": "team-mock"}]})
        if "/list/" in url:
            return Resp({"id": f"mock-{uuid.uuid4().hex[:6]}", "url": "https://app.clickup.com/t/mock"})
        return Resp({})

    real = ClickUpClient()
    mode = "real" if real.configured else "mocked"
    ck = real if real.configured else ClickUpClient(token="mock-token", list_id="mock-list", transport=fake_transport)
    stack.dispatcher.result_hooks = [ClickUpSync(ck, tags=["verification"], name_prefix="[TEST verification] ")]
    stack.dispatcher.workers["clickup"] = ClickUpWorker(ck)
    rr = stack.dispatcher.dispatch(Task.create("research", "Summarize the open risks in Project Falcon",
                                               context={"conversation_id": conv_id, "clickup": {"tags": ["verification"]}}))
    acts = [a["action"] for a in (rr.output or {}).get("clickup", [])] if isinstance((rr.output or {}).get("clickup"), list) else []
    record("M4_clickup_actions", acts == ["create_task", "add_comment", "update_status", "log_time"],
           f"mode={mode} actions={acts} http={calls[:5] if mode == 'mocked' else 'real API'}")

    # M5: multi-worker orchestration with context intact (research → clickup)
    calls.clear()
    agent = stack.agent
    r5 = agent.respond("Research the Project Falcon deadlines in my notes, then create a ClickUp task titled "
                       "'TEST verification Falcon deadlines' containing that summary.", conversation_id=conv_id, mode="agent")
    det = r5.action_details
    tasks = agent.store.get_messages(conv_id)[-1]["meta"].get("tasks", [])
    rows = stack.dispatcher.list_tasks(conv_id, limit=10)
    ck_row = next((t for t in rows if t["type"] == "clickup"), None)
    prev_ok = bool(ck_row and ck_row["context"].get("previous_results") and ck_row["context"].get("history"))
    record("M5_multi_worker", det.get("workers", [])[:2] == ["research", "clickup"] and prev_ok,
           f"workers={det.get('workers')} clickup_task_had_previous_results+history={prev_ok} reply={r5.reply[:140]!r} mode={mode}")

    # Barge-in
    long_q = "Tell me a detailed story about a dragon who learns to bake bread, in at least six sentences."
    ev, wavs = asyncio.run(voice_turn(ws_url, wav_to_pcm16(pipeline, long_q), conversation_id=conv_id,
                                      barge_pcm=wav_to_pcm16(pipeline, "Stop. What time is it?")))
    types = [e["type"] for e in ev]
    interrupted = any(e["type"] == "tts_done" and e.get("interrupted") for e in ev) or "barge_in" in types
    record("barge_in", "barge_in" in types and interrupted, f"events={types[:14]}")

    passed = sum(ok for ok, _ in RESULTS.values())
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    Path(ROOT / "data" / "logs" / "e2e_agent_results.json").write_text(json.dumps(RESULTS, indent=2))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
