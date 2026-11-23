const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
// One mode control shared by Home and Agent (#38): agent | route | stream | cursor.
const homeModeSelect = document.getElementById("chat-mode");
const handsFreeToggle = document.getElementById("hands-free-toggle");
const handsFreeStopBtn = document.getElementById("hands-free-stop");
const chatEmptyState = document.getElementById("chat-empty-state");
const chatMicState = document.getElementById("chat-mic-state");
const speakRepliesToggle = document.getElementById("speak-replies-toggle");
const sendBtn = document.getElementById("send-btn");
const cursorBtn = document.getElementById("cursor-btn");
const micBtn = document.getElementById("mic-btn");
const toggleMicBtn = document.getElementById("toggle-mic-btn");
const micBadge = document.getElementById("mic-badge");
const micBadgeConfig = document.getElementById("mic-badge-config");
const micHint = document.getElementById("mic-hint");
const statusBar = document.getElementById("status-bar");
const portLabel = document.getElementById("port-label");
const requestLog = document.getElementById("request-log");
const refreshLogsBtn = document.getElementById("refresh-logs-btn");
const liveTranscript = document.getElementById("live-transcript");
const liveTranscriptLabel = document.getElementById("live-transcript-label");
const liveTranscriptStatus = document.getElementById("live-transcript-status");
const liveTranscriptLive = document.getElementById("live-transcript-live");
const liveTranscriptFinalWrap = document.getElementById("live-transcript-final-wrap");
const liveTranscriptFinal = document.getElementById("live-transcript-final");

const VAD = {
  speechThreshold: 0.018,
  speechStartThreshold: 0.014,
  conversationSilenceMs: 3000,
  liveIntervalMs: 1000,
  firstLiveDelayMs: 150,
  minSpeechMs: 450,
  maxConversationMs: 120000,
  pollMs: 100,
  speechConfirmMs: 300,
  noiseMarginStart: 2.8,
  noiseMarginSpeech: 3.4,
  minLiveDraftChars: 2,
  minZcr: 0.012,
  maxZcr: 0.38,
};

const PREROLL_MAX_CHUNKS = 10;
const WEBM_HEADER_CHUNK_COUNT = 2;
const CHAT_HISTORY_LIMIT = 20;
const MIN_UPLOAD_BYTES = 256;

const ALWAYS_LISTEN_KEY = "voice-assistant-always-listen"; // legacy; migrated to HANDSFREE_KEY
const HANDSFREE_KEY = "voice-assistant-hands-free";
const ROUTE_CURSOR_KEY = "voice-assistant-route-cursor"; // legacy, migrated to CHAT_MODE_KEY
const ROUTE_TOOLS_KEY = "voice-assistant-route-tools"; // legacy, migrated to CHAT_MODE_KEY
const CHAT_MODE_KEY = "sahir-chat-mode";
const CHAT_MODES = ["agent", "route", "stream", "cursor"];
const SPEAK_REPLIES_KEY = "voice-assistant-speak-replies";
const MIC_ENABLED_KEY = "voice-assistant-mic-enabled";
const CONV_ID_KEY = "voice-assistant-conversation-id";
const VIEW_KEY = "voice-assistant-view";

const HEALTH_POLL_MS = 30000;
const HEALTH_RETRY_MS = 5000;

// Real port (#42): the page's own port, else the server's WEB_PORT from /api/config.
let webPort = window.location.port || "";
function currentWebPort() {
  return webPort || (window.location.protocol === "https:" ? "443" : "80");
}
portLabel.textContent = currentWebPort();

let micStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let recorderMimeType = "audio/webm";
let micReady = false;
let healthData = null;
let busy = false;
let pttActive = false;

let audioContext = null;
let analyserNode = null;
let handsFreeClient = null;
let handsFreeWanted = false;
let handsFreeVoiceState = "off";
let lastLiveRequestAt = 0;
let liveDraft = "";
let liveRequestInFlight = false;
let pttLiveTimer = null;
let firstLiveTimer = null;
let commandWatchTimer = null;
let commandCapturing = false;
let commandRecorder = null;
let commandChunks = [];
let commandSpeechStartedAt = null;
let commandSilenceStartedAt = null;
let activeRequestController = null;
let currentTtsAudio = null;
let lastAssistantReply = "";
let voiceCommandsCache = [];
let chatHistory = [];
let homeConversationId = null;
let conversationChunkStart = -1;
let highpassNode = null;
let serverBuildId = null;
let healthFailures = 0;
let healthTimer = null;
let pendingReload = false;

const COMMAND_VAD = {
  speechThreshold: 0.022,
  silenceMs: 900,
  minSpeechMs: 300,
  pollMs: 100,
};

const ACTION_LABELS = {
  stop_speech: "Stop speaking",
  cancel_request: "Cancel request",
  repeat_last: "Repeat last reply",
  pause_listening: "Pause Hands-free",
  resume_listening: "Resume Hands-free",
};

function rememberChatTurn(role, text) {
  const content = String(text || "").trim();
  if ((role !== "user" && role !== "assistant") || !content) return;
  chatHistory.push({ role, content });
  if (chatHistory.length > CHAT_HISTORY_LIMIT) {
    chatHistory = chatHistory.slice(-CHAT_HISTORY_LIMIT);
  }
}

function addMessage(role, text, { skipRemember = false, meta = null } = {}) {
  const el = document.createElement("div");
  if (meta?.injection) {
    el.className = "msg injection";
    const details = document.createElement("details");
    details.className = "injection-block";
    details.open = false;
    const sum = document.createElement("summary");
    sum.textContent = "Background from supervisor";
    const body = document.createElement("div");
    body.className = "injection-body";
    body.textContent = text.replace(/^Background from supervisor[^\n]*\n+/i, "");
    details.append(sum, body);
    el.appendChild(details);
    chatLog.appendChild(el);
    chatLog.scrollTop = chatLog.scrollHeight;
    updateChatEmptyState();
    return;
  }
  el.className = `msg ${role}`;
  if ((role === "user" || role === "assistant") && typeof renderMarkdown === "function") {
    const content = document.createElement("div");
    content.className = "msg-content markdown-body";
    content.innerHTML = renderMarkdown(text);
    el.appendChild(content);
    if (!skipRemember) rememberChatTurn(role, text);
  } else {
    el.textContent = text;
  }
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  updateChatEmptyState();
}

function getChatHistoryForApi() {
  return chatHistory.map(({ role, content }) => ({ role, content }));
}

function currentChatMode() {
  const v = homeModeSelect?.value;
  return CHAT_MODES.includes(v) ? v : "agent";
}

function homeChatMode({ forceCursor = false } = {}) {
  if (forceCursor) return "cursor";
  return currentChatMode();
}

function handsFreePrefEnabled() {
  if (localStorage.getItem(HANDSFREE_KEY) != null) {
    return localStorage.getItem(HANDSFREE_KEY) === "1";
  }
  return localStorage.getItem(ALWAYS_LISTEN_KEY) === "1";
}

function persistHandsFree(enabled) {
  const v = enabled ? "1" : "0";
  localStorage.setItem(HANDSFREE_KEY, v);
  localStorage.setItem(ALWAYS_LISTEN_KEY, v);
}

function handsFreeStreaming() {
  return Boolean(handsFreeClient?.isActive?.());
}

function updateHandsFreeStopButton() {
  if (!handsFreeStopBtn) return;
  const on = handsFreeStreaming();
  handsFreeStopBtn.disabled = !on;
}

