// Copyright (c) 2026 Faryaan Khan
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
/* Skyward dashboard: vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (typeof u === "string" && /^https:\/\//.test(u) ? u : null);   // only ever link to https URLs
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = body.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? (d[0]?.msg || "Please check the form") : `Request failed (${r.status})`);
  }
  return body;
};
const toast = (msg) => { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3600); };
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const state = { route: null, month: null, search: null, selected: null, rates: { USD: 1 }, currency: "USD", alertCfg: {} };
let curveChart, histChart;

/* ================= time helpers ================= */
const fmtDate = (iso, opts = { day: "numeric", month: "short" }) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, opts);
const fmtDay = (iso) => fmtDate(iso, { weekday: "short", day: "numeric", month: "short" });
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z")) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;   // local time, not UTC
function nextMonth() { const d = new Date(); return ym(new Date(d.getFullYear(), d.getMonth() + 1, 1)); }

/* ================= currency ================= */
const COMMON = ["USD", "EUR", "GBP", "MYR", "SGD", "AUD", "JPY", "CNY", "HKD", "INR", "IDR", "THB", "PHP", "VND", "KRW",
  "AED", "SAR", "QAR", "PKR", "BDT", "CAD", "NZD", "CHF", "TRY", "ZAR", "BRL", "MXN"];
const REGION_CUR = { MY: "MYR", SG: "SGD", GB: "GBP", US: "USD", AU: "AUD", IN: "INR", ID: "IDR", TH: "THB", JP: "JPY", CN: "CNY",
  HK: "HKD", PH: "PHP", VN: "VND", KR: "KRW", AE: "AED", SA: "SAR", QA: "QAR", PK: "PKR", BD: "BDT", CA: "CAD", NZ: "NZD",
  CH: "CHF", TR: "TRY", ZA: "ZAR", BR: "BRL", MX: "MXN", DE: "EUR", FR: "EUR", ES: "EUR", IT: "EUR", NL: "EUR", IE: "EUR",
  PT: "EUR", AT: "EUR", BE: "EUR", FI: "EUR", GR: "EUR" };
function guessCurrency() {
  try { return REGION_CUR[new Intl.Locale(navigator.language).maximize().region] || "USD"; } catch { return "USD"; }
}
const rate = () => state.rates[state.currency] || 1;
const fmtCache = {};
function money(usd) {
  if (usd == null || Number.isNaN(usd)) return "–";
  const cur = state.currency;
  const f = fmtCache[cur] || (fmtCache[cur] = new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 0 }));
  return f.format(usd * rate());
}
async function loadRates() {
  try { state.rates = (await api("/api/rates")).rates; } catch { state.rates = { USD: 1 }; }
  const saved = localStorage.getItem("skyward_currency");
  state.currency = [saved, guessCurrency(), "USD"].find((c) => c && state.rates[c]);
  const codes = Object.keys(state.rates).sort();
  const opt = (c) => `<option value="${esc(c)}"${c === state.currency ? " selected" : ""}>${esc(c)}</option>`;
  $("currency").innerHTML = `<optgroup label="Popular">${COMMON.filter((c) => state.rates[c]).map(opt).join("")}</optgroup>` +
    `<optgroup label="All currencies">${codes.filter((c) => !COMMON.includes(c)).map(opt).join("")}</optgroup>`;
  $("w-cur").textContent = `(${state.currency})`;
}
$("currency").addEventListener("change", (e) => {
  state.currency = e.target.value;
  localStorage.setItem("skyward_currency", state.currency);
  $("w-cur").textContent = `(${state.currency})`;
  rerenderAll();
});

/* ================= theme: sun <-> moon, smooth ================= */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("skyward_theme", t);
  const btn = $("theme-toggle");
  btn.setAttribute("aria-label", t === "dark" ? "Switch to light mode" : "Switch to dark mode");
  btn.setAttribute("aria-pressed", String(t === "light"));
}
$("theme-toggle").addEventListener("click", (e) => {
  const root = document.documentElement;
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  const r = e.currentTarget.getBoundingClientRect();
  root.style.setProperty("--vt-x", `${((r.left + r.width / 2) / innerWidth) * 100}%`);
  root.style.setProperty("--vt-y", `${((r.top + r.height / 2) / innerHeight) * 100}%`);
  root.classList.add("theming");                      // no per-element fades during the switch
  const done = () => { root.classList.remove("theming"); recolorCharts(); globeTheme(); };
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.startViewTransition(() => applyTheme(next)).finished.finally(done);
  } else { applyTheme(next); done(); }
});
applyTheme(document.documentElement.dataset.theme || "dark");

