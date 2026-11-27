/* Skyward v2 dashboard: vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`);
  return body;
};
const toast = (msg) => { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3200); };
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const state = { from: null, to: null, month: null, route: null, search: null, selected: null, rates: { USD: 1 }, currency: "USD" };
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
  const opt = (c) => `<option value="${c}"${c === state.currency ? " selected" : ""}>${c}</option>`;
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

/* ================= theme: sun <-> moon ================= */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("skyward_theme", t);
  const btn = $("theme-toggle");
  btn.setAttribute("aria-label", t === "dark" ? "Switch to light mode" : "Switch to dark mode");
  btn.setAttribute("aria-pressed", String(t === "light"));
}
$("theme-toggle").addEventListener("click", (e) => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  const r = e.currentTarget.getBoundingClientRect();
  document.documentElement.style.setProperty("--vt-x", `${((r.left + r.width / 2) / innerWidth) * 100}%`);
  document.documentElement.style.setProperty("--vt-y", `${((r.top + r.height / 2) / innerHeight) * 100}%`);
  const swap = () => { applyTheme(next); rerenderCharts(); };
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) document.startViewTransition(swap);
  else swap();
});
applyTheme(document.documentElement.dataset.theme || "dark");

/* ================= booking providers ================= */
const PROVIDERS = [
  { name: "Aviasales", color: "#1ea7fd", url: (o, d, date, link) => link || `https://www.aviasales.com/search/${o}${date.slice(8, 10)}${date.slice(5, 7)}${d}1` },
  { name: "Google Flights", color: "#4285f4", url: (o, d, date) => `https://www.google.com/travel/flights?q=${encodeURIComponent(`one way flights from ${o} to ${d} on ${date}`)}&curr=${state.currency}` },
  { name: "Skyscanner", color: "#0770e3", url: (o, d, date) => `https://www.skyscanner.net/transport/flights/${o.toLowerCase()}/${d.toLowerCase()}/${date.slice(2, 4)}${date.slice(5, 7)}${date.slice(8, 10)}/?adultsv2=1&currency=${state.currency}` },
  { name: "Kayak", color: "#ff690f", url: (o, d, date) => `https://www.kayak.com/flights/${o}-${d}/${date}?sort=price_a` },
  { name: "Expedia", color: "#fddb32", url: (o, d, date) => `https://www.expedia.com/Flights-Search?trip=oneway&leg1=from:${o},to:${d},departure:${date.slice(5, 7)}/${date.slice(8, 10)}/${date.slice(0, 4)}TANYT&passengers=adults:1&mode=search` },
  { name: "Momondo", color: "#b146c2", url: (o, d, date) => `https://www.momondo.com/flight-search/${o}-${d}/${date}?sort=price_a` },
];
const providerLinks = (o, d, date, link, cls = "provider") => PROVIDERS.map((p) =>
  `<a class="${cls}" href="${esc(p.url(o, d, date, link))}" target="_blank" rel="noopener${p.name === "Aviasales" ? " sponsored" : ""}"><i style="background:${p.color}"></i>${p.name}</a>`).join("");