function ensureHandsFreeClient() {
  if (handsFreeClient) return handsFreeClient;
  if (!window.SahirVoiceClient) return null;
  handsFreeClient = new window.SahirVoiceClient({
    meterEl: document.getElementById("voice-meter"),
    getConversationId: () => homeConversationId,
    getChatMode: () => currentChatMode(),
    getTts: () => (speakRepliesToggle ? speakRepliesToggle.checked : true),
    onBeforeStart: () => {
      handsFreeVoiceState = "connecting";
      updateMicUi();
    },
    onStarted: () => {
      handsFreeVoiceState = "listening";
      liveTranscript?.classList.add("active");
      updateHandsFreeStopButton();
      updateMicUi();
    },
    onState: (state) => {
      handsFreeVoiceState = state || "listening";
      if (liveTranscriptStatus) {
        const labels = {
          listening: "Hands-free · listening",
          transcribing: "Transcribing…",
          thinking: "Thinking…",
          speaking: "Speaking…",
          "hearing you": "Hearing you…",
        };
        liveTranscriptStatus.textContent = labels[state] || state;
        liveTranscriptStatus.classList.toggle("thinking", state === "thinking" || state === "transcribing");
      }
      updateMicUi();
    },
    onPartial: (text) => {
      showLiveDraft(text);
    },
    onTranscript: (text, meta = {}) => {
      if (!text || meta.discarded) {
        if (meta.discarded) hideLiveTranscript();
        return;
      }
      showFinalCorrection(liveDraft, text);
      addMessage("user", text, { skipRemember: false });
    },
    onReply: (m) => {
      const reply = m.text || "";
      if (m.conversation_id) {
        homeConversationId = m.conversation_id;
        localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
        localStorage.setItem("agentConv", String(homeConversationId));
      }
      if (reply) {
        addMessage("assistant", reply);
        lastAssistantReply = reply;
      }
      if (m.action === "pause_listening") {
        handsFreeToggle.checked = false;
        persistHandsFree(false);
        stopHandsFree({ notify: false });
        addMessage("system", "Hands-free paused.");
      } else if (m.action === "resume_listening") {
        handsFreeToggle.checked = true;
        persistHandsFree(true);
        startHandsFree().catch(() => {});
        addMessage("system", "Hands-free resumed.");
      } else if (m.action === "switch_chat" && m.conversation_id) {
        window.sahirOpenConversation?.(m.conversation_id);
      }
      updateMicUi();
    },
    onBargeIn: () => {
      addMessage("system", "Barge-in — stopped reply.");
    },
    onTtsDone: () => {
      resetLiveTranscript();
    },
    onError: (msg) => {
      addMessage("system", `Hands-free error: ${msg}`);
    },
    onStop: (reason) => {
      handsFreeVoiceState = "off";
      updateHandsFreeStopButton();
      refreshLiveTranscriptIdle();
      updateMicUi();
      if (
        reason !== "user" &&
        handsFreeWanted &&
        handsFreeToggle.checked &&
        !busy &&
        !pttActive
      ) {
        window.setTimeout(() => {
          startHandsFree().catch(() => {});
        }, 400);
      }
    },
  });
  return handsFreeClient;
}

async function startHandsFree() {
  if (!handsFreeToggle?.checked) return;
  handsFreeWanted = true;
  if (micReady) await teardownMicGraph();
  const client = ensureHandsFreeClient();
  if (!client) throw new Error("Voice client unavailable");
  await client.start();
}

function stopHandsFree({ notify = true } = {}) {
  handsFreeWanted = false;
  handsFreeClient?.stop("user");
  handsFreeVoiceState = "off";
  updateHandsFreeStopButton();
  if (notify) refreshLiveTranscriptIdle();
  updateMicUi();
}

window.sahirStartHandsFree = startHandsFree;
window.sahirStopHandsFree = stopHandsFree;
window.sahirSpeakRepliesEnabled = () => (speakRepliesToggle ? speakRepliesToggle.checked : true);

function initialChatMode() {
  const saved = localStorage.getItem(CHAT_MODE_KEY);
  if (CHAT_MODES.includes(saved)) return saved;
  if (localStorage.getItem(ROUTE_CURSOR_KEY) === "1") return "cursor";
  if (localStorage.getItem(ROUTE_TOOLS_KEY) === "1") return "route";
  return "agent";
}

window.sahirSetChatMode = function sahirSetChatMode(mode, { persistLocal = true, persistServer = true } = {}) {
  if (!CHAT_MODES.includes(mode)) return;
  if (persistLocal) localStorage.setItem(CHAT_MODE_KEY, mode);
  if (homeModeSelect && homeModeSelect.value !== mode) homeModeSelect.value = mode;
  if (persistServer && homeConversationId) {
    fetch(`/api/conversations/${homeConversationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }).catch(() => {});
  }
};

function focusHomeChatInput() {
  if (messageInput && !messageInput.disabled && !busy) {
    messageInput.focus();
  }
}

function isCursorSendChord(e) {
  return e.key === "Enter" && (e.ctrlKey || e.metaKey);
}

async function applyComposerRoute(ev, { userMessage } = {}) {
  const target = Number(ev.route_target_chat_id || ev.conversation_id) || null;
  const shouldSwitch =
    ev.route_command === "switch_chat" ||
    ev.route_label === "D" ||
    (ev.route_target_chat_id && target !== homeConversationId);
  if (!shouldSwitch || !target || !window.sahirOpenConversation) return false;
  if (target === homeConversationId) return false;
  await window.sahirOpenConversation(target);
  if (userMessage) addMessage("user", userMessage, { skipRemember: true });
  return true;
}

async function consumeAgentSse(res, { onToken, onMeta, userMessage } = {}) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let reply = "";
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
      try {
        ev = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (ev.type === "route") {
        await applyComposerRoute(ev, { userMessage });
        onMeta?.(ev);
      }
      if (ev.type === "meta" && ev.conversation_id) {
        homeConversationId = ev.conversation_id;
        localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
        onMeta?.(ev);
      }
      if (ev.type === "token") {
        reply += ev.text;
        onToken?.(reply);
      }
      if (ev.type === "done") {
        reply = ev.reply || reply;
        await applyComposerRoute(ev, { userMessage });
        if (ev.conversation_id) {
          homeConversationId = ev.conversation_id;
          localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
        }
        return { reply, ...ev };
      }
      if (ev.type === "error") throw new Error(ev.error || "Stream error");
    }
  }
  return { reply };
}

function updateChatEmptyState() {
  if (!chatEmptyState || !chatLog) return;
  const hasMsgs = chatLog.querySelector(".msg.user, .msg.assistant");
  chatEmptyState.classList.toggle("hidden", !!hasMsgs);
}

function renderHomeMessagesFromHistory(messages) {
  if (!chatLog) return;
  chatLog.setAttribute("aria-busy", "true");
  chatLog.innerHTML = "";
  chatHistory = [];
  for (const m of messages) {
    const meta = m.meta || {};
    if (m.role === "system" && meta.injection) {
      addMessage("system", m.content, { skipRemember: true, meta });
    } else if (m.role === "user" || m.role === "assistant") {
      addMessage(m.role, m.content, { skipRemember: false });
    }
  }
  chatLog.removeAttribute("aria-busy");
  updateChatEmptyState();
}

async function loadHomeConversation() {
  try {
    const projRes = await fetch("/api/projects");
    const projData = await projRes.json();
    const projects = projData.projects || [];
    const general = projects.find((p) => p.id === projData.general_project_id) || projects[0];
    const supId = general?.supervisor_conversation_id;

    let id = Number(localStorage.getItem(CONV_ID_KEY)) || Number(localStorage.getItem("agentConv"));
    if (id) {
      const probe = await fetch(`/api/conversations/${id}`);
      if (!probe.ok) id = 0;
    }
    if (!id && supId) id = Number(supId);
    if (!id) {
      const listRes = await fetch("/api/conversations?limit=50");
      const convs = (await listRes.json()).conversations || [];
      id = convs[0]?.id;
    }
    if (!id) {
      window.sahirWip?.setHeaderContext?.({ project: general?.name || "General", chat: "—" });
      return;
    }
    if (window.sahirOpenConversation) {
      await window.sahirOpenConversation(id);
      return;
    }
    const convRes = await fetch(`/api/conversations/${id}`);
    if (!convRes.ok) return;
    const conv = await convRes.json();
    homeConversationId = conv.id;
    localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
    localStorage.setItem("agentConv", String(homeConversationId));
    const msgs = (conv.messages || []).filter((m) => m.role === "user" || m.role === "assistant");
    renderHomeMessagesFromHistory(msgs.slice(-CHAT_HISTORY_LIMIT));
    window.sahirWip?.setHeaderContext?.({
      project: conv.project_name || "General",
      chat: conv.title || `Chat #${conv.id}`,
    });
  } catch (err) {
    console.warn("Could not restore home chat", err);
  }
}

window.loadHomeConversation = loadHomeConversation;

