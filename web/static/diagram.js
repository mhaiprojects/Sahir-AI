/* Lazy Mermaid render for diagram messages (kind: diagram in message meta). */

let _mermaidReady = null;

function loadScriptOnce(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-src="${src}"]`)) {
      resolve();
      return;
    }
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.dataset.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(el);
  });
}

function mermaidGlobal() {
  return globalThis.mermaid || window.mermaid;
}

function normalizeMermaidSource(src) {
  const s = String(src || "").trim();
  if (!s) return s;
  let bareTitle = "";
  const kept = [];
  for (const line of s.split("\n")) {
    const m = line.match(/^\s*title\s+(.+)\s*$/);
    if (m && !bareTitle) {
      bareTitle = m[1].trim().replace(/^"|"$/g, "");
      continue;
    }
    kept.push(line);
  }
  const body = kept.join("\n").trim();
  if (bareTitle) {
    if (/^---\s*[\s\S]*?title:/m.test(body)) return body;
    return `---\ntitle: ${bareTitle}\n---\n${body}`;
  }
  return body;
}

async function ensureMermaid() {
  const existing = mermaidGlobal();
  if (existing) return existing;
  if (!_mermaidReady) {
    _mermaidReady = (async () => {
      await loadScriptOnce("/static/vendor/mermaid.min.js");
      const mm = mermaidGlobal();
      if (!mm?.initialize) throw new Error("mermaid global missing after vendor load");
      mm.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "neutral",
      });
      return mm;
    })();
  }
  return _mermaidReady;
}

function svgFilename(title) {
  const base = (title || "diagram").replace(/[^\w\-]+/g, "-").slice(0, 40) || "diagram";
  return `${base}.svg`;
}

async function renderDiagramBlock(container, { mermaidSource, title = "Diagram", messageId = 0 }) {
  const normalizedSource = normalizeMermaidSource(mermaidSource);
  const wrap = document.createElement("div");
  wrap.className = "diagram-message";
  const head = document.createElement("div");
  head.className = "diagram-head";
  head.textContent = title;
  const mount = document.createElement("div");
  mount.className = "diagram-render";
  mount.dataset.messageId = String(messageId || "");
  const toolbar = document.createElement("div");
  toolbar.className = "diagram-toolbar";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "diagram-btn";
  copyBtn.textContent = "Copy source";
  copyBtn.addEventListener("click", () => {
    navigator.clipboard?.writeText(normalizedSource).catch(() => {});
  });
  const dlBtn = document.createElement("button");
  dlBtn.type = "button";
  dlBtn.className = "diagram-btn";
  dlBtn.textContent = "Download SVG";
  toolbar.append(copyBtn, dlBtn);
  wrap.append(head, mount, toolbar);
  container.appendChild(wrap);

  try {
    const mm = await ensureMermaid();
    const id = `mmd-${messageId || Date.now()}`;
    const { svg } = await mm.render(id, normalizedSource);
    mount.innerHTML = svg;
    mount.dataset.diagramRendered = "true";
    wrap.dataset.diagramRendered = "true";
    dlBtn.addEventListener("click", () => {
      const blob = new Blob([svg], { type: "image/svg+xml" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = svgFilename(title);
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 500);
    });
  } catch (err) {
    mount.textContent = `Diagram render failed: ${err.message || err}`;
  }
}

function appendCalcCodeBlock(container, code, { sandbox = false } = {}) {
  const details = document.createElement("details");
  details.className = "calc-code-block";
  const sum = document.createElement("summary");
  sum.textContent = sandbox ? "Show sandboxed Python" : "Show calculation code";
  const pre = document.createElement("pre");
  pre.className = "calc-code";
  pre.textContent = code;
  details.append(sum, pre);
  container.appendChild(details);
}