(function () {
  let markedConfigured = false;

  function escapeHtml(text) {
    return String(text)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function configureMarked() {
    if (markedConfigured || typeof marked === "undefined") return typeof marked !== "undefined";
    marked.setOptions({
      breaks: true,
      gfm: true,
    });
    marked.use({
      renderer: {
        link({ href, title, tokens }) {
          const label = this.parser.parseInline(tokens);
          const safeHref = escapeHtml(href || "#");
          const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
          return `<a href="${safeHref}"${titleAttr} target="_blank" rel="noopener noreferrer">${label}</a>`;
        },
      },
    });
    markedConfigured = true;
    return true;
  }

  function renderMarkdown(text) {
    const source = String(text ?? "");
    if (!source.trim()) return "";

    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
      configureMarked();
      const html = marked.parse(source);
      return DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ADD_ATTR: ["target", "rel"],
      });
    }

    return escapeHtml(source).replaceAll("\n", "<br>");
  }

  window.renderMarkdown = renderMarkdown;
})();
