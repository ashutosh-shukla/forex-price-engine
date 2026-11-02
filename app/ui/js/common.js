/* Shared helpers: config, API calls, websocket client, formatting. Plain JavaScript, no libraries. */
const App = { apiKey: "", adminKey: "", liveTfs: ["1m", "5m", "15m", "1h", "4h"], storeTicks: false };

const ALL_TFS = ["1m","2m","3m","5m","10m","15m","30m","1h","2h","3h","4h","6h","8h","12h","1d"];

const HELP = {
  open:"First mid price of the candle", high:"Highest mid price", low:"Lowest mid price", close:"Last mid price",
  volume:"Tick count: number of price updates received (volume proxy, not traded volume)",
  tick_count:"Price updates received in the candle", up_ticks:"Updates where mid went up", down_ticks:"Updates where mid went down",
  move_up:"Sum of all upward mid moves", move_down:"Sum of all downward mid moves", path_length:"move_up + move_down: total distance travelled",
  reversals:"Direction changes (up to down or down to up)", max_run_up:"Longest streak of consecutive up ticks", max_run_down:"Longest streak of consecutive down ticks",
  max_jump_up:"Largest single upward tick", max_jump_down:"Largest single downward tick", spread_sum:"Sum of spreads", min_spread:"Smallest spread", max_spread:"Largest spread",
  bid_close:"Last bid", ask_close:"Last ask",
  imbalance:"(up_ticks - down_ticks) / (up_ticks + down_ticks). -1 all selling ticks, +1 all buying ticks",
  move_imbalance:"(move_up - move_down) / (move_up + move_down). Same, weighted by size",
  efficiency:"|close - start| / path_length. Near 1 = clean move, near 0 = heavy oscillation",
  close_location:"(close - low) / (high - low). 0 = closed at the low, 1 = at the high",
  avg_spread:"spread_sum / tick_count", pressure:"buying / selling / balanced from pressure_score (a heuristic)",
  pressure_score:"0.5 x imbalance + 0.5 x move_imbalance", max_jump:"Larger of the two single-tick jumps, signed", jump_dir:"Direction of the largest jump",
  jump_share:"Largest single move / candle range", choppiness:"reversals / (up_ticks + down_ticks)", up_down_ratio:"up_ticks / down_ticks",
  tick_velocity:"Ticks per second", realized_vol:"sqrt(sum of squared tick moves), in price units", range:"high - low", net_move:"close - open",
  direction:"up / down / flat", body_ratio:"|close - open| / range", wick_upper:"high - max(open, close)", wick_lower:"min(open, close) - low", avg_mid:"mean mid price",
  minutes_present:"1-minute candles found inside this candle", metric_minutes:"How many of those have full live metrics (backfilled minutes have none)",
  complete:"false while the candle is still forming",
};

/* ---------- config / keys ---------- */
async function loadConfig() {
  try {
    const r = await fetch("/ui/config.json");
    if (r.ok) {
      const c = await r.json();
      App.apiKey = c.apiKey || ""; App.adminKey = c.adminKey || "";
      App.liveTfs = c.liveTimeframes || App.liveTfs; App.storeTicks = !!c.storeTicks;
    }
  } catch (e) {}
  App.apiKey = App.apiKey || localStorage.getItem("pe_api_key") || "";
  App.adminKey = App.adminKey || localStorage.getItem("pe_admin_key") || "";
}

/* ---------- HTTP ---------- */
async function request(path, { method = "GET", body = null, admin = false, auth = true } = {}) {
  const headers = {};
  if (auth) { if (admin) headers["X-Admin-Key"] = App.adminKey; else headers["X-API-Key"] = App.apiKey; }
  if (body) headers["Content-Type"] = "application/json";
  const t0 = performance.now();
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : null });
  const ms = Math.round(performance.now() - t0);
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch (e) { data = text; }
  if (!r.ok) { const err = new Error((data && data.detail) || r.statusText); err.status = r.status; err.data = data; err.ms = ms; throw err; }
  return { data, status: r.status, ms };
}
const api = (path, o) => request(path, o).then(x => x.data);