/* ================= place autocomplete ================= */
function autocomplete(input) {
  const list = input.parentElement.querySelector(".suggest");
  let items = [], active = -1, timer;
  const close = () => { list.classList.remove("open"); active = -1; };
  const choose = (p) => {
    input.value = `${p.city}${p.type === "airport" ? ` · ${p.name}` : ""} (${p.code})`;
    input.dataset.code = p.code; input.dataset.name = p.city;
    state[input.id] = { code: p.code, name: p.city };
    close();
  };
  const render = () => {
    list.innerHTML = items.map((p, i) => `<li role="option" data-i="${i}" class="${i === active ? "active" : ""}">
      <span><b>${esc(p.type === "airport" ? p.name : p.city)}</b><small>${esc(p.type === "airport" ? `${p.city}, ${p.country}` : `${p.country} · all airports`)}</small></span>
      <span class="code">${esc(p.code)}</span></li>`).join("");
    list.classList.toggle("open", items.length > 0);
  };
  input.addEventListener("input", () => {
    delete input.dataset.code; state[input.id] = null;
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
function setPlace(input, code, name) {
  input.dataset.code = code; input.dataset.name = name || code;
  input.value = name && name !== code ? `${name} (${code})` : code;
  state[input.id] = { code, name: name || code };
}

$("swap").addEventListener("click", () => {
  const a = { code: $("from").dataset.code, name: $("from").dataset.name, v: $("from").value };
  const b = { code: $("to").dataset.code, name: $("to").dataset.name, v: $("to").value };
  if (b.code) setPlace($("from"), b.code, b.name); else { $("from").value = b.v; delete $("from").dataset.code; }
  if (a.code) setPlace($("to"), a.code, a.name); else { $("to").value = a.v; delete $("to").dataset.code; }
});

/* ================= globe ================= */
const RAD = Math.PI / 180;
const FALLBACK = { KUL: [2.75, 101.71], SIN: [1.36, 103.99], BKK: [13.69, 100.75], NRT: [35.77, 140.39], LHR: [51.47, -0.45],
  SYD: [-33.94, 151.18], HKG: [22.31, 113.92], JFK: [40.64, -73.78], LAX: [33.94, -118.41], CDG: [49.01, 2.55] };
// major cities (lat, lon): drawn as clusters of "city lights"
const CITIES = [[40.7,-74],[34,-118.2],[41.9,-87.6],[29.8,-95.4],[25.8,-80.2],[43.7,-79.4],[45.5,-73.6],[19.4,-99.1],[-23.5,-46.6],[-22.9,-43.2],
  [-34.6,-58.4],[4.7,-74.1],[-12,-77],[51.5,-0.1],[48.9,2.4],[52.5,13.4],[40.4,-3.7],[41.9,12.5],[52.4,4.9],[50.1,8.7],[55.8,37.6],
  [59.3,18.1],[41,29],[30,31.2],[6.5,3.4],[-1.3,36.8],[-26.2,28],[33.9,-6.8],[25.2,55.3],[24.7,46.7],[25.3,51.5],[35.7,51.4],
  [24.9,67],[28.6,77.2],[19.1,72.9],[13,77.6],[13.1,80.3],[22.6,88.4],[23.8,90.4],[27.7,85.3],[6.9,79.9],[13.8,100.5],[21,105.8],
  [10.8,106.7],[3.1,101.7],[1.35,103.8],[-6.2,106.8],[-7.3,112.7],[14.6,121],[22.3,114.2],[23.1,113.3],[31.2,121.5],[39.9,116.4],
  [30.6,114.3],[29.6,106.5],[34.3,108.9],[37.6,127],[35.7,139.7],[34.7,135.5],[25,121.5],[-33.9,151.2],[-37.8,145],[-27.5,153],
  [-31.9,115.9],[-36.8,174.8],[60.2,24.9],[50.4,30.5],[52.2,21],[47.5,19],[38,23.7],[44.4,26.1],[53.3,-6.3],[57.7,12],[45.5,9.2],
  [43.3,5.4],[38.7,-9.1],[36.8,10.2],[9,38.7],[5.6,-0.2],[14.7,-17.4],[-4.3,15.3],[-8.8,13.2],[33.5,36.3],[31.8,35.2],[40.4,49.9],
  [41.3,69.3],[43.2,76.9],[47.9,106.9],[49.3,-123.1],[47.6,-122.3],[37.8,-122.4],[39.7,-105],[33.4,-112],[32.7,-117.2],[38.9,-77],
  [42.4,-71.1],[39.9,-75.2],[33.7,-84.4],[44.9,-93.3],[18.5,-69.9],[23.1,-82.4],[10.5,-66.9],[-33.5,-70.7],[-15.8,-47.9],[-30,-51.2]];
let rng = 42; const rand = () => ((rng = (rng * 16807) % 2147483647) / 2147483647);
const LIGHTS = CITIES.flatMap(([la, lo]) => Array.from({ length: 7 }, (_, i) => [la + (rand() - .5) * (i ? 3.2 : 0), lo + (rand() - .5) * (i ? 4 : 0), i ? .25 + rand() * .5 : 1]));

function vec(lat, lon) { return [Math.cos(lat * RAD) * Math.cos(lon * RAD), Math.cos(lat * RAD) * Math.sin(lon * RAD), Math.sin(lat * RAD)]; }
function slerp(a, b, t) {
  const dot = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])), w = Math.acos(dot);
  if (w < 1e-6) return a;
  const s = Math.sin(w), k1 = Math.sin((1 - t) * w) / s, k2 = Math.sin(t * w) / s;
  return [a[0] * k1 + b[0] * k2, a[1] * k1 + b[1] * k2, a[2] * k1 + b[2] * k2];
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(...a) || 1; return a.map((x) => x / l); };
const comb = (a, ka, b, kb) => [a[0] * ka + b[0] * kb, a[1] * ka + b[1] * kb, a[2] * ka + b[2] * kb];

