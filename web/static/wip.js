/* Routine / WIP panel: a terminal-style live feed of everything the backend is doing,
   streamed from GET /api/activity/stream (Server-Sent Events, see activity.py). */
(() => {
  "use strict";
  const panel = document.getElementById("wip-panel");
  if (!panel) return;
  const out = document.getElementById("wip-output");
  const toggle = document.getElementById("wip-toggle");
  const clearBtn = document.getElementById("wip-clear");
  const jumpBtn = document.getElementById("wip-jump");
  const statusEl = document.getElementById("wip-status");
  const lastEl = document.getElementById("wip-last");
  const COLLAPSE_KEY = "sahir-wip-collapsed";
  const CLEARED_KEY = "sahir-wip-cleared-id";
  const MAX_LINES = 1500;

  let follow = true;
  let unseen = 0;
  let clearedId = Number(sessionStorage.getItem(CLEARED_KEY) || 0);

  const pad = (n) => String(n).padStart(2, "0");
  const clock = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

  function setCollapsed(collapsed) {
    panel.classList.toggle("collapsed", collapsed);
    document.body.classList.toggle("wip-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.title = collapsed ? "Expand the activity panel" : "Collapse the activity panel";
    toggle.querySelector(".wip-toggle-icon").textContent = collapsed ? "▸" : "▾";
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    if (!collapsed && follow) out.scrollTop = out.scrollHeight;
  }

  function atBottom() {
    return out.scrollHeight - out.scrollTop - out.clientHeight < 12;
  }

  function updateJump() {
    jumpBtn.hidden = follow;
    jumpBtn.textContent = unseen ? `Paused · ${unseen} new ↓` : "Paused ↓";
  }

  out.addEventListener("scroll", () => {
    const bottom = atBottom();
    if (bottom !== follow) {
      follow = bottom;
      if (follow) unseen = 0;
      updateJump();
    }
  }, { passive: true });

  jumpBtn.addEventListener("click", () => {
    follow = true;
    unseen = 0;
    out.scrollTop = out.scrollHeight;
    updateJump();
  });

  function append(ev) {
    if (!ev || ev.id <= clearedId) return;
    out.querySelector(".wip-empty")?.remove();
    const line = document.createElement("div");
    line.className = `wip-line k-${ev.kind || "log"}${ev.level === "error" || ev.level === "warning" ? " is-bad" : ""}`;
    const time = document.createElement("span");
    time.className = "wip-time";
    time.textContent = clock(ev.ts);
    const src = document.createElement("span");
    src.className = "wip-src";
    src.textContent = ev.source || ev.kind || "";
    const text = document.createElement("span");
    text.className = "wip-text";
    text.textContent = ev.text;
    line.append(time, src, text);
    out.appendChild(line);
    while (out.childElementCount > MAX_LINES) out.firstElementChild.remove();
    lastEl.textContent = `${clock(ev.ts)}  ${ev.text.split("\n")[0]}`;
    panel.dataset.lastId = String(ev.id);
    if (follow) out.scrollTop = out.scrollHeight;
    else { unseen += 1; updateJump(); }
  }

  clearBtn.addEventListener("click", () => {
    clearedId = Number(panel.dataset.lastId || clearedId);
    sessionStorage.setItem(CLEARED_KEY, String(clearedId));
    out.innerHTML = '<div class="wip-empty">Cleared. New activity will appear here.</div>';
    lastEl.textContent = "";
    follow = true;
    unseen = 0;
    updateJump();
  });

  toggle.addEventListener("click", () => setCollapsed(!panel.classList.contains("collapsed")));

  function setStatus(state, text) {
    statusEl.dataset.state = state;
    statusEl.title = text;
    statusEl.setAttribute("aria-label", text);
    document.body.classList.toggle("wip-live", state === "live");
  }

  let es = null;
  function connect() {
    if (!window.EventSource) {
      setStatus("off", "This browser can't stream activity");
      return;
    }
    es = new EventSource("/api/activity/stream");
    setStatus("connecting", "Connecting to the activity stream…");
    es.onopen = () => setStatus("live", "Live: streaming from the server");
    es.onmessage = (msg) => {
      try { append(JSON.parse(msg.data)); } catch { /* ignore malformed */ }
    };
    // EventSource reconnects on its own (resuming with Last-Event-ID); just show the state.
    es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "off" : "connecting",
      "Reconnecting to the activity stream…");
  }

  setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
  updateJump();
  connect();
  window.sahirWip = { append, connect };
})();
