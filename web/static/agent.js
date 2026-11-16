/* Agent Manager: chats, memory, RAG documents, live voice (WebSocket + barge-in), tasks. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const md = (t) => (window.renderMarkdown ? window.renderMarkdown(t) : esc(t));
  const when = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };
  const state = { convId: null, convs: [], busy: false };

  function activityEl() {
    return $("#agent-activity");
  }

  function clearActivity() {
    const el = activityEl();
    if (!el) return;
    el.innerHTML = "";
    el.classList.add("hidden");
  }

  function pushActivity(message, stage = "") {
    const el = activityEl();
    if (!el || !message) return;
    el.classList.remove("hidden");
    const line = document.createElement("div");
    line.className = "line" + (stage ? ` stage-${stage}` : "");
    line.textContent = message;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function describeToolCalls(ev) {
    (ev.calls || []).forEach((c) => {
      const name = c.name || "tool";
      pushActivity(`Tool call: ${name}`, "tool");
    });
  }

  async function consumeSseResponse(res, { pending, box, accRef }) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (!line.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        if (ev.type === "meta") state.convId = ev.conversation_id;
        if (ev.type === "progress") pushActivity(ev.message, ev.stage || "progress");
        if (ev.type === "model") pushActivity(ev.message || `Model: ${ev.model}`, "model");
        if (ev.type === "tool_calls") describeToolCalls(ev);
        if (ev.type === "delegate") pushActivity(ev.message || `Workers: ${(ev.workers || []).join(", ")}`, "delegate");
        if (ev.type === "worker_result") {
          pushActivity(`Worker ${ev.worker}: ${ev.status}`, "delegate");
        }
        if (ev.type === "token") {
          accRef.v += ev.text;
          pending.querySelector(".body").innerHTML = md(accRef.v);
          box.scrollTop = 1e9;
        }
        if (ev.type === "error") toast(ev.error, true);
      }
    }
  }

  function toast(msg, bad = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (bad ? " bad" : "");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => el.classList.add("hidden"), 3500);
  }

  async function api(path, opts = {}) {
    const init = { ...opts, headers: { ...(opts.headers || {}) } };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, init);
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `HTTP ${res.status}`);
    }
    return data;
  }

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll(".ag-tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".ag-tab").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-selected", x === b ? "true" : "false");
    });
    document.querySelectorAll(".ag-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
    localStorage.setItem("agentTab", b.dataset.tab);
    if (b.dataset.tab === "memory") loadMemories();
    if (b.dataset.tab === "docs") loadDocs();
    if (b.dataset.tab === "tasks") loadTasks();
  }));
  window.initTablistKeys?.(document.querySelector(".ag-tabs"));

  async function loadStatus() {
    try {
      const s = await api("/api/agent/status");
      $("#ag-status").textContent = `${s.chat_model} · ${s.embed_model} · ${s.vector_backend} · ${s.conversations} chats · ${s.memories_active} memories · ${s.documents} docs`;
      const badge = $("#pending-badge");
      badge.textContent = s.memories_pending;
      badge.classList.toggle("hidden", !s.memories_pending);
    } catch (e) { $("#ag-status").textContent = "status error: " + e.message; }
  }

  // --------------------------------------------------------------- chats
  async function loadConversations() {
    const q = $("#conv-search").value.trim();
    const data = await api("/api/conversations" + (q ? "?q=" + encodeURIComponent(q) : ""));
    state.convs = data.conversations;
    $("#conv-list").innerHTML = state.convs.map((c) => `
      <li class="${c.id === state.convId ? "active" : ""}" data-id="${c.id}">
        <div class="t">${esc(c.title)}</div>
        <div class="s">${esc(c.source)} · ${c.message_count} msgs · ${when(c.updated_at)}</div>
        <div class="p">${esc(c.last_message || "")}</div>
      </li>`).join("") || `<li class="empty">No chats yet</li>`;
  }

  function renderMessage(m) {
    const meta = m.meta || {};
    const chips = [];
    if (meta.action && m.role === "assistant") chips.push(`<span class="chip">${esc(meta.action)}</span>`);
    (meta.sources?.memories || []).forEach((s) => chips.push(`<span class="chip mem" title="${esc(s.content)}">memory #${s.id}</span>`));
    (meta.sources?.chunks || []).forEach((s) => chips.push(`<span class="chip doc">${esc(s.filename)} #${s.idx}</span>`));
    (meta.tasks || []).forEach((t) => chips.push(`<span class="chip task">${esc(t.task.type)} → ${esc(t.result.status)}</span>`));
    return `<div class="msg ${m.role}" data-id="${m.id || ""}">
      <div class="body">${m.role === "assistant" ? md(m.content) : esc(m.content)}</div>
      <div class="meta">${when(m.created_at || new Date().toISOString())} ${chips.join(" ")}</div></div>`;
  }

  async function openConversation(id) {
    state.convId = id;
    const c = await api(`/api/conversations/${id}`);
    $("#conv-title").textContent = c.title;
    $("#conv-actions").hidden = false;
    $("#conv-export-md").href = `/api/conversations/${id}/export?format=md`;
    $("#conv-export-json").href = `/api/conversations/${id}/export?format=json`;
    // aria-busy: don't announce the whole history when a chat is opened (#36).
    $("#messages").setAttribute("aria-busy", "true");
    $("#messages").innerHTML = c.messages.map(renderMessage).join("");
    $("#messages").removeAttribute("aria-busy");
    $("#messages").scrollTop = 1e9;
    localStorage.setItem("agentConv", id);
    localStorage.setItem("voice-assistant-conversation-id", id);
    loadConversations();
  }

  $("#conv-list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) openConversation(Number(li.dataset.id));
  });
  let searchT;
  $("#conv-search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(loadConversations, 250); });
  async function startNewChat() {
    const c = await api("/api/conversations", { method: "POST", json: {} });
    await openConversation(c.id);
    $("#chat-input")?.focus();
  }

  $("#conv-new")?.addEventListener("click", () => { startNewChat().catch((e) => toast(e.message, true)); });
  $("#conv-new-top")?.addEventListener("click", () => { startNewChat().catch((e) => toast(e.message, true)); });
  $("#conv-rename").addEventListener("click", async () => {
    const cur = $("#conv-title").textContent;
    const title = prompt("Rename conversation", cur);
    if (!title || title === cur) return;
    await api(`/api/conversations/${state.convId}`, { method: "PATCH", json: { title } });
    openConversation(state.convId);
  });
  $("#conv-delete").addEventListener("click", async () => {
    if (!confirm("Delete this conversation permanently?")) return;
    await api(`/api/conversations/${state.convId}`, { method: "DELETE" });
    state.convId = null;
    $("#messages").innerHTML = "";
    $("#conv-title").textContent = "Select or start a chat";
    $("#conv-actions").hidden = true;
    loadConversations(); loadStatus();
  });
  $("#conv-extract").addEventListener("click", async () => {
    toast("Extracting memories with the local model…");
    try {
      const r = await api("/api/memories/extract", { method: "POST", json: { conversation_id: state.convId } });
      toast(r.count ? `${r.count} memory suggestion(s) added for review` : "No new memories found");
      loadStatus();
    } catch (e) { toast(e.message, true); }
  });

  function focusAgentChatInput() {
    const el = $("#chat-input");
    if (el && !el.disabled && !state.busy) el.focus();
  }
  window.focusAgentChatInput = focusAgentChatInput;

  let agentSubmitModeOverride = null;

  const agentChatForm = $("#agent-chat-form");
  const chatInput = $("#chat-input");
  if (chatInput) {
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (!chatInput.value.trim() || state.busy) return;
        agentSubmitModeOverride = "cursor";
        agentChatForm?.requestSubmit();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        agentChatForm?.requestSubmit();
      }
    });
  }

  agentChatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#chat-input").value.trim();
    if (!text || state.busy) return;
    const mode = agentSubmitModeOverride || $("#chat-mode").value;
    agentSubmitModeOverride = null;
    $("#chat-input").value = "";
    clearActivity();
    state.busy = true; $("#chat-send").disabled = true;
    const box = $("#messages");
    box.insertAdjacentHTML("beforeend", renderMessage({ role: "user", content: text }));
    box.insertAdjacentHTML("beforeend", `<div class="msg assistant pending"><div class="body">…</div><div class="meta"></div></div>`);
    const pending = box.lastElementChild;
    box.scrollTop = 1e9;
    pushActivity(`Mode: ${mode}`, "progress");
    try {
      const res = await fetch("/api/agent/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: state.convId, mode }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const accRef = { v: "" };
      await consumeSseResponse(res, { pending, box, accRef });
      await openConversation(state.convId);
    } catch (err) {
      pending.querySelector(".body").textContent = "Error: " + err.message;
      toast(err.message, true);
    } finally {
      state.busy = false; $("#chat-send").disabled = false; loadStatus();
      focusAgentChatInput();
    }
  });

  // -------------------------------------------------------------- memory
  async function loadMemories() {
    const status = $("#mem-filter").value, q = $("#mem-search").value.trim();
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    const { memories } = await api("/api/memories?" + params);
    $("#mem-list").innerHTML = memories.map((m) => `
      <li class="card ${m.status}" data-id="${m.id}">
        <div class="row between"><span class="chip">${esc(m.category)}</span>
          <span class="s">${esc(m.status)} · ${esc(m.source)} · ${when(m.updated_at)}</span></div>
        <div class="content" data-content>${esc(m.content)}</div>
        <div class="row">
          ${m.status !== "active" ? `<button data-act="approve">Approve</button>` : ""}
          ${m.status !== "rejected" ? `<button data-act="reject">Reject</button>` : ""}
          <button data-act="edit">Edit</button>
          <button data-act="delete" class="danger">Delete</button>
          ${m.source_conversation_id ? `<button data-act="open" data-conv="${m.source_conversation_id}">Source chat</button>` : ""}
        </div></li>`).join("") || `<li class="empty">No memories</li>`;
    loadStatus();
  }
  $("#mem-filter").addEventListener("change", loadMemories);
  $("#mem-search").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(loadMemories, 250); });
  $("#mem-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/memories", { method: "POST", json: { content: $("#mem-content").value, category: $("#mem-category").value } });
      $("#mem-content").value = ""; toast("Memory saved"); loadMemories();
    } catch (err) { toast(err.message, true); }
  });
  $("#mem-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]"); if (!btn) return;
    const id = btn.closest("li").dataset.id, act = btn.dataset.act;
    try {
      if (act === "approve") await api(`/api/memories/${id}`, { method: "PATCH", json: { status: "active" } });
      if (act === "reject") await api(`/api/memories/${id}`, { method: "PATCH", json: { status: "rejected" } });
      if (act === "delete" && confirm("Delete memory?")) await api(`/api/memories/${id}`, { method: "DELETE" });
      if (act === "edit") {
        const cur = btn.closest("li").querySelector("[data-content]").textContent;
        const content = prompt("Edit memory", cur);
        if (content && content !== cur) await api(`/api/memories/${id}`, { method: "PATCH", json: { content } });
      }
      if (act === "open") { document.querySelector('.ag-tab[data-tab="chats"]').click(); openConversation(Number(btn.dataset.conv)); return; }
      loadMemories();
    } catch (err) { toast(err.message, true); }
  });
  $("#mem-test-btn").addEventListener("click", async () => {
    const q = prompt("Recall test — what should the agent remember about…");
    if (!q) return;
    const { results } = await api("/api/memories/search", { method: "POST", json: { query: q, k: 5 } });
    alert(results.length ? results.map((r) => `${r.score.toFixed(3)}  ${r.content}`).join("\n") : "No active memories matched");
  });

  // ----------------------------------------------------------- documents
  async function loadDocs() {
    const { documents } = await api("/api/rag/documents");
    $("#doc-list").innerHTML = documents.map((d) => `
      <li class="card" data-id="${d.id}"><div class="row between">
        <b>${esc(d.filename)}</b><span class="s">${d.chunk_count} chunks · ${(d.size_bytes / 1024).toFixed(1)} KB · ${esc(d.embed_model || "")} · ${when(d.created_at)}</span></div>
        <div class="row"><button data-act="view">View chunks</button><button data-act="delete" class="danger">Delete</button></div>
        <div class="chunks hidden"></div></li>`).join("") || `<li class="empty">No documents indexed yet</li>`;
    loadStatus();
  }
  async function uploadFiles(files) {
    if (!files.length) return;
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    $("#doc-upload-status").textContent = `Indexing ${files.length} file(s)… (embedding with Ollama)`;
    try {
      const res = await fetch("/api/rag/documents", { method: "POST", body: fd });
      const data = await res.json();
      const results = data.results || data.detail || [];
      $("#doc-upload-status").textContent = results.map((r) => r.ok ? `✓ ${r.filename}${r.duplicate ? " (already indexed)" : ` (${r.document.chunk_count} chunks)`}` : `✗ ${r.filename}: ${r.error}`).join("  ");
    } catch (e) { $("#doc-upload-status").textContent = "Upload failed: " + e.message; }
    $("#doc-files").value = "";
    loadDocs();
  }
  $("#doc-form").addEventListener("submit", (e) => { e.preventDefault(); uploadFiles($("#doc-files").files); });
  ["dragover", "dragenter"].forEach((ev) => $("#doc-form").addEventListener(ev, (e) => { e.preventDefault(); $("#doc-form").classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => $("#doc-form").addEventListener(ev, () => $("#doc-form").classList.remove("over")));
  $("#doc-form").addEventListener("drop", (e) => { e.preventDefault(); uploadFiles(e.dataTransfer.files); });
  $("#doc-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]"); if (!btn) return;
    const li = btn.closest("li"), id = li.dataset.id;
    if (btn.dataset.act === "delete" && confirm("Delete document and its chunks?")) {
      await api(`/api/rag/documents/${id}`, { method: "DELETE" }); loadDocs();
    }
    if (btn.dataset.act === "view") {
      const box = li.querySelector(".chunks");
      if (box.classList.toggle("hidden")) return;
      const d = await api(`/api/rag/documents/${id}?limit=100`);
      box.innerHTML = d.chunks.map((c) => `<pre>#${c.idx} ${esc(c.content)}</pre>`).join("");
    }
  });
  $("#rag-search-btn").addEventListener("click", async () => {
    const query = $("#rag-query").value.trim(); if (!query) return;
    try {
      const { results } = await api("/api/rag/search", { method: "POST", json: { query, k: 5 } });
      $("#rag-results").innerHTML = results.map((r) => `<li class="card"><div class="row between"><b>${esc(r.filename)} #${r.idx}</b><span class="chip">${r.score.toFixed(3)}</span></div><pre>${esc(r.content)}</pre></li>`).join("") || `<li class="empty">No matches</li>`;
    } catch (e) { toast(e.message, true); }
  });
  $("#rag-query").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#rag-search-btn").click(); });

  // --------------------------------------------------------------- tasks
  async function loadTasks() {
    const d = await api("/api/agent/tasks?limit=100");
    $("#tasks-info").textContent = `Workers: ${d.workers.map((w) => w.type).join(", ")} · ClickUp ${d.clickup_configured ? "connected" : "not configured (set CLICKUP_API_TOKEN in .env)"}`;
    $("#tasks-body").innerHTML = d.tasks.map((t) => `<tr>
      <td>${when(t.created_at)}</td><td>${esc(t.type)}</td><td>${esc(t.description).slice(0, 160)}</td>
      <td class="st-${esc(t.status)}">${esc(t.status)}${t.attempts > 1 ? ` (${t.attempts}×)` : ""}</td>
      <td>${t.duration != null ? t.duration.toFixed(1) + "s" : ""}</td>
      <td><details><summary>view</summary><pre>${esc(JSON.stringify({ output: t.output, errors: t.errors }, null, 2))}</pre></details></td></tr>`).join("")
      || `<tr><td colspan="6" class="empty">No delegated tasks yet</td></tr>`;
  }
  $("#tasks-refresh").addEventListener("click", loadTasks);

  // ---------------------------------------------------------- live voice
  const voice = { ws: null, ctx: null, stream: null, node: null, playing: [], playCtx: null, nextAudioMeta: null, playHead: 0 };
  const vlog = (html, cls = "") => { const el = $("#voice-log"); el.insertAdjacentHTML("beforeend", `<div class="msg ${cls}">${html}</div>`); el.scrollTop = 1e9; };
  const setVState = (s) => { $("#voice-state").textContent = s; $("#voice-state").dataset.state = s; };

  function stopPlayback() {
    voice.playing.forEach((src) => { try { src.stop(); } catch {} });
    voice.playing = [];
    if (voice.playCtx) voice.playHead = voice.playCtx.currentTime;
  }

  async function playWav(buf) {
    voice.playCtx = voice.playCtx || new AudioContext();
    const audio = await voice.playCtx.decodeAudioData(buf);
    const src = voice.playCtx.createBufferSource();
    src.buffer = audio; src.connect(voice.playCtx.destination);
    const start = Math.max(voice.playCtx.currentTime, voice.playHead);
    src.start(start); voice.playHead = start + audio.duration;
    voice.playing.push(src);
    src.onended = () => { voice.playing = voice.playing.filter((x) => x !== src); };
  }

  async function startVoice() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    voice.ws = new WebSocket(`${proto}//${location.host}/ws/voice`);
    voice.ws.binaryType = "arraybuffer";
    voice.ws.onopen = () => voice.ws.send(JSON.stringify({ type: "hello", conversation_id: state.convId, mode: "agent", tts: $("#voice-tts").checked }));
    voice.ws.onclose = () => { setVState("disconnected"); stopVoice(true); };
    voice.ws.onmessage = async (ev) => {
      if (typeof ev.data !== "string") { await playWav(ev.data); return; }
      const m = JSON.parse(ev.data);
      if (m.type === "ready") { setVState("listening"); if (m.tts_backend) vlog(`<i>connected · VAD ${esc(m.vad)} · TTS ${esc(m.tts_backend)}</i>`); }
      else if (m.type === "speech_start") setVState("hearing you");
      else if (m.type === "barge_in") { stopPlayback(); vlog("<i>⏹ barge-in — stopped reply</i>"); }
      else if (m.type === "status") setVState(m.state);
      else if (m.type === "transcript") { if (m.text) vlog(esc(m.text), "user"); }
      else if (m.type === "reply") { state.convId = m.conversation_id; vlog(md(m.text) + `<div class="meta"><span class="chip">${esc(m.action)}</span> ${m.latency_ms} ms</div>`, "assistant"); }
      else if (m.type === "error") vlog("Error: " + esc(m.error), "bad");
    };
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    voice.ctx = new AudioContext({ sampleRate: 16000 });
    const srcNode = voice.ctx.createMediaStreamSource(voice.stream);
    // ScriptProcessor keeps this dependency-free; 1024 samples ≈ 64 ms @16 kHz.
    voice.node = voice.ctx.createScriptProcessor(1024, 1, 1);
    voice.node.onaudioprocess = (e) => {
      const f32 = e.inputBuffer.getChannelData(0);
      let peak = 0; const i16 = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) { const s = Math.max(-1, Math.min(1, f32[i])); i16[i] = s * 32767; peak = Math.max(peak, Math.abs(s)); }
      $("#voice-meter").style.width = Math.min(100, peak * 140) + "%";
      if (voice.ws && voice.ws.readyState === 1) voice.ws.send(i16.buffer);
    };
    srcNode.connect(voice.node); voice.node.connect(voice.ctx.destination);
    $("#voice-toggle").textContent = "Stop live voice"; $("#voice-interrupt").disabled = false;
  }

  function stopVoice(fromClose = false) {
    stopPlayback();
    try { voice.node && voice.node.disconnect(); } catch {}
    voice.stream && voice.stream.getTracks().forEach((t) => t.stop());
    voice.ctx && voice.ctx.close().catch(() => {});
    if (!fromClose && voice.ws) voice.ws.close();
    Object.assign(voice, { ws: null, ctx: null, stream: null, node: null });
    $("#voice-toggle").textContent = "Start live voice"; $("#voice-interrupt").disabled = true;
    if (!fromClose) setVState("idle");
    loadConversations();
  }

  $("#voice-toggle").addEventListener("click", () => {
    if (voice.ws) stopVoice();
    else startVoice().catch((e) => { toast("Mic/voice error: " + e.message, true); stopVoice(); });
  });
  $("#voice-interrupt").addEventListener("click", () => { stopPlayback(); voice.ws && voice.ws.send(JSON.stringify({ type: "interrupt" })); });

  // ---------------------------------------------------------------- init
  window.refreshAgentView = async function refreshAgentView() {
    await loadStatus();
    await loadConversations();
  };

  (async () => {
    await refreshAgentView();
    const saved = Number(localStorage.getItem("agentConv") || localStorage.getItem("voice-assistant-conversation-id"));
    if (saved && state.convs.some((c) => c.id === saved)) {
      await openConversation(saved);
    } else if (state.convs.length) {
      await openConversation(state.convs[0].id);
    }
    const tab = localStorage.getItem("agentTab");
    if (tab) document.querySelector(`.ag-tab[data-tab="${tab}"]`)?.click();
    focusAgentChatInput();
    setInterval(loadStatus, 15000);
  })();
})();