/* ================= airlines & booking sites ================= */
const AIRLINES = window.AIRLINES || {};   // directory in /static/airlines.js
const airlineName = (code) => (AIRLINES[code] ? AIRLINES[code][0] : code || "–");
const dmy = (date) => ({ y: date.slice(0, 4), m: date.slice(5, 7), d: date.slice(8, 10) });
const aviasalesUrl = (o, d, date, link) => safeUrl(link) || `https://www.aviasales.com/search/${o}${dmy(date).d}${dmy(date).m}${d}1`;
const PROVIDERS = [
  { name: "Aviasales", color: "#1ea7fd", url: aviasalesUrl },
  { name: "Google Flights", color: "#4285f4", url: (o, d, date) => `https://www.google.com/travel/flights?q=${encodeURIComponent(`one way flights from ${o} to ${d} on ${date}`)}&curr=${state.currency}` },
  { name: "Skyscanner", color: "#0770e3", url: (o, d, date) => `https://www.skyscanner.net/transport/flights/${o.toLowerCase()}/${d.toLowerCase()}/${date.slice(2, 4)}${dmy(date).m}${dmy(date).d}/?adultsv2=1&currency=${state.currency}` },
  { name: "Trip.com", color: "#287dfa", url: (o, d, date) => `https://www.trip.com/flights/showfarefirst?dcity=${o.toLowerCase()}&acity=${d.toLowerCase()}&ddate=${date}&triptype=ow&class=y&quantity=1&curr=${state.currency}` },
  { name: "Booking.com", color: "#003580", url: (o, d, date) => `https://flights.booking.com/flights/${o}.AIRPORT-${d}.AIRPORT/?type=ONEWAY&adults=1&cabinClass=ECONOMY&from=${o}.AIRPORT&to=${d}.AIRPORT&depart=${date}&sort=CHEAPEST` },
  { name: "Expedia", color: "#fddb32", url: (o, d, date) => `https://www.expedia.com/Flights-Search?trip=oneway&leg1=from:${o},to:${d},departure:${dmy(date).m}/${dmy(date).d}/${dmy(date).y}TANYT&passengers=adults:1&mode=search` },
  { name: "Kayak", color: "#ff690f", url: (o, d, date) => `https://www.kayak.com/flights/${o}-${d}/${date}?sort=price_a` },
  { name: "Cheapflights", color: "#f5c518", url: (o, d, date) => `https://www.cheapflights.com/flight-search/${o}-${d}/${date}?sort=price_a` },
  { name: "Momondo", color: "#b146c2", url: (o, d, date) => `https://www.momondo.com/flight-search/${o}-${d}/${date}?sort=price_a` },
];
function providerLinks(o, d, date, link, carrier, cls = "provider") {
  const items = PROVIDERS.map((p) => [p.name, p.color, p.url(o, d, date, link)]);
  if (AIRLINES[carrier]) items.splice(1, 0, [`${AIRLINES[carrier][0]} (airline)`, "#c9a96b", `https://www.${AIRLINES[carrier][1]}`]);
  return items.map(([name, color, url]) => `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener${name === "Aviasales" ? " sponsored" : ""}"><i style="background:${color}"></i>${esc(name)}</a>`).join("");
}