function addModelTag(data) {
  // Show which model answered (and whether the big fallback model was used).
  const details = data?.action_details || {};
  if (!details.model) return;
  const last = chatLog.lastElementChild;
  if (!last || !last.classList.contains("assistant")) return;
  const tag = document.createElement("div");
  tag.className = "msg-model";
  tag.textContent = details.escalated
    ? `${details.model} (fallback: ${String(details.escalated).replace("invalid_output:", "invalid output ")})`
    : details.model;
  last.appendChild(tag);
}

function setBusy(nextBusy) {
  busy = nextBusy;
  sendBtn.disabled = busy;
  if (cursorBtn) cursorBtn.disabled = busy;
  micBtn.disabled = busy || !micReady || handsFreeToggle?.checked;
  if (busy && handsFreeStreaming()) {
    stopHandsFree({ notify: false });
  }
  messageInput.disabled = busy;

  if (busy) {
    if (handsFreeStreaming()) stopHandsFree({ notify: false });
  } else {
    stopCommandCapture();
    if (pendingReload) {
      reloadForNewBuild();
    }
    if (handsFreeToggle?.checked && handsFreeWanted && !handsFreeStreaming()) {
      startHandsFree().catch(() => {});
    }
  }
  updateMicUi();
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

function formatAction(action) {
  if (!action) return "none";
  return action.replaceAll("_", " ");
}

function buildLogEntryElement(log) {
  const entry = document.createElement("article");
  entry.className = `log-entry${log.status === "error" ? " error" : ""}`;
  entry.dataset.logId = String(log.id);

  const meta = document.createElement("div");
  meta.className = "log-meta";
  meta.innerHTML = `
    <span>#${log.id}</span>
    <span>${formatTime(log.created_at)}</span>
    <span>${log.input_type}</span>
    ${log.client_ip ? `<span>${log.client_ip}</span>` : ""}
    ${log.duration_ms != null ? `<span>${log.duration_ms} ms</span>` : ""}
    <span class="log-action">${formatAction(log.action)}</span>
  `;
  entry.appendChild(meta);
  entry.appendChild(makeLogBlock("User", log.user_message));
  if (log.ai_reply) entry.appendChild(makeLogBlock("Assistant", log.ai_reply));

  if (log.action_details && Object.keys(log.action_details).length) {
    const details = { ...log.action_details };
    delete details.reply;
    if (Object.keys(details).length) {
      entry.appendChild(makeLogBlock("Action details", JSON.stringify(details, null, 2)));
    }
  }

  if (log.error_message) {
    entry.appendChild(makeLogBlock("Error", log.error_message));
  }

  return entry;
}

function prependLogEntry(log) {
  const empty = requestLog.querySelector(".empty");
  if (empty) empty.remove();

  const existing = requestLog.querySelector(`[data-log-id="${log.id}"]`);
  if (existing) existing.remove();

  requestLog.insertBefore(buildLogEntryElement(log), requestLog.firstChild);
  requestLog.scrollTop = 0;
}

function renderLogs(logs) {
  requestLog.innerHTML = "";
  if (!logs.length) {
    requestLog.innerHTML = '<p class="empty">No requests yet.</p>';
    return;
  }

  for (const log of logs) {
    requestLog.appendChild(buildLogEntryElement(log));
  }
  requestLog.scrollTop = 0;
}

function makeLogBlock(label, text) {
  const block = document.createElement("div");
  block.className = "log-block";
  block.innerHTML = `<div class="log-label">${label}</div><div class="log-text"></div>`;
  block.querySelector(".log-text").textContent = text;
  return block;
}

async function refreshLogs() {
  try {
    const res = await fetch("/api/logs?limit=100");
    const data = await res.json();
    renderLogs(data.logs || []);
  } catch (err) {
    requestLog.innerHTML = `<p class="empty">Could not load logs: ${err.message}</p>`;
  }
}

function updateWakeWordNote(cfg) {
  const note = document.getElementById("wake-word-note");
  if (!note || !cfg) return;
  const phrase = String(cfg.wakeword_model || "").replaceAll("_", " ");
  const desktop = cfg.wakeword_enabled && phrase
    ? ` The desktop app (bot.py) listens for “${phrase}”.`
    : "";
  if (cfg.web_wakeword) {
    note.innerHTML = "";
    note.append("Wake word: ");
    const strong = document.createElement("strong");
    strong.textContent = phrase ? `“${phrase}”` : "on";
    note.append(strong, ". Hands-free waits for it before recording.");
    return;
  }
  note.innerHTML = "";
  note.append("Wake word: ");
  const strong = document.createElement("strong");
  strong.textContent = "off in the browser";
  note.append(strong, `. With Hands-free on, speech streams to the server VAD.${desktop}`);
}

async function loadWebConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    VAD.conversationSilenceMs = Math.round(
      (data.conversation_silence_seconds || 3) * 1000
    );
    VAD.liveIntervalMs = Math.round(
      (data.live_transcribe_interval_seconds || 2) * 1000
    );
    updateWakeWordNote(data);
    if (!window.location.port && data.web_port) {
      webPort = String(data.web_port);
      portLabel.textContent = currentWebPort();
    }
  } catch (_) {
    /* defaults */
  }
}

function scheduleHealthCheck(delayMs) {
  if (healthTimer) clearTimeout(healthTimer);
  healthTimer = setTimeout(refreshHealth, delayMs);
}

function canReloadNow() {
  return !busy && !pttActive && !handsFreeStreaming() && !currentTtsAudio;
}

function reloadForNewBuild() {
  if (!canReloadNow()) {
    pendingReload = true;
    return;
  }
  pendingReload = false;
  addMessage("system", "Server was updated — reloading the UI…");
  setTimeout(() => window.location.reload(), 1200);
}

function handleHealthOk(data) {
  const recovered = healthFailures > 0;
  healthFailures = 0;
  if (data.build_id) {
    if (serverBuildId && data.build_id !== serverBuildId) {
      reloadForNewBuild();
    }
    if (!serverBuildId) serverBuildId = data.build_id;
  }
  if (recovered && !pendingReload) {
    addMessage("system", "Connection to the server restored.");
    loadWebConfig();
    refreshLogs();
    refreshCommands();
  }
}

async function refreshHealth() {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    healthData = await res.json();
    handleHealthOk(healthData);
    statusBar.innerHTML = "";

    const tx = healthData.transcription || {};
    // Label the TTS badge with the active backend (#43).
    const tts = healthData.tts || {};
    const ttsBackend = String(tts.backend || "piper").toLowerCase();
    const ttsName = `${ttsBackend.charAt(0).toUpperCase()}${ttsBackend.slice(1)} TTS`;
    const ttsOk = ttsBackend === "kokoro" ? Boolean(tts.kokoro_model_present) : healthData.piper?.ok;
    const items = [
      ["Ollama", healthData.ollama?.ok, healthData.ollama?.target_loaded ? "model ready" : "check model"],
      ["Cursor CLI", healthData.cursor_cli?.ok],
      [ttsName, ttsOk],
      ["Whisper STT", healthData.whisper?.ok],
      ["HTTPS", healthData.https_enabled, healthData.https_enabled ? "enabled" : "off"],
      ["Live STT", true, tx.whisper_live_model || "tiny"],
      ["Final STT", true, tx.whisper_final_model || "base"],
    ];

    for (const [name, ok, extra] of items) {
      const badge = document.createElement("span");
      badge.className = `badge ${ok ? "ok" : "bad"}`;
      badge.textContent = extra ? `${name}: ${extra}` : name;
      statusBar.appendChild(badge);
    }

    if (tx.conversation_silence_seconds) {
      VAD.conversationSilenceMs = Math.round(tx.conversation_silence_seconds * 1000);
    }
    if (tx.live_interval_seconds) {
      VAD.liveIntervalMs = Math.round(tx.live_interval_seconds * 1000);
    }

    updateMicHint();
    scheduleHealthCheck(HEALTH_POLL_MS);
  } catch (_) {
    statusBar.innerHTML = `<span class="badge bad">Health check failed</span>`;
    healthFailures += 1;
    if (healthFailures === 1) {
      addMessage("system", "Lost connection to the server — retrying…");
    }
    scheduleHealthCheck(HEALTH_RETRY_MS);
  }
}

function isMobileBrowser() {
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
}

