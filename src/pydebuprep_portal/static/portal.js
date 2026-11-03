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

    const copyBtn = event.target.closest("[data-copy]");
    if (copyBtn && navigator.clipboard) {
      const target = copyBtn.dataset.copy || copyBtn.closest(".copy-box")?.querySelector("code, pre")?.textContent || "";
      if (target) {
        navigator.clipboard.writeText(target.trim()).then(() => {
          const original = copyBtn.textContent;
          copyBtn.dataset.copied = "true";
          copyBtn.textContent = "Copied!";
          setTimeout(() => {
            delete copyBtn.dataset.copied;
            copyBtn.textContent = original;
          }, 1600);
        });
      }
    }
  });
})();
