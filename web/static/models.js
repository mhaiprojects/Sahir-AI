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
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
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

  function render(data) {
    listEl.innerHTML = "";
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
    setStatus(`Active: ${data.active_model} · Fallback: ${data.fallback_model}` +
      (other ? ` · Also loaded: ${other}` : ""));
  }

  async function refreshModels() {
    try {
      render(await api("/api/models"));
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
      if (s.running) {
        benchStatus.textContent = `Running… ${s.progress?.message || ""}`;
        benchTimer = setTimeout(pollBench, 3000);
      } else {
        benchStatus.textContent = s.exit_code && s.exit_code !== 0 ? `Last run failed (exit ${s.exit_code}); see data/bench/last_run.log` : "";
        if (benchTimer) { benchTimer = null; refreshModels(); }
      }
    } catch (err) {
      benchStatus.textContent = `Benchmark status error: ${err.message}`;
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
