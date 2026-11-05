(() => {
  const root = document.documentElement;
  const key = "portal_theme";
  // Runs in <head>, before the body is drawn, so a saved theme never flashes the other.
  // localStorage may be blocked (private windows, site data off): then the system's.
  let saved = null;
  try {
    saved = localStorage.getItem(key);
  } catch (_) {}
  if (saved === "light" || saved === "dark") {
    root.dataset.theme = saved;
  }

  const flash = (button, message) => {
    const original = button.textContent;
    button.dataset.copied = "true";
    button.textContent = message;
    setTimeout(() => {
      delete button.dataset.copied;
      button.textContent = original;
    }, 1600);
  };

  // Copy what a URL gives. ClipboardItem takes the text as a promise, so browsers that only
  // allow copying during the click (Safari) accept it while the text is still loading.
  const copyFromUrl = (button) => {
    if (!navigator.clipboard) {
      flash(button, "Needs HTTPS");
      return;
    }
    const text = fetch(button.dataset.copyUrl, { credentials: "same-origin" }).then((reply) => {
      if (!reply.ok) throw new Error(String(reply.status));
      return reply.text();
    });
    const written = window.ClipboardItem
      ? navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": text.then((body) => new Blob([body], { type: "text/plain" })),
          }),
        ])
      : text.then((body) => navigator.clipboard.writeText(body));
    written.then(
      () => flash(button, "Copied!"),
      () => flash(button, "Copy failed"),
    );
  };

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("#theme-toggle");
    if (toggle) {
      const dark =
        root.dataset.theme === "dark" ||
        (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
      const next = dark ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem(key, next);
      } catch (_) {}
      return;
    }

    const copyUrlBtn = event.target.closest("[data-copy-url]");
    if (copyUrlBtn) {
      copyFromUrl(copyUrlBtn);
      return;
    }

    const copyBtn = event.target.closest("[data-copy]");
    if (copyBtn && navigator.clipboard) {
      const target = copyBtn.dataset.copy || copyBtn.closest(".copy-box")?.querySelector("code, pre")?.textContent || "";
      if (target) {
        navigator.clipboard.writeText(target.trim()).then(() => flash(copyBtn, "Copied!"));
      }
    }
  });
})();
