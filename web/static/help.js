// Field help: adds a small "i" button next to every element that has data-help="…".
// Hover (mouse) or keyboard focus shows the text; click/tap pins it open (mobile);
// Escape or a tap elsewhere closes it. Presentation only: no app state is touched.
//   data-help        the explanation (1–2 sentences)
//   data-help-label  short name for the screen-reader label (optional)
//   data-help-place  "after" (default) | "inside" (append as last child) | "wrap"
(() => {
  const pop = document.createElement("div");
  pop.id = "help-popover";
  pop.className = "help-popover";
  pop.setAttribute("role", "tooltip");
  pop.hidden = true;
  document.body.appendChild(pop);

  let current = null;
  let pinned = false;
  let hideTimer = null;
  let count = 0;

  function labelFor(el) {
    const raw = el.dataset.helpLabel || el.getAttribute("aria-label") || el.textContent || "";
    return raw.trim().replace(/\s+/g, " ").slice(0, 60) || "this control";
  }

  function position() {
    if (!current) return;
    if (!current.isConnected || current.offsetParent === null) { close(); return; }
    const r = current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const margin = 8;
    pop.style.left = "0px";
    pop.style.top = "0px";
    const w = pop.offsetWidth;
    const h = pop.offsetHeight;
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(margin, Math.min(left, vw - w - margin));
    let top = r.bottom + 6;
    if (top + h > vh - margin && r.top - 6 - h >= margin) top = r.top - 6 - h;
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top)}px`;
  }

  function open(btn, pin) {
    clearTimeout(hideTimer);
    if (current && current !== btn) current.setAttribute("aria-expanded", "false");
    current = btn;
    pinned = !!pin;
    pop.textContent = btn.dataset.helpText;
    pop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    position();
  }

  function close() {
    clearTimeout(hideTimer);
    if (current) current.setAttribute("aria-expanded", "false");
    current = null;
    pinned = false;
    pop.hidden = true;
  }

  function softClose() {
    if (pinned) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(close, 120);
  }

  function build(el) {
    const text = (el.dataset.help || "").trim();
    if (!text || el.dataset.helpBuilt) return;
    el.dataset.helpBuilt = "1";
    const id = `help-text-${++count}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "help-tip";
    btn.setAttribute("aria-label", `Help: ${labelFor(el)}`);
    btn.setAttribute("aria-describedby", id);
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "help-popover");
    btn.dataset.helpText = text;
    const glyph = document.createElement("span");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "i";
    const desc = document.createElement("span");
    desc.id = id;
    desc.className = "sr-only";
    desc.textContent = text;
    btn.append(glyph, desc);

    const place = el.dataset.helpPlace || "after";
    if (place === "inside") {
      el.appendChild(btn);
    } else if (place === "wrap") {
      const wrap = document.createElement("span");
      wrap.className = "help-wrap";
      el.replaceWith(wrap);
      wrap.append(el, btn);
    } else {
      el.insertAdjacentElement("afterend", btn);
    }

    btn.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse" && !pinned) open(btn, false); });
    btn.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") softClose(); });
    btn.addEventListener("focus", () => { if (current !== btn) open(btn, false); });
    btn.addEventListener("blur", () => softClose());
    btn.addEventListener("click", (e) => {
      // Stop the click reaching a surrounding <summary>/<label>/<form>.
      e.preventDefault();
      e.stopPropagation();
      if (current === btn && pinned) close();
      else open(btn, true);
    });
  }

  pop.addEventListener("pointerenter", () => clearTimeout(hideTimer));
  pop.addEventListener("pointerleave", () => softClose());

  document.addEventListener("pointerdown", (e) => {
    if (!current) return;
    if (current.contains(e.target) || pop.contains(e.target)) return;
    close();
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && current) {
      close();
    }
  }, true);
  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, true);

  document.querySelectorAll("[data-help]").forEach(build);
  window.sahirBuildHelp = (root = document) => root.querySelectorAll("[data-help]").forEach(build);
})();
