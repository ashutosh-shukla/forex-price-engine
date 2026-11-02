mountNav("console.html");
const $ = id => document.getElementById(id);
const csv = v => v.split(",").map(x => x.trim()).filter(Boolean);

/* every endpoint the engine exposes, with default parameters */
const EPS = [
  { m: "GET", p: "/health/live", auth: "none", params: [], note: "Process is up" },
  { m: "GET", p: "/health/ready", auth: "none", params: [], note: "Database + engine + stream health" },
  { m: "GET", p: "/v1/pairs", auth: "client", params: [], note: "Active pairs and timeframes" },
  { m: "GET", p: "/v1/price/latest", auth: "client", params: [["symbols", "EUR_USD,GBP_USD"]], note: "Latest bid / ask / mid" },
  { m: "GET", p: "/v1/candles", auth: "client", params: [["symbol", "EUR_USD"], ["tf", "5m"], ["limit", "5"], ["metrics", "full"], ["from", ""], ["to", ""]], note: "Candles (all timeframes). Empty from/to = latest N" },
  { m: "GET", p: "/v1/candles/forming", auth: "client", params: [["symbol", "EUR_USD"], ["tf", "5m"], ["metrics", "full"]], note: "The unfinished candle" },
  { m: "GET", p: "/v1/metrics/summary", auth: "client", params: [["symbol", "EUR_USD"], ["tf", "5m"], ["window", "20"]], note: "Order-flow summary over N candles" },
  { m: "GET", p: "/v1/ticks", auth: "client", params: [["symbol", "EUR_USD"], ["limit", "20"]], note: "Raw ticks (needs STORE_TICKS=true)" },
  { m: "GET", p: "/v1/status", auth: "client", params: [], note: "Feed health per pair" },
  { m: "POST", p: "/v1/ws-token", auth: "client", body: '{"symbols":["EUR_USD"],"timeframes":["1m","5m"],"metrics":true,"ttl_seconds":60}', params: [], note: "Short-lived websocket token" },
  { m: "GET", p: "/v1/admin/clients", auth: "admin", params: [], note: "List API clients" },
  { m: "GET", p: "/v1/admin/pairs", auth: "admin", params: [], note: "List pairs" },
];
let cur = null;

function renderEps() {
  $("eplist").innerHTML = EPS.map((e, i) => `<div class="ep" data-i="${i}"><span class="m ${e.m}">${e.m}</span><span class="mono">${e.p}</span><span class="muted" style="margin-left:auto;font-size:12px">${esc(e.note)}</span></div>`).join("");
  document.querySelectorAll(".ep").forEach(el => el.onclick = () => pick(+el.dataset.i));
}
function pick(i) {
  cur = EPS[i]; document.querySelectorAll(".ep").forEach((el, k) => el.classList.toggle("on", k === i));
  $("eptitle").textContent = cur.m + " " + cur.p;
  $("epform").innerHTML = cur.params.map(([k, v]) => `<label>${k}<input data-p="${k}" value="${esc(v)}"></label>`).join("") +
    (cur.body ? `<label style="width:100%">body (JSON)<textarea id="epbody">${esc(cur.body)}</textarea></label>` : "") + (!cur.params.length && !cur.body ? '<span class="muted">no parameters</span>' : "");
  $("epsend").disabled = false;
}
$("epsend").onclick = async () => {
  const q = new URLSearchParams(); document.querySelectorAll("#epform input[data-p]").forEach(el => { if (el.value) q.set(el.dataset.p, el.value); });
  const path = cur.p + (q.toString() ? "?" + q : "");
  try {
    const r = await request(path, { method: cur.m, body: cur.body ? JSON.parse($("epbody").value) : null, admin: cur.auth === "admin", auth: cur.auth !== "none" });
    $("epmeta").textContent = `${r.status} OK · ${r.ms} ms`; $("epout").textContent = pretty(r.data);
  } catch (e) { $("epmeta").textContent = `${e.status || "error"} · ${e.ms || ""} ms`; $("epout").textContent = pretty(e.data || e.message); }
};

