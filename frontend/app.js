const $ = (id) => document.getElementById(id);
const api = (p, o) => fetch(p, o).then((r) => (r.status === 204 ? null : r.json()));
const money = (v) => (v == null ? "–" : "$" + Number(v).toFixed(0));
let curveChart, histChart;

function chart(el, existing, labels, sets) {
  existing && existing.destroy();
  return new Chart(el, {
    type: "line",
    data: { labels, datasets: sets.map((s) => ({ pointRadius: 0, tension: 0.3, borderWidth: 2, ...s })) },
    options: {
      plugins: { legend: { labels: { color: "#93a0c4" } } },
      scales: { x: { ticks: { color: "#93a0c4", maxTicksLimit: 8 }, grid: { color: "#1b2547" } },
                y: { ticks: { color: "#93a0c4" }, grid: { color: "#1b2547" } } },
    },
  });
}

async function loadRoutes() {
  const routes = await api("/api/routes");
  $("route").innerHTML = routes.map((r) => `<option>${r.code}</option>`).join("");
  const d = new Date(); d.setDate(d.getDate() + 90);
  $("depart").value = d.toISOString().slice(0, 10);
  $("route").onchange = loadHistory;
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
  if (a.detail) return alert(a.detail);
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
  $("deal-list").innerHTML = deals.map((d) => `<li><span><b>${d.route}</b> · ${d.depart} ${d.is_sale ? '<span class="tag">flash sale</span>' : ""}</span>
    <span>${money(d.price)} <span class="off">-${d.discount_pct}%</span></span></li>`).join("") || "<li>No deals right now</li>";
}

async function loadWatches() {
  const [w, al] = await Promise.all([api("/api/watches"), api("/api/alerts")]);
  $("watch-list").innerHTML = w.map((x) => `<li><span><b>${x.route}</b> · ${x.depart} ${x.target_price ? "· target " + money(x.target_price) : ""}</span>
    <button class="ghost" onclick="unwatch(${x.id})">Remove</button></li>`).join("") || "<li>No watches yet</li>";
  $("alert-list").innerHTML = al.map((x) => `<li><span>${x.message}</span><span class="tag">${x.kind}</span></li>`).join("") || "<li>No alerts yet</li>";
}

async function unwatch(id) { await api(`/api/watches/${id}`, { method: "DELETE" }); loadWatches(); }

$("search").onsubmit = forecast;
$("watch").onsubmit = async (e) => {
  e.preventDefault();
  await api("/api/watches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
    route: $("route").value, depart: $("depart").value,
    target_price: $("w-target").value ? Number($("w-target").value) : null,
    email: $("w-email").value || null, telegram_chat_id: $("w-tg").value || null }) });
  loadWatches();
};
$("check").onclick = async () => { await api("/api/watches/check", { method: "POST" }); loadWatches(); };

loadRoutes().then(() => Promise.all([loadHistory(), forecast(), loadDeals(), loadWatches()]));