/* ================= place autocomplete ================= */
function autocomplete(input) {
  const list = input.parentElement.querySelector(".suggest");
  let items = [], active = -1, timer;
  const close = () => { list.classList.remove("open"); active = -1; };
  const choose = (p) => { setPlace(input, p.code, p.city, p.type === "airport" ? p.name : null); close(); };
  const render = () => {
    list.innerHTML = items.map((p, i) => `<li role="option" data-i="${i}" class="${i === active ? "active" : ""}">
      <span><b>${esc(p.type === "airport" ? p.name : p.city)}</b><small>${esc(p.type === "airport" ? `${p.city}, ${p.country}` : `${p.country} · all airports`)}</small></span>
      <span class="code">${esc(p.code)}</span></li>`).join("");
    list.classList.toggle("open", items.length > 0);
  };
  input.addEventListener("input", () => {
    delete input.dataset.code;
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { items = []; close(); return; }
    timer = setTimeout(async () => {
      try { items = await api(`/api/places?q=${encodeURIComponent(q)}`); active = items.length ? 0 : -1; render(); } catch { items = []; close(); }
    }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (!list.classList.contains("open")) return;
    if (e.key === "ArrowDown") { active = (active + 1) % items.length; render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = (active - 1 + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { choose(items[active]); e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  list.addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) { choose(items[+li.dataset.i]); e.preventDefault(); } });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("focus", () => input.select());
}
autocomplete($("from"));
autocomplete($("to"));
const codeOf = (input) => input.dataset.code || (/^[A-Za-z]{3}$/.test(input.value.trim()) ? input.value.trim().toUpperCase() : null);
function setPlace(input, code, name, airportName) {
  input.dataset.code = code; input.dataset.name = name || code;
  input.value = airportName ? `${name} · ${airportName} (${code})` : name && name !== code ? `${name} (${code})` : code;
}
$("swap").addEventListener("click", () => {
  const a = { code: $("from").dataset.code, name: $("from").dataset.name, v: $("from").value };
  const b = { code: $("to").dataset.code, name: $("to").dataset.name, v: $("to").value };
  if (b.code) setPlace($("from"), b.code, b.name); else { $("from").value = b.v; delete $("from").dataset.code; }
  if (a.code) setPlace($("to"), a.code, a.name); else { $("to").value = a.v; delete $("to").dataset.code; }
});

/* ================= interactive 3D earth (globe.gl, loaded on demand) ================= */
const GLOBE_JS = { src: "https://cdn.jsdelivr.net/npm/globe.gl@2.46.2/dist/globe.gl.min.js", integrity: "sha256-LD5EXATRISFZEKiWiLlgkcinIHHBIqT4MAgaObY2yUw=" };
const TEX = "https://cdn.jsdelivr.net/npm/three-globe@2.45.6/example/img/";
const FALLBACK = { KUL: [2.75, 101.71], SIN: [1.36, 103.99], BKK: [13.69, 100.75], NRT: [35.77, 140.39], LHR: [51.47, -0.45],
  SYD: [-33.94, 151.18], HKG: [22.31, 113.92], JFK: [40.64, -73.78], LAX: [33.94, -118.41], CDG: [49.01, 2.55] };
const G = { world: null, airports: [], route: null, idle: null, picked: null };

function loadGlobe() {
  const wrap = $("globe-wrap");
  const hasWebGL = (() => { try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; } })();
  if (!hasWebGL) { $("globe-loading").textContent = "Your browser can't show the 3D earth (WebGL is off)."; return; }
  // only download the 3D engine when the globe is about to be visible
  const io = new IntersectionObserver((ents) => {
    if (!ents.some((e) => e.isIntersecting)) return;
    io.disconnect();
    const s = document.createElement("script");
    Object.assign(s, { src: GLOBE_JS.src, integrity: GLOBE_JS.integrity, crossOrigin: "anonymous", async: true });
    s.onload = initGlobe;
    s.onerror = () => { $("globe-loading").textContent = "Couldn't load the 3D earth."; };
    document.head.appendChild(s);
  }, { rootMargin: "200px" });
  io.observe(wrap);
}

function globeTex() {
  return document.documentElement.dataset.theme === "light" ? TEX + "earth-blue-marble.jpg" : TEX + "earth-night.jpg";
}
function globeTheme() {
  if (!G.world) return;
  const light = document.documentElement.dataset.theme === "light";
  G.world.globeImageUrl(globeTex()).atmosphereColor(light ? "#b89a62" : "#c9a96b").atmosphereAltitude(light ? 0.14 : 0.2)
    .pointColor(() => (light ? "rgba(120,88,40,.75)" : "rgba(233,205,148,.75)"));
}

async function initGlobe() {
  const el = $("globe"), wrap = $("globe-wrap");
  const world = (G.world = window.Globe({ animateIn: true, rendererConfig: { antialias: true, alpha: true, powerPreference: "high-performance" } })(el));
  world.width(wrap.clientWidth).height(wrap.clientHeight)
    .backgroundColor("rgba(0,0,0,0)")
    .globeImageUrl(globeTex()).bumpImageUrl(TEX + "earth-topology.png")
    .showAtmosphere(true)
    .pointsMerge(true).pointAltitude(0.003).pointRadius(0.11).pointLat("lat").pointLng("lon")
    .arcColor((a) => (a.trail ? "rgba(230,205,148,.35)" : ["rgba(230,205,148,.2)", "#f0d9a4", "rgba(230,205,148,.2)"]))
    .arcStroke((a) => (a.trail ? 0.35 : 0.8)).arcDashLength((a) => (a.trail ? 1 : 0.35)).arcDashGap((a) => (a.trail ? 0 : 0.25))
    .arcDashAnimateTime((a) => (a.trail ? 0 : 2400)).arcAltitudeAutoScale(0.6)
    .ringColor(() => (t) => `rgba(230,205,148,${1 - t})`).ringMaxRadius(3.2).ringPropagationSpeed(2).ringRepeatPeriod(1400)
    .labelLat("lat").labelLng("lon").labelText("code").labelSize(0.9).labelDotRadius(0.35)
    .labelColor(() => "#f4e3bd").labelResolution(2).labelAltitude(0.01)
    .onGlobeClick(({ lat, lng }) => pickNearest(lat, lng))
    .onLabelClick((l) => openPop(G.airports.find((a) => a.code === l.code) || l));
  world.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  globeTheme();
  const ctl = world.controls();
  Object.assign(ctl, { autoRotate: true, autoRotateSpeed: 0.35, enableDamping: true, dampingFactor: 0.08, minDistance: 140, maxDistance: 520, zoomSpeed: 0.6 });
  ctl.addEventListener("start", () => {
    ctl.autoRotate = false; clearTimeout(G.idle); $("globe-hint").classList.add("fade");
  });
  ctl.addEventListener("end", () => { clearTimeout(G.idle); G.idle = setTimeout(() => { ctl.autoRotate = true; }, 9000); });
  new ResizeObserver(() => world.width(wrap.clientWidth).height(wrap.clientHeight)).observe(wrap);
  $("globe-loading").remove();
  if (state.search) showRouteOnGlobe();
  try {
    G.airports = await api("/api/airports");
    world.pointsData(G.airports);
  } catch { /* globe still works without the airport layer */ }
}

function routeCoords() {
  const r = state.search?.route; if (!r) return null;
  const o = r.origin_coords[0] != null ? r.origin_coords : FALLBACK[r.origin];
  const d = r.dest_coords[0] != null ? r.dest_coords : FALLBACK[r.dest];
  return o && d ? { r, o, d } : null;
}
function showRouteOnGlobe() {
  if (!G.world) return;
  const rc = routeCoords(); if (!rc) return;
  const { r, o, d } = rc;
  const ends = [{ code: r.origin, name: r.origin_name, lat: o[0], lon: o[1] }, { code: r.dest, name: r.dest_name, lat: d[0], lon: d[1] }];
  const arc = { startLat: o[0], startLng: o[1], endLat: d[0], endLng: d[1] };
  G.world.arcsData([{ ...arc, trail: true }, arc]).ringsData(ends).labelsData(ends);
  // fly the camera to the middle of the route, zoomed to fit
  const toV = (lat, lon) => { const a = lat * Math.PI / 180, b = lon * Math.PI / 180; return [Math.cos(a) * Math.cos(b), Math.cos(a) * Math.sin(b), Math.sin(a)]; };
  const va = toV(o[0], o[1]), vb = toV(d[0], d[1]);
  const m = [va[0] + vb[0], va[1] + vb[1], va[2] + vb[2]], len = Math.hypot(...m) || 1;
  const dist = Math.acos(Math.max(-1, Math.min(1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2])));
  const lat = Math.asin(m[2] / len) * 180 / Math.PI, lng = Math.atan2(m[1], m[0]) * 180 / Math.PI;
  G.world.controls().autoRotate = false;
  G.world.pointOfView({ lat, lng, altitude: Math.max(0.75, Math.min(1.9, 0.55 + dist * 0.75)) }, 1400);
  clearTimeout(G.idle); G.idle = setTimeout(() => { G.world.controls().autoRotate = true; }, 12000);
}
function pickNearest(lat, lng) {
  if (!G.airports.length) return;
  const rad = Math.PI / 180;
  let best = null, bestD = Infinity;
  for (const a of G.airports) {
    const dLat = (a.lat - lat) * rad, dLon = (a.lon - lng) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat * rad) * Math.cos(a.lat * rad) * Math.sin(dLon / 2) ** 2;
    if (h < bestD) { bestD = h; best = a; }
  }
  const km = 2 * 6371 * Math.asin(Math.sqrt(bestD));
  const pov = G.world.pointOfView();
  if (best && km < 120 + pov.altitude * 450) openPop(best); else closePop();
}
function openPop(a) {
  G.picked = a;
  $("pop-name").textContent = `${a.name || a.city || a.code} (${a.code})`;
  $("pop-sub").textContent = [a.city && a.city !== a.name ? a.city : null, a.country].filter(Boolean).join(", ");
  $("globe-pop").hidden = false;
  G.world.controls().autoRotate = false;
}
function closePop() { $("globe-pop").hidden = true; G.picked = null; }
for (const [id, field] of [["pop-from", "from"], ["pop-to", "to"]]) {
  $(id).addEventListener("click", () => {
    const a = G.picked; if (!a) return;
    setPlace($(field), a.code, a.city || a.name, a.city && a.name !== a.city ? a.name : null);
    closePop();
    if (codeOf($("from")) && codeOf($("to")) && codeOf($("from")) !== codeOf($("to"))) runSearch();
    else toast(field === "from" ? "Now pick where you're flying to (tap another airport)" : "Now pick where you're flying from");
  });
}

