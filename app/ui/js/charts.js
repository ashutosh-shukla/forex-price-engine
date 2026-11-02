mountNav("charts.html");
let candles = [], sock = null, nextFrom = null, sym = "", tf = "";
const $ = id => document.getElementById(id);
const COLS_BASIC = ["t","open","high","low","close","volume","complete","imbalance","move_imbalance","efficiency","close_location","avg_spread","pressure"];
const COLS_FULL = ["t","open","high","low","close","volume","complete","direction","range","net_move","body_ratio","wick_upper","wick_lower","up_ticks","down_ticks","imbalance","move_up","move_down","move_imbalance","path_length","efficiency","reversals","choppiness","max_run_up","max_run_down","max_jump","jump_dir","jump_share","tick_velocity","realized_vol","avg_spread","min_spread","max_spread","avg_mid","close_location","pressure_score","pressure","minutes_present","metric_minutes","bid_close","ask_close"];

const toMs = v => istInputToMs(v);

async function load(append = false) {
  sym = $("symbol").value; tf = $("tf").value;
  const q = new URLSearchParams({ symbol: sym, tf, limit: $("limit").value, metrics: $("metrics").value });
  const f = append ? nextFrom : toMs($("from").value), t = toMs($("to").value);
  if (f) q.set("from", f); if (t) q.set("to", t);
  $("info").textContent = "loading..."; const t0 = performance.now();
  try {
    const d = await api("/v1/candles?" + q);
    candles = append ? candles.concat(d.candles) : d.candles; nextFrom = d.next_from || null; $("next").disabled = !nextFrom;
    $("info").textContent = `${d.count} candles in ${Math.round(performance.now() - t0)} ms · ${tf} candles are built from stored 1-minute candles` + (nextFrom ? " · more available (Next page)" : "");
    draw(); table(); loadSummary(); resubscribe();
  } catch (e) { $("info").textContent = "Error: " + e.message; }
}

