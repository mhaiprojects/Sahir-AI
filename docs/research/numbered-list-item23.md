# Item 23 — numbered-list replies (#9–#11)

## Behaviour

- Assistant replies are parsed into numbered items (`1.` / `1)`); a single paragraph becomes item 1.
- Actions are attached only from concrete tool/orchestrator results (`attach_actions_from_turn`), never from the parser.
- Items are always in `meta` (including single-paragraph replies with an action for “do action 1”).
- `tts_text` and numbered-list UI apply only when the reply has **two or more** explicit `1.` / `1)` lines.
- Tier 0: `do action N`, `run item N`, ordinals → `do_numbered_action`; pending confirm → `numbered_confirm` (yes/no).
- UI: `numbered-reply-list` with optional `numbered-action-chip`; destructive actions use `window.confirm` before POST `/api/agent/numbered/execute`.

## #12 layout note

Unified chat view (`index.html`): `#chat-log` (results) is above `#chat-form` (composer controls) in the same column — satisfies “results above, controls at the bottom” for desktop and &lt;980px mobile.