async function getMicPermissionState() {
  try {
    if (!navigator.permissions?.query) return "unknown";
    const status = await navigator.permissions.query({ name: "microphone" });
    return status.state || "unknown";
  } catch (_) {
    return "unknown";
  }
}

async function watchMicPermission() {
  try {
    if (!navigator.permissions?.query) return;
    const status = await navigator.permissions.query({ name: "microphone" });
    status.onchange = () => {
      if (status.state === "denied" && micReady) {
        addMessage("system", "Microphone permission was revoked in the browser.");
      }
      updateMicHint();
    };
  } catch (_) {
    /* Permissions API not supported for microphone (e.g. older Safari) */
  }
}

async function promptForMicPermission() {
  const state = await getMicPermissionState();
  if (state === "prompt") {
    addMessage("system", "Your browser will ask for microphone access — choose Allow.");
  } else if (state === "denied") {
    addMessage(
      "system",
      "Microphone is blocked for this site. Click the lock/site-settings icon in the address bar, allow Microphone, then try again."
    );
  }
  return state;
}

function resolveGetUserMedia() {
  if (navigator.mediaDevices?.getUserMedia) {
    return (constraints) => navigator.mediaDevices.getUserMedia(constraints);
  }
  const legacy =
    navigator.getUserMedia ||
    navigator.webkitGetUserMedia ||
    navigator.mozGetUserMedia;
  if (!legacy) return null;
  return (constraints) =>
    new Promise((resolve, reject) => {
      legacy.call(navigator, constraints, resolve, reject);
    });
}

function getLiveTranscriptIdleState() {
  if (!micReady) {
    return {
      status: "Mic off",
      text: "Enable the microphone to see live captions while you speak.",
    };
  }
  if (busy) {
    return {
      status: "Processing…",
      text: liveDraft || "Waiting for assistant reply…",
    };
  }
  if (handsFreeStreaming()) {
    return {
      status: "Hands-free active",
      text: liveDraft || "Speak clearly — server VAD detects end of turn.",
    };
  }
  if (handsFreeToggle?.checked) {
    return {
      status: "Hands-free starting…",
      text: "Connecting streaming voice…",
    };
  }
  return {
    status: "Ready",
    text: "Hold to listen or turn on Hands-free.",
  };
}

function refreshLiveTranscriptIdle() {
  if (pttActive || handsFreeStreaming()) return;
  if (liveTranscriptStatus?.classList.contains("thinking")) return;

  const { status, text } = getLiveTranscriptIdleState();
  liveTranscript.classList.remove("active", "hidden");
  if (liveTranscriptLabel) liveTranscriptLabel.textContent = "Live transcription";
  liveTranscriptLive.textContent = text;
  liveTranscriptLive.classList.add("idle");
  liveTranscriptLive.classList.remove("stale");
  if (liveTranscriptFinal) liveTranscriptFinal.textContent = "";
  if (liveTranscriptFinalWrap) liveTranscriptFinalWrap.classList.add("hidden");
  if (liveTranscriptStatus) {
    liveTranscriptStatus.textContent = status;
    liveTranscriptStatus.classList.remove("thinking");
  }
}

function showLivePanel(statusText, { thinking = false } = {}) {
  liveTranscript.classList.remove("hidden");
  liveTranscript.classList.add("active");
  if (liveTranscriptStatus) {
    liveTranscriptStatus.textContent = statusText;
    liveTranscriptStatus.classList.toggle("thinking", thinking);
  }
}

function showLiveDraft(text) {
  liveDraft = text || "";
  if (liveTranscriptLabel) liveTranscriptLabel.textContent = "Live transcription";
  liveTranscriptLive.textContent = liveDraft || "Listening…";
  liveTranscriptLive.classList.remove("stale", "idle");
  if (liveTranscriptFinalWrap) liveTranscriptFinalWrap.classList.add("hidden");
  showLivePanel("Updating as you speak", { thinking: !liveDraft });
}

function showRefiningDraft(text) {
  liveDraft = text || "";
  if (liveTranscriptLabel) liveTranscriptLabel.textContent = "Live transcription";
  liveTranscriptLive.textContent = liveDraft || "…";
  liveTranscriptLive.classList.add("stale");
  liveTranscriptLive.classList.remove("idle");
  if (liveTranscriptFinalWrap) liveTranscriptFinalWrap.classList.add("hidden");
  showLivePanel("Refining with high-quality model…", { thinking: true });
}

function showFinalCorrection(liveText, finalText) {
  const corrected =
    liveText &&
    finalText &&
    liveText.trim().toLowerCase() !== finalText.trim().toLowerCase();

  if (liveTranscriptLabel) {
    liveTranscriptLabel.textContent = corrected ? "Transcription corrected" : "Transcription";
  }
  liveTranscriptLive.textContent = liveText || finalText || "";
  liveTranscriptLive.classList.toggle("stale", corrected);
  liveTranscriptLive.classList.remove("idle");

  if (corrected && liveTranscriptFinal && liveTranscriptFinalWrap) {
    liveTranscriptFinal.textContent = finalText;
    liveTranscriptFinalWrap.classList.remove("hidden");
    showLivePanel("Corrected", { thinking: false });
  } else {
    if (liveTranscriptFinalWrap) liveTranscriptFinalWrap.classList.add("hidden");
    showLivePanel("Final", { thinking: false });
  }
}

function showLiveTranscript(text) {
  showLiveDraft(text);
}

function resetLiveTranscript() {
  liveDraft = "";
  refreshLiveTranscriptIdle();
}

function hideLiveTranscript() {
  resetLiveTranscript();
}

function clearLiveTimers() {
  if (pttLiveTimer) {
    clearInterval(pttLiveTimer);
    pttLiveTimer = null;
  }
  if (firstLiveTimer) {
    clearTimeout(firstLiveTimer);
    firstLiveTimer = null;
  }
}

function scheduleLiveUpdates() {
  clearLiveTimers();
  firstLiveTimer = setTimeout(() => {
    requestLiveTranscript();
    lastLiveRequestAt = Date.now();
  }, VAD.firstLiveDelayMs);

  pttLiveTimer = setInterval(() => {
    if (Date.now() - lastLiveRequestAt >= VAD.liveIntervalMs) {
      lastLiveRequestAt = Date.now();
      requestLiveTranscript();
    }
  }, VAD.liveIntervalMs);
}

function stopCurrentTts() {
  handsFreeClient?.stopPlayback?.();
  if (currentTtsAudio) {
    currentTtsAudio.pause();
    currentTtsAudio.currentTime = 0;
    currentTtsAudio = null;
  }
}

function abortActiveRequest() {
  if (activeRequestController) {
    activeRequestController.abort();
    activeRequestController = null;
  }
}

function startCommandWatcher() {
  if (commandWatchTimer || !micReady) return;
  commandWatchTimer = setInterval(runCommandWatchLoop, COMMAND_VAD.pollMs);
}

function stopCommandWatcher() {
  if (commandWatchTimer) {
    clearInterval(commandWatchTimer);
    commandWatchTimer = null;
  }
  stopCommandCapture();
}

function beginCommandRecorder() {
  if (!micStream || commandRecorder || mediaRecorder || pttActive) return false;
  commandChunks = [];
  const mimeType = pickRecorderMimeType() || "audio/webm";
  commandRecorder = mimeType
    ? new MediaRecorder(micStream, { mimeType })
    : new MediaRecorder(micStream);
  commandRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) commandChunks.push(event.data);
  };
  commandRecorder.start(200);
  return true;
}

async function finishCommandRecorder() {
  if (!commandRecorder || commandRecorder.state === "inactive") {
    return null;
  }
  const recorder = commandRecorder;
  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });
  const blob = commandChunks.length
    ? new Blob(commandChunks, { type: recorder.mimeType || "audio/webm" })
    : null;
  commandChunks = [];
  commandRecorder = null;
  return blob && blob.size ? blob : null;
}

function stopCommandCapture() {
  commandCapturing = false;
  commandSpeechStartedAt = null;
  commandSilenceStartedAt = null;
  if (commandRecorder && commandRecorder.state !== "inactive") {
    commandRecorder.stop();
  }
  commandRecorder = null;
  commandChunks = [];
}

