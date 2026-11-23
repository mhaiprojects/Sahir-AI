/* Agent Manager: memory, RAG documents, tasks. Hands-free lives in voice_client.js + app.js. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const md = (t) => (window.renderMarkdown ? window.renderMarkdown(t) : esc(t));
  const when = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };
  const state = { convId: null, convs: [], busy: false };

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

  window.sahirOnView = function (view) {
    if (view === "memory") loadMemories();
    if (view === "docs") loadDocs();
    if (view === "tasks") loadTasks();
    if (view === "voice") {
      document.getElementById("voice-alias-btn")?.click();
    }
  };

  async function loadStatus() {
    try {
      const s = await api("/api/agent/status");
      $("#ag-status").textContent = `${s.chat_model} · ${s.embed_model} · ${s.vector_backend} · ${s.conversations} chats · ${s.memories_active} memories · ${s.documents} docs`;
      const badge = $("#pending-badge");
      badge.textContent = s.memories_pending;
      badge.classList.toggle("hidden", !s.memories_pending);
    } catch (e) { $("#ag-status").textContent = "status error: " + e.message; }
  }

  const savedMode = localStorage.getItem("sahir-chat-mode");
  if (savedMode && document.getElementById("chat-mode")) document.getElementById("chat-mode").value = savedMode;
  document.getElementById("chat-mode")?.addEventListener("change", (e) => window.sahirSetChatMode?.(e.target.value));

  function activeConvId() {
    return window.sahirActiveConversationId?.() ?? state.convId;
  }

  let searchT;

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
      if (act === "open") {
        window.sahirOpenConversation?.(Number(btn.dataset.conv));
        window.sahirActivateView?.("chat");
        return;
      }
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

  (async () => {
    await loadStatus();
    setInterval(loadStatus, 15000);
  })();
})();