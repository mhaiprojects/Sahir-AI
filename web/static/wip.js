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
  const ctxProject = document.getElementById("wip-ctx-project");
  const ctxChat = document.getElementById("wip-ctx-chat");
  const ctxRouter = document.getElementById("wip-ctx-router");
  const ctxSupervisor = document.getElementById("wip-ctx-supervisor");
  const COLLAPSE_KEY = "sahir-wip-collapsed";
  const CLEARED_KEY = "sahir-wip-cleared-id";
  const MAX_LINES = 1500;
  const HEADER_KINDS = new Set(["route", "switch", "supervisor", "inject", "spawn"]);
  const EM_DASH = "—";

  let follow = true;
  let unseen = 0;
  let clearedId = Number(sessionStorage.getItem(CLEARED_KEY) || 0);

  const pad = (n) => String(n).padStart(2, "0");
  const clock = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

  function formatRouter(d) {
    if (!d || (d.label == null && !d.tier && d.confidence == null && d.ms == null)) return EM_DASH;
    const parts = [];
    if (d.label != null) parts.push(String(d.label));
    if (d.tier != null) parts.push(`T${d.tier}`);
    if (d.confidence != null) parts.push(String(d.confidence));
    if (d.ms != null) parts.push(`${d.ms} ms`);
    return parts.length ? parts.join(" · ") : EM_DASH;
  }

  function formatSupervisor(d) {
    if (!d) return EM_DASH;
    const s = d.state ?? d.message;
    return s != null && String(s).trim() ? String(s) : EM_DASH;
  }

  function applyHeaderEvent(ev) {
    const d = ev.data || {};
    if (ev.kind === "switch") {
      const proj = d.project_name || d.project || "General";
      const chat = d.chat_title || d.title || (d.conversation_id ? `Chat #${d.conversation_id}` : EM_DASH);
      if (ctxProject) { ctxProject.textContent = proj; ctxProject.title = proj; }
      if (ctxChat) { ctxChat.textContent = chat; ctxChat.title = chat; }
    }
    if (ev.kind === "route" && ctxRouter) {
      const t = formatRouter(d);
      ctxRouter.textContent = t;
      ctxRouter.title = t;
    }
    if (ev.kind === "supervisor" && ctxSupervisor) {
      const t = formatSupervisor(d);
      ctxSupervisor.textContent = t;
      ctxSupervisor.title = t;
    }
    if ((ev.kind === "spawn" || ev.kind === "inject") && ctxSupervisor) {
      const t = ev.kind === "spawn"
        ? `spawning “${d.chat_title || d.title || "sub-chat"}”`
        : `injecting → ${d.chat_title || d.title || "chat"}`;
      ctxSupervisor.textContent = t;
      ctxSupervisor.title = t;
    }
  }

  function applyHeaderFromBacklog(events) {
    for (const ev of events || []) {
      if (HEADER_KINDS.has(ev.kind)) applyHeaderEvent(ev);
    }
  }

  function setHeaderContext({ project, chat } = {}) {
    if (project != null && ctxProject) {
      ctxProject.textContent = project || EM_DASH;
      ctxProject.title = project || "";
    }
    if (chat != null && ctxChat) {
      ctxChat.textContent = chat || EM_DASH;
      ctxChat.title = chat || "";
    }
  }

  async function refreshWipContext(convId) {
    if (!convId) {
      setHeaderContext({ project: "General", chat: EM_DASH });
      return;
    }
    try {
      const res = await fetch(`/api/conversations/${convId}`);
      if (!res.ok) return;
      const conv = await res.json();
      setHeaderContext({
        project: conv.project_name || "General",
        chat: conv.title || `Chat #${conv.id}`,
      });
    } catch { /* ignore */ }
  }

  async function publishSwitchFromConversation(conv) {
    if (!conv?.id) return;
    setHeaderContext({
      project: conv.project_name || "General",
      chat: conv.title || `Chat #${conv.id}`,
    });
    try {
      await fetch("/api/activity/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "switch",
          source: "ui",
          data: {
            conversation_id: conv.id,
            project_id: conv.project_id,
            project_name: conv.project_name || "General",
            chat_title: conv.title || `Chat #${conv.id}`,
            kind: conv.kind,
          },
        }),
      });
    } catch { /* ignore */ }
  }

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
    if (HEADER_KINDS.has(ev.kind)) applyHeaderEvent(ev);
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
    es.onopen = () => {
      setStatus("live", "Live: streaming from the server");
      fetch("/api/activity?limit=120")
        .then((r) => r.json())
        .then((body) => applyHeaderFromBacklog(body.events))
        .catch(() => {});
    };
    es.onmessage = (msg) => {
      try { append(JSON.parse(msg.data)); } catch { /* ignore malformed */ }
    };
    es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "off" : "connecting",
      "Reconnecting to the activity stream…");
  }

  const wipQs = new URLSearchParams(window.location.search);
  if (wipQs.get("wip") === "collapsed") {
    setCollapsed(true);
  } else {
    const stored = localStorage.getItem(COLLAPSE_KEY);
    if (stored === "1") setCollapsed(true);
    else if (stored === "0") setCollapsed(false);
    else if (window.matchMedia("(max-width: 980px)").matches) setCollapsed(true);
    else setCollapsed(false);
  }
  updateJump();
  connect();

  const convKey = "voice-assistant-conversation-id";
  const storedConv = Number(localStorage.getItem(convKey) || localStorage.getItem("agentConv") || 0);
  if (storedConv) {
    refreshWipContext(storedConv);
  } else {
    fetch("/api/projects")
      .then((r) => r.json())
      .then((body) => {
        const general = (body.projects || []).find((p) => p.id === body.general_project_id);
        setHeaderContext({ project: general?.name || "General", chat: "—" });
      })
      .catch(() => {});
  }

  window.sahirWip = {
    append,
    connect,
    refreshWipContext,
    publishSwitchFromConversation,
    setHeaderContext,
  };
})();