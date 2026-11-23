/* Projects rail: tree, new chat/project, links to secondary pages (unified UI). */
(() => {
  "use strict";
  const tree = document.getElementById("project-rail-tree");
  const search = document.getElementById("rail-search");
  const newChatBtn = document.getElementById("rail-new-chat");
  const newProjectBtn = document.getElementById("rail-new-project");
  const layout = document.querySelector(".unified-layout");
  const menuBtn = document.getElementById("rail-menu-btn");
  const backdrop = document.getElementById("rail-backdrop");
  if (!tree) return;

  let projects = [];
  let generalProjectId = null;
  let conversations = [];
  let currentProjectId = null;
  let filter = "";

  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const trim = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  function closeDrawer() {
    layout?.classList.remove("rail-open");
    menuBtn?.setAttribute("aria-expanded", "false");
  }

  function openDrawer() {
    layout?.classList.add("rail-open");
    menuBtn?.setAttribute("aria-expanded", "true");
  }

  menuBtn?.addEventListener("click", () => {
    if (layout?.classList.contains("rail-open")) closeDrawer();
    else openDrawer();
  });
  backdrop?.addEventListener("click", closeDrawer);

  function chatsForProject(projectId, supervisorId) {
    let list = conversations.filter((c) => c.project_id === projectId);
    if (filter) {
      const f = filter.toLowerCase();
      list = list.filter((c) => (c.title || "").toLowerCase().includes(f));
    }
    list.sort((a, b) => {
      const aSup = a.id === supervisorId ? 0 : 1;
      const bSup = b.id === supervisorId ? 0 : 1;
      if (aSup !== bSup) return aSup - bSup;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || "")) * -1;
    });
    return list;
  }

  function renderTree() {
    const activeId = window.sahirActiveConversationId?.() ?? null;
    const ordered = [...projects].sort((a, b) => {
      if (a.id === generalProjectId) return -1;
      if (b.id === generalProjectId) return 1;
      return String(a.name).localeCompare(String(b.name));
    });
    tree.innerHTML = ordered.map((p) => {
      const supId = p.supervisor_conversation_id;
      const chats = chatsForProject(p.id, supId);
      const items = chats.map((c) => {
        const isSup = c.id === supId || c.kind === "supervisor";
        const label = trim(String(c.title || `Chat #${c.id}`), 42);
        const star = isSup ? "★ " : "• ";
        return `<li><button type="button" class="${activeId === c.id ? "active" : ""}${isSup ? " sup" : ""}" data-conv="${c.id}" data-project="${p.id}">${star}${esc(label)}</button></li>`;
      }).join("");
      const expanded = chats.length > 0 ? "true" : "false";
      return `<div class="rail-project" data-project="${p.id}">
        <button type="button" class="rail-project-head" aria-expanded="${expanded}">
          <span class="rail-chevron" aria-hidden="true">▾</span>
          <span>${esc(p.name)}</span>
        </button>
        <ul class="rail-chats">${items || '<li><span class="hint" style="padding:0.2rem 0.45rem;font-size:0.75rem;color:var(--muted)">No chats</span></li>'}</ul>
      </div>`;
    }).join("");
  }

  tree.addEventListener("click", async (e) => {
    const head = e.target.closest(".rail-project-head");
    if (head) {
      const open = head.getAttribute("aria-expanded") === "true";
      head.setAttribute("aria-expanded", open ? "false" : "true");
      return;
    }
    const chatBtn = e.target.closest("button[data-conv]");
    if (!chatBtn) return;
    const id = Number(chatBtn.dataset.conv);
    currentProjectId = Number(chatBtn.dataset.project) || currentProjectId;
    try {
      await window.sahirOpenConversation?.(id);
      closeDrawer();
      window.sahirActivateView?.("chat", { replaceUrl: true });
    } catch (err) {
      window.sahirToast?.(err.message, true);
    }
  });

  async function refresh() {
    const [projRes, convRes] = await Promise.all([
      fetch("/api/projects"),
      fetch("/api/conversations?limit=300"),
    ]);
    const projData = await projRes.json();
    const convData = await convRes.json();
    projects = projData.projects || [];
    generalProjectId = projData.general_project_id;
    conversations = convData.conversations || [];
    if (!currentProjectId && generalProjectId) currentProjectId = generalProjectId;
    renderTree();
  }

  search?.addEventListener("input", () => {
    filter = search.value.trim();
    renderTree();
  });

  newChatBtn?.addEventListener("click", async () => {
    try {
      const pid = currentProjectId || generalProjectId;
      const res = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: pid || undefined }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const conv = await res.json();
      await refresh();
      await window.sahirOpenConversation?.(conv.id);
      closeDrawer();
      window.sahirActivateView?.("chat");
    } catch (err) {
      window.sahirToast?.(err.message, true);
    }
  });

  newProjectBtn?.addEventListener("click", async () => {
    const name = prompt("New project name");
    if (!name?.trim()) return;
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const p = await res.json();
      currentProjectId = p.id;
      await refresh();
      if (p.supervisor_conversation_id) {
        await window.sahirOpenConversation?.(p.supervisor_conversation_id);
      }
    } catch (err) {
      window.sahirToast?.(err.message, true);
    }
  });

  window.sahirRefreshRail = refresh;
  window.sahirSetCurrentProject = (id) => { currentProjectId = id; };
  refresh();
})();