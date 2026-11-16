# Local-first agent upgrade (Nov 2026)

Plan and outcome of the upgrade that turned the voice assistant into a four-layer local-first agent: voice I/O, orchestrator, delegation layer and workers. Remaining work is tracked on the [project board](https://github.com/users/mhaiprojects/projects/1).

## Goal

A local-first voice chat agent that converses naturally and delegates tasks to worker agents on demand, with ClickUp integration so workers can create and update tasks.

- **Layer 1, voice I/O:** local Whisper STT and Piper or Kokoro TTS, low-latency streaming, interruptions and barge-in.
- **Layer 2, orchestrator:** a local LLM via Ollama that keeps the conversation history, decides between answering and delegating, and folds worker results back into the conversation.
- **Layer 3, delegation:** structured task schema (id, type, description, context, priority, callback) and result schema (task_id, status, output, errors, duration); a queue with timeout and retry; context preserved across handoffs.
- **Layer 4, workers:** start with one worker doing one thing well, prove the full loop (voice in → orchestrate → delegate → result → voice out), then add workers.
- **ClickUp:** map worker output to create task, update status, log time and add comment.
- **Out of scope:** cloud-hosted components, a native mobile app, custom model training.

## What was built

| Area | Result |
|---|---|
| Data layer | SQLite chats with FTS5, memory with a human review queue, RAG over txt/md/pdf using sqlite-vec (numpy fallback) and `nomic-embed-text` |
| Orchestrator and delegation | Orchestrator (L2), task/result schema with queue, timeout and retry (L3), research and cursor-agent code workers (L4), ClickUp REST v2 client with result → ClickUp sync |
| TTS | Optional Kokoro-82M via sherpa-onnx; Piper stays the default |
| Web | Agent REST API and a `/ws/voice` realtime loop (Silero VAD, sentence-by-sentence TTS, barge-in); VAD gate on the legacy endpoints |
| UI | Agent Manager at `/agent` (Chats, Memory, Documents, Live voice, Tasks) |
| Docs | `docs/upgrade-research.md`, `docs/architecture.md` |

## Milestones

| Milestone | Result |
|---|---|
| M1 voice loop works locally (mic → text → LLM → speech) | Pass |
| M2 orchestrator emits and receives a delegated task | Pass |
| M3 one worker completes a real task end to end | Pass (research and code workers) |
| M4 ClickUp actions fire from worker output | Pass with mocked HTTP only; live use needs credentials ([#16](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/16)) |
| M5 multi-worker orchestration with context intact | Pass |
| Barge-in | Pass |

Tests at the time: 49 pytest tests offline, 28/28 web checks, smoke test and end-to-end agent script passing.

## Still open

- Live ClickUp credentials and an end-to-end run: [#16](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/16)
- A real-microphone browser test of Live voice: [#18](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/18)
- Next research picks (Moonshine, llama.cpp Vulkan, Wyoming, custom wake phrases): [#8](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/8), [#7](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/7), [#29](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/29), [#30](https://github.com/mhaiprojects/MH-VoiceAssistant-A/issues/30)