function drawGlobe(o, d) {
  const svg = $("globe"), W = 800, H = 340, ARC_Y = 250, PEAK_Y = 60;
  const A = o && o[0] != null ? o : null, B = d && d[0] != null ? d : null;
  // Camera: look at the sphere so the route lies flat across the view, with the horizon curving above it.
  let c, u, e, R = 520, dist = 0, lift = 0;
  if (A && B) {
    const va = vec(A[0], A[1]), vb = vec(B[0], B[1]);
    dist = Math.acos(Math.max(-1, Math.min(1, dot(va, vb))));
    const m = norm(slerp(va, vb, 0.5));
    let n = cross(va, vb); n = Math.hypot(...n) < 1e-6 ? norm(cross(m, [0, 0, 1])) : norm(n);
    if (dot(n, [0, 0, 1]) < 0) n = n.map((x) => -x);              // keep north roughly "up"
    R = Math.max(330, Math.min(6000, 260 / Math.sin(Math.max(dist / 2, 0.004))));
    const sinT = Math.min(0.985, (R + 40 - ARC_Y) / (R * Math.cos(Math.min(dist / 2, 1.45))));
    const cosT = Math.sqrt(1 - sinT * sinT);
    c = comb(m, cosT, n, -sinT); u = comb(n, cosT, m, sinT); e = norm(cross(u, c));
    if (dot(e, vb) < dot(e, va)) e = e.map((x) => -x);           // origin on the left, destination on the right
    const midY = R + 40 - R * sinT;                                // where the great circle itself peaks
    lift = Math.max(14, Math.min(90, midY - PEAK_Y)) / (R * sinT);  // extra height for the flight arc
  } else {
    const m = vec(15, 100);
    c = comb(m, Math.cos(0.9), [0, 0, 1], -Math.sin(0.9)); c = norm(c);
    u = norm(cross(cross(c, [0, 0, 1]), c)); e = norm(cross(u, c));
  }
  const cx = W / 2, cy = R + 40;
  const projV = (p, k = 1) => ({ x: cx + R * k * dot(p, e), y: cy - R * k * dot(p, u), vis: dot(p, c) > -0.02 });
  const proj = (lat, lon, k = 1) => projV(vec(lat, lon), k);
  const line = (pts) => { let dstr = "", pen = false; for (const p of pts) { if (!p.vis || p.y > H + 60) { pen = false; continue; } dstr += `${pen ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`; pen = true; } return dstr; };
  let grat = "";
  for (let lon = -180; lon < 180; lon += 15) grat += line(Array.from({ length: 61 }, (_, i) => proj(-90 + i * 3, lon)));
  for (let lat = -75; lat <= 75; lat += 15) grat += line(Array.from({ length: 121 }, (_, i) => proj(lat, -180 + i * 3)));
  const lights = LIGHTS.map(([la, lo, w]) => { const p = proj(la, lo); return p.vis && p.y < H + 5 ? `<circle class="light" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${(0.6 + w * 1.1).toFixed(2)}" opacity="${(0.25 + w * 0.6).toFixed(2)}"/>` : ""; }).join("");

  let arc = "", pins = "";
  if (A && B) {
    const va = vec(A[0], A[1]), vb = vec(B[0], B[1]);
    const pts = Array.from({ length: 81 }, (_, i) => { const t = i / 80; return projV(norm(slerp(va, vb, t)), 1 + lift * Math.sin(Math.PI * t)); });
    const path = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");
    const pa = proj(A[0], A[1]), pb = proj(B[0], B[1]);
    arc = `<path class="arc-trail" d="${path}"/><path id="arc-path" class="arc" d="${path}" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1">
      <animate attributeName="stroke-dashoffset" from="1" to="0" dur="1.4s" fill="freeze" calcMode="spline" keySplines=".4 0 .2 1"/></path>
      <g class="jet"><path d="M10 0 L-6 -5 L-3 0 L-6 5 Z"/><animateMotion dur="7s" begin="0.8s" repeatCount="indefinite" rotate="auto" keyPoints="0;1" keyTimes="0;1" calcMode="linear"><mpath href="#arc-path"/></animateMotion></g>`;
    const pin = (p, code, anchor) => `<circle class="pin-ring" cx="${p.x}" cy="${p.y}" r="9"/><circle class="pin" cx="${p.x}" cy="${p.y}" r="3.5"/>
      <text class="label" x="${p.x}" y="${p.y - 16}" text-anchor="${anchor}">${esc(code)}</text>`;
    pins = pin(pa, state.route?.slice(0, 3) || "", "middle") + pin(pb, state.route?.slice(4) || "", "middle");
  }
  svg.innerHTML = `<defs>
      <radialGradient id="sphere-fill" cx="50%" cy="0%" r="75%"><stop offset="0%" style="stop-color:var(--ocean-1)"/><stop offset="100%" style="stop-color:var(--ocean-2)"/></radialGradient>
      <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    <circle class="sphere" cx="${cx}" cy="${cy}" r="${R}"/>
    <path class="grat" d="${grat}"/>${lights}${arc}${pins}`;
}