function startCommandCapture(now) {
  if (commandCapturing || !busy) return;
  if (beginCommandRecorder()) {
    commandCapturing = true;
    commandSpeechStartedAt = now;
    commandSilenceStartedAt = null;
  }
}

async function transcribeCommandBlob(blob) {
  const ext = blob.type.includes("ogg") ? "command.ogg" : "command.webm";
  const form = new FormData();
  form.append("audio", blob, ext);
  const res = await fetch("/api/transcribe/live", { method: "POST", body: form });
  if (!res.ok) return "";
  const data = await res.json();
  return (data.transcript || "").trim();
}

async function matchVoiceCommand(text) {
  const res = await fetch("/api/commands/match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.matched ? data : null;
}

async function executeVoiceCommand(action, keyword, heardText) {
  addMessage("system", `Voice command "${keyword}" → ${ACTION_LABELS[action] || action}`);

  if (action === "stop_speech") {
    stopCurrentTts();
    return;
  }
  if (action === "cancel_request") {
    stopCurrentTts();
    handsFreeClient?.interrupt?.();
    abortActiveRequest();
    setBusy(false);
    return;
  }
  if (action === "repeat_last") {
    if (!lastAssistantReply) {
      addMessage("system", "Nothing to repeat yet.");
      return;
    }
    addMessage("assistant", lastAssistantReply);
    await maybePlayTts(lastAssistantReply);
    return;
  }
  if (action === "pause_listening") {
    handsFreeToggle.checked = false;
    persistHandsFree(false);
    stopHandsFree({ notify: true });
    micBtn.disabled = busy || !micReady;
    addMessage("system", "Hands-free paused.");
    updateMicUi();
    return;
  }
  if (action === "resume_listening") {
    handsFreeToggle.checked = true;
    persistHandsFree(true);
    handsFreeWanted = true;
    if (!busy) {
      startHandsFree().catch((err) => addMessage("system", err.message));
    }
    micBtn.disabled = true;
    addMessage("system", "Hands-free resumed.");
    updateMicUi();
  }

  if (heardText) {
    prependLogEntry({
      id: Date.now(),
      created_at: new Date().toISOString(),
      client_ip: null,
      input_type: "command",
      user_message: heardText,
      ai_reply: ACTION_LABELS[action] || action,
      routed: false,
      action,
      action_details: { keyword },
      duration_ms: null,
      status: "success",
      error_message: null,
    });
  }
}

async function finishCommandCapture() {
  if (!commandCapturing) return;
  const speechMs = commandSpeechStartedAt ? Date.now() - commandSpeechStartedAt : 0;
  commandCapturing = false;
  commandSpeechStartedAt = null;
  commandSilenceStartedAt = null;

  const blob = await finishCommandRecorder();
  if (!blob || speechMs < COMMAND_VAD.minSpeechMs) return;

  try {
    const heard = await transcribeCommandBlob(blob);
    if (!heard) return;
    const match = await matchVoiceCommand(heard);
    if (match) {
      await executeVoiceCommand(match.action, match.keyword, heard);
    }
  } catch (err) {
    addMessage("system", `Command detection error: ${err.message}`);
  }
}

async function runCommandWatchLoop() {
  if (!micReady || !analyserNode || !busy) return;
  if (pttActive || handsFreeStreaming()) return;

  const level = getMicLevel();
  const now = Date.now();
  const speaking = level >= COMMAND_VAD.speechThreshold;

  if (commandCapturing) {
    if (speaking) {
      commandSilenceStartedAt = null;
      return;
    }
    if (!commandSilenceStartedAt) {
      commandSilenceStartedAt = now;
      return;
    }
    if (now - commandSilenceStartedAt >= COMMAND_VAD.silenceMs) {
      await finishCommandCapture();
    }
    return;
  }

  if (speaking) {
    startCommandCapture(now);
  }
}

function updateMicHint() {
  const onLocalhost = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  const secure = window.isSecureContext;
  const silenceSec = (VAD.conversationSilenceMs / 1000).toFixed(1);

  if (!secure && !onLocalhost) {
    micHint.classList.remove("hidden");
    micHint.textContent = isMobileBrowser()
      ? "On mobile, open the site via https:// (not http://). Generate certs on the PC with scripts/generate_web_cert.sh and set WEB_SSL_CERT/WEB_SSL_KEY in .env."
      : "Microphone access from other devices requires HTTPS. Run scripts/generate_web_cert.sh, set WEB_SSL_CERT/WEB_SSL_KEY, and reopen this page via https://.";
    return;
  }

  if (!micReady) {
    micHint.classList.remove("hidden");
    micHint.textContent = "Click Enable microphone, then use hold-to-listen or turn on Hands-free.";
    return;
  }

  if (handsFreeToggle?.checked || handsFreeStreaming()) {
    micHint.classList.remove("hidden");
    micHint.textContent =
      `Hands-free streams to the server VAD — speak naturally; barge in by talking over a reply or press Stop. Say command keywords (e.g. "stop", "cancel") while the assistant is speaking.`;
    return;
  }

  if (micReady && busy) {
    micHint.classList.remove("hidden");
    micHint.textContent = "Listening for command keywords while processing…";
    return;
  }

  micHint.classList.add("hidden");
}

function setMicBadge(state, text) {
  for (const el of [micBadge, micBadgeConfig]) {
    if (!el) continue;
    el.className = `badge ${state}`;
    el.textContent = text;
  }
  if (chatMicState) chatMicState.textContent = text.replace(/^Mic:\s*/i, "Mic: ");
}

function updateToggleMicButton() {
  if (!toggleMicBtn) return;
  if (micReady) {
    toggleMicBtn.textContent = "Disable microphone";
    toggleMicBtn.classList.add("mic-on");
    toggleMicBtn.title = "Turn off microphone access";
  } else {
    toggleMicBtn.textContent = "Enable microphone";
    toggleMicBtn.classList.remove("mic-on");
    toggleMicBtn.title = "Request microphone access";
  }
}

function updateMicUi() {
  if (handsFreeStreaming()) {
    micBtn.textContent = "Hands-free on";
    micBtn.classList.add("listening");
    micBtn.classList.remove("recording");
    setMicBadge("ok", `Mic: ${handsFreeVoiceState || "hands-free"}`);
  } else if (handsFreeToggle?.checked) {
    micBtn.textContent = "Hands-free…";
    micBtn.classList.add("listening");
    setMicBadge("ok", "Mic: hands-free");
  } else if (busy && micReady) {
    micBtn.textContent = "Processing…";
    micBtn.classList.remove("listening", "recording");
    setMicBadge("ok", "Mic: command watch");
  } else {
    micBtn.textContent = "Hold to listen";
    micBtn.classList.remove("listening", "recording");
    if (micReady) setMicBadge("ok", "Mic: ready");
  }
  updateToggleMicButton();
  updateMicHint();
  if (!pttActive && !handsFreeStreaming() && !liveTranscriptStatus?.classList.contains("thinking")) {
    refreshLiveTranscriptIdle();
  }
  updateHandsFreeStopButton();
}

async function ensureMicAccess() {
  const onLocalhost = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  if (!window.isSecureContext && !onLocalhost) {
    throw new Error(
      isMobileBrowser()
        ? "Mobile requires HTTPS for the microphone. On your PC: bash scripts/generate_web_cert.sh, set WEB_SSL_CERT and WEB_SSL_KEY in .env, restart, then open https://<pc-ip>:" + currentWebPort() + " on this phone."
        : "Microphone requires HTTPS when opened from a LAN IP. Enable WEB_SSL_CERT/WEB_SSL_KEY."
    );
  }

  const getUserMedia = resolveGetUserMedia();
  if (!getUserMedia) {
    throw new Error(
      "Microphone API unavailable. Use Chrome or Safari over HTTPS, not plain http:// on a LAN address."
    );
  }

  await teardownMicGraph();

  const constraints = isMobileBrowser()
    ? {
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      }
    : {
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      };

  micStream = await getUserMedia(constraints);

  micReady = true;
  micBtn.disabled = handsFreeToggle.checked;
  setMicBadge("ok", "Mic: ready");
  await setupMicGraph();
  startCommandWatcher();
  updateMicUi();

}

async function setupMicGraph() {
  if (!micStream) return;

  audioContext = new AudioContext();
  if (audioContext.state === "suspended") {
    // Restored-on-load mic has no user gesture yet; resume on first interaction.
    audioContext.resume().catch(() => {});
    const resumeOnGesture = () => {
      audioContext?.resume().catch(() => {});
    };
    document.addEventListener("pointerdown", resumeOnGesture, { once: true });
    document.addEventListener("keydown", resumeOnGesture, { once: true });
  }

  const source = audioContext.createMediaStreamSource(micStream);
  highpassNode = audioContext.createBiquadFilter();
  highpassNode.type = "highpass";
  highpassNode.frequency.value = 100;
  highpassNode.Q.value = 0.7;

  analyserNode = audioContext.createAnalyser();
  analyserNode.fftSize = 2048;
  source.connect(highpassNode);
  highpassNode.connect(analyserNode);
}

async function teardownMicGraph() {
  stopCommandWatcher();
  clearLiveTimers();
  if (!handsFreeToggle?.checked) hideLiveTranscript();

  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  mediaRecorder = null;
  recordedChunks = [];

  if (audioContext) {
    await audioContext.close().catch(() => {});
    audioContext = null;
  }
  analyserNode = null;
  highpassNode = null;

  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }

  micReady = false;
}

