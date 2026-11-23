// Models view: select / load / eject chat models, fallback options and benchmark.
(function () {
  const listEl = document.getElementById("models-list");
  const statusEl = document.getElementById("models-status");
  const autoFb = document.getElementById("auto-fallback-toggle");
  const forceFb = document.getElementById("force-fallback-toggle");
  const benchStatus = document.getElementById("bench-status");
  const benchResults = document.getElementById("bench-results");
  const benchRun = document.getElementById("bench-run-btn");
  const benchQuick = document.getElementById("bench-quick-btn");
  if (!listEl) return;

  let benchTimer = null;
  let benchErrors = 0;

  function fmtBytes(n) {
    if (n == null || Number.isNaN(Number(n))) return "n/a";
    if (!n) return "0 B";
    const gb = n / 1e9;
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;
  }

  function setStatus(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? { credentials: "same-origin" } : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  function badge(text, cls) {
    const b = document.createElement("span");
    b.className = `model-badge ${cls || ""}`;
    b.textContent = text;
    return b;
  }

  let vramBannerEl = document.getElementById("models-vram-banner");

  function ensureVramBanner() {
    if (vramBannerEl) return vramBannerEl;
    vramBannerEl = document.createElement("div");
    vramBannerEl.id = "models-vram-banner";
    vramBannerEl.className = "models-vram-banner";
    vramBannerEl.hidden = true;
    statusEl.insertAdjacentElement("afterend", vramBannerEl);
    return vramBannerEl;
  }

  let storageEl = document.getElementById("models-storage");
  const routerPinEl = document.getElementById("models-router-pin");
  const routerPinText = document.getElementById("models-router-pin-text");
  const routerReloadBtn = document.getElementById("router-reload-btn");

  function renderStorage(storage) {
    if (!storageEl) return;
    if (!storage || !storage.path) {
      storageEl.hidden = true;
      storageEl.textContent = "";
      storageEl.classList.remove("warn");
      return;
    }
    const free = storage.disk_free_bytes;
    const total = storage.disk_total_bytes;
    const freeTxt = free == null ? "?" : fmtBytes(free);
    const totalTxt = total == null ? "?" : fmtBytes(total);
    const src = {
      env: "Sahir .env",
      systemd: "Ollama systemd unit",
      system_default: "system Ollama default",
      user_default: "user Ollama default",
    }[storage.resolved_from] || storage.resolved_from;
    storageEl.hidden = false;
    storageEl.classList.toggle(
      "warn",
      !!(storage.warn_low_space || storage.warn_on_root || (storage.warnings || []).length),
    );
    let text = `Model store: ${storage.path} · ${freeTxt} free / ${totalTxt} (${src})`;
    if (storage.warnings && storage.warnings.length) {
      text += ` — ${storage.warnings.join(" ")}`;
    }
    storageEl.textContent = text;
  }

  function renderRouterPlacement(data) {
    if (!routerPinEl || !routerPinText) return;
    const p = data.router_placement;
    if (!p || !p.enabled) {
      routerPinEl.hidden = true;
      return;
    }
    routerPinEl.hidden = false;
    const pct = p.gpu_percent != null ? `${p.gpu_percent}% GPU` : (p.state || "—");
    const last = p.last_check_at ? ` · checked ${new Date(p.last_check_at * 1000).toLocaleTimeString()}` : "";
    routerPinText.textContent =
      `Pinned router ${data.router_model || p.model}: ${pct} (${p.state || "unknown"})${last}`;
  }

  function render(data) {
    listEl.innerHTML = "";
    renderStorage(data.model_storage);
    renderRouterPlacement(data);
    const banner = ensureVramBanner();
    const warn = data.vram_active_warning;
    if (warn && warn.message) {
      banner.hidden = false;
      banner.textContent = warn.message;
    } else {
      banner.hidden = true;
      banner.textContent = "";
    }
    for (const m of data.models) {
      const card = document.createElement("div");
      card.className = `model-card${m.active ? " active" : ""}`;
      card.dataset.model = m.name;

      const label = document.createElement("label");
      label.className = "model-select";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "active-model";
      radio.value = m.name;
      radio.checked = m.active;
      radio.addEventListener("change", () => act("select", m.name));
      const text = document.createElement("div");
      const name = document.createElement("div");
      name.className = "model-name";
      name.textContent = m.name;
      const meta = document.createElement("div");
      meta.className = "model-meta";
      const parts = [fmtBytes(m.size)];
      if (m.parameter_size) parts.push(m.parameter_size);
      if (m.quantization) parts.push(m.quantization);
      if (m.loaded) {
        parts.push(`in memory ${fmtBytes(m.loaded.size)}: GPU ${m.loaded.gpu_percent}% ` +
          `(${fmtBytes(m.loaded.size_vram)} VRAM / ${fmtBytes(m.loaded.size_ram)} RAM)`);
      }
      meta.textContent = parts.join(" · ");
      const badges = document.createElement("div");
      badges.className = "model-badges";
      if (m.active) badges.appendChild(badge("Active", "active"));
      if (m.fallback) badges.appendChild(badge("Fallback", "fallback"));
      if (m.default) badges.appendChild(badge("Default", ""));
      if (m.loaded) badges.appendChild(badge(`Loaded · GPU ${m.loaded.gpu_percent}%`, "loaded"));
      if (m.thinking) badges.appendChild(badge("Thinking off", ""));
      if (m.vram_warning) badges.appendChild(badge("VRAM tight", "vram-warn"));
      text.append(name, meta, badges);
      label.append(radio, text);

      const actions = document.createElement("div");
      actions.className = "model-actions";
      const load = document.createElement("button");
      load.type = "button";
      load.className = "secondary";
      load.textContent = m.loaded ? "Reload" : "Load";
      load.addEventListener("click", () => act("load", m.name));
      const eject = document.createElement("button");
      eject.type = "button";
      eject.className = "secondary";
      eject.textContent = "Eject";
      eject.disabled = !m.loaded;
      eject.addEventListener("click", () => act("eject", m.name));
      actions.append(load, eject);

      card.append(label, actions);
      listEl.appendChild(card);
    }
    autoFb.checked = !!data.auto_fallback;
    forceFb.checked = !!data.force_fallback;
    const other = (data.other_loaded || []).map((o) => `${o.name} ${fmtBytes(o.size)}`).join(", ");
    const backend = data.llm_backend || data.llm_status?.backend || "ollama";
    let line = `LLM backend: ${backend} · Active: ${data.active_model} · Fallback: ${data.fallback_model}` +
      (other ? ` · Also loaded: ${other}` : "");
    if (backend === "openai" && data.llm_status?.openai_base_url) {
      line += ` · ${data.llm_status.openai_base_url}`;
    }
    if (data.live_stt_backend) {
      line += ` · Live captions: ${data.live_stt_backend}`;
    }
    setStatus(line);
  }

  async function refreshModels() {
    try {
      const data = await api("/api/models");
      try {
        const health = await fetch("/api/health", { credentials: "same-origin", cache: "no-store" })
          .then((r) => r.json());
        const tx = health.transcription || {};
        if (tx.live_stt_backend) data.live_stt_backend = tx.live_stt_backend;
      } catch {
        /* health optional */
      }
      render(data);
    } catch (err) {
      setStatus(`Could not list models: ${err.message}`, true);
    }
  }
  window.refreshModels = refreshModels;

  async function act(kind, model) {
    const verb = { select: "Selecting", load: "Loading", eject: "Ejecting" }[kind];
    setStatus(`${verb} ${model}…`);
    listEl.querySelectorAll("button, input").forEach((el) => { el.disabled = true; });
    try {
      const r = await api(`/api/models/${kind}`, { model });
      await refreshModels();
      // Show the load result after the refresh so render() doesn't overwrite it (#40).
      if (kind === "load") {
        const already = !r.load_duration_ms && Number(r.seconds) < 0.5;
        const took = r.load_duration_ms ? `${(r.load_duration_ms / 1000).toFixed(1)}s` : `${r.seconds}s`;
        const msg = already ? `${model} was already in memory (${r.seconds}s)` : `Loaded ${model} in ${took}`;
        setStatus(`${msg} · ${statusEl.textContent}`);
      }
    } catch (err) {
      setStatus(`${verb} ${model} failed: ${err.message}`, true);
      await refreshModels();
    }
  }

  async function setOptions(opts) {
    try {
      await api("/api/models/options", opts);
      await refreshModels();
    } catch (err) {
      setStatus(`Could not save option: ${err.message}`, true);
    }
  }
  autoFb.addEventListener("change", () => setOptions({ auto_fallback: autoFb.checked }));
  forceFb.addEventListener("change", () => setOptions({ force_fallback: forceFb.checked }));
  document.getElementById("refresh-models-btn").addEventListener("click", refreshModels);
  if (routerReloadBtn) {
    routerReloadBtn.addEventListener("click", async () => {
      setStatus("Reloading pinned router on GPU…");
      try {
        const r = await api("/api/models/router/reload");
        await refreshModels();
        setStatus(`Router reload done (${r.gpu_percent != null ? r.gpu_percent + "% GPU" : "ok"})`);
      } catch (err) {
        setStatus(`Router reload failed: ${err.message}`, true);
      }
    });
  }

  function renderBench(latest) {
    benchResults.innerHTML = "";
    if (!latest || !latest.results) return;
    const table = document.createElement("table");
    const head = ["Model", "Load s", "GPU %", "TTFT ms", "tok/s", "Decision p50 ms", "Decision p90 ms",
      "Valid JSON %", "Correct %", "w/ heuristic %"];
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    head.forEach((h) => { const th = document.createElement("th"); th.textContent = h; hr.appendChild(th); });
    thead.appendChild(hr);
    const tbody = document.createElement("tbody");
    for (const r of latest.results) {
      const tr = document.createElement("tr");
      const vals = r.error ? [r.model, `error: ${r.error}`] : [
        r.model, r.load_s, r.gpu_percent, r.ttft_ms.p50 ?? "-", r.tokens_per_s.p50 ?? "-",
        r.decision_ms.p50 ?? "-", r.decision_ms.p90 ?? "-", r.valid_json_pct, r.correct_pct,
        r.effective_correct_pct];
      vals.forEach((v) => { const td = document.createElement("td"); td.textContent = String(v); tr.appendChild(td); });
      tbody.appendChild(tr);
    }
    table.append(thead, tbody);
    const when = document.createElement("p");
    when.className = "config-note";
    when.textContent = `Run ${latest.started} · ${latest.n_prompts} decision prompts. Full report: data/bench/latest.md`;
    benchResults.append(when, table);
  }

  async function pollBench() {
    try {
      const s = await api("/api/models/benchmark");
      renderBench(s.latest);
      benchRun.disabled = benchQuick.disabled = s.running;
      benchErrors = 0;
      if (s.running) {
        const where = s.external ? " (started outside this page)" : "";
        benchStatus.textContent = `Running${where}… ${s.progress?.message || ""}`;
        benchTimer = setTimeout(pollBench, 3000);
      } else {
        benchStatus.textContent = s.exit_code && s.exit_code !== 0 ? `Last run failed (exit ${s.exit_code}); see data/bench/last_run.log` : "";
        if (benchTimer) { benchTimer = null; refreshModels(); }
      }
    } catch (err) {
      // Keep polling with backoff instead of stopping after one failure (#41).
      benchErrors += 1;
      const delay = Math.min(30000, 3000 * 2 ** Math.min(benchErrors, 4));
      benchStatus.textContent = `Benchmark status error: ${err.message} (retrying in ${Math.round(delay / 1000)}s)`;
      benchTimer = setTimeout(pollBench, delay);
    }
  }

  async function startBench(quick) {
    if (!confirm(quick ? "Run a quick benchmark of all models?" : "Run the full benchmark? It takes several minutes.")) return;
    try {
      await api("/api/models/benchmark", { quick });
      benchTimer = setTimeout(pollBench, 1000);
      benchStatus.textContent = "Starting…";
      benchRun.disabled = benchQuick.disabled = true;
    } catch (err) {
      benchStatus.textContent = `Could not start: ${err.message}`;
    }
  }
  benchRun.addEventListener("click", () => startBench(false));
  benchQuick.addEventListener("click", () => startBench(true));

  pollBench();
})();