/* ================= search ================= */
async function runSearch({ silent = false } = {}) {
  const o = codeOf($("from")), d = codeOf($("to")), month = $("month").value;
  if (!o || !d) { toast("Choose a city or airport for both From and To."); return; }
  if (o === d) { toast("Origin and destination must be different."); return; }
  state.route = `${o}-${d}`; state.month = month;
  history.replaceState(null, "", `?from=${o}&to=${d}&month=${month}`);
  if (!silent) ["s-cheapest", "s-typical", "s-call"].forEach((id) => $(id).classList.add("loading"));
  try {
    const s = await api(`/api/search?origin=${o}&destination=${d}&month=${month}${silent ? "&background=1" : ""}`);
    const keep = silent && s.fares.some((f) => f.depart === state.selected) ? state.selected : null;
    state.search = s; state.selected = keep;
    if (!$("from").dataset.name || $("from").dataset.name === o) setPlace($("from"), o, s.route.origin_name);
    if (!$("to").dataset.name || $("to").dataset.name === d) setPlace($("to"), d, s.route.dest_name);
    renderSearch();
    if (!silent) showRouteOnGlobe();
    loadHistory();
    if (s.summary) selectDate(keep || s.summary.cheapest.depart);
    else { $("fc-chip").textContent = "–"; $("fc-reason").textContent = "No fares found for this month yet."; curveChart && curveChart.destroy(); curveChart = null; }
  } catch (e) {
    toast(e.message);
  } finally {
    ["s-cheapest", "s-typical", "s-call"].forEach((id) => $(id).classList.remove("loading"));
  }
}
$("search").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

