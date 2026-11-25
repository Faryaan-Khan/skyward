const $ = (id) => document.getElementById(id);
const api = (p, o) => fetch(p, o).then((r) => (r.status === 204 ? null : r.json()));
const money = (v) => (v == null ? "–" : "$" + Number(v).toFixed(0));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const REFRESH_MS = 60_000;
let curveChart, histChart;

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z")) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

function chart(el, existing, labels, sets) {
  existing && existing.destroy();
  return new Chart(el, {
    type: "line",
    data: { labels, datasets: sets.map((s) => ({ pointRadius: 0, tension: 0.3, borderWidth: 2, ...s })) },
    options: {
      animation: false,
      plugins: { legend: { labels: { color: "#93a0c4" } } },
      scales: { x: { ticks: { color: "#93a0c4", maxTicksLimit: 8 }, grid: { color: "#1b2547" } },
                y: { ticks: { color: "#93a0c4" }, grid: { color: "#1b2547" } } },
    },
  });
}

async function loadStatus() {
  const s = await api("/api/status");
  $("status").className = "status " + (s.live ? "on" : "off");
  $("status-text").textContent = s.live
    ? `Live prices · updated ${ago(s.last_updated)}`
    : "Demo mode: synthetic data (live feed not connected yet)";
}

async function loadRoutes() {
  const routes = await api("/api/routes");
  $("route").innerHTML = routes.map((r) => `<option>${esc(r.code)}</option>`).join("");
  const d = new Date(); d.setDate(d.getDate() + 60);
  $("depart").value = d.toISOString().slice(0, 10);
  $("route").onchange = () => { loadHistory(); loadLive(); forecast(); };
}

async function loadLive() {
  const route = $("route").value;
  const l = await api(`/api/live/${route}`);
  $("live-route").textContent = route;
  if (!l.live || !l.fares.length) {
    $("live-updated").textContent = "";
    $("live-body").innerHTML = `<tr><td colspan="7">No live fares for this route yet. They appear after the next price refresh.</td></tr>`;
    return;
  }
  $("live-updated").textContent = "Updated " + ago(l.fetched_at);
  $("live-body").innerHTML = l.fares.map((f) => {
    const ch = f.change == null ? "–" : f.change === 0 ? "=" :
      `<span class="${f.change < 0 ? "down" : "up"}">${f.change < 0 ? "▼" : "▲"} ${money(Math.abs(f.change))}</span>`;
    const vs = Math.round((1 - f.price / f.expected) * 100);
    const vsTxt = vs > 0 ? `<span class="down">${vs}% below</span>` : vs < 0 ? `<span class="up">${-vs}% above</span>` : "on par";
    const link = f.link && f.link.startsWith("https://") ? `<a href="${esc(f.link)}" target="_blank" rel="noopener sponsored">Book</a>` : "";
    return `<tr data-depart="${esc(f.depart)}" data-price="${f.price}"><td>${esc(f.depart)}</td><td><b>${money(f.price)}</b></td><td>${ch}</td><td>${vsTxt}</td>
      <td>${esc(f.carrier || "–")}${f.flight_number ? " " + esc(f.flight_number) : ""}</td><td>${f.transfers ?? "–"}</td><td>${link}</td></tr>`;
  }).join("");
  document.querySelectorAll("#live-body tr[data-depart]").forEach((tr) => {
    tr.onclick = (e) => {
      if (e.target.tagName === "A") return;
      $("depart").value = tr.dataset.depart; $("current").value = ""; forecast();
      $("forecast").scrollIntoView({ behavior: "smooth" });
    };
  });
}

async function loadHistory() {
  const h = await api(`/api/history/${$("route").value}`);
  histChart = chart($("hist"), histChart, h.map((x) => x.observed), [
    { label: "Weekly low", data: h.map((x) => x.low), borderColor: "#2ecc8f" },
    { label: "Weekly average", data: h.map((x) => x.avg), borderColor: "#5b8cff" },
  ]);
}

