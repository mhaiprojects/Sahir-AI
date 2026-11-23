# Calculation and diagram workers

## Calc worker (`calc`)

### Fast path (Tier 0 + worker)

Plain arithmetic runs in a **safe AST evaluator** (`agent/calc_eval.py`):

- Whitelisted operators and `math` helpers (`sqrt`, `sin`, `cos`, `log`, `round`, …).
- Spoken normalisation (“times”, “divided by”, “square root of”, “15% of 240”, “plus GST”).
- Caps on exponent size and result magnitude.

Voice/text router Tier 0 sends qualifying utterances to the `calc` command **without** calling the chat model.

### Sandbox path

For larger requests the calc worker asks the model for a **short Python snippet**, then runs it in **bubblewrap** (`agent/calc_sandbox.py`):

| Control | Limit |
|--------|--------|
| Isolation | `bwrap --unshare-all --unshare-user --cap-drop ALL` |
| Devices | `--dev /dev` (minimal private devtmpfs, **not** host `--dev-bind`) |
| Interpreter | `sys._base_executable` with `-I -S` (no site-packages, no repo venv bind) |
| Filesystem | Read-only base stdlib + dynamic libs only; `/tmp` tmpfs; **no** `$HOME`, repo, or host `/dev` nodes |
| Network | No network namespace |
| CPU | 5 s soft `RLIMIT_CPU` |
| Address space | ~512 MB |
| Output file | 1 MB max |
| stdout/stderr | 16 KB cap (process killed if exceeded; incremental read) |
| Wall clock | 10 s (measured `runtime_s` reported) |

**AST pre-check (defence in depth):** no attribute access; no names starting with `_`; block `getattr`, `type`, `object`, `globals`, `__builtins__`, etc.; suspicious string literals; import whitelist only.

**Runtime prelude:** restricted `__builtins__` (numeric/print helpers only, no `__import__`); whitelisted modules pre-imported; custom `__import__` not exposed to user code.

**If `bwrap` is missing**, the sandbox path **refuses** — there is **no** unsandboxed fallback.

### What the sandbox does *not* protect against

- **Kernel / CVE** bugs in bubblewrap or the kernel.
- **Side channels** (timing, cache) between sandboxed and host processes.
- **Host resource exhaustion** if many sandboxes are started at once.
- **Malicious but allowed stdlib** (e.g. heavy CPU inside permitted imports) — mitigated by CPU/time limits only.
- **Mis-generated code** that prints secrets already present in the prompt (the model sees conversation context).

## Design worker (`design`)

Produces **Mermaid** source (flowchart, sequence, mind map, …), validates syntax lightly, and allows **one** LLM repair pass.

Diagrams are stored in assistant message `meta`:

- `kind`: `"diagram"`
- `mermaid`, `diagram_title`, `diagram_steps`, `tts_text`

The UI loads **mermaid 11.4.0** from `web/static/vendor/mermaid.min.js` (see `mermaid.vendor.md` for SHA-256; exposes `globalThis.mermaid`) with `securityLevel: 'strict'`. Legacy bare `title …` lines in stored source are normalized to YAML frontmatter before render. No server-side `dot` or mermaid-cli required.

TTS uses a short summary (“Here's the diagram: …, with N steps”), not the raw source.

## Delegation heuristics

- **Design:** “draw / diagram / flowchart / sequence diagram / mind map / architecture diagram …” (not coding tasks).
- **Calc:** numeric / GST / percent / statistics phrasing when Tier 0 does not apply.
- **Code** worker still wins for repo/Cursor-style requests.