/* ================= search ================= */
async function runSearch({ silent = false } = {}) {
  const o = codeOf($("from")), d = codeOf($("to")), month = $("month").value;
  if (!o || !d) { toast("Choose a city or airport from the suggestions for both From and To."); return; }
  if (o === d) { toast("Origin and destination must be different."); return; }
  state.route = `${o}-${d}`; state.month = month;
  history.replaceState(null, "", `?from=${o}&to=${d}&month=${month}`);
  if (!silent) ["s-cheapest", "s-typical", "s-call"].forEach((id) => $(id).classList.add("loading"));
  try {
    const s = await api(`/api/search?origin=${o}&destination=${d}&month=${month}`);
    const keep = silent && s.fares.some((f) => f.depart === state.selected) ? state.selected : null;
    state.search = s; state.selected = keep;
    if (!$("from").dataset.name || $("from").dataset.name === o) setPlace($("from"), o, s.route.origin_name);
    if (!$("to").dataset.name || $("to").dataset.name === d) setPlace($("to"), d, s.route.dest_name);
    renderSearch();
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
  const sum = s.summary;
  $("s-cheapest").textContent = sum ? money(sum.cheapest.price) : "–";
  $("s-cheapest-date").textContent = sum ? fmtDay(sum.cheapest.depart) : "no fares yet";
  $("s-typical").textContent = sum ? money(sum.typical_price) : "–";
  $("s-call").textContent = sum ? sum.advice.action : "–";
  $("s-call").className = sum ? sum.advice.action : "";
  $("s-call-sub").textContent = sum ? (sum.advice.action === "WAIT" ? `best ~${fmtDate(sum.advice.best_buy_date)}` : "for the cheapest date") : "";
  $("hero-providers").innerHTML = sum ? providerLinks(r.origin, r.dest, sum.cheapest.depart, sum.cheapest.link)
    : providerLinks(r.origin, r.dest, `${s.month}-15`, null);
  drawGlobe(r.origin_coords[0] != null ? r.origin_coords : FALLBACK[r.origin], r.dest_coords[0] != null ? r.dest_coords : FALLBACK[r.dest]);
  $("fares-updated").textContent = s.fetched_at ? `Prices updated ${ago(s.fetched_at)}` : "";

  $("fare-body").innerHTML = s.fares.length ? s.fares.map((f) => {
    const vs = f.vs_expected_pct;
    const vsTxt = Math.abs(vs) < 2 ? `<span class="muted">typical</span>` : vs < 0 ? `<span class="down">${-vs}% below</span>` : `<span class="up">${vs}% above</span>`;
    const outlook = f.predicted_drop_pct >= 5 ? `<span class="chip gold">▼ ${f.predicted_drop_pct}% by ${fmtDate(f.best_buy_date)}</span>` : `<span class="chip good">Book now</span>`;
    return `<tr data-date="${f.depart}" class="${state.selected === f.depart ? "sel" : ""}">
      <td>${fmtDay(f.depart)}</td><td><b>${money(f.price)}</b></td><td>${vsTxt}</td><td>${outlook}</td>
      <td>${esc(f.carrier || "–")}${f.flight_number ? " " + esc(f.flight_number) : ""}</td><td>${f.transfers === 0 ? "Direct" : f.transfers ?? "–"}</td>
      <td><details class="book"><summary>Compare ▾</summary><div class="menu">${providerLinks(r.origin, r.dest, f.depart, f.link, "")}</div></details></td></tr>`;
  }).join("") : `<tr><td colspan="7" class="empty">No cached fares for this month yet. Try another month, or check the booking sites above.</td></tr>`;
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
    responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
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
      backgroundColor: gradient(el.getContext("2d"), gold.startsWith("#") ? gold : "#c9a96b"), fill: true, pointRadius: 0, tension: .35, borderWidth: 2 }] } });
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
function rerenderCharts() { renderForecast(); renderHistory(); }

