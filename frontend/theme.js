// Copyright (c) 2026 Faryaan Khan
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Apply the saved (or ?theme=) colour mode before the page paints, so there's no flash.
// Kept as a separate file because the Content-Security-Policy blocks inline scripts.
(function () {
  var q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark") localStorage.setItem("skyward_theme", q);
  var t = localStorage.getItem("skyward_theme") ||
    (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = t;
})();
