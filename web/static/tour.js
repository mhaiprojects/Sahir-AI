const TOUR_STORAGE_KEY = "voice-assistant-tour-done";

const TOUR_STEPS = [
  {
    title: "Welcome to Sahir",
    body: "One screen for chat, voice, projects, and operator tools. The right column streams live activity; the left rail holds projects and secondary pages.",
    target: null,
  },
  {
    title: "Projects rail",
    body: "Browse projects (General first), open the supervisor ★ or any chat, start + New chat or + New project. Footer links open Memory, Documents, Tasks, a Hands-free shortcut, Models, Settings, and Logs. On a phone, use ☰ to open the rail.",
    target: "#project-rail",
  },
  {
    title: "Chat bar",
    body: "Pick project, quick-switch chat, mode (saved on this chat), model, and see mic state. Use the rail for the full chat tree.",
    target: "#chat-bar",
  },
  {
    title: "Live captions",
    body: "While you use Hold to listen or Hands-free, live and corrected captions appear here.",
    target: "#live-transcript",
  },
  {
    title: "Composer",
    body: "Send obeys the mode picker. Ctrl+Enter always sends to Cursor. Hold to listen (HTTP PTT), Hands-free (/ws/voice), Stop (barge-in), and Speak replies are here once. Wake word and Right Ctrl push-to-talk are desktop app only (bot.py), not in the browser.",
    target: "#chat-form",
  },
  {
    title: "Desktop-only voice",
    body: "Wake word (e.g. hey_jarvis) and Right Ctrl push-to-talk run only in the desktop app (python bot.py). The browser offers Hold to listen and Hands-free instead.",
    target: "#desktop-only-voice-note",
  },
  {
    title: "Activity panel",
    body: "Routine / WIP streams commands, tools, and router context. The header shows project, active chat, router, and supervisor state.",
    target: "#wip-panel",
  },
  {
    title: "Settings",
    body: "Voice command keywords and combined service + mic status live under Settings in the rail footer.",
    target: "#nav-btn-settings",
  },
  {
    title: "You're all set",
    body: "Enable the microphone or type a message. No authentication on the LAN — use only on trusted networks.",
    target: null,
  },
];

let tourIndex = 0;
let tourActive = false;
let tourElements = null;
let tourReturnFocus = null;

function createTourElements() {
  const overlay = document.createElement("div");
  overlay.id = "tour-overlay";
  overlay.className = "tour-overlay hidden";
  overlay.setAttribute("aria-hidden", "true");

  const spotlight = document.createElement("div");
  spotlight.className = "tour-spotlight hidden";
  spotlight.setAttribute("aria-hidden", "true");

  const card = document.createElement("div");
  card.className = "tour-card";
  card.tabIndex = -1;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "tour-title");
  card.setAttribute("aria-describedby", "tour-body");

  card.innerHTML = `
    <div class="tour-card-header">
      <span id="tour-step-label" class="tour-step-label"></span>
      <button type="button" id="tour-close-btn" class="tour-close-btn" aria-label="Close tour">&times;</button>
    </div>
    <h3 id="tour-title" class="tour-title"></h3>
    <p id="tour-body" class="tour-body"></p>
    <div class="tour-actions">
      <button type="button" id="tour-skip-btn" class="secondary small">Skip tour</button>
      <div class="tour-nav">
        <button type="button" id="tour-back-btn" class="secondary small">Back</button>
        <button type="button" id="tour-next-btn" class="small">Next</button>
      </div>
    </div>
  `;

  document.body.append(overlay, spotlight, card);
  return { overlay, spotlight, card };
}

function positionSpotlight(target, spotlight) {
  if (!target) {
    spotlight.classList.add("hidden");
    return;
  }
  const r = target.getBoundingClientRect();
  const pad = 6;
  spotlight.classList.remove("hidden");
  spotlight.style.top = `${Math.max(0, r.top - pad)}px`;
  spotlight.style.left = `${Math.max(0, r.left - pad)}px`;
  spotlight.style.width = `${r.width + pad * 2}px`;
  spotlight.style.height = `${r.height + pad * 2}px`;
}

function renderTourStep() {
  if (!tourElements) return;
  const step = TOUR_STEPS[tourIndex];
  const { card, overlay, spotlight } = tourElements;
  document.getElementById("tour-title").textContent = step.title;
  document.getElementById("tour-body").textContent = step.body;
  document.getElementById("tour-step-label").textContent = `Step ${tourIndex + 1} of ${TOUR_STEPS.length}`;
  document.getElementById("tour-back-btn").disabled = tourIndex === 0;
  document.getElementById("tour-next-btn").textContent = tourIndex === TOUR_STEPS.length - 1 ? "Done" : "Next";

  let target = step.target ? document.querySelector(step.target) : null;
  if (step.target === "#project-rail" && window.innerWidth <= 980) {
    document.querySelector(".unified-layout")?.classList.add("rail-open");
    target = document.querySelector(step.target);
  }
  if (step.target === "#nav-btn-settings") window.sahirActivateView?.("settings", { replaceUrl: false });
  if (step.target === "#wip-panel" || step.target === "#chat-form" || step.target === "#chat-bar") {
    window.sahirActivateView?.("chat", { replaceUrl: false });
  }

  positionSpotlight(target, spotlight);
  if (target) {
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const style = document.createElement("style");
    style.id = "tour-highlight-style";
    document.getElementById("tour-highlight-style")?.remove();
    style.textContent = `${step.target}{position:relative;z-index:10001!important;}`;
    document.head.appendChild(style);
  } else {
    document.getElementById("tour-highlight-style")?.remove();
  }

  overlay.classList.remove("hidden");
  card.classList.remove("hidden");
  card.focus();
}

function endTour(markDone = false) {
  if (!tourElements) return;
  tourActive = false;
  tourElements.overlay.classList.add("hidden");
  tourElements.card.classList.add("hidden");
  tourElements.spotlight.classList.add("hidden");
  document.getElementById("tour-highlight-style")?.remove();
  document.querySelector(".unified-layout")?.classList.remove("rail-open");
  if (markDone) localStorage.setItem(TOUR_STORAGE_KEY, "1");
  tourReturnFocus?.focus?.();
}

function startTour() {
  if (tourActive) return;
  tourActive = true;
  tourReturnFocus = document.activeElement;
  if (!tourElements) tourElements = createTourElements();
  tourIndex = 0;

  const { card } = tourElements;
  document.getElementById("tour-close-btn").onclick = () => endTour(true);
  document.getElementById("tour-skip-btn").onclick = () => endTour(true);
  document.getElementById("tour-back-btn").onclick = () => { tourIndex -= 1; renderTourStep(); };
  document.getElementById("tour-next-btn").onclick = () => {
    if (tourIndex >= TOUR_STEPS.length - 1) endTour(true);
    else { tourIndex += 1; renderTourStep(); }
  };
  tourElements.overlay.onclick = () => endTour(true);
  renderTourStep();
}

document.getElementById("start-tour-btn")?.addEventListener("click", startTour);

function shouldAutoStartTour() {
  try {
    const qs = new URLSearchParams(window.location.search);
    if (qs.has("notour")) return false;
  } catch (_) {
    /* ignore */
  }
  return !localStorage.getItem(TOUR_STORAGE_KEY);
}

if (shouldAutoStartTour()) {
  window.setTimeout(() => startTour(), 800);
}