mountNav("metrics.html");
const $ = id => document.getElementById(id);
const FORM_COLS = ["tf","open","high","low","close","direction","pressure","imbalance","move_imbalance","efficiency","choppiness","tick_velocity","realized_vol","max_jump","jump_share","reversals","max_run_up","max_run_down","avg_spread","close_location","volume"];
const HIST_COLS = ["t","open","close","direction","pressure","imbalance","move_imbalance","efficiency","choppiness","reversals","max_jump","tick_velocity","realized_vol","avg_spread","close_location","volume","metric_minutes"];
let sym = "", state = {}, sock = null;

function val(k, v, d) {
  if (v === undefined || v === null) return "–";
  if (k === "t") return fmtTime(v);
  if (typeof v === "number") return Number.isInteger(v) ? fmtInt(v) : fmt(v, /^(open|high|low|close|max_jump|realized|avg_spread)/.test(k) ? d + 1 : 3);
  return v;
}
function klass(k, v) { return ["imbalance","move_imbalance","max_jump"].includes(k) ? cls(v) : k === "direction" ? (v === "up" ? "up" : v === "down" ? "down" : "") : k === "pressure" ? (v === "buying" ? "up" : v === "selling" ? "down" : "") : ""; }
function renderTable(rows, cols, d) {
  return `<table><thead><tr>${cols.map(k => `<th title="${esc(HELP[k] || "")}">${k}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${cols.map(k => `<td class="${klass(k, r[k])}">${val(k, r[k], d)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function renderForming() { $("forming").innerHTML = renderTable(App.liveTfs.filter(t => state[t]).map(t => Object.assign({ tf: t }, state[t])), FORM_COLS, decimalsFor(sym)); $("stamp").textContent = "updated " + new Date().toLocaleTimeString(); }

async function loadAll() {
  sym = $("symbol").value; state = {};
  for (const tf of App.liveTfs) { try { const d = await api(`/v1/candles/forming?symbol=${sym}&tf=${tf}&metrics=full`); state[tf] = d.candle; } catch (e) {} }
  renderForming(); loadHistory();
  if (sock) { sock.clear(); sock.subscribe("candle_metrics", [sym], App.liveTfs); }
}
async function loadHistory() {
  try { const d = await api(`/v1/candles?symbol=${sym}&tf=${$("htf").value}&limit=${$("hlimit").value}&metrics=full`);
    $("history").innerHTML = renderTable(d.candles.slice().reverse(), HIST_COLS, decimalsFor(sym)); }
  catch (e) { $("history").textContent = e.message; }
}
function onMessage(m) {
  if (m.e !== "candle_metrics" || m.symbol !== sym) return;
  state[m.tf] = Object.assign({}, m, { volume: m.volume });
  if (!window._r || Date.now() - window._r > 300) { window._r = Date.now(); renderForming(); }
  if (m.final && m.tf === $("htf").value) setTimeout(loadHistory, 1500);
}
function guide() {
  const keys = ["imbalance","move_imbalance","efficiency","close_location","pressure_score","pressure","max_jump","jump_share","choppiness","reversals","max_run_up","tick_velocity","realized_vol","avg_spread","up_down_ratio","wick_upper","body_ratio"];
  $("guide").innerHTML = keys.map(k => `<dt>${k}</dt><dd>${esc(HELP[k])}</dd>`).join("");
}
async function start() {
  await loadConfig(); await checkApi(); setInterval(checkApi, 10000); guide();
  if (needKey()) return;
  fillSelect($("symbol"), await loadPairs()); fillSelect($("htf"), ALL_TFS, "5m");
  sock = new LiveSocket({ onMessage, onStatus: wsStatusPill }); sock.connect();
  $("symbol").onchange = loadAll; $("htf").onchange = $("hlimit").onchange = loadHistory;
  await loadAll(); setInterval(() => { App.liveTfs.forEach(async tf => { if (!sock.ready) { try { state[tf] = (await api(`/v1/candles/forming?symbol=${sym}&tf=${tf}&metrics=full`)).candle; } catch (e) {} } }); }, 5000);
}
start().catch(e => toast("Error: " + e.message));