function getAudioMetrics() {
  if (!analyserNode) return { rms: 0, zcr: 0 };
  const data = new Float32Array(analyserNode.fftSize);
  analyserNode.getFloatTimeDomainData(data);
  let sum = 0;
  let crossings = 0;
  for (let i = 0; i < data.length; i += 1) {
    sum += data[i] * data[i];
    if (i > 0 && (data[i] >= 0) !== (data[i - 1] >= 0)) {
      crossings += 1;
    }
  }
  return {
    rms: Math.sqrt(sum / data.length),
    zcr: crossings / data.length,
  };
}

function getMicLevel() {
  return getAudioMetrics().rms;
}

function pickRecorderMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];
  for (const type of candidates) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

function logClientError(context, err) {
  console.error(`[voice-assistant] ${context}`, err);
}

function attachRecorderHandlers(recorder, label) {
  recorder.onerror = (event) => {
    const message = event.error?.message || "unknown recorder error";
    logClientError(`${label} recorder error`, event.error || message);
    addMessage("system", `Microphone recorder error: ${message}`);
  };
}

function chunksForUpload(startIndex = -1) {
  if (startIndex < 0 || startIndex === 0) {
    return recordedChunks;
  }
  const headerCount = Math.min(WEBM_HEADER_CHUNK_COUNT, recordedChunks.length);
  const header = recordedChunks.slice(0, headerCount);
  const body = recordedChunks.slice(startIndex);
  if (!body.length) return header;
  if (startIndex <= headerCount) {
    return recordedChunks.slice(0);
  }
  return [...header, ...body];
}

function getActiveRecordedChunks() {
  return chunksForUpload(conversationChunkStart);
}

function getRecordedBlob() {
  const chunks = getActiveRecordedChunks();
  if (!chunks.length) return null;
  return new Blob(chunks, { type: recorderMimeType });
}

function beginRecorder() {
  if (!micStream || mediaRecorder) return;

  recordedChunks = [];
  conversationChunkStart = -1;
  recorderMimeType = pickRecorderMimeType() || "audio/webm";
  mediaRecorder = recorderMimeType
    ? new MediaRecorder(micStream, { mimeType: recorderMimeType })
    : new MediaRecorder(micStream);
  attachRecorderHandlers(mediaRecorder, "PTT");

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) recordedChunks.push(event.data);
  };

  mediaRecorder.start(100);
}

async function finishRecorder() {
  if (!mediaRecorder || mediaRecorder.state === "inactive") {
    return null;
  }

  const recorder = mediaRecorder;
  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });

  const blob = getRecordedBlob();
  recordedChunks = [];
  mediaRecorder = null;
  return blob && blob.size ? blob : null;
}

async function requestLiveTranscript() {
  const canRun = pttActive && mediaRecorder;
  if (liveRequestInFlight || !canRun) return;

  const blob = getRecordedBlob();
  if (!blob || blob.size < MIN_UPLOAD_BYTES) return;

  liveRequestInFlight = true;
  try {
    const ext = blob.type.includes("ogg") ? "partial.ogg" : "partial.webm";
    const form = new FormData();
    form.append("audio", blob, ext);

    const res = await fetch("/api/transcribe/live", { method: "POST", body: form });
    if (!res.ok) return;

    const data = await res.json();
    if (data.transcript) {
      liveDraft = data.transcript;
      showLiveDraft(liveDraft);
    } else if (!liveDraft) {
      showLiveDraft("");
    }
  } catch (_) {
    /* live captions are best-effort */
  } finally {
    liveRequestInFlight = false;
  }
}

async function sendAudioBlob(blob, mode = "ptt", livePreview = "") {
  const voiceStartedAt = Date.now();
  setBusy(true);
  showRefiningDraft(livePreview);
  activeRequestController = new AbortController();

  try {
    const ext = blob.type.includes("ogg") ? "recording.ogg" : "recording.webm";
    const form = new FormData();
    form.append("audio", blob, ext);

    const params = new URLSearchParams({
      route: String(currentChatMode() !== "stream"),
      force_cursor: String(currentChatMode() === "cursor"),
      mode,
      phase: "final",
    });
    if (homeConversationId) params.set("conversation_id", String(homeConversationId));
    if (livePreview) params.set("live_transcript", livePreview);
    const historyJson = JSON.stringify(getChatHistoryForApi());
    if (historyJson !== "[]") params.set("history", historyJson);

    const res = await fetch(`/api/transcribe?${params.toString()}`, {
      method: "POST",
      body: form,
      signal: activeRequestController.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }

    const data = await res.json();
    const finalText = data.transcript || "";
    const draftText = data.live_transcript || livePreview || "";

    showFinalCorrection(draftText, finalText);
    await new Promise((resolve) => setTimeout(resolve, 800));

    if (finalText) addMessage("user", finalText);
    addMessage("assistant", data.reply || "(no reply text)");
    addModelTag(data);
    lastAssistantReply = data.reply || "";
    reportCursorCompletion(data, voiceStartedAt);

    prependLogEntry({
      id: data.log_id,
      created_at: new Date().toISOString(),
      client_ip: null,
      input_type: "voice",
      user_message: finalText || draftText || "[voice input]",
      ai_reply: data.reply,
      routed: currentChatMode() !== "stream",
      action: data.action,
      action_details: data.action_details || {},
      duration_ms: null,
      status: "success",
      error_message: null,
    });

    await maybePlayTts(data.reply);
  } catch (err) {
    if (err.name === "AbortError") {
      addMessage("system", "Request cancelled.");
    } else {
      hideLiveTranscript();
      logClientError("sendAudioBlob failed", err);
      addMessage("system", `Mic error: ${err.message}`);
    }
  } finally {
    activeRequestController = null;
    setBusy(false);
    resetLiveTranscript();
  }
}

async function startPttRecording() {
  if (handsFreeStreaming() || handsFreeToggle?.checked) {
    addMessage("system", "Turn off Hands-free to use hold-to-listen.");
    return;
  }
  if (!micReady || !micStream) {
    addMessage("system", "Enable the microphone first.");
    return;
  }

  pttActive = true;
  liveDraft = "";
  beginRecorder();
  showLiveDraft("");
  scheduleLiveUpdates();
  micBtn.classList.add("recording");
  micBtn.textContent = "Recording…";
  showLivePanel("Recording…", { thinking: true });
}

async function stopPttRecording() {
  if (!pttActive || !mediaRecorder) return;

  pttActive = false;
  clearLiveTimers();
  micBtn.classList.remove("recording");
  micBtn.textContent = "Hold to listen";

  const draft = liveDraft;
  const blob = await finishRecorder();
  if (!blob) {
    hideLiveTranscript();
    addMessage("system", "No audio captured.");
    return;
  }

  await sendAudioBlob(blob, "ptt", draft);
}

async function maybePlayTts(text) {
  // Spoken replies are controlled only by "Speak replies" (#35), not by routing.
  if (speakRepliesToggle && !speakRepliesToggle.checked) return;
  if (!String(text || "").trim()) return;
  stopCurrentTts();
  try {
    const audioRes = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, route: false }),
      signal: activeRequestController?.signal,
    });
    if (audioRes.ok) {
      const blob = await audioRes.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      currentTtsAudio = audio;
      try {
        await audio.play();
        await new Promise((resolve) => {
          audio.onended = resolve;
          audio.onerror = resolve;
          audio.onpause = resolve; // stopCurrentTts() pauses; don't wait forever
        });
      } finally {
        // Free the WAV blob once playback ends, fails or is stopped (#47).
        audio.onended = audio.onerror = audio.onpause = null;
        URL.revokeObjectURL(url);
        if (currentTtsAudio === audio) currentTtsAudio = null;
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      /* optional */
    }
  }
}

