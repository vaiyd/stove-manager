// Light/dark toggle: apply the stored theme before first paint, then wire the navbar button.
(() => {
  "use strict";
  const KEY = "stove-pilot-theme";
  const LIGHT = "caramellatte";
  const DARK = "coffee";
  const META_DARK = "#261b25"; // coffee base-100
  const root = document.documentElement;
  const meta = document.querySelector('meta[name="theme-color"]');
  const metaLight = meta ? meta.content : "";
  const system = window.matchMedia("(prefers-color-scheme: dark)");

  const read = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === LIGHT || value === DARK ? value : null;
    } catch (_) {
      return null;
    }
  };
  const save = (theme) => {
    try {
      localStorage.setItem(KEY, theme);
    } catch (_) {
      // Storage unavailable: the choice only lasts for this page.
    }
  };
  // data-mode is the effective mode (CSS hook for the icons); data-theme is set only when stored.
  const sync = () => {
    const stored = read();
    if (stored) root.setAttribute("data-theme", stored);
    const dark = stored ? stored === DARK : system.matches;
    root.setAttribute("data-mode", dark ? "dark" : "light");
    if (meta) meta.content = dark ? META_DARK : metaLight;
  };

  sync();
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-theme-toggle]")) return;
    save(root.getAttribute("data-mode") === "dark" ? LIGHT : DARK);
    sync();
  });
  system.addEventListener("change", () => {
    if (read() === null) sync();
  });
})();