/* ---------- formatting ---------- */
const fmt = (n, d = 5) => (n === null || n === undefined || Number.isNaN(n)) ? "–" : Number(n).toFixed(d);
const fmtInt = n => (n === null || n === undefined) ? "–" : Number(n).toLocaleString();
const IST_MS = 19800000;   // India Standard Time = UTC + 5:30. Everything on screen is shown in IST.
const fmtIST = (ms, seconds = true) => ms ? new Date(Number(ms) + IST_MS).toISOString().replace("T", " ").slice(0, seconds ? 19 : 16) : "–";
const fmtTime = ms => fmtIST(ms);
const istNow = () => fmtIST(Date.now()) + " IST";
const istInputToMs = v => v ? Date.parse(v + ":00Z") - IST_MS : null;          // <input type="datetime-local"> read as IST
const msToIstInput = ms => fmtIST(ms, false).replace(" ", "T");
const decimalsFor = sym => (/_JPY$|^XAU|^XAG/.test(sym) ? 3 : 5);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pretty = o => typeof o === "string" ? o : JSON.stringify(o, null, 2);
function cls(v) { return v > 0 ? "up" : v < 0 ? "down" : ""; }
function toast(msg, ms = 3500) { const d = document.createElement("div"); d.className = "toast"; d.textContent = msg; document.body.appendChild(d); setTimeout(() => d.remove(), ms); }

/* ---------- page chrome ---------- */
function mountNav(active) {
  const pages = [["index.html", "Live"], ["analysis.html", "Analysis"], ["charts.html", "Charts"], ["metrics.html", "Order-flow"], ["console.html", "API console"]];
  document.body.insertAdjacentHTML("afterbegin", `<header class="top"><span class="brand">Price Engine</span><nav>${
    pages.map(([h, n]) => `<a href="${h}" class="${h === active ? "active" : ""}">${n}</a>`).join("")
  }</nav><span class="spacer"></span><span id="pill-api" class="pill wait"><i></i>API</span><span id="pill-ws" class="pill wait"><i></i>WebSocket</span></header>`);
}
function setPill(id, state, text) { const el = document.getElementById(id); if (!el) return; el.className = "pill " + state; el.lastChild.textContent = text; }
async function checkApi() {
  try { await api("/health/live", { auth: false }); const h = await request("/health/ready", { auth: false }).catch(e => ({ data: e.data }));
    const d = h.data || {}; setPill("pill-api", d.status === "ready" ? "ok" : "wait", "API " + (d.stream || "")); }
  catch (e) { setPill("pill-api", "bad", "API offline"); }
}
function needKey() {
  if (App.apiKey) return false;
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<div class="panel"><b>No API key yet.</b> Start the engine in development mode (python engine_ctl.py start) and reload,
    or paste a key on the <a href="console.html">API console</a> page.</div>`);
  return true;
}

/* ---------- websocket client with automatic token + reconnect ---------- */
class LiveSocket {
  constructor({ onMessage, onStatus }) { this.onMessage = onMessage; this.onStatus = onStatus || (() => {}); this.subs = []; this.ws = null; this.ready = false; this.retry = 0; this.closed = false; }
  subscribe(channel, symbols, tfs) { this.subs.push({ channel, symbols, tfs }); if (this.ready) this._send({ op: "subscribe", channel, symbols, tfs }); }
  clear() { if (this.ready) this.subs.forEach(s => this._send({ op: "unsubscribe", ...s })); this.subs = []; }
  _send(o) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); }
  async connect() {
    this.closed = false; this.onStatus("wait", "connecting");
    try {
      const { token } = await api("/v1/ws-token", { method: "POST", body: { metrics: true, ttl_seconds: 120 } });
      const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/v1/stream";
      this.ws = new WebSocket(url);
      this.ws.onopen = () => this.ws.send(JSON.stringify({ op: "auth", token }));
      this.ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        if (m.e === "authed") { this.ready = true; this.retry = 0; this.onStatus("ok", "live"); this.subs.forEach(s => this._send({ op: "subscribe", ...s })); }
        this.onMessage(m, Date.now());
      };
      this.ws.onclose = () => { this.ready = false; if (!this.closed) this._reconnect(); };
      this.ws.onerror = () => {};
    } catch (e) { this.onStatus("bad", "error: " + e.message); this._reconnect(); }
  }
  _reconnect() { this.onStatus("wait", "reconnecting"); const d = Math.min(15000, 500 * 2 ** this.retry++); setTimeout(() => !this.closed && this.connect(), d); }
  close() { this.closed = true; if (this.ws) this.ws.close(); }
}
function wsStatusPill(state, text) { setPill("pill-ws", state, "WebSocket " + text); }

async function loadPairs() { const d = await api("/v1/pairs"); return d.pairs.map(p => p.symbol); }
function fillSelect(sel, items, value) { sel.innerHTML = items.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join(""); if (value) sel.value = value; }