function reportCursorCompletion(data, startedAt, { always = false } = {}) {
  if (!always && data?.action !== "run_cursor_agent") return;
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  const details = data?.action_details || {};
  const failed = details.success === false || (details.exit_code != null && details.exit_code !== 0);
  addMessage(
    "system",
    failed
      ? `Cursor task finished with an error after ${seconds}s (exit ${details.exit_code ?? "?"}).`
      : `Cursor task completed in ${seconds}s.`
  );
}

async function sendChat(message, { forceCursor = false } = {}) {
  const useCursor = forceCursor || currentChatMode() === "cursor";
  const mode = homeChatMode({ forceCursor: useCursor });
  const startedAt = Date.now();
  setBusy(true);
  addMessage("user", message);
  if (useCursor) addMessage("system", "Sending task to Cursor CLI…");
  activeRequestController = new AbortController();

  const pending = document.createElement("div");
  pending.className = "msg assistant";
  pending.setAttribute("aria-busy", "true");
  const pendingBody = document.createElement("div");
  pendingBody.className = "msg-content markdown-body";
  pendingBody.textContent = "…";
  pending.appendChild(pendingBody);
  chatLog.appendChild(pending);
  chatLog.scrollTop = chatLog.scrollHeight;

  try {
    const res = await fetch("/api/agent/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        conversation_id: homeConversationId,
        mode,
      }),
      signal: activeRequestController.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }

    const data = await consumeAgentSse(res, {
      userMessage: message,
      onToken: (partial) => {
        if (typeof renderMarkdown === "function") {
          pendingBody.innerHTML = renderMarkdown(partial);
        } else {
          pendingBody.textContent = partial;
        }
        chatLog.scrollTop = chatLog.scrollHeight;
      },
    });
    const reply = data.reply || "(no reply text)";
    if (typeof renderMarkdown === "function") {
      pendingBody.innerHTML = renderMarkdown(reply);
    } else {
      pendingBody.textContent = reply;
    }
    pending.removeAttribute("aria-busy");
    rememberChatTurn("assistant", reply);
    addModelTag({ action_details: data.action_details || {} });
    lastAssistantReply = reply;
    reportCursorCompletion(
      { action: data.action, action_details: data.action_details },
      startedAt,
      { always: useCursor },
    );

    prependLogEntry({
      id: data.assistant_message_id || 0,
      created_at: new Date().toISOString(),
      client_ip: null,
      input_type: "text",
      user_message: message,
      ai_reply: data.reply,
      routed: data.routed,
      action: data.action,
      action_details: data.action_details || {},
      duration_ms: null,
      status: "success",
      error_message: null,
    });

    await maybePlayTts(reply);
  } catch (err) {
    pending.remove();
    if (err.name === "AbortError") {
      addMessage("system", useCursor ? "Cursor request cancelled." : "Request cancelled.");
    } else {
      addMessage("system", useCursor ? `Cursor error: ${err.message}` : `Error: ${err.message}`);
    }
  } finally {
    activeRequestController = null;
    setBusy(false);
    focusHomeChatInput();
  }
}

// Ctrl+Enter and "Send to Cursor" use the same streaming endpoint as Send
// (mode "cursor"), so Cursor progress shows in the activity feed (#37).
function sendToCursor(message) {
  return sendChat(message, { forceCursor: true });
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;
  messageInput.value = "";
  sendChat(text);
});

messageInput.addEventListener("keydown", (e) => {
  if (isCursorSendChord(e)) {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;
    messageInput.value = "";
    sendToCursor(text).finally(() => focusHomeChatInput());
    return;
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

if (cursorBtn) {
  cursorBtn.addEventListener("click", () => {
    const text = messageInput.value.trim();
    if (!text) {
      addMessage("system", "Type a task first, then click Send to Cursor.");
      focusHomeChatInput();
      return;
    }
    messageInput.value = "";
    sendToCursor(text).finally(() => focusHomeChatInput());
  });
}

handsFreeToggle?.addEventListener("change", async () => {
  persistHandsFree(handsFreeToggle.checked);
  if (handsFreeToggle.checked) {
    try {
      await promptForMicPermission();
      handsFreeWanted = true;
      micBtn.disabled = true;
      await startHandsFree();
      addMessage("system", "Hands-free enabled (streaming via /ws/voice).");
    } catch (err) {
      handsFreeToggle.checked = false;
      persistHandsFree(false);
      addMessage("system", `Could not start Hands-free: ${err.message}`);
    }
  } else {
    stopHandsFree();
    micBtn.disabled = busy || !micReady;
    addMessage("system", "Hands-free disabled.");
  }
  updateMicUi();
});

handsFreeStopBtn?.addEventListener("click", () => {
  handsFreeClient?.interrupt?.();
  stopCurrentTts();
});

if (toggleMicBtn) {
  toggleMicBtn.addEventListener("click", async () => {
    if (micReady) {
      toggleMicBtn.disabled = true;
      handsFreeToggle.checked = false;
      persistHandsFree(false);
      stopHandsFree({ notify: false });
      localStorage.setItem(MIC_ENABLED_KEY, "0");
      await teardownMicGraph();
      micBtn.disabled = true;
      setMicBadge("warn", "Mic: not enabled");
      addMessage("system", "Microphone disabled.");
      updateMicUi();
      toggleMicBtn.disabled = false;
      return;
    }

    toggleMicBtn.disabled = true;
    setMicBadge("warn", "Mic: requesting…");
    try {
      await promptForMicPermission();
      await ensureMicAccess();
      localStorage.setItem(MIC_ENABLED_KEY, "1");
      addMessage("system", "Microphone enabled.");
    } catch (err) {
      micReady = false;
      micBtn.disabled = true;
      handsFreeToggle.checked = false;
      persistHandsFree(false);
      localStorage.setItem(MIC_ENABLED_KEY, "0");
      setMicBadge("bad", "Mic: blocked");
      addMessage("system", `Microphone error: ${err.message}`);
      updateMicHint();
    } finally {
      toggleMicBtn.disabled = false;
    }
  });
}

micBtn.addEventListener("mousedown", (e) => {
  e.preventDefault();
  startPttRecording();
});
micBtn.addEventListener("mouseup", stopPttRecording);
micBtn.addEventListener("mouseleave", () => {
  if (pttActive) stopPttRecording();
});

// Keyboard push-to-talk: hold Space or Enter while the button is focused (#36).
micBtn.addEventListener("keydown", (e) => {
  if (e.key !== " " && e.key !== "Enter") return;
  e.preventDefault();
  if (e.repeat || pttActive) return;
  startPttRecording();
});
micBtn.addEventListener("keyup", (e) => {
  if (e.key !== " " && e.key !== "Enter") return;
  e.preventDefault();
  stopPttRecording();
});
micBtn.addEventListener("blur", () => {
  if (pttActive) stopPttRecording();
});

micBtn.addEventListener("touchstart", (e) => {
  e.preventDefault();
  startPttRecording();
}, { passive: false });
micBtn.addEventListener("touchend", (e) => {
  e.preventDefault();
  stopPttRecording();
}, { passive: false });

window.sahirReleaseHomeMic = function sahirReleaseHomeMic() {
  if (pttActive) stopPttRecording();
};

function enableHandsFreeShortcut() {
  handsFreeToggle.checked = true;
  persistHandsFree(true);
  handsFreeWanted = true;
  window.sahirActivateView?.("chat");
  startHandsFree().catch((err) => addMessage("system", err.message));
}

document.getElementById("voice-alias-btn")?.addEventListener("click", enableHandsFreeShortcut);

refreshLogsBtn.addEventListener("click", refreshLogs);

function parseInitialView() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("view");
  if (fromQuery) return fromQuery;
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash) return hash.split("/")[0];
  return localStorage.getItem(VIEW_KEY) || "chat";
}

function initViewNav() {
  const navButtons = document.querySelectorAll(".rail-footer-btn[data-view]");
  const views = document.querySelectorAll(".main-shell > .view");
  if (!navButtons.length) return;

  function activateView(viewName, { updateUrl = true, replaceUrl = false } = {}) {
    if (viewName === "voice") {
      enableHandsFreeShortcut();
      viewName = "chat";
    }
    navButtons.forEach((btn) => {
      const active = btn.dataset.view === viewName;
      btn.classList.toggle("active", active);
    });
    views.forEach((panel) => {
      const active = panel.id === `view-${viewName}`;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
    localStorage.setItem(VIEW_KEY, viewName);
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("view", viewName);
      url.hash = "";
      const state = { view: viewName };
      if (replaceUrl) {
        window.history.replaceState(state, "", url);
      } else {
        window.history.pushState(state, "", url);
      }
    }
    if (viewName === "logs") {
      refreshLogs();
    }
    if (viewName === "models" && typeof refreshModels === "function") {
      refreshModels();
    }
    if (viewName === "memory") window.sahirOnView?.("memory");
    if (viewName === "docs") window.sahirOnView?.("docs");
    if (viewName === "tasks") window.sahirOnView?.("tasks");
    if (viewName === "settings" && typeof refreshHealth === "function") refreshHealth();
    if (viewName === "chat") focusHomeChatInput();
    if (viewName !== "chat") document.querySelector(".unified-layout")?.classList.remove("rail-open");
  }

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => activateView(btn.dataset.view));
  });

  window.addEventListener("popstate", () => {
    activateView(parseInitialView(), { updateUrl: false });
  });

  let pick = parseInitialView();
  if (pick === "home" || pick === "agent") pick = "chat";
  if (pick === "config") pick = "settings";
  activateView(pick, { replaceUrl: true });
  window.sahirActivateView = activateView;
}