/* ---------- status ---------- */
async function refreshStatus() {
  try { const h = (await request("/health/ready", { auth: false }).catch(e => ({ data: e.data }))).data; $("health").innerHTML = Object.entries(h).map(([k, v]) => `<b>${k}</b>: ${esc(v)}`).join(" &nbsp;·&nbsp; "); } catch (e) {}
  try {
    const s = await api("/v1/status"); const rows = Object.entries(s.pairs || {});
    $("feed").innerHTML = `<table><thead><tr><th>pair</th><th>last tick (IST)</th><th>age (s)</th></tr></thead><tbody>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${fmtTime(v.last_tick_ms)}</td><td class="${v.age_s > 120 ? "down" : ""}">${v.age_s ?? "–"}</td></tr>`).join("")}</tbody></table>`;
    const { pairs, ...rest } = s; $("statusjson").textContent = pretty(rest);
  } catch (e) { $("feed").textContent = e.message; }
}

/* ---------- websocket tester ---------- */
let ws = null; const log = [];
const wlog = t => { log.unshift(new Date().toLocaleTimeString() + "  " + t); log.length = Math.min(log.length, 200); $("wslog").textContent = log.join("\n"); };
$("wsconn").onclick = async () => {
  try {
    const { token } = await api("/v1/ws-token", { method: "POST", body: { metrics: true, ttl_seconds: 60 } });
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/v1/stream");
    ws.onopen = () => { wlog("socket open, sending token"); ws.send(JSON.stringify({ op: "auth", token })); $("wsstat").textContent = "connected"; };
    ws.onmessage = e => wlog(e.data); ws.onclose = ev => { wlog("closed (code " + ev.code + ")"); $("wsstat").textContent = "closed"; };
  } catch (e) { wlog("token error: " + e.message); }
};
$("wsdisc").onclick = () => ws && ws.close();
const wsSend = o => { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(o)); wlog("> " + JSON.stringify(o)); } else wlog("not connected"); };
$("wssub").onclick = () => wsSend({ op: "subscribe", channel: $("wsch").value, symbols: csv($("wssym").value), tfs: csv($("wstf").value) });
$("wsunsub").onclick = () => wsSend({ op: "unsubscribe", channel: $("wsch").value, symbols: csv($("wssym").value), tfs: csv($("wstf").value) });
$("wsping").onclick = () => wsSend({ op: "ping" });

/* ---------- admin ---------- */
const admin = (path, method = "GET", body = null) => api("/v1/admin" + path, { method, body, admin: true });
async function refreshAdmin() {
  try {
    const c = (await admin("/clients")).clients;
    $("clients").innerHTML = `<table><thead><tr><th>name</th><th>id</th><th>pairs</th><th>metrics</th><th>ws</th><th>req/min</th><th>active</th><th></th></tr></thead><tbody>${c.map(x => `<tr><td>${esc(x.name)}</td><td class="mono">${x.client_id}</td><td>${x.allowed_symbols ? x.allowed_symbols.length : "all"}</td><td>${x.allow_metrics}</td><td>${x.allow_ws}</td><td>${x.rate_limit_per_min || "∞"}</td><td>${x.active}</td>
      <td><button class="ghost" data-a="tog" data-id="${x.client_id}" data-v="${x.active}">${x.active ? "disable" : "enable"}</button> <button class="ghost" data-a="rot" data-id="${x.client_id}">new key</button></td></tr>`).join("")}</tbody></table>`;
    const p = (await admin("/pairs")).pairs;
    $("pairs").innerHTML = `<table><thead><tr><th>pair</th><th>decimals</th><th>active</th><th></th></tr></thead><tbody>${p.map(x => `<tr><td>${x.symbol}</td><td>${x.price_decimals}</td><td>${x.active}</td><td><button class="ghost" data-a="pair" data-s="${x.symbol}" data-v="${x.active}">${x.active ? "deactivate" : "activate"}</button></td></tr>`).join("")}</tbody></table>`;
  } catch (e) { $("clients").textContent = e.message + " (check the admin key on the System status tab)"; }
}
document.addEventListener("click", async ev => {
  const b = ev.target.closest("button[data-a]"); if (!b) return;
  try {
    if (b.dataset.a === "tog") await admin("/clients/" + b.dataset.id, "PATCH", { active: b.dataset.v !== "true" });
    if (b.dataset.a === "rot") { const r = await admin(`/clients/${b.dataset.id}/rotate-key`, "POST"); $("c-out").hidden = false; $("c-out").textContent = pretty(r); }
    if (b.dataset.a === "pair") await admin("/pairs/" + b.dataset.s, "PATCH", { active: b.dataset.v !== "true" });
    refreshAdmin();
  } catch (e) { toast(e.message); }
});
$("c-create").onclick = async () => {
  try {
    const r = await admin("/clients", "POST", { name: $("c-name").value, allowed_symbols: csv($("c-syms").value).length ? csv($("c-syms").value) : null, allowed_timeframes: csv($("c-tfs").value).length ? csv($("c-tfs").value) : null,
      allowed_ips: csv($("c-ips").value).length ? csv($("c-ips").value) : null, rate_limit_per_min: +$("c-rate").value, allow_metrics: $("c-met").checked, allow_ws: $("c-ws").checked, allow_ticks: $("c-ticks").checked });
    $("c-out").hidden = false; $("c-out").textContent = pretty(r); refreshAdmin();
  } catch (e) { toast(e.message); }
};
$("p-add").onclick = async () => { try { await admin("/pairs", "POST", { symbol: $("p-sym").value.toUpperCase(), price_decimals: +$("p-dec").value }); toast("Pair added. History downloads automatically."); refreshAdmin(); } catch (e) { toast(e.message); } };
$("b-go").onclick = async () => { try { await admin("/backfill", "POST", { symbol: $("b-sym").value.toUpperCase(), days: +$("b-days").value }); toast("History download started. See logs/main.log"); } catch (e) { toast(e.message); } };
$("stop").onclick = async () => { if (confirm("Stop the engine? Restart it with start.bat")) { try { await admin("/shutdown", "POST"); toast("Engine is stopping."); } catch (e) { toast(e.message); } } };

/* ---------- keys + tabs ---------- */
$("k-save").onclick = () => { localStorage.setItem("pe_api_key", $("k-api").value.trim()); localStorage.setItem("pe_admin_key", $("k-admin").value.trim()); App.apiKey = $("k-api").value.trim(); App.adminKey = $("k-admin").value.trim(); toast("Saved in this browser"); refreshStatus(); };
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b));
  ["status", "explorer", "ws", "admin"].forEach(t => $("t-" + t).hidden = t !== b.dataset.t);
  if (b.dataset.t === "admin") refreshAdmin();
});
(async function () {
  await loadConfig(); $("k-api").value = App.apiKey; $("k-admin").value = App.adminKey; renderEps(); await checkApi(); setInterval(checkApi, 10000);
  refreshStatus(); setInterval(refreshStatus, 3000);
})();
