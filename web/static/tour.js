const TOUR_STORAGE_KEY = "voice-assistant-tour-done";

const TOUR_STEPS = [
  {
    title: "Welcome to Sahir",
    body:
      "This control panel runs on your PC and lets you chat, control the machine, and run coding tasks from any device on your home network.",
    target: null,
  },
  {
    title: "Navigation",
    body:
      "Use the left sidebar to switch between Home (voice and chat), Logs (request history), Config (voice commands), Models (chat model, fallback and benchmark) and Agent (chats, memory, documents, live voice, tasks). Arrow keys move between sections.",
    target: ".nav-sidebar",
  },
  {
    title: "Enable the microphone",
    body:
      "Click Enable microphone on Home before using voice. Browsers require HTTPS when you open this page from another device on your LAN — use the self-signed cert from scripts/generate_web_cert.sh.",
    target: "#toggle-mic-btn",
  },
  {
    title: "Live transcription",
    body:
      "While you speak (always listening or hold-to-listen), live captions appear here at the top so you can see what the assistant hears before the final transcription.",
    target: "#live-transcript",
  },
  {
    title: "Voice controls",
    body:
      "Toggle the mic, hold to listen, and choose routing options below. Always listening keeps the mic open for hands-free conversations. Speak replies turns spoken answers on or off.",
    target: ".voice-buttons-row",
  },
  {
    title: "Mode",
    body:
      "One mode for Home and Agent: Agent (orchestrator with tools and memory), Tools (quick local router), Chat (plain model) or Cursor (coding agent). Speak replies turns spoken answers on or off.",
    target: ".options-row",
  },
  {
    title: "Conversation history",
    body:
      "Your messages and assistant replies appear here. System notices (like mic status) show in the center.",
    target: "#chat-log",
  },
  {
    title: "Type a message",
    body:
      "Ask questions, request PC actions, or send coding tasks. Press Send or Enter to submit; Send to Cursor forces a Cursor CLI task.",
    target: "#message-input",
  },
  {
    title: "Voice commands",
    body:
      "Define keyword shortcuts (e.g. \"stop\") that work while the assistant is speaking or processing. Open Config in the sidebar.",
    target: "#nav-btn-config",
  },
  {
    title: "Models",
    body:
      "Pick the active chat model, load or eject models, set the big fallback model, and run the benchmark.",
    target: "#nav-btn-models",
  },
  {
    title: "Agent",
    body:
      "Browse and export past chats, review memories, index documents for retrieval, use streaming Live voice with barge-in, and see delegated tasks.",
    target: "#nav-btn-agent",
  },
  {
    title: "Request log",
    body:
      "Every text and voice request is logged with timestamps, routing action, and errors. Open Logs in the sidebar and use Refresh to reload.",
    target: "#nav-btn-logs",
  },
  {
    title: "You're all set",
    body:
      "Enable the microphone or type a message to begin. This panel has no authentication — use it only on trusted networks.",
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

  document.body.appendChild(overlay);
  document.body.appendChild(spotlight);
  document.body.appendChild(card);

  return {
    overlay,
    spotlight,
    card,
    stepLabel: card.querySelector("#tour-step-label"),
    title: card.querySelector("#tour-title"),
    body: card.querySelector("#tour-body"),
    backBtn: card.querySelector("#tour-back-btn"),
    nextBtn: card.querySelector("#tour-next-btn"),
    skipBtn: card.querySelector("#tour-skip-btn"),
    closeBtn: card.querySelector("#tour-close-btn"),
  };
}

function getTargetRect(selector) {
  if (!selector) return null;
  const el = document.querySelector(selector);
  if (!el || el.classList.contains("hidden")) return null;
  return el.getBoundingClientRect();
}

function positionSpotlight(rect) {
  const pad = 8;
  tourElements.spotlight.style.top = `${Math.max(0, rect.top - pad)}px`;
  tourElements.spotlight.style.left = `${Math.max(0, rect.left - pad)}px`;
  tourElements.spotlight.style.width = `${rect.width + pad * 2}px`;
  tourElements.spotlight.style.height = `${rect.height + pad * 2}px`;
  tourElements.spotlight.classList.remove("hidden");
}

function positionCard(rect) {
  const card = tourElements.card;
  const margin = 16;
  const cardRect = card.getBoundingClientRect();
  let top;
  let left;

  if (!rect) {
    top = Math.max(margin, (window.innerHeight - cardRect.height) / 2);
    left = Math.max(margin, (window.innerWidth - cardRect.width) / 2);
    card.classList.add("centered");
  } else {
    card.classList.remove("centered");
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;

    if (spaceBelow >= cardRect.height + margin * 2) {
      top = rect.bottom + margin;
    } else if (spaceAbove >= cardRect.height + margin * 2) {
      top = rect.top - cardRect.height - margin;
    } else {
      top = Math.max(margin, window.innerHeight - cardRect.height - margin);
    }

    left = rect.left + rect.width / 2 - cardRect.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - cardRect.width - margin));
  }

  card.style.top = `${top}px`;
  card.style.left = `${left}px`;
}

