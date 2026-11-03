(() => {
  const root = document.documentElement;
  const key = "portal_theme";
  const saved = localStorage.getItem(key);
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
      localStorage.setItem(key, next);
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
