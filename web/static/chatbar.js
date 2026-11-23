/* Home chat bar: Agent (mode), Chat (conversation) and Model pickers in the main area.
   Loaded after app.js; uses its script-scope state (homeConversationId, busy, CONV_ID_KEY,
   renderHomeMessagesFromHistory, addMessage, CHAT_HISTORY_LIMIT). */
(() => {
  "use strict";
  const chatSelect = document.getElementById("home-chat-select");
  const newBtn = document.getElementById("home-chat-new");
  const modelSelect = document.getElementById("home-model-select");
  if (!chatSelect || !modelSelect) return;

  const fmtGB = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`);
  const trim = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  // ------------------------------------------------------------------ chats
  let convs = [];
  async function refreshChats() {
    try {
      const res = await fetch("/api/conversations?limit=50");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      convs = (await res.json()).conversations || [];
    } catch (err) {
      chatSelect.innerHTML = `<option value="">Chats unavailable</option>`;
      return;
    }
    const current = homeConversationId;
    chatSelect.innerHTML = "";
    if (!current || !convs.some((c) => c.id === current)) {
      const opt = new Option(current ? `Chat #${current}` : "New chat (starts when you send)", current || "");
      chatSelect.appendChild(opt);
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
    const msgs = (conv.messages || []).filter((m) => m.role === "user" || m.role === "assistant");
    renderHomeMessagesFromHistory(msgs.slice(-CHAT_HISTORY_LIMIT));
    if (!msgs.length) addMessage("system", `Chat “${conv.title}” is empty. Type or speak to start.`);
  }

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
    } catch (err) {
      addMessage("system", `Could not open chat: ${err.message}`);
    }
    refreshChats();
  });

  newBtn?.addEventListener("click", async () => {
    if (busy) {
      addMessage("system", "Wait for the current reply before starting a new chat.");
      return;
    }
    try {
      const res = await fetch("/api/conversations", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const conv = await res.json();
      await openChat(conv.id);
      await refreshChats();
      document.getElementById("message-input")?.focus();
    } catch (err) {
      addMessage("system", `Could not start a new chat: ${err.message}`);
    }
  });

  // Keep the list fresh: after replies land (titles and counts change) and when ids change.
  let seenConv = homeConversationId;
  let t = null;
  const schedule = () => { clearTimeout(t); t = setTimeout(refreshChats, 800); };
  const chatLog = document.getElementById("chat-log");
  if (chatLog) new MutationObserver(schedule).observe(chatLog, { childList: true });
  setInterval(() => {
    if (homeConversationId !== seenConv) { seenConv = homeConversationId; refreshChats(); }
  }, 1000);

  // ----------------------------------------------------------------- models
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
    modelSelect.title = data.active_source === "user"
      ? `Active: ${data.active_model} (your pick)`
      : `Active: ${data.active_model} (smallest installed model, chosen automatically)`;
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

  // Refresh when Home is shown again (the Agent or Models views may have changed things).
  document.getElementById("nav-btn-home")?.addEventListener("click", () => { refreshChats(); refreshModels(); });
  setInterval(refreshModels, 60000);

  window.sahirRefreshChatBar = () => Promise.all([refreshChats(), refreshModels()]);
  refreshModels();
  // app.js restores the last chat asynchronously; give it a moment before the first listing.
  setTimeout(refreshChats, 600);
})();