function renderTourStep() {
  const step = TOUR_STEPS[tourIndex];
  const targetEl = step.target ? document.querySelector(step.target) : null;

  if (targetEl && !targetEl.classList.contains("hidden")) {
    targetEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  tourElements.stepLabel.textContent = `Step ${tourIndex + 1} of ${TOUR_STEPS.length}`;
  tourElements.title.textContent = step.title;
  tourElements.body.textContent = step.body;
  tourElements.backBtn.disabled = tourIndex === 0;

  const isLast = tourIndex === TOUR_STEPS.length - 1;
  tourElements.nextBtn.textContent = isLast ? "Done" : "Next";

  window.requestAnimationFrame(() => {
    const rect = getTargetRect(step.target);
    if (rect) {
      positionSpotlight(rect);
    } else {
      tourElements.spotlight.classList.add("hidden");
    }
    positionCard(rect);
  });
}

function endTour(markDone = true) {
  if (!tourActive) return;
  tourActive = false;
  document.body.classList.remove("tour-active");

  tourElements.overlay.classList.add("hidden");
  tourElements.spotlight.classList.add("hidden");
  tourElements.card.classList.add("hidden");
  tourElements.overlay.setAttribute("aria-hidden", "true");

  if (markDone) {
    localStorage.setItem(TOUR_STORAGE_KEY, "1");
  }
  if (tourReturnFocus && document.contains(tourReturnFocus)) {
    tourReturnFocus.focus();
  }
  tourReturnFocus = null;
}

// Keep Tab / Shift+Tab inside the tour dialog while it is open (#36).
function trapTourFocus(event) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    tourElements.card.querySelectorAll("button:not([disabled])")
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !tourElements.card.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !tourElements.card.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}

function startTour({ force = false } = {}) {
  if (tourActive) return;
  if (!force && localStorage.getItem(TOUR_STORAGE_KEY) === "1") return;

  if (!tourElements) {
    tourElements = createTourElements();
    tourElements.overlay.addEventListener("click", () => endTour(true));
    tourElements.skipBtn.addEventListener("click", () => endTour(true));
    tourElements.closeBtn.addEventListener("click", () => endTour(true));
    tourElements.backBtn.addEventListener("click", () => {
      if (tourIndex > 0) {
        tourIndex -= 1;
        renderTourStep();
      }
    });
    tourElements.nextBtn.addEventListener("click", () => {
      if (tourIndex < TOUR_STEPS.length - 1) {
        tourIndex += 1;
        renderTourStep();
      } else {
        endTour(true);
      }
    });

    window.addEventListener("keydown", (event) => {
      if (!tourActive) return;
      if (event.key === "Escape") endTour(true);
      trapTourFocus(event);
      if (event.key === "ArrowRight" && !tourElements.nextBtn.disabled) {
        tourElements.nextBtn.click();
      }
      if (event.key === "ArrowLeft" && !tourElements.backBtn.disabled) {
        tourElements.backBtn.click();
      }
    });

    window.addEventListener("resize", () => {
      if (tourActive) renderTourStep();
    });
  }

  tourIndex = 0;
  tourActive = true;
  tourReturnFocus = document.activeElement;
  document.body.classList.add("tour-active");
  tourElements.overlay.classList.remove("hidden");
  tourElements.card.classList.remove("hidden");
  tourElements.overlay.setAttribute("aria-hidden", "false");
  renderTourStep();
  tourElements.nextBtn.focus();
}

function initTour() {
  const startBtn = document.getElementById("start-tour-btn");
  if (startBtn) {
    startBtn.addEventListener("click", () => startTour({ force: true }));
  }

  window.requestAnimationFrame(() => {
    startTour({ force: false });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initTour);
} else {
  initTour();
}