function renderSearch() {
  const s = state.search; if (!s) return;
  const r = s.route;
  $("t-from").textContent = r.origin_name; $("t-to").textContent = r.dest_name;
  $("t-sub").textContent = `${r.origin} → ${r.dest} · ${fmtDate(s.month + "-01", { month: "long", year: "numeric" })}` +
    (s.fares.length ? ` · ${s.fares.length} dates with fares` : "");
  const sum = s.summary, link = $("s-cheapest-link");
  $("s-cheapest").textContent = sum ? money(sum.cheapest.price) : "–";
  $("s-cheapest-date").textContent = sum ? `${fmtDay(sum.cheapest.depart)} · ${airlineName(sum.cheapest.carrier)}` : "no fares yet";
  if (sum) link.href = aviasalesUrl(r.origin, r.dest, sum.cheapest.depart, sum.cheapest.link); else link.removeAttribute("href");
  $("s-typical").textContent = sum ? money(sum.typical_price) : "–";
  $("s-call").textContent = sum ? sum.advice.action : "–";
  $("s-call").className = sum ? sum.advice.action : "";
  $("s-call-sub").textContent = sum ? (sum.advice.action === "WAIT" ? `best ~${fmtDate(sum.advice.best_buy_date)}` : "for the cheapest date") : "";
  $("hero-providers").innerHTML = sum ? providerLinks(r.origin, r.dest, sum.cheapest.depart, sum.cheapest.link, sum.cheapest.carrier)
    : providerLinks(r.origin, r.dest, `${s.month}-15`, null, null);
  $("fares-updated").textContent = s.fetched_at ? `Prices updated ${ago(s.fetched_at)}` : "";

  $("fare-body").innerHTML = s.fares.length ? s.fares.map((f) => {
    const vs = f.vs_expected_pct;
    const vsTxt = Math.abs(vs) < 2 ? `<span class="muted">typical</span>` : vs < 0 ? `<span class="down">${-vs}% below</span>` : `<span class="up">${vs}% above</span>`;
    const outlook = f.predicted_drop_pct >= 5 ? `<span class="chip gold" title="Model forecast, not a guaranteed price">▼ ${f.predicted_drop_pct}% by ${fmtDate(f.best_buy_date)}</span>` : `<span class="chip good">Book now</span>`;
    const src = aviasalesUrl(r.origin, r.dest, f.depart, f.link);
    return `<tr data-date="${esc(f.depart)}" class="${state.selected === f.depart ? "sel" : ""}">
      <td>${fmtDay(f.depart)}</td>
      <td><a class="price" href="${esc(src)}" target="_blank" rel="noopener sponsored" title="Open this exact fare on Aviasales">${money(f.price)} ↗</a></td>
      <td>${vsTxt}</td><td>${outlook}</td>
      <td title="${esc(f.carrier || "")}">${esc(airlineName(f.carrier))}${f.flight_number ? ` <small class="muted">${esc(f.carrier || "")}${esc(f.flight_number)}</small>` : ""}</td>
      <td>${f.transfers === 0 ? "Direct" : f.transfers ?? "–"}</td>
      <td><details class="book"><summary>Compare${f.offers?.length > 1 ? ` ${f.offers.length} airlines` : ""} ▾</summary><div class="menu">
        ${f.offers?.length ? `<p class="menu-h">Airlines on this date</p>${offerLinks(r, f.depart, f.offers)}<p class="menu-h">Search other sites</p>` : ""}
        ${providerLinks(r.origin, r.dest, f.depart, f.link, f.carrier, "")}</div></details></td></tr>`;
  }).join("") : `<tr><td colspan="7" class="empty">No cached fares for this month yet. Try another month, or check the booking sites above.</td></tr>`;
  renderAirlines();
}

// each airline's real offer for one date: price links to that exact offer
function offerLinks(r, date, offers) {
  return offers.map((x) => `<a href="${esc(aviasalesUrl(r.origin, r.dest, date, x.link))}" target="_blank" rel="noopener sponsored">
    <i style="background:#c9a96b"></i><span class="grow">${esc(airlineName(x.carrier))}<small> · ${x.transfers === 0 ? "direct" : `${x.transfers ?? "?"} stop`}</small></span><b>${money(x.price)}</b></a>`).join("");
}