/* ================= side lists ================= */
async function loadDrops() {
  try { state.drops = await api("/api/drops?limit=8"); } catch { state.drops = []; }
  renderDrops();
}
function renderDrops() {
  const list = state.drops || [];
  $("drop-list").innerHTML = list.length ? list.map((x) => `<li class="hot click" data-route="${esc(x.route)}" data-date="${esc(x.depart)}">
      <span><b>${esc(x.origin_name)} → ${esc(x.dest_name)}</b><small>Departs ${fmtDate(x.depart)} · best to buy ~${fmtDate(x.best_buy_date)}</small></span>
      <span class="right"><span class="price-move"><s>${money(x.price)}</s> ${money(x.predicted_price)}</span><span class="chip gold">▼ ${x.drop_pct}%</span></span></li>`).join("")
    : `<li class="empty">No significant drops predicted right now.</li>`;
}
async function loadDeals() {
  try { state.deals = await api("/api/deals?limit=8"); } catch { state.deals = []; }
  renderDeals();
}
function renderDeals() {
  const list = state.deals || [];
  $("deal-list").innerHTML = list.length ? list.map((x) => `<li class="hot click" data-route="${esc(x.route)}" data-date="${esc(x.depart)}">
      <span><b>${esc(x.origin_name || x.route.slice(0, 3))} → ${esc(x.dest_name || x.route.slice(4))}</b><small>${fmtDay(x.depart)}${x.carrier ? " · " + esc(x.carrier) : ""}${x.source === "synthetic" ? " · demo" : ""}</small></span>
      <span class="right"><b>${money(x.price)}</b><span class="chip good">−${x.discount_pct}%</span></span></li>`).join("")
    : `<li class="empty">No standout deals at the moment.</li>`;
}
async function loadRoutes() {
  try { state.routes = await api("/api/routes"); } catch { state.routes = []; }
  $("route-tiles").innerHTML = state.routes.slice(0, 6).map((r) => `<button class="tile" type="button" data-route="${esc(r.code)}">
      <b>${esc(r.origin_name)} → ${esc(r.dest_name)}</b><small>${esc(r.origin)}–${esc(r.dest)}${r.searches ? ` · ${r.searches} searches` : ""}</small></button>`).join("");
}
function openRoute(route, date) {
  const r = (state.routes || []).find((x) => x.code === route) || (state.drops || []).find((x) => x.route === route) || {};
  setPlace($("from"), route.slice(0, 3), r.origin_name || route.slice(0, 3));
  setPlace($("to"), route.slice(4), r.dest_name || route.slice(4));
  $("month").value = date ? date.slice(0, 7) : $("month").value;
  runSearch().then(() => date && selectDate(date));
  $("search-card").scrollIntoView({ behavior: "smooth", block: "start" });
}
for (const id of ["drop-list", "deal-list"]) $(id).addEventListener("click", (e) => { const li = e.target.closest("li[data-route]"); if (li) openRoute(li.dataset.route, li.dataset.date); });
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
const myWatches = () => JSON.parse(localStorage.getItem("skyward_watches") || "[]");   // [{id, token}]
const saveWatches = (w) => localStorage.setItem("skyward_watches", JSON.stringify(w));
async function loadWatches() {
  const mine = myWatches();
  const data = mine.length ? await api(`/api/watches?tokens=${encodeURIComponent(mine.map((w) => w.token).join(","))}`).catch(() => ({ watches: [], alerts: [] }))
    : { watches: [], alerts: [] };
  $("watch-list").innerHTML = data.watches.map((w) => `<li class="hot"><span><b>${esc(w.route)}</b><small>${fmtDay(w.depart)}${w.target_price ? " · target " + money(w.target_price) : ""}${w.has_email ? " · email" : ""}${w.has_telegram ? " · telegram" : ""}</small></span>
      <button class="btn ghost" type="button" data-id="${w.id}">Remove</button></li>`).join("");
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
  const target = $("w-target").value ? Number($("w-target").value) / rate() : null;   // stored in USD
  try {
    const w = await api("/api/watches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      route: state.route, depart: $("w-date").value, target_price: target,
      email: $("w-email").value || null, telegram_chat_id: $("w-tg").value || null }) });
    saveWatches([...myWatches(), { id: w.id, token: w.token }]);
    loadWatches(); toast(`Watching ${state.route} on ${fmtDay(w.depart)}`);
  } catch (err) { toast(err.message); }
});

/* ================= misc ================= */
function greet() {
  const h = new Date().getHours();
  $("greeting").innerHTML = `Good ${h < 5 ? "evening" : h < 12 ? "morning" : h < 18 ? "afternoon" : "evening"}, <em>traveller</em>`;
}
function rerenderAll() { renderSearch(); renderDrops(); renderDeals(); renderForecast(); renderHistory(); loadWatches(); loadStatus(); }

// highlight the nav item for the section in view
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
  setPlace($("from"), (p.get("from") || "KUL").toUpperCase().slice(0, 3), null);
  setPlace($("to"), (p.get("to") || "LHR").toUpperCase().slice(0, 3), null);
  drawGlobe(null, null);
  await loadRates();
  loadStatus(); loadRoutes(); loadDrops(); loadDeals(); loadWatches();
  runSearch();
}
// Chart.js is loaded with defer before this file, so it's ready here
init();

setInterval(() => { if (!document.hidden) { loadStatus(); loadDrops(); loadDeals(); } }, 60_000);
setInterval(() => { if (!document.hidden && state.route) runSearch({ silent: true }); }, 5 * 60_000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStatus(); });
