// Copyright (c) 2026 Faryaan Khan
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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
})();