// "Compare airlines" card: every airline's cheapest fare this month on the route
function renderAirlines() {
  const s = state.search, list = s?.airlines || [];
  $("airline-count").textContent = list.length ? `${list.length} airlines found` : "";
  $("airline-body").innerHTML = list.length ? list.map((a, i) => {
    const r = s.route, site = AIRLINES[a.carrier];
    return `<tr><td><b>${esc(airlineName(a.carrier))}</b> <small class="muted">${esc(a.carrier)}</small>${i === 0 ? ' <span class="chip good">cheapest</span>' : ""}</td>
      <td><a class="price" href="${esc(aviasalesUrl(r.origin, r.dest, a.depart, a.link))}" target="_blank" rel="noopener sponsored" title="Open this exact fare">${money(a.price)} ↗</a></td>
      <td>${fmtDay(a.depart)}</td><td>${a.transfers === 0 ? "Direct" : a.transfers ?? "–"}</td><td>${a.dates} ${a.dates === 1 ? "date" : "dates"}</td>
      <td>${site ? `<a href="https://www.${esc(site[1])}" target="_blank" rel="noopener">${esc(site[1])} ↗</a>` : '<span class="muted">–</span>'}</td></tr>`;
  }).join("") : `<tr><td colspan="6" class="empty">Search a route to compare airlines.</td></tr>`;
}
$("fare-body").addEventListener("click", (e) => {
  if (e.target.closest("details, a")) return;
  const tr = e.target.closest("tr[data-date]"); if (tr) selectDate(tr.dataset.date);
});
document.addEventListener("click", (e) => { document.querySelectorAll("details.book[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; }); });

async function selectDate(date) {
  state.selected = date;
  document.querySelectorAll("#fare-body tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.date === date));
  $("w-date").value = date;
  const fare = state.search?.fares.find((f) => f.depart === date);
  try {
    const q = new URLSearchParams({ depart: date });
    if (fare) q.set("current_price", fare.price);
    state.forecast = await api(`/api/forecast/${state.route}?${q}`);
    renderForecast();
  } catch (e) { $("fc-reason").textContent = e.message; }
}

/* ================= charts ================= */
function baseChartOptions() {
  const muted = css("--muted"), grid = css("--line");
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: muted, boxWidth: 12, boxHeight: 2 } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
    scales: { x: { ticks: { color: muted, maxTicksLimit: 7 }, grid: { color: grid } },
      y: { ticks: { color: muted, callback: (v) => money(v) }, grid: { color: grid } } },
  };
}
function gradient(ctx, color) {
  const g = ctx.createLinearGradient(0, 0, 0, 260);
  g.addColorStop(0, color + "55"); g.addColorStop(1, color + "00"); return g;
}
function renderForecast() {
  const a = state.forecast; if (!a || !window.Chart) return;
  $("fc-chip").textContent = a.action; $("fc-chip").className = "chip " + (a.action === "BUY" ? "good" : "gold");
  $("fc-reason").textContent = `${fmtDay(a.depart)}: ${money(a.current_price)} now, expected low ${money(a.expected_low)} around ${fmtDate(a.best_buy_date)}. ` +
    a.reason.replace(/about (\d+) USD/, (_, n) => `about ${money(+n)}`);
  const el = $("curve"), gold = css("--gold");
  curveChart && curveChart.destroy();
  curveChart = new Chart(el, { type: "line", options: baseChartOptions(), data: {
    labels: a.curve.map((c) => fmtDate(c.date)),
    datasets: [{ label: "Expected price if bought this day", data: a.curve.map((c) => c.price), borderColor: gold,
      backgroundColor: gradient(el.getContext("2d"), gold), fill: true, pointRadius: 0, tension: .35, borderWidth: 2 }] } });
}
async function loadHistory() {
  if (!state.route) return;
  try { state.history = await api(`/api/history/${state.route}`); } catch { state.history = []; }
  renderHistory();
}
function renderHistory() {
  if (!window.Chart) return;
  const h = state.history || [], el = $("hist");
  histChart && histChart.destroy();
  histChart = new Chart(el, { type: "line", options: baseChartOptions(), data: {
    labels: h.map((x) => fmtDate(x.observed, { month: "short", year: "2-digit" })),
    datasets: [
      { label: "Weekly low", data: h.map((x) => x.low), borderColor: css("--good"), pointRadius: 0, tension: .3, borderWidth: 1.8 },
      { label: "Weekly average", data: h.map((x) => x.avg), borderColor: css("--gold"), pointRadius: 0, tension: .3, borderWidth: 1.8 },
    ] } });
}
// after a theme switch: recolour the existing charts in place (no rebuild = no jank)
function recolorCharts() {
  const muted = css("--muted"), grid = css("--line"), gold = css("--gold"), good = css("--good");
  for (const ch of [curveChart, histChart]) {
    if (!ch) continue;
    for (const ax of ["x", "y"]) { ch.options.scales[ax].ticks.color = muted; ch.options.scales[ax].grid.color = grid; }
    ch.options.plugins.legend.labels.color = muted;
  }
  if (curveChart) { const ds = curveChart.data.datasets[0]; ds.borderColor = gold; ds.backgroundColor = gradient($("curve").getContext("2d"), gold); }
  if (histChart) { histChart.data.datasets[0].borderColor = good; histChart.data.datasets[1].borderColor = gold; }
  curveChart?.update("none"); histChart?.update("none");
}

/* ================= side lists ================= */
async function loadDrops() {
  try { state.drops = await api("/api/drops?limit=8"); } catch { state.drops = []; }
  renderDrops();
}
function renderDrops() {   // predictions: open in Skyward (they can't be bought at that price yet)
  const list = state.drops || [];
  $("drop-list").innerHTML = list.length ? list.map((x) => `<li class="hot click" data-route="${esc(x.route)}" data-date="${esc(x.depart)}" title="Model forecast: open in Skyward">
      <span><b>${esc(x.origin_name)} → ${esc(x.dest_name)}</b><small>Departs ${fmtDate(x.depart)} · best to buy ~${fmtDate(x.best_buy_date)}</small></span>
      <span class="right"><span class="price-move"><s>${money(x.price)}</s> ${money(x.predicted_price)}</span><span class="chip gold">▼ ${x.drop_pct}%</span></span></li>`).join("")
    : `<li class="empty">No significant drops predicted right now.</li>`;
}
async function loadDeals() {
  try { state.deals = await api("/api/deals?limit=10"); } catch { state.deals = []; }
  renderDeals();
}
function renderDeals() {   // real fares: link straight to the exact offer
  const list = (state.deals || []).slice().sort((a, b) => b.discount_pct - a.discount_pct || b.saving - a.saving);
  $("deal-list").innerHTML = list.length ? list.map((x) => {
    const url = aviasalesUrl(x.route.slice(0, 3), x.route.slice(4), x.depart, x.link);
    return `<li class="hot"><a class="row" href="${esc(url)}" target="_blank" rel="noopener sponsored" title="Open this exact fare">
      <span><b>${esc(x.origin_name || x.route.slice(0, 3))} → ${esc(x.dest_name || x.route.slice(4))}</b>
        <small>${fmtDay(x.depart)} · ${esc(airlineName(x.carrier))}${x.source === "synthetic" ? " · demo" : ""}</small></span>
      <span class="right"><b>${money(x.price)} ↗</b><span class="chip good">−${x.discount_pct}%</span><span class="saving">save ~${money(x.saving)}</span></span></a></li>`;
  }).join("") : `<li class="empty">No standout deals at the moment.</li>`;
}
async function loadRoutes() {
  try { state.routes = await api("/api/routes"); } catch { state.routes = []; }
  const upd = state.routes.find((r) => r.updated);
  $("popular-note").textContent = upd ? `updated ${fmtDate(upd.updated.slice(0, 10))}` : "updated weekly";
  $("route-tiles").innerHTML = state.routes.slice(0, 6).map((r) => `<button class="tile" type="button" data-route="${esc(r.code)}">
      <b>${esc(r.origin_name)} → ${esc(r.dest_name)}</b><small>${esc(r.origin)}–${esc(r.dest)}</small><span class="reason">${esc(r.reason || "")}</span></button>`).join("");
}
function openRoute(route, date) {
  const r = (state.routes || []).find((x) => x.code === route) || (state.drops || []).find((x) => x.route === route) || {};
  setPlace($("from"), route.slice(0, 3), r.origin_name || route.slice(0, 3));
  setPlace($("to"), route.slice(4), r.dest_name || route.slice(4));
  $("month").value = date ? date.slice(0, 7) : $("month").value;
  runSearch().then(() => date && selectDate(date));
  $("search-card").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("drop-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-route]"); if (li) openRoute(li.dataset.route, li.dataset.date); });
$("route-tiles").addEventListener("click", (e) => { const b = e.target.closest("[data-route]"); if (b) openRoute(b.dataset.route); });

/* ================= status ================= */
async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("live-dot").classList.toggle("on", s.live);
    $("side-status-title").textContent = s.live ? "Live prices" : "Demo data";
    $("side-status-sub").textContent = s.live ? `updated ${ago(s.last_updated)}` : "live feed not connected";
    $("hero-chip").textContent = s.live ? "● Live" : "Demo"; $("hero-chip").className = "chip " + (s.live ? "live" : "");
    $("kpi-routes").textContent = s.routes_live ?? "–";
    $("kpi-fares").textContent = new Intl.NumberFormat(undefined, { notation: "compact" }).format(s.rows_by_source?.travelpayouts || 0);
    $("s-mae").textContent = s.model_mae ? money(s.model_mae) : "–";
    $("version").textContent = "v" + s.version;
  } catch { $("side-status-title").textContent = "Offline"; }
}

