const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
// One mode control shared by Home and Agent (#38): agent | route | stream | cursor.
const homeModeSelect = document.getElementById("home-mode");
const alwaysListenToggle = document.getElementById("always-listen-toggle");
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
  minSpeechMsAlways: 750,
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

const ALWAYS_LISTEN_KEY = "voice-assistant-always-listen";
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

portLabel.textContent = window.location.port || "8765";

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
let vadTimer = null;
let listenState = "off"; // off | waiting | in_conversation | paused
let lastSpeechAt = null;
let conversationStartedAt = null;
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
let noiseFloor = 0.008;
let speechCandidateStartedAt = null;
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
  pause_listening: "Pause always listening",
  resume_listening: "Resume always listening",
};

function rememberChatTurn(role, text) {
  const content = String(text || "").trim();
  if ((role !== "user" && role !== "assistant") || !content) return;
  chatHistory.push({ role, content });
  if (chatHistory.length > CHAT_HISTORY_LIMIT) {
    chatHistory = chatHistory.slice(-CHAT_HISTORY_LIMIT);
  }
}

function addMessage(role, text, { skipRemember = false } = {}) {
  const el = document.createElement("div");
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
}

function getChatHistoryForApi() {
  return chatHistory.map(({ role, content }) => ({ role, content }));
}

const activityFeed = document.getElementById("activity-feed");
const activityList = document.getElementById("activity-list");
const activityClearBtn = document.getElementById("activity-clear-btn");

function clearActivityFeed() {
  if (!activityList) return;
  activityList.innerHTML = "";
  activityFeed?.classList.add("hidden");
}

function pushActivityFeed(text, kind = "") {
  if (!activityList || !text) return;
  activityFeed?.classList.remove("hidden");
  const li = document.createElement("li");
  if (kind) li.className = `ev-${kind}`;
  li.textContent = text;
  activityList.appendChild(li);
  activityList.scrollTop = activityList.scrollHeight;
}

if (activityClearBtn) {
  activityClearBtn.addEventListener("click", clearActivityFeed);
}

function currentChatMode() {
  const v = homeModeSelect?.value;
  return CHAT_MODES.includes(v) ? v : "agent";
}

function homeChatMode({ forceCursor = false } = {}) {
  if (forceCursor) return "cursor";
  return currentChatMode();
}

function initialChatMode() {
  const saved = localStorage.getItem(CHAT_MODE_KEY);
  if (CHAT_MODES.includes(saved)) return saved;
  if (localStorage.getItem(ROUTE_CURSOR_KEY) === "1") return "cursor";
  if (localStorage.getItem(ROUTE_TOOLS_KEY) === "1") return "route";
  return "agent";
}

window.sahirSetChatMode = function sahirSetChatMode(mode) {
  if (!CHAT_MODES.includes(mode)) return;
  localStorage.setItem(CHAT_MODE_KEY, mode);
  if (homeModeSelect && homeModeSelect.value !== mode) homeModeSelect.value = mode;
  const agentSelect = document.getElementById("chat-mode");
  if (agentSelect && agentSelect.value !== mode) agentSelect.value = mode;
};

function focusHomeChatInput() {
  if (messageInput && !messageInput.disabled && !busy) {
    messageInput.focus();
  }
}

function isCursorSendChord(e) {
  return e.key === "Enter" && (e.ctrlKey || e.metaKey);
}

