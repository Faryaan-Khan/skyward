/* "How it works" page: theme toggle + start each animation when its card scrolls into view. */
"use strict";
(function () {
  const root = document.documentElement, btn = document.getElementById("theme-toggle");
  btn.addEventListener("click", (e) => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    const r = e.currentTarget.getBoundingClientRect();
    root.style.setProperty("--vt-x", `${((r.left + r.width / 2) / innerWidth) * 100}%`);
    root.style.setProperty("--vt-y", `${((r.top + r.height / 2) / innerHeight) * 100}%`);
    root.classList.add("theming");
    const apply = () => { root.dataset.theme = next; localStorage.setItem("skyward_theme", next); };
    if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document.startViewTransition(apply).finished.finally(() => root.classList.remove("theming"));
    } else { apply(); root.classList.remove("theming"); }
  });
  const io = new IntersectionObserver((ents) => ents.forEach((e) => e.isIntersecting && e.target.classList.add("play")),
    { threshold: 0.35 });
  document.querySelectorAll(".step, .how-card, .how-hero").forEach((el) => io.observe(el));

  // live numbers for the deep-dive section, straight from the running system
  const ago = (iso) => {
    if (!iso) return "–";
    const s = (Date.now() - new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z")) / 1000;
    return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
  };
  fetch("/api/status").then((r) => r.json()).then((s) => {
    const vals = {
      mae: s.model_mae != null ? `$${s.model_mae.toFixed(2)}` : "–",
      mape: s.model_mape != null ? `${(s.model_mape * 100).toFixed(1)}%` : "–",
      fares: new Intl.NumberFormat().format(s.rows_by_source?.travelpayouts || 0),
      routes: s.routes_live ?? "–",
      trained: ago(s.model_trained_at),
      refreshed: ago(s.last_updated),
    };
    document.querySelectorAll("[data-k]").forEach((el) => { el.textContent = vals[el.dataset.k] ?? "–"; });
  }).catch(() => {});
})();