function draw() {
  const svg = $("chart"), W = 1100, H = 560, L = 60, R = 16, T = 12, pH = 330, vH = 90, iH = 90, gap = 14;
  if (!candles.length) { svg.innerHTML = '<text x="20" y="30">No candles yet. Let the engine run for a few minutes, or Load again.</text>'; return; }
  const n = candles.length, d = decimalsFor(sym), bw = (W - L - R) / n;
  const hi = Math.max(...candles.map(c => c.high)), lo = Math.min(...candles.map(c => c.low)), pad = (hi - lo) * 0.05 || 0.0001;
  const y = v => T + (hi + pad - v) / (hi - lo + 2 * pad) * pH, x = i => L + i * bw + bw / 2;
  let s = ""; 
  for (let k = 0; k <= 5; k++) { const v = lo - pad + (hi - lo + 2 * pad) * k / 5, yy = y(v); s += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="#2a3340" stroke-width=".5"/><text x="4" y="${yy + 3}">${v.toFixed(d)}</text>`; }
  candles.forEach((c, i) => {
    const up = c.close >= c.open, col = up ? "#26c281" : "#f0506e", cx = x(i), top = y(Math.max(c.open, c.close)), bot = y(Math.min(c.open, c.close));
    s += `<g><title>${fmtTime(c.t)}\nO ${fmt(c.open, d)} H ${fmt(c.high, d)} L ${fmt(c.low, d)} C ${fmt(c.close, d)}\nticks ${c.volume}` + (c.imbalance !== undefined ? `\nimbalance ${fmt(c.imbalance, 2)} · efficiency ${fmt(c.efficiency, 2)} · ${c.pressure || ""}` : "") + `</title>` +
      `<line x1="${cx}" x2="${cx}" y1="${y(c.high)}" y2="${y(c.low)}" stroke="${col}"/><rect x="${cx - bw * 0.35}" y="${top}" width="${Math.max(1, bw * 0.7)}" height="${Math.max(1, bot - top)}" fill="${col}"${c.complete ? "" : ' stroke="#fff" stroke-width="1"'}/></g>`;
  });
  const vy0 = T + pH + gap, vmax = Math.max(1, ...candles.map(c => c.volume || 0));
  s += `<text x="4" y="${vy0 + 10}">ticks</text>`;
  candles.forEach((c, i) => { const h = (c.volume || 0) / vmax * vH; s += `<rect x="${x(i) - bw * 0.35}" y="${vy0 + vH - h}" width="${Math.max(1, bw * 0.7)}" height="${h}" fill="#4c8dff" opacity=".55"/>`; });
  const iy0 = vy0 + vH + gap, has = candles.some(c => c.imbalance !== undefined && c.imbalance !== null);
  s += `<text x="4" y="${iy0 + 10}">imbalance</text><line x1="${L}" x2="${W - R}" y1="${iy0 + iH / 2}" y2="${iy0 + iH / 2}" stroke="#2a3340"/>`;
  if (has) candles.forEach((c, i) => { if (c.imbalance == null) return; const h = c.imbalance * iH / 2; s += `<rect x="${x(i) - bw * 0.35}" y="${h >= 0 ? iy0 + iH / 2 - h : iy0 + iH / 2}" width="${Math.max(1, bw * 0.7)}" height="${Math.abs(h)}" fill="${h >= 0 ? "#26c281" : "#f0506e"}" opacity=".8"/>`; });
  else s += `<text x="${L + 10}" y="${iy0 + iH / 2 - 6}">choose metrics = basic or full (backfilled history has no metrics)</text>`;
  const step = Math.max(1, Math.floor(n / 8)); for (let i = 0; i < n; i += step) s += `<text x="${x(i) - 28}" y="${H - 4}">${fmtTime(candles[i].t).slice(5, 16)}</text>`;
  svg.innerHTML = s;
}

function table() {
  const level = $("metrics").value, cols = level === "full" ? COLS_FULL : level === "basic" ? COLS_BASIC : COLS_BASIC.slice(0, 7);
  const d = decimalsFor(sym), rows = candles.slice().reverse().slice(0, 400);
  const cell = (c, k) => { const v = c[k]; if (v === undefined || v === null) return "–"; if (k === "t") return fmtTime(v); if (typeof v === "number") return Number.isInteger(v) && !["open","high","low","close"].includes(k) ? fmtInt(v) : fmt(v, /^(open|high|low|close|bid_close|ask_close|avg_mid|range|net_move|wick|max_jump|move|path|spread|avg_spread|min_s|max_s|realized)/.test(k) ? d + 1 : 3); return String(v); };
  $("tablewrap").innerHTML = `<table><thead><tr>${cols.map(k => `<th title="${esc(HELP[k] || "")}">${k}</th>`).join("")}</tr></thead><tbody>${
    rows.map(c => `<tr>${cols.map(k => `<td class="${["imbalance","move_imbalance","net_move","pressure_score","max_jump"].includes(k) ? cls(c[k]) : k === "pressure" ? (c[k] === "buying" ? "up" : c[k] === "selling" ? "down" : "") : ""}">${cell(c, k)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

async function loadSummary() {
  try {
    const d = await api(`/v1/metrics/summary?symbol=${sym}&tf=${tf}&window=${$("win").value}`);
    if (!d.summary) { $("summary").innerHTML = `<div class="muted" style="grid-column:1/-1">Not enough candles with live metrics yet (${d.available} found).</div>`; return; }
    const keys = ["candles","up_candles","down_candles","imbalance","move_imbalance","efficiency","pressure","pressure_score","choppiness","reversals","max_jump","jump_dir","realized_vol","tick_velocity","avg_spread","close_location","range","net_move"];
    $("summary").innerHTML = keys.map(k => `<div title="${esc(HELP[k] || "")}"><span>${k}</span><b>${typeof d.summary[k] === "number" ? fmt(d.summary[k], Number.isInteger(d.summary[k]) ? 0 : 5) : d.summary[k]}</b></div>`).join("");
  } catch (e) { $("summary").innerHTML = `<div class="muted" style="grid-column:1/-1">${esc(e.message)}</div>`; }
  try { const f = await api(`/v1/candles/forming?symbol=${sym}&tf=${App.liveTfs.includes(tf) ? tf : "1m"}&metrics=full`); $("forming").textContent = pretty(f); } catch (e) { $("forming").textContent = e.message; }
}

function onMessage(m) {
  if (m.e !== "candle_metrics" || m.symbol !== sym || m.tf !== tf || !$("live").checked || nextFrom || !candles.length) return;
  const last = candles[candles.length - 1], c = Object.assign({}, m, { volume: m.volume, complete: m.final });
  if (m.t === last.t) candles[candles.length - 1] = c; else if (m.t > last.t) { candles.push(c); if (candles.length > +$("limit").value) candles.shift(); }
  draw(); if (!window._tp || Date.now() - window._tp > 1000) { window._tp = Date.now(); table(); }
}
function resubscribe() {
  if (!sock) { sock = new LiveSocket({ onMessage, onStatus: wsStatusPill }); sock.connect(); }
  sock.clear(); if (App.liveTfs.includes(tf)) sock.subscribe("candle_metrics", [sym], [tf]);
}

async function start() {
  await loadConfig(); await checkApi(); setInterval(checkApi, 10000);
  if (needKey()) return;
  fillSelect($("symbol"), await loadPairs()); fillSelect($("tf"), ALL_TFS, "5m");
  $("load").onclick = () => load(false); $("next").onclick = () => load(true); $("sumbtn").onclick = loadSummary;
  $("symbol").onchange = $("tf").onchange = $("metrics").onchange = () => load(false);
  await load(false); setInterval(() => { if ($("live").checked) loadSummary(); }, 5000);
}
start().catch(e => toast("Error: " + e.message));
