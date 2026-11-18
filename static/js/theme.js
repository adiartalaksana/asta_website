/* Day / dark theme switch shared by the public site and the client portal. */
(() => {
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  const THEME_COLORS = { dark: "#07090d", light: "#f5f7fa" };
  const stored = () => { try { return localStorage.getItem("aaa-theme"); } catch { return null; } };
  const store = (value) => { try { localStorage.setItem("aaa-theme", value); } catch { /* storage unavailable */ } };

  const apply = (theme, animate) => {
    if (animate) {
      root.classList.add("theme-switching");
      window.setTimeout(() => root.classList.remove("theme-switching"), 500);
    }
    root.setAttribute("data-theme", theme);
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute("content", THEME_COLORS[theme]));
    const english = root.lang === "en";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const next = theme === "dark" ? "light" : "dark";
      button.setAttribute("aria-pressed", String(theme === "light"));
      button.setAttribute("aria-label", english ? `Switch to ${next === "light" ? "day" : "dark"} theme` : `Ganti ke tema ${next === "light" ? "terang" : "gelap"}`);
      button.title = button.getAttribute("aria-label");
    });
    window.dispatchEvent(new CustomEvent("aaa:themechange", { detail: { theme } }));
  };

  const current = () => (root.getAttribute("data-theme") === "light" ? "light" : "dark");

  document.addEventListener("DOMContentLoaded", () => {
    apply(current(), false);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = current() === "dark" ? "light" : "dark";
        store(next);
        apply(next, true);
      });
    });
  });
  // Follow the operating-system setting until the visitor picks a theme.
  media?.addEventListener?.("change", (event) => {
    if (!stored()) apply(event.matches ? "light" : "dark", true);
  });
  window.aaaTheme = { current, refreshLabels: () => apply(current(), false) };
})();