async function consumeAgentSse(res, { onToken, onMeta } = {}) {
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
      if (ev.type === "meta" && ev.conversation_id) {
        homeConversationId = ev.conversation_id;
        localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
        onMeta?.(ev);
      }
      if (ev.type === "progress") pushActivityFeed(ev.message, ev.stage === "cursor" ? "cursor" : "progress");
      if (ev.type === "model") pushActivityFeed(ev.message || `Model: ${ev.model}`, "model");
      if (ev.type === "tool_calls") {
        (ev.calls || []).forEach((c) => pushActivityFeed(`Tool call: ${c.name || "tool"}`, "tool"));
      }
      if (ev.type === "delegate") {
        pushActivityFeed(ev.message || `Workers: ${(ev.workers || []).join(", ")}`, "tool");
      }
      if (ev.type === "worker_result") {
        pushActivityFeed(`Worker ${ev.worker}: ${ev.status}`, "tool");
      }
      if (ev.type === "token") {
        reply += ev.text;
        onToken?.(reply);
      }
      if (ev.type === "done") {
        reply = ev.reply || reply;
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

function renderHomeMessagesFromHistory(messages) {
  if (!chatLog) return;
  // aria-busy stops screen readers announcing the whole restored history (#36).
  chatLog.setAttribute("aria-busy", "true");
  chatLog.innerHTML = "";
  chatHistory = [];
  for (const m of messages) {
    if (m.role === "user" || m.role === "assistant") {
      addMessage(m.role, m.content, { skipRemember: false });
    }
  }
  chatLog.removeAttribute("aria-busy");
}

async function loadHomeConversation() {
  const saved = Number(localStorage.getItem(CONV_ID_KEY));
  try {
    const listRes = await fetch("/api/conversations?limit=50");
    const listData = await listRes.json();
    const convs = listData.conversations || [];
    let id = saved || Number(localStorage.getItem("agentConv"));
    if (!id || !convs.some((c) => c.id === id)) {
      id = convs.length ? convs[0].id : null;
    }
    if (!id) return;
    const convRes = await fetch(`/api/conversations/${id}`);
    if (!convRes.ok) return;
    const conv = await convRes.json();
    homeConversationId = conv.id;
    localStorage.setItem(CONV_ID_KEY, String(homeConversationId));
    localStorage.setItem("agentConv", String(homeConversationId));
    const msgs = (conv.messages || []).filter((m) => m.role === "user" || m.role === "assistant");
    renderHomeMessagesFromHistory(msgs.slice(-CHAT_HISTORY_LIMIT));
  } catch (err) {
    console.warn("Could not restore home chat", err);
  }
}

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
  micBtn.disabled = busy || !micReady || alwaysListenToggle.checked;
  messageInput.disabled = busy;

  if (busy) {
    pauseMainListening();
  } else {
    stopCommandCapture();
    if (pendingReload) {
      reloadForNewBuild();
    }
    if (alwaysListenToggle.checked && micReady) {
      resumeAlwaysListening();
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
    note.append(strong, ". Always listening waits for it before recording.");
    return;
  }
  note.innerHTML = "";
  note.append("Wake word: ");
  const strong = document.createElement("strong");
  strong.textContent = "off in the browser";
  note.append(strong, `. With Always listening on, any speech starts a request.${desktop}`);
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
  } catch (_) {
    /* defaults */
  }
}

function scheduleHealthCheck(delayMs) {
  if (healthTimer) clearTimeout(healthTimer);
  healthTimer = setTimeout(refreshHealth, delayMs);
}

function canReloadNow() {
  return !busy && !pttActive && listenState !== "in_conversation" && !currentTtsAudio;
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
    const items = [
      ["Ollama", healthData.ollama?.ok, healthData.ollama?.target_loaded ? "model ready" : "check model"],
      ["Cursor CLI", healthData.cursor_cli?.ok],
      ["Piper TTS", healthData.piper?.ok],
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
  if (alwaysListenToggle.checked && listenState === "waiting") {
    return {
      status: "Waiting for speech",
      text: "Speak clearly to start a conversation…",
    };
  }
  return {
    status: "Ready",
    text: "Hold to listen or enable always listening.",
  };
}

function refreshLiveTranscriptIdle() {
  if (pttActive || listenState === "in_conversation") return;
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
    alwaysListenToggle.checked = false;
    localStorage.setItem(ALWAYS_LISTEN_KEY, "0");
    stopAlwaysListening();
    micBtn.disabled = busy || !micReady;
    addMessage("system", "Always listening paused.");
    updateMicUi();
    return;
  }
  if (action === "resume_listening") {
    alwaysListenToggle.checked = true;
    localStorage.setItem(ALWAYS_LISTEN_KEY, "1");
    if (micReady && !busy) {
      startAlwaysListening();
    }
    micBtn.disabled = true;
    addMessage("system", "Always listening resumed.");
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
  if (listenState === "in_conversation" || pttActive) return;

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
    micHint.textContent = "Click Enable microphone, then use hold-to-listen or turn on Always listening.";
    return;
  }

  if (alwaysListenToggle.checked) {
    micHint.classList.remove("hidden");
    micHint.textContent =
      `Always listening is on. Background noise is filtered — speak clearly to start. Say command keywords (e.g. "stop", "cancel") while the assistant is speaking. ${silenceSec}s silence ends a conversation.`;
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
  if (alwaysListenToggle.checked && listenState === "waiting") {
    micBtn.textContent = "Listening…";
    micBtn.classList.add("listening");
    micBtn.classList.remove("recording");
    setMicBadge("ok", "Mic: always listening");
  } else if (alwaysListenToggle.checked && listenState === "in_conversation") {
    micBtn.textContent = "In conversation…";
    micBtn.classList.add("recording");
    micBtn.classList.remove("listening");
    setMicBadge("ok", "Mic: conversation");
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
  if (!pttActive && listenState !== "in_conversation" && !liveTranscriptStatus?.classList.contains("thinking")) {
    refreshLiveTranscriptIdle();
  }
}

async function ensureMicAccess() {
  const onLocalhost = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  if (!window.isSecureContext && !onLocalhost) {
    throw new Error(
      isMobileBrowser()
        ? "Mobile requires HTTPS for the microphone. On your PC: bash scripts/generate_web_cert.sh, set WEB_SSL_CERT and WEB_SSL_KEY in .env, restart, then open https://<pc-ip>:8765 on this phone."
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
  micBtn.disabled = alwaysListenToggle.checked;
  setMicBadge("ok", "Mic: ready");
  await setupMicGraph();
  startCommandWatcher();
  updateMicUi();

  if (alwaysListenToggle.checked) {
    startAlwaysListening();
  }
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
  stopAlwaysListening();
  stopCommandWatcher();
  stopAlwaysRecorder();
  clearLiveTimers();
  hideLiveTranscript();

  if (vadTimer) {
    clearInterval(vadTimer);
    vadTimer = null;
  }

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
  listenState = "off";
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

function getAdaptiveStartThreshold() {
  return Math.max(
    VAD.speechStartThreshold,
    noiseFloor * VAD.noiseMarginStart + 0.006,
  );
}

function getAdaptiveSpeechThreshold() {
  return Math.max(
    VAD.speechThreshold,
    noiseFloor * VAD.noiseMarginSpeech + 0.008,
  );
}

function updateNoiseFloor(level) {
  if (listenState !== "waiting") return;
  const startThreshold = getAdaptiveStartThreshold();
  if (level > startThreshold * 0.8) return;

  const alpha = 0.1;
  noiseFloor = noiseFloor * (1 - alpha) + level * alpha;
  noiseFloor = Math.min(Math.max(noiseFloor, 0.003), 0.05);
}

function looksLikeSpeech({ rms, zcr }) {
  if (rms < getAdaptiveStartThreshold()) return false;
  return zcr >= VAD.minZcr && zcr <= VAD.maxZcr;
}

function isActiveSpeech({ rms, zcr }) {
  const threshold = getAdaptiveSpeechThreshold();
  if (listenState === "in_conversation") {
    if (rms < threshold * 0.78) return false;
    return zcr >= VAD.minZcr * 0.55;
  }
  if (rms < threshold) return false;
  return zcr >= VAD.minZcr * 0.75;
}

function resetNoiseGate() {
  noiseFloor = 0.008;
  speechCandidateStartedAt = null;
}

function shouldDiscardAlwaysCapture({ speechMs, draft }) {
  const draftText = String(draft || "").trim();
  if (draftText.length >= VAD.minLiveDraftChars) return false;
  if (speechMs < VAD.minSpeechMsAlways) return true;
  // No live caption and relatively short capture — likely ambient noise.
  return speechMs < 2200;
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

function beginAlwaysRecorder() {
  if (!micStream || mediaRecorder || pttActive || busy) return false;

  recordedChunks = [];
  conversationChunkStart = -1;
  recorderMimeType = pickRecorderMimeType() || "audio/webm";
  mediaRecorder = recorderMimeType
    ? new MediaRecorder(micStream, { mimeType: recorderMimeType })
    : new MediaRecorder(micStream);
  attachRecorderHandlers(mediaRecorder, "always-listening");

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) recordedChunks.push(event.data);
  };

  mediaRecorder.start(100);
  return true;
}

function stopAlwaysRecorder() {
  conversationChunkStart = -1;
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  mediaRecorder = null;
  recordedChunks = [];
}

async function finishAlwaysConversationRecording() {
  const startIndex = conversationChunkStart;
  let blob = null;

  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    const recorder = mediaRecorder;
    await new Promise((resolve) => {
      recorder.onstop = resolve;
      recorder.stop();
    });
    const chunks = [...recordedChunks];
    blob = chunks.length ? new Blob(chunks, { type: recorderMimeType }) : null;
    mediaRecorder = null;
    recordedChunks = [];
  } else {
    blob = getRecordedBlob();
  }

  conversationChunkStart = -1;

  if (alwaysListenToggle.checked && micReady && !busy && !pttActive) {
    beginAlwaysRecorder();
  }

  if (!blob || blob.size < MIN_UPLOAD_BYTES) {
    logClientError("always-listening capture too small", { size: blob?.size || 0 });
    return null;
  }
  return blob;
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

function abortConversationRecording() {
  clearLiveTimers();
  conversationChunkStart = -1;
  lastSpeechAt = null;
  conversationStartedAt = null;
  hideLiveTranscript();
}

function startAlwaysListening() {
  if (!micReady || !alwaysListenToggle.checked || busy || pttActive) return;
  window.sahirStopLiveVoice?.("Home always listening");

  listenState = "waiting";
  lastSpeechAt = null;
  conversationStartedAt = null;
  lastLiveRequestAt = 0;
  resetNoiseGate();

  if (vadTimer) clearInterval(vadTimer);
  vadTimer = setInterval(runVadLoop, VAD.pollMs);
  beginAlwaysRecorder();
  updateMicUi();
}

function pauseMainListening() {
  if (vadTimer) {
    clearInterval(vadTimer);
    vadTimer = null;
  }

  stopAlwaysRecorder();

  if (listenState === "in_conversation") {
    abortConversationRecording();
  }

  if (listenState !== "off") {
    listenState = "paused";
    updateMicUi();
  }
}

function pauseAlwaysListening() {
  pauseMainListening();
}

function stopAlwaysListening() {
  if (vadTimer) {
    clearInterval(vadTimer);
    vadTimer = null;
  }

  stopAlwaysRecorder();

  if (listenState === "in_conversation") {
    abortConversationRecording();
  }

  listenState = "off";
  updateMicUi();
}

function resumeAlwaysListening() {
  if (!alwaysListenToggle.checked || !micReady || busy || pttActive) return;
  startAlwaysListening();
}

function startConversation(now) {
  listenState = "in_conversation";
  conversationStartedAt = now;
  lastSpeechAt = now;
  lastLiveRequestAt = 0;
  liveDraft = "";
  conversationChunkStart = Math.max(0, recordedChunks.length - PREROLL_MAX_CHUNKS);
  showLiveDraft("");
  scheduleLiveUpdates();
  updateMicUi();
}

async function requestLiveTranscript() {
  const canRun =
    listenState === "in_conversation" || (pttActive && mediaRecorder);
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

async function endConversation() {
  if (listenState !== "in_conversation") return;

  listenState = "waiting";
  clearLiveTimers();
  updateMicUi();

  const speechMs = conversationStartedAt ? Date.now() - conversationStartedAt : 0;
  const draft = liveDraft;
  showRefiningDraft(draft);

  const blob = await finishAlwaysConversationRecording();
  lastSpeechAt = null;
  conversationStartedAt = null;

  if (!blob || speechMs < VAD.minSpeechMsAlways) {
    hideLiveTranscript();
    resumeAlwaysListening();
    return;
  }

  if (shouldDiscardAlwaysCapture({ speechMs, draft })) {
    hideLiveTranscript();
    resumeAlwaysListening();
    return;
  }

  await sendAudioBlob(blob, "always", draft);
}

async function runVadLoop() {
  if (listenState !== "waiting" && listenState !== "in_conversation") return;
  if (busy || pttActive) return;

  const metrics = getAudioMetrics();
  const now = Date.now();
  updateNoiseFloor(metrics.rms);

  if (listenState === "waiting") {
    if (!looksLikeSpeech(metrics)) {
      speechCandidateStartedAt = null;
      return;
    }
    if (!speechCandidateStartedAt) {
      speechCandidateStartedAt = now;
      return;
    }
    if (now - speechCandidateStartedAt < VAD.speechConfirmMs) {
      return;
    }
    speechCandidateStartedAt = null;
    startConversation(now);
    return;
  }

  if (listenState === "in_conversation") {
    if (isActiveSpeech(metrics)) {
      lastSpeechAt = now;
    }

    const conversationMs = conversationStartedAt ? now - conversationStartedAt : 0;
    const silentMs = lastSpeechAt ? now - lastSpeechAt : 0;

    if (
      lastSpeechAt &&
      !isActiveSpeech(metrics) &&
      silentMs >= VAD.conversationSilenceMs
    ) {
      await endConversation();
      return;
    }

    if (conversationMs >= VAD.maxConversationMs) {
      await endConversation();
      return;
    }
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

    if (mode === "always" && (data.discarded || !finalText.trim())) {
      hideLiveTranscript();
      return;
    }

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
  if (!micReady || !micStream || alwaysListenToggle.checked) {
    addMessage("system", "Enable the microphone first.");
    return;
  }

  window.sahirStopLiveVoice?.("Home push-to-talk");
  pauseAlwaysListening();
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
    resumeAlwaysListening();
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
      const audio = new Audio(URL.createObjectURL(blob));
      currentTtsAudio = audio;
      await audio.play();
      await new Promise((resolve) => {
        audio.onended = resolve;
        audio.onerror = resolve;
      });
      if (currentTtsAudio === audio) currentTtsAudio = null;
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
  clearActivityFeed();
  pushActivityFeed(`Mode: ${mode}`, "progress");
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

alwaysListenToggle.addEventListener("change", async () => {
  localStorage.setItem(ALWAYS_LISTEN_KEY, alwaysListenToggle.checked ? "1" : "0");

  if (alwaysListenToggle.checked) {
    if (!micReady) {
      try {
        await promptForMicPermission();
        await ensureMicAccess();
        localStorage.setItem(MIC_ENABLED_KEY, "1");
      } catch (err) {
        alwaysListenToggle.checked = false;
        localStorage.setItem(ALWAYS_LISTEN_KEY, "0");
        addMessage("system", `Could not start always listening: ${err.message}`);
        return;
      }
    }
    micBtn.disabled = true;
    startAlwaysListening();
    addMessage("system", "Always listening enabled.");
  } else {
    stopAlwaysListening();
    micBtn.disabled = busy || !micReady;
    addMessage("system", "Always listening disabled.");
  }

  updateMicUi();
});

if (toggleMicBtn) {
  toggleMicBtn.addEventListener("click", async () => {
    if (micReady) {
      toggleMicBtn.disabled = true;
      alwaysListenToggle.checked = false;
      localStorage.setItem(ALWAYS_LISTEN_KEY, "0");
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
      if (alwaysListenToggle.checked) {
        startAlwaysListening();
      }
    } catch (err) {
      micReady = false;
      micBtn.disabled = true;
      alwaysListenToggle.checked = false;
      localStorage.setItem(ALWAYS_LISTEN_KEY, "0");
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

// Shared microphone state between Home quick voice and Agent live voice (#33).
// Agent live voice calls this before it opens the mic.
window.sahirReleaseHomeMic = function sahirReleaseHomeMic() {
  if (pttActive) stopPttRecording();
  if (alwaysListenToggle.checked) {
    alwaysListenToggle.checked = false;
    localStorage.setItem(ALWAYS_LISTEN_KEY, "0");
    stopAlwaysListening();
    micBtn.disabled = busy || !micReady;
    addMessage("system", "Always listening turned off: Agent live voice is using the microphone.");
    updateMicUi();
  }
};

document.getElementById("open-live-voice-btn")?.addEventListener("click", () => {
  document.getElementById("nav-btn-agent")?.click();
  document.querySelector('.ag-tab[data-tab="voice"]')?.click();
  document.getElementById("voice-toggle")?.focus();
});

refreshLogsBtn.addEventListener("click", refreshLogs);

function parseInitialView() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("view");
  if (fromQuery) return fromQuery;
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash) return hash.split("/")[0];
  return localStorage.getItem(VIEW_KEY) || "home";
}

function initViewNav() {
  const navButtons = document.querySelectorAll(".nav-item[data-view]");
  const views = document.querySelectorAll(".view");
  if (!navButtons.length) return;

  function activateView(viewName, { updateUrl = true, replaceUrl = false } = {}) {
    navButtons.forEach((btn) => {
      const active = btn.dataset.view === viewName;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
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
    if (viewName === "agent" && typeof window.refreshAgentView === "function") {
      window.refreshAgentView();
    }
    if (viewName === "home") {
      focusHomeChatInput();
    }
    if (viewName === "agent" && typeof window.focusAgentChatInput === "function") {
      window.focusAgentChatInput();
    }
  }

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => activateView(btn.dataset.view));
  });

  window.addEventListener("popstate", () => {
    activateView(parseInitialView(), { updateUrl: false });
  });

  const initial = parseInitialView();
  activateView(initial, { replaceUrl: true });
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
const syncNavTabs = initTablistKeys(document.querySelector(".nav-rail"));
window.addEventListener("popstate", () => syncNavTabs?.());
loadHomeConversation();

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

const savedAlwaysListen = localStorage.getItem(ALWAYS_LISTEN_KEY) === "1";
alwaysListenToggle.checked = savedAlwaysListen;

window.sahirSetChatMode(initialChatMode());
homeModeSelect?.addEventListener("change", () => window.sahirSetChatMode(homeModeSelect.value));

if (speakRepliesToggle) {
  speakRepliesToggle.checked = localStorage.getItem(SPEAK_REPLIES_KEY) !== "0";
  speakRepliesToggle.addEventListener("change", () => {
    localStorage.setItem(SPEAK_REPLIES_KEY, speakRepliesToggle.checked ? "1" : "0");
    if (!speakRepliesToggle.checked) stopCurrentTts();
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
addMessage("system", "Ready. Enable the microphone or type a message.");
setMicBadge("warn", "Mic: not enabled");
updateToggleMicButton();
updateMicHint();
refreshLiveTranscriptIdle();
restoreMicPreference();
focusHomeChatInput();