// Roving tabindex + arrow/Home/End keys for role="tablist" groups (#36).
function initTablistKeys(tablist) {
  if (!tablist) return;
  const tabs = () => Array.from(tablist.querySelectorAll('[role="tab"]'));
  const sync = () => {
    tabs().forEach((t) => {
      t.tabIndex = t.getAttribute("aria-selected") === "true" ? 0 : -1;
    });
  };
  tablist.addEventListener("keydown", (e) => {
    const list = tabs();
    const i = list.indexOf(document.activeElement);
    if (i < 0) return;
    const vertical = tablist.getAttribute("aria-orientation") === "vertical";
    const next = vertical ? "ArrowDown" : "ArrowRight";
    const prev = vertical ? "ArrowUp" : "ArrowLeft";
    let j = null;
    if (e.key === next || e.key === "ArrowDown" || e.key === "ArrowRight") j = (i + 1) % list.length;
    else if (e.key === prev || e.key === "ArrowUp" || e.key === "ArrowLeft") j = (i - 1 + list.length) % list.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = list.length - 1;
    if (j === null) return;
    e.preventDefault();
    list[j].focus();
    list[j].click();
  });
  tablist.addEventListener("click", () => setTimeout(sync, 0));
  sync();
  return sync;
}
window.initTablistKeys = initTablistKeys;

initViewNav();
window.sahirActiveConversationId = () => homeConversationId;
window.updateChatEmptyState = updateChatEmptyState;

const commandForm = document.getElementById("command-form");
const commandKeywordInput = document.getElementById("command-keyword");
const commandActionSelect = document.getElementById("command-action");
const commandListEl = document.getElementById("command-list");

function populateActionSelect(actions) {
  if (!commandActionSelect) return;
  commandActionSelect.innerHTML = "";
  for (const action of actions) {
    const opt = document.createElement("option");
    opt.value = action;
    opt.textContent = ACTION_LABELS[action] || action;
    commandActionSelect.appendChild(opt);
  }
}

function renderCommandList(commands) {
  if (!commandListEl) return;
  voiceCommandsCache = commands;
  commandListEl.innerHTML = "";
  if (!commands.length) {
    commandListEl.innerHTML = '<p class="empty">No commands defined.</p>';
    return;
  }

  for (const cmd of commands) {
    const item = document.createElement("article");
    item.className = `command-item${cmd.enabled ? "" : " disabled"}`;
    item.innerHTML = `
      <div class="command-item-header">
        <span class="command-keyword">"${cmd.keyword}"</span>
        <label class="checkbox"><input type="checkbox" data-enable-id="${cmd.id}" ${cmd.enabled ? "checked" : ""}> On</label>
      </div>
      <div class="command-action-label">${ACTION_LABELS[cmd.action] || cmd.action}</div>
      <div class="command-item-actions">
        <button type="button" class="secondary small" data-delete-id="${cmd.id}">Delete</button>
      </div>
    `;
    commandListEl.appendChild(item);
  }

  commandListEl.querySelectorAll("[data-delete-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-delete-id");
      await fetch(`/api/commands/${id}`, { method: "DELETE" });
      await refreshCommands();
    });
  });

  commandListEl.querySelectorAll("[data-enable-id]").forEach((box) => {
    box.addEventListener("change", async () => {
      const id = box.getAttribute("data-enable-id");
      await fetch(`/api/commands/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: box.checked }),
      });
      await refreshCommands();
    });
  });
}

async function refreshCommands() {
  try {
    const res = await fetch("/api/commands");
    const data = await res.json();
    populateActionSelect(data.actions || []);
    renderCommandList(data.commands || []);
  } catch (err) {
    if (commandListEl) {
      commandListEl.innerHTML = `<p class="empty">Could not load commands: ${err.message}</p>`;
    }
  }
}

if (commandForm) {
  commandForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const keyword = commandKeywordInput.value.trim();
    const action = commandActionSelect.value;
    if (!keyword || !action) return;

    const res = await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword, action }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addMessage("system", `Could not add command: ${err.detail || res.statusText}`);
      return;
    }
    commandKeywordInput.value = "";
    await refreshCommands();
  });
}

handsFreeToggle.checked = handsFreePrefEnabled();

window.sahirSetChatMode(initialChatMode());
homeModeSelect?.addEventListener("change", () => window.sahirSetChatMode(homeModeSelect.value));

if (speakRepliesToggle) {
  speakRepliesToggle.checked = localStorage.getItem(SPEAK_REPLIES_KEY) !== "0";
  speakRepliesToggle.addEventListener("change", () => {
    localStorage.setItem(SPEAK_REPLIES_KEY, speakRepliesToggle.checked ? "1" : "0");
    if (!speakRepliesToggle.checked) stopCurrentTts();
    if (handsFreeStreaming()) handsFreeClient.reconnect().catch(() => {});
  });
}


async function restoreMicPreference() {
  if (localStorage.getItem(MIC_ENABLED_KEY) !== "1") return;
  const state = await getMicPermissionState();
  if (state !== "granted") {
    if (state === "denied") localStorage.setItem(MIC_ENABLED_KEY, "0");
    addMessage("system", "Microphone was on last time — click Enable microphone to turn it back on.");
    return;
  }
  try {
    await ensureMicAccess();
    addMessage("system", "Microphone restored from your last session.");
  } catch (err) {
    setMicBadge("warn", "Mic: not enabled");
    addMessage("system", `Could not restore microphone: ${err.message}`);
  }
}

loadWebConfig().then(() => {
  refreshHealth();
  refreshLogs();
  refreshCommands();
});
window.addEventListener("online", () => refreshHealth());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshHealth();
});
watchMicPermission();
updateChatEmptyState();
setMicBadge("warn", "Mic: not enabled");
updateToggleMicButton();
updateMicHint();
refreshLiveTranscriptIdle();
restoreMicPreference();
if (handsFreeToggle?.checked) {
  handsFreeWanted = true;
  promptForMicPermission()
    .then(() => startHandsFree())
  .catch(() => {
      handsFreeToggle.checked = false;
      persistHandsFree(false);
    });
}
focusHomeChatInput();
