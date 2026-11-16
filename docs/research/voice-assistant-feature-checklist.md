# Sahir feature checklist (reference)

The product requirements written down during the original planning of this assistant (June 2026 notes and roadmap), compared against what this repository implements. First-pass review of the docs, web UI and agent code on 16 Nov 2026, not a full runtime test.

Open work is tracked as issues on the [Sahir project board](https://github.com/users/mhaiprojects/projects/1); this page is a reference list, not a task list.

Status values: **Present**, **Partial**, **Missing**.

## Input and conversation

| Feature | Requirement | Status | Evidence / gap | Issue |
|---|---|---|---|---|
| Dictaphone-style assistant | A voice assistant you talk to like a dictaphone | Present | Web hold-to-talk and always-listening (`docs/features.md`); desktop push-to-talk and wake word | — |
| Hold mic sends the initial recording | Holding the mic button records and sends the initial audio, with live captions | Present | Hold-to-talk on mouse/touch; live captions via `/api/transcribe/live` | — |
| Add context while results generate | The mic "pauses" so you can add more context while the reply is being generated | Partial | Live voice supports barge-in (talking cancels the reply), but extra context is not folded into a reply in progress | [#13](https://github.com/mhaiprojects/Sahir-AI/issues/13) |

## Results and actions

| Feature | Requirement | Status | Evidence / gap | Issue |
|---|---|---|---|---|
| Numbered-list answers | Every reply is a numbered list of actions or results | Missing | No structured numbered output | [#9](https://github.com/mhaiprojects/Sahir-AI/issues/9) |
| Act by line number | Say "do action 3" to run that item | Missing | No line-number command handling | [#10](https://github.com/mhaiprojects/Sahir-AI/issues/10) |
| Tap a line to run it | Numbered results are tappable (action picker) | Missing | No tappable action list in the UI | [#11](https://github.com/mhaiprojects/Sahir-AI/issues/11) |
| Split screen | Results on top, controls and mic at the bottom | Partial | Captions on top and controls below, but no action list | [#12](https://github.com/mhaiprojects/Sahir-AI/issues/12) |

## Pipeline and integrations

| Feature | Requirement | Status | Evidence / gap | Issue |
|---|---|---|---|---|
| STT → LLM pipeline | Voice goes through speech-to-text, then an LLM, then structured output | Present (STT→LLM) | faster-whisper + Ollama (`docs/architecture.md`); structured numbered output is tracked in #9 | [#9](https://github.com/mhaiprojects/Sahir-AI/issues/9) |
| ClickUp actions | Execute chosen actions in ClickUp | Partial | Worker and client exist; real calls need `CLICKUP_API_TOKEN` and `CLICKUP_LIST_ID` | [#16](https://github.com/mhaiprojects/Sahir-AI/issues/16) |
| Email actions | Execute chosen actions against email | Missing | No email integration | [#14](https://github.com/mhaiprojects/Sahir-AI/issues/14) |
| Calendar actions | Execute chosen actions against a calendar | Missing | No calendar integration | [#15](https://github.com/mhaiprojects/Sahir-AI/issues/15) |
| Autonomous local task agent | A local agent that carries out a list of tasks, not only chats | Partial | Orchestrator, delegation queue and workers exist; not driven by the numbered-list UX | [#9](https://github.com/mhaiprojects/Sahir-AI/issues/9), [#10](https://github.com/mhaiprojects/Sahir-AI/issues/10) |
| Storage, calculations and design tools | "Take any task and use relevant data storage, calculations and design tools to deliver" | Partial | Chats, memory, RAG and a code worker exist; no calculation or design workers | [#27](https://github.com/mhaiprojects/Sahir-AI/issues/27) |

## Deployment and packaging

| Feature | Requirement | Status | Evidence / gap | Issue |
|---|---|---|---|---|
| Mobile-first | Designed first for a phone dictaphone flow | Partial | Responsive viewport and LAN HTTPS; no dedicated mobile layout | [#24](https://github.com/mhaiprojects/Sahir-AI/issues/24) |
| VPS proof of concept with Docker | Host a PoC on a small VPS using Docker | Partial | Docker Compose packaging exists; no VPS deployment yet | [#23](https://github.com/mhaiprojects/Sahir-AI/issues/23) |
| Publish FOSS on GitHub | Open-source components on GitHub | Present | This public repository (GPL-3.0) | — |
| Self-contained repo with docs, Pages demos | Each project is its own repo with docs; GitHub Pages for demos | Partial | Repo and `docs/` exist; no GitHub Pages site | [#25](https://github.com/mhaiprojects/Sahir-AI/issues/25) |

## Summary

| Status | Count |
|---|---|
| Present | 4 |
| Partial | 8 |
| Missing | 5 |

The biggest gaps against the original requirements are numbered-list results, acting on a line by voice or tap, and email and calendar actions.
