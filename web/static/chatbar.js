/* Chat bar: project, conversation, mode and model pickers (unified UI). */
(() => {
  "use strict";
  const chatSelect = document.getElementById("home-chat-select");
  const projectSelect = document.getElementById("chat-project-select");
  const modelSelect = document.getElementById("home-model-select");
  if (!chatSelect || !modelSelect) return;

  const fmtGB = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`);
  const trim = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  let projects = [];
  let generalProjectId = null;
  let convs = [];

  function uiMode(convMode) {
    if (convMode === "chat") return "stream";
    if (convMode === "tools") return "route";
    return convMode || "agent";
  }

  function applyProjectSelectValue(projectId) {
    if (!projectSelect?.options.length) return;
    const pid = projectId ?? generalProjectId;
    if (pid == null) return;
    const v = String(pid);
    if ([...projectSelect.options].some((o) => o.value === v)) projectSelect.value = v;
  }

  function syncConvActionsMenu() {
    const menu = document.getElementById("conv-actions-menu");
    if (!menu) return;
    const narrow = window.matchMedia("(max-width: 980px)").matches;
    if (narrow) menu.removeAttribute("open");
    else menu.setAttribute("open", "");
  }

  function updateContextUi(conv) {
    const proj = document.getElementById("breadcrumb-project");
    const chat = document.getElementById("breadcrumb-chat");
    const badge = document.getElementById("supervisor-badge");
    const actions = document.getElementById("conv-actions");
    if (proj) proj.textContent = conv.project_name || "General";
    if (chat) chat.textContent = conv.title || `Chat #${conv.id}`;
    const isSup = conv.kind === "supervisor";
    badge?.classList.toggle("hidden", !isSup);
    if (actions) {
      actions.hidden = false;
      const md = document.getElementById("conv-export-md");
      const js = document.getElementById("conv-export-json");
      if (md) md.href = `/api/conversations/${conv.id}/export?format=md`;
      if (js) js.href = `/api/conversations/${conv.id}/export?format=json`;
      const rename = document.getElementById("conv-rename");
      const del = document.getElementById("conv-delete");
      const move = document.getElementById("conv-move");
      const supBg = document.getElementById("conv-supervisor-bg");
      if (rename) rename.hidden = isSup;
      if (del) del.hidden = isSup;
      if (move) move.hidden = isSup;
      if (supBg) supBg.hidden = isSup || conv.kind === "supervisor";
    }
    applyProjectSelectValue(conv.project_id);
    window.sahirSetCurrentProject?.(conv.project_id);
    syncConvActionsMenu();
  }

  async function refreshProjects() {
    if (!projectSelect) return;
    try {
      const res = await fetch("/api/projects");
      const data = await res.json();
      projects = data.projects || [];
      generalProjectId = data.general_project_id;
      projects.sort((a, b) => {
        if (a.id === generalProjectId) return -1;
        if (b.id === generalProjectId) return 1;
        return String(a.name).localeCompare(String(b.name));
      });
      projectSelect.replaceChildren(...projects.map((p) => new Option(p.name, String(p.id))));
      applyProjectSelectValue();
    } catch {
      projectSelect.replaceChildren(new Option("Projects unavailable", ""));
    }
  }

  async function refreshChats(projectId) {
    try {
      const q = projectId ? `?limit=80&project_id=${projectId}` : "?limit=50";
      const res = await fetch(`/api/conversations${q}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      convs = (await res.json()).conversations || [];
    } catch (err) {
      chatSelect.innerHTML = `<option value="">Chats unavailable</option>`;
      return;
    }
    const current = homeConversationId;
    chatSelect.innerHTML = "";
    if (!current || !convs.some((c) => c.id === current)) {
      chatSelect.appendChild(new Option(current ? `Chat #${current}` : "Select a chat", current || ""));
    }
    for (const c of convs) {
      const title = trim(String(c.title || `Chat #${c.id}`), 48);
      chatSelect.appendChild(new Option(`${title} · ${c.message_count} msgs`, String(c.id)));
    }
    chatSelect.value = current ? String(current) : "";
  }

  async function openChat(id) {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const conv = await res.json();
    homeConversationId = conv.id;
    localStorage.setItem(CONV_ID_KEY, String(conv.id));
    localStorage.setItem("agentConv", String(conv.id));
    if (conv.mode) window.sahirSetChatMode?.(uiMode(conv.mode), { persistLocal: true, persistServer: false });
    const msgs = (conv.messages || []).filter((m) => m.role === "user" || m.role === "assistant");
    renderHomeMessagesFromHistory(msgs.slice(-CHAT_HISTORY_LIMIT));
    updateContextUi(conv);
    window.sahirWip?.setHeaderContext?.({
      project: conv.project_name || "General",
      chat: conv.title || `Chat #${conv.id}`,
    });
    window.sahirWip?.publishSwitchFromConversation(conv);
    window.sahirRefreshRail?.();
    applyProjectSelectValue(conv.project_id);
    await refreshChats(conv.project_id);
  }

  window.sahirOpenConversation = openChat;
  window.sahirToast = (msg, bad) => {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.className = "toast" + (bad ? " bad" : "");
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 3500);
  };

  chatSelect.addEventListener("change", async () => {
    const id = Number(chatSelect.value);
    if (busy) {
      addMessage("system", "Wait for the current reply before switching chats.");
      chatSelect.value = homeConversationId ? String(homeConversationId) : "";
      return;
    }
    if (!id) return;
    try {
      await openChat(id);
      window.sahirActivateView?.("chat", { replaceUrl: true });
    } catch (err) {
      addMessage("system", `Could not open chat: ${err.message}`);
    }
  });

  projectSelect?.addEventListener("change", () => {
    refreshChats(Number(projectSelect.value) || undefined);
  });

  document.getElementById("conv-rename")?.addEventListener("click", async () => {
    if (!homeConversationId) return;
    const cur = document.getElementById("breadcrumb-chat")?.textContent || "";
    const title = prompt("Rename conversation", cur);
    if (!title || title === cur) return;
    await fetch(`/api/conversations/${homeConversationId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
    });
    await openChat(homeConversationId);
    window.sahirRefreshRail?.();
  });

  document.getElementById("conv-delete")?.addEventListener("click", async () => {
    if (!homeConversationId || !confirm("Delete this conversation permanently?")) return;
    const res = await fetch(`/api/conversations/${homeConversationId}`, { method: "DELETE" });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`;
      window.sahirToast?.(String(detail), true);
      return;
    }
    homeConversationId = null;
    chatLog.innerHTML = "";
    chatHistory = [];
    updateChatEmptyState?.();
    document.getElementById("conv-actions").hidden = true;
    await refreshChats();
    window.sahirRefreshRail?.();
  });

  document.getElementById("conv-move")?.addEventListener("click", async () => {
    if (!homeConversationId) return;
    const projRes = await fetch("/api/projects");
    const { projects, general_project_id: gId } = await projRes.json();
    const names = (projects || []).map((p) => `${p.id}: ${p.name}`).join("\n");
    const raw = prompt(`Move to project id (current projects):\n${names}`);
    if (!raw?.trim()) return;
    const pid = Number(raw.split(":")[0].trim()) || Number(raw.trim());
    if (!pid) return;
    try {
      const res = await fetch(`/api/conversations/${homeConversationId}/move`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: pid }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      await openChat(homeConversationId);
      window.sahirRefreshRail?.();
      window.sahirToast?.("Chat moved.", false);
    } catch (err) {
      window.sahirToast?.(err.message, true);
    }
  });

  document.getElementById("conv-supervisor-bg")?.addEventListener("click", async () => {
    if (!homeConversationId) return;
    const reason = prompt("Optional reason for background injection", "user requested context") || "user requested context";
    const content = prompt("What background should the supervisor inject?");
    if (!content?.trim()) return;
    try {
      const convRes = await fetch(`/api/conversations/${homeConversationId}`);
      const conv = await convRes.json();
      const proj = projects.find((p) => p.id === conv.project_id);
      const fromId = proj?.supervisor_conversation_id;
      const res = await fetch("/api/supervisor/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: homeConversationId,
          content: content.trim(),
          reason,
          from_conversation_id: fromId,
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      await openChat(homeConversationId);
      window.sahirToast?.("Background injected.", false);
    } catch (err) {
      window.sahirToast?.(err.message, true);
    }
  });

  document.getElementById("conv-extract")?.addEventListener("click", async () => {
    if (!homeConversationId) return;
    addMessage("system", "Extracting memories…");
    try {
      const res = await fetch("/api/memories/extract", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: homeConversationId }),
      });
      const r = await res.json();
      addMessage("system", r.count ? `${r.count} memory suggestion(s) added for review` : "No new memories found");
    } catch (err) {
      addMessage("system", `Extract failed: ${err.message}`);
    }
  });

  let seenConv = homeConversationId;
  let t = null;
  const schedule = () => { clearTimeout(t); t = setTimeout(() => refreshChats(Number(projectSelect?.value) || undefined), 800); };
  const chatLog = document.getElementById("chat-log");
  if (chatLog) new MutationObserver(schedule).observe(chatLog, { childList: true });
  setInterval(() => {
    if (homeConversationId !== seenConv) { seenConv = homeConversationId; refreshChats(Number(projectSelect?.value) || undefined); }
  }, 1000);

  async function refreshModels() {
    let data;
    try {
      const res = await fetch("/api/models");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      modelSelect.innerHTML = `<option value="">Models unavailable</option>`;
      modelSelect.disabled = true;
      return;
    }
    modelSelect.disabled = false;
    modelSelect.innerHTML = "";
    for (const m of data.models || []) {
      let label = `${m.name} · ${fmtGB(m.size)}`;
      if (m.name === data.smallest_model) label += " · smallest";
      if (m.active && data.active_source === "auto") label += " (auto)";
      modelSelect.appendChild(new Option(label, m.name, false, !!m.active));
    }
    if (!modelSelect.options.length) modelSelect.appendChild(new Option("No chat models installed", ""));
  }

  modelSelect.addEventListener("change", async () => {
    const model = modelSelect.value;
    if (!model) return;
    modelSelect.disabled = true;
    try {
      const res = await fetch("/api/models/select", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      addMessage("system", `Chat model set to ${model}.`);
      window.refreshModels?.();
    } catch (err) {
      addMessage("system", `Could not switch model: ${err.message}`);
    }
    await refreshModels();
  });

  document.getElementById("nav-btn-chat")?.addEventListener("click", () => {
    refreshChats(Number(projectSelect?.value) || undefined);
    refreshModels();
  });
  setInterval(refreshModels, 60000);

  window.sahirSyncConvActionsMenu = syncConvActionsMenu;
  window.sahirRefreshChatBar = () => Promise.all([refreshProjects(), refreshChats(), refreshModels()]);
  syncConvActionsMenu();
  window.addEventListener("resize", syncConvActionsMenu);
  refreshModels();
  refreshProjects()
    .then(() => {
      applyProjectSelectValue();
      return refreshChats(Number(projectSelect?.value) || generalProjectId || undefined);
    })
    .then(() => window.loadHomeConversation?.())
  .catch(() => {});
})();