/* ================= price alerts (private: tokens stay in this browser) ================= */
const myWatches = () => { try { return JSON.parse(localStorage.getItem("skyward_watches") || "[]"); } catch { return []; } };
const saveWatches = (w) => localStorage.setItem("skyward_watches", JSON.stringify(w));
async function loadAlertConfig() {
  try { state.alertCfg = await api("/api/alerts/config"); } catch { state.alertCfg = {}; }
  $("w-email-field").hidden = !state.alertCfg.email;
  $("w-tg-field").hidden = !state.alertCfg.telegram;
  if (!state.alertCfg.email && !state.alertCfg.telegram) {
    $("alert-how").textContent = "Price alerts are being set up and will be switched on soon.";
    $("watch").querySelector("button").disabled = true;
  }
}
async function loadWatches() {
  const mine = myWatches();
  const data = mine.length ? await api(`/api/watches?tokens=${encodeURIComponent(mine.map((w) => w.token).join(","))}`).catch(() => ({ watches: [], alerts: [] }))
    : { watches: [], alerts: [] };
  $("watch-list").innerHTML = data.watches.map((w) => {
    const status = [w.has_email ? (w.email_verified ? "email ✓" : "email: confirm via inbox") : null,
      w.has_telegram ? "telegram ✓" : null].filter(Boolean).join(" · ");
    const tg = safeUrl(w.telegram_link) && state.alertCfg.telegram && !w.has_telegram
      ? ` <a href="${esc(w.telegram_link)}" target="_blank" rel="noopener">Connect Telegram ↗</a>` : "";
    return `<li class="hot"><span><b>${esc(w.route)}</b><small>${fmtDay(w.depart)}${w.target_price ? " · under " + money(w.target_price) : " · any good deal"} · ${esc(status)}${tg}</small></span>
      <button class="btn ghost" type="button" data-id="${esc(w.id)}">Remove</button></li>`;
  }).join("");
  $("alert-list").innerHTML = data.alerts.map((a) => `<li><span><small>${esc(ago(a.created))}</small>${esc(a.message)}</span></li>`).join("");
}
$("watch-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-id]"); if (!b) return;
  const w = myWatches().find((x) => String(x.id) === b.dataset.id); if (!w) return;
  await api(`/api/watches/${encodeURIComponent(w.token)}`, { method: "DELETE" });
  saveWatches(myWatches().filter((x) => x.token !== w.token)); loadWatches(); toast("Alert removed");
});
$("watch").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.route) { toast("Search a route first."); return; }
  const email = state.alertCfg.email ? $("w-email").value.trim() : "";
  const telegram = state.alertCfg.telegram && $("w-tg").checked;
  if (!email && !telegram) { toast("Add your email or tick Telegram so we can reach you."); return; }
  const target = $("w-target").value ? Number($("w-target").value) / rate() : null;   // stored in USD
  try {
    const w = await api("/api/watches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      route: state.route, depart: $("w-date").value, target_price: target, email: email || null, telegram }) });
    saveWatches([...myWatches(), { id: w.id, token: w.token }]);
    loadWatches();
    if (safeUrl(w.telegram_link)) window.open(w.telegram_link, "_blank", "noopener");
    toast(w.email_confirmation_sent ? "Almost done: check your inbox and confirm the alert." :
      telegram ? "Press Start in Telegram to connect your alert." : `Watching ${state.route} on ${fmtDay(w.depart)}`);
  } catch (err) { toast(err.message); }
});