async function forecast(e) {
  e && e.preventDefault();
  const q = new URLSearchParams({ depart: $("depart").value });
  if ($("current").value) q.set("current_price", $("current").value);
  const a = await api(`/api/forecast/${$("route").value}?${q}`);
  if (a.detail) { if (e) alert(typeof a.detail === "string" ? a.detail : "Check the departure date"); return; }
  $("advice").classList.remove("hidden");
  $("action").textContent = a.action; $("action").className = "pill " + a.action;
  $("reason").textContent = a.reason;
  $("s-now").textContent = money(a.current_price); $("s-low").textContent = money(a.expected_low);
  $("s-day").textContent = a.best_buy_date; $("s-mae").textContent = "±" + money(a.confidence_band);
  curveChart = chart($("curve"), curveChart, a.curve.map((c) => c.date), [
    { label: "Forecast price if bought on this day", data: a.curve.map((c) => c.price), borderColor: "#ffb547", fill: false },
  ]);
}

async function loadDeals() {
  const deals = await api("/api/deals");
  $("deal-list").innerHTML = deals.map((d) => `<li><span><b>${esc(d.route)}</b> · ${esc(d.depart)}
      ${d.is_sale ? '<span class="tag">flash sale</span>' : ""}${d.source === "synthetic" ? '<span class="tag dim">demo</span>' : '<span class="tag live">live</span>'}</span>
    <span>${money(d.price)} <span class="off">-${d.discount_pct}%</span></span></li>`).join("") || "<li>No deals right now</li>";
}

// Watches are private: the server returns a manage token that only this browser keeps.
const myTokens = () => JSON.parse(localStorage.getItem("skyward_watch_tokens") || "[]");
const saveTokens = (t) => localStorage.setItem("skyward_watch_tokens", JSON.stringify(t));

async function loadWatches() {
  const toks = myTokens();
  const data = toks.length ? await api(`/api/watches?tokens=${encodeURIComponent(toks.join(","))}`) : { watches: [], alerts: [] };
  $("watch-list").innerHTML = data.watches.map((x) => {
    return `<li><span><b>${esc(x.route)}</b> · ${esc(x.depart)} ${x.target_price ? "· target " + money(x.target_price) : ""}
      ${x.has_email ? '<span class="tag">email</span>' : ""}${x.has_telegram ? '<span class="tag">telegram</span>' : ""}</span>
      <button class="ghost" data-id="${x.id}">Remove</button></li>`;
  }).join("") || "<li>No watches yet</li>";
  $("alert-list").innerHTML = data.alerts.map((x) => `<li><span>${esc(x.message)}</span><span class="tag">${esc(x.kind)}</span></li>`).join("") || "<li>No alerts yet</li>";
  // watch id -> manage token, saved when the watch was created
  const idToToken = JSON.parse(localStorage.getItem("skyward_watch_ids") || "{}");
  document.querySelectorAll("#watch-list button[data-id]").forEach((b) => {
    b.onclick = async () => {
      const tok = idToToken[b.dataset.id];
      if (!tok) return;
      await api(`/api/watches/${encodeURIComponent(tok)}`, { method: "DELETE" });
      saveTokens(myTokens().filter((t) => t !== tok));
      delete idToToken[b.dataset.id]; localStorage.setItem("skyward_watch_ids", JSON.stringify(idToToken));
      loadWatches();
    };
  });
}

$("search").onsubmit = forecast;
$("watch").onsubmit = async (e) => {
  e.preventDefault();
  const w = await api("/api/watches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
    route: $("route").value, depart: $("depart").value,
    target_price: $("w-target").value ? Number($("w-target").value) : null,
    email: $("w-email").value || null, telegram_chat_id: $("w-tg").value || null }) });
  if (!w.token) return alert(typeof w.detail === "string" ? w.detail : "Could not create the watch");
  saveTokens([...myTokens(), w.token]);
  const ids = JSON.parse(localStorage.getItem("skyward_watch_ids") || "{}"); ids[w.id] = w.token;
  localStorage.setItem("skyward_watch_ids", JSON.stringify(ids));
  loadWatches();
};

function refreshLive() {
  if (document.hidden) return; // don't poll from background tabs
  loadStatus(); loadLive(); loadDeals();
}

loadRoutes().then(() => Promise.all([loadStatus(), loadLive(), loadHistory(), forecast(), loadDeals(), loadWatches()]));
setInterval(refreshLive, REFRESH_MS);
document.addEventListener("visibilitychange", () => !document.hidden && refreshLive());