/* ================= misc ================= */
function greet() {
  const h = new Date().getHours();
  $("greeting").innerHTML = `Good ${h < 5 ? "evening" : h < 12 ? "morning" : h < 18 ? "afternoon" : "evening"}, <em>traveller</em>`;
}
function rerenderAll() { renderSearch(); renderDrops(); renderDeals(); renderForecast(); renderHistory(); loadWatches(); loadStatus(); }

const navLinks = [...document.querySelectorAll(".nav a[href^='#']")];
const spy = new IntersectionObserver((entries) => {
  entries.forEach((en) => { if (en.isIntersecting) navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id)); });
}, { rootMargin: "-40% 0px -55% 0px" });
["top", "search-card", "fares", "drops", "deals", "alerts"].forEach((id) => $(id) && spy.observe($(id)));

async function init() {
  greet();
  const p = new URLSearchParams(location.search);
  $("month").value = /^\d{4}-\d{2}$/.test(p.get("month") || "") ? p.get("month") : nextMonth();
  $("month").min = ym(new Date());
  const code = (v, dflt) => (/^[A-Za-z]{3}$/.test(v || "") ? v.toUpperCase() : dflt);
  setPlace($("from"), code(p.get("from"), "KUL"), null);
  setPlace($("to"), code(p.get("to"), "LHR"), null);
  loadGlobe();
  await loadRates();
  loadStatus(); loadRoutes(); loadDrops(); loadDeals(); loadAlertConfig().then(loadWatches);
  runSearch();
}
init();

setInterval(() => { if (!document.hidden) { loadStatus(); loadDrops(); loadDeals(); } }, 60_000);
// live prices: re-check the open route every minute (the server re-fetches anything older than ~2 min)
setInterval(() => { if (!document.hidden && state.route) runSearch({ silent: true }); }, 60_000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStatus(); });
