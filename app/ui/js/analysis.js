mountNav("analysis.html");
const $ = id => document.getElementById(id);
const TFS = ["1m", "5m", "15m", "30m", "1h"];
let st = null, rows = [], shownTfs = new Set(TFS), notes = [], runs = [];
const shownGroups = new Set(["candle", "flow", "outcome", "manual"]);
const GROUPS = { candle: "Candle", flow: "Order-flow", rolling: "Rolling windows", z: "Z-scores", official: "OANDA official", outcome: "What happened next", manual: "Your labels" };

const P = v => fmt(v, 3), R = v => fmt(v, 2), PC = v => v == null ? "–" : fmt(v, 3) + "%", I = v => v == null ? "–" : fmtInt(v), S = v => v == null ? "–" : String(v);
/* [key, title, group, formatter, help] */
const COLS = [
  ["sessions", "session", "candle", S, "Trading sessions active at the candle's start (IST windows)"],
  ["open", "open", "candle", P, HELP.open], ["high", "high", "candle", P, HELP.high], ["low", "low", "candle", P, HELP.low], ["close", "close", "candle", P, HELP.close],
  ["rng", "range", "candle", P, "high - low"], ["tick_count", "ticks (stream)", "candle", I, "Price updates received from OANDA's stream in this candle"],
  ["body_ratio", "body ratio", "candle", v => v == null ? "–" : fmt(v * 100, 0) + "%", "|close - open| / (high - low). Also called range efficiency"],
  ["net_move", "net move", "candle", P, "close - open (price displacement)"], ["momentum", "momentum", "candle", P, "close - the last price before the candle"],
  ["ret_pct", "return %", "candle", PC, "Simple return: momentum / last price before the candle"], ["logret_pct", "log return %", "candle", PC, "ln(close / last price before the candle)"],
  ["momentum_accel", "momentum accel", "candle", P, "momentum - the previous candle's momentum"],
  ["max_jump", "max jump", "flow", P, HELP.max_jump], ["jump_share", "jump share", "flow", R, "|max jump| / range. Above 1 = the biggest move was the gap from the previous candle"],
  ["max_run_up", "max run up", "flow", I, HELP.max_run_up], ["max_run_down", "max run down", "flow", I, HELP.max_run_down],
  ["max_pullback_high", "max pullback from high", "flow", P, "Deepest fall from the candle's running high, inside the candle"],
  ["max_bounce_low", "max bounce from low", "flow", P, "Highest rise from the candle's running low, inside the candle"],
  ["imbalance", "imbalance", "flow", R, HELP.imbalance], ["move_imbalance", "move imbalance", "flow", R, HELP.move_imbalance],
  ["up_ticks", "up ticks", "flow", I, HELP.up_ticks], ["down_ticks", "down ticks", "flow", I, HELP.down_ticks], ["move_up", "move up", "flow", P, HELP.move_up], ["move_down", "move down", "flow", P, HELP.move_down],
  ["path_length", "path", "flow", P, HELP.path_length], ["reversals", "reversals", "flow", I, HELP.reversals], ["rv", "realized vol", "flow", P, HELP.realized_vol], ["rv_pct", "realized vol %", "flow", PC, "realized vol / average price"],
  ["tick_rate", "ticks/sec", "flow", R, HELP.tick_velocity], ["avg_spread", "avg spread", "flow", P, HELP.avg_spread], ["max_spread", "max spread", "flow", P, HELP.max_spread],
];
for (const h of [5, 15, 30, 60]) COLS.push(
  [`ret_${h}s`, `ret ${h}s %`, "rolling", PC, `Return over the last ${h} seconds at the candle close: (P(t) - P(t-h)) / P(t-h)`],
  [`mom_${h}s`, `mom ${h}s`, "rolling", P, `P(t) - P(t-${h}s)`], [`rng_${h}s`, `range ${h}s`, "rolling", P, `High - low over the last ${h} seconds`],
  [`acc_${h}s`, `accel ${h}s`, "rolling", P, `Change of the ${h}s momentum: P(t) - 2P(t-h) + P(t-2h)`]);
COLS.push(
  ["ret_z", "return z", "z", R, "How unusual this candle's return is versus the last ~100 candles of this timeframe (needs 20)"], ["range_z", "range z", "z", R, "Same for the range"], ["ticks_z", "ticks z", "z", R, "Same for the tick count (live candles only)"],
  ["off_open", "OANDA open", "official", P, "OANDA's own candle"], ["off_high", "OANDA high", "official", P, ""], ["off_low", "OANDA low", "official", P, ""], ["off_close", "OANDA close", "official", P, ""],
  ["off_volume", "volume (OANDA)", "official", I, "OANDA's tick count for the candle (about 3x what the stream delivers)"], ["off_spread_close", "spread at close", "official", P, "OANDA ask close - bid close"],
  ["coverage", "stream coverage", "official", R, "Stream ticks / OANDA volume. How much of OANDA's activity our tick metrics saw"],
  ["next_dir", "next candle", "outcome", S, "Direction of the next candle of this timeframe"], ["next_ret_pct", "next ret %", "outcome", PC, ""], ["next_range", "next range", "outcome", P, ""],
  ["ret_5m", "after 5m %", "outcome", PC, "Price change 5 minutes after the close"], ["ret_15m", "after 15m %", "outcome", PC, ""], ["ret_60m", "after 60m %", "outcome", PC, ""],
  ["mfe_5m", "best rise 5m", "outcome", P, "Highest price above the close within 5 minutes (MFE for a long)"], ["mae_5m", "worst fall 5m", "outcome", P, "Lowest price below the close within 5 minutes (MAE for a long)"],
  ["mfe_15m", "best rise 15m", "outcome", P, ""], ["mae_15m", "worst fall 15m", "outcome", P, ""], ["mfe_60m", "best rise 60m", "outcome", P, ""], ["mae_60m", "worst fall 60m", "outcome", P, ""],
  ["algo_direction", "my algo direction", "manual", null, "Your algorithm's call (blank for now)"], ["actual_direction", "actual direction", "manual", S, "Filled automatically: direction of the next candle"],
  ["label_15m", "what happened (15m)", "manual", null, "Your label"], ["note", "note", "manual", null, "Your note for this candle"]);

const dirCell = r => { const c = r.direction === "BULLISH" ? "bull" : r.direction === "BEARISH" ? "bear" : "flat";
  return `<span class="${c}">${r.direction}${r.body_ratio != null ? " " + fmt(r.body_ratio * 100, 0) + "%" : ""}</span>`; };

function renderTable() {
  const cols = COLS.filter(c => shownGroups.has(c[2]));
  const head = `<thead><tr><th class="k" title="Candle start time, IST">time (IST)</th><th>tf</th><th title="Direction and how much of the candle's range is body">direction + body</th>${cols.map(c => `<th title="${esc(c[4] || "")}">${c[1]}</th>`).join("")}</tr></thead>`;
  const body = rows.filter(r => shownTfs.has(r.tf)).map(r => {
    const tds = cols.map(c => {
      const k = c[0], v = r[k];
      if (k === "algo_direction") return `<td><select data-f="algo_direction" data-i="${r.ts_ms}|${r.tf}"><option value=""></option>${st.directions.map(x => `<option ${v === x ? "selected" : ""}>${x}</option>`).join("")}</select></td>`;
      if (k === "label_15m") return `<td><select data-f="label_15m" data-i="${r.ts_ms}|${r.tf}"><option value=""></option>${st.labels.map(x => `<option ${v === x ? "selected" : ""}>${x}</option>`).join("")}</select></td>`;
      if (k === "note") return `<td><input class="note" data-f="note" data-i="${r.ts_ms}|${r.tf}" value="${esc(v || "")}" placeholder="note"></td>`;
      const txt = c[3](v);
      const color = ["imbalance", "move_imbalance", "net_move", "momentum", "ret_pct", "max_jump"].includes(k) || k.startsWith("ret_") ? cls(v) : "";
      return `<td class="${color}">${txt}</td>`;
    }).join("");
    return `<tr><td class="k mono">${fmtIST(r.ts_ms)}${r.partial ? ' <span class="warn" title="Partial: the engine started after this candle began">!</span>' : ""}</td><td>${r.tf}</td><td>${dirCell(r)}</td>${tds}</tr>`;
  }).join("");
  $("tbl").innerHTML = head + `<tbody>${body || `<tr><td colspan="${cols.length + 3}" class="muted" style="text-align:left;padding:14px">No candles yet. Press Start analysis; rows appear as candles close (a 1-minute row every minute).</td></tr>`}</tbody>`;
}

async function loadRows() {
  const a = document.activeElement;
  if (a && a.closest && a.closest("#tbl") && ["INPUT", "SELECT"].includes(a.tagName)) return;      // don't disturb typing
  const d = await api(`/v1/analysis/rows?symbol=${$("symbol").value}&limit=400`, { admin: true });
  rows = d.rows; renderTable();
}

function renderLive(d) {
  const head = ["tf", "closes in", "direction + body", "open", "high", "low", "close", "range", "ticks", "net move", "momentum", "imbalance", "move imb", "max jump", "jump share", "run up", "run down", "pullback high", "bounce low", "reversals", "spread", "ret 5s %", "ret 15s %", "ret 30s %", "ret 60s %", "ret z"];
  const tfs = Object.keys(d.timeframes).filter(t => shownTfs.has(t));
  $("live").innerHTML = `<thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${tfs.map(tf => { const r = d.timeframes[tf]; const c = [
    `<b>${tf}</b>`, r.closes_in_s + "s", dirCell(r), P(r.open_px), P(r.high_px), P(r.low_px), P(r.close_px), P(r.rng), I(r.tick_count), P(r.net_move), P(r.momentum), R(r.imbalance), R(r.move_imbalance),
    P(r.max_jump), R(r.jump_share), I(r.max_run_up), I(r.max_run_down), P(r.max_pullback_high), P(r.max_bounce_low), I(r.reversals), P(r.avg_spread), PC(r.ret_5s), PC(r.ret_15s), PC(r.ret_30s), PC(r.ret_60s), R(r.ret_z)];
    return `<tr>${c.map(x => `<td>${x}</td>`).join("")}</tr>`; }).join("")}</tbody>`;
}

function renderState() {
  const m = st.market, b = $("banner");
  if (!st.ready) { b.className = "banner closed"; b.textContent = "The analysis tables are missing in Oracle. Run initdb.bat once (or paste sql\\oracle_schema.sql in Database Actions), restart the engine, then reload."; }
  else if (!m.open) { b.className = "banner closed"; b.textContent = m.text + (st.active ? "  (Analysis is still ON and will capture again when the market reopens.)" : ""); }
  else if (st.active) { b.className = "banner running"; b.textContent = `ANALYSIS ON for ${st.symbol} since ${fmtIST(st.run.started_ms)} IST - ${st.captured} candles captured, ${st.pending_outcomes} still waiting for their outcome. ${m.text}`; }
  else { b.className = "banner idle"; b.textContent = "Analysis is OFF. " + m.text + " Press Start analysis to begin saving candles."; }
  $("start").disabled = !st.ready || st.active || !m.open; $("stop").disabled = !st.active;
  $("sess").textContent = st.sessions_now || "none";
}

const tick = async () => {
  $("clock").textContent = istNow();
};
async function poll() {
  try { st = await api("/v1/analysis/state", { admin: true }); renderState(); renderLive(await api(`/v1/analysis/live?symbol=${$("symbol").value}`, { admin: true })); } catch (e) { $("banner").className = "banner closed"; $("banner").textContent = "Cannot reach the engine: " + e.message; }
}

async function loadNotes() {
  const d = await api("/v1/analysis/notes", { admin: true }); notes = d.notes;
  $("notes").innerHTML = notes.map(n => `<div class="note-item"><time>${fmtIST(n.ts_ms)}</time><span style="flex:1">${esc(n.note_text)}</span><a href="#" data-del="${n.note_id}">delete</a></div>`).join("") || '<span class="muted">No notes yet.</span>';
}
async function loadRuns() {
  const d = await api("/v1/analysis/runs", { admin: true }); runs = d.runs;
  $("x-run").innerHTML = '<option value="">all runs</option>' + runs.map(r => `<option value="${r.run_id}">${fmtIST(r.started_ms)} IST - ${r.candles} candles${r.stopped_ms ? "" : " (running)"}</option>`).join("");
}
async function download(path, name) {
  try {
    const r = await fetch(path, { headers: { "X-Admin-Key": App.adminKey } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name; a.click(); URL.revokeObjectURL(a.href);
  } catch (e) { toast("Download failed: " + e.message); }
}
function exportQuery() {
  const q = new URLSearchParams({ symbol: $("symbol").value, tfs: [...shownTfs].join(",") });
  const f = istInputToMs($("x-from").value), t = istInputToMs($("x-to").value);
  if ($("x-run").value) q.set("run_id", $("x-run").value); if (f) q.set("from", f); if (t) q.set("to", t); return q;
}

async function start() {
  await loadConfig(); await checkApi(); setInterval(checkApi, 10000);
  if (!App.adminKey) { document.querySelector("main").insertAdjacentHTML("afterbegin", '<div class="panel"><b>No admin key.</b> Start the engine in development mode, or paste the admin key on the <a href="console.html">API console</a> page.</div>'); return; }
  fillSelect($("symbol"), (await loadPairs().catch(() => ["XAU_USD"])), "XAU_USD");
  $("tfchips").innerHTML = TFS.map(t => `<span class="chip on" data-tf="${t}">${t}</span>`).join("");
  $("groups").innerHTML = Object.entries(GROUPS).map(([k, n]) => `<label><input type="checkbox" data-g="${k}" ${shownGroups.has(k) ? "checked" : ""}> ${n}</label>`).join("");
  $("tfchips").onclick = e => { const t = e.target.dataset.tf; if (!t) return; shownTfs.has(t) ? shownTfs.delete(t) : shownTfs.add(t); e.target.className = "chip " + (shownTfs.has(t) ? "on" : "off"); renderTable(); };
  $("groups").onchange = e => { const g = e.target.dataset.g; e.target.checked ? shownGroups.add(g) : shownGroups.delete(g); renderTable(); };
  $("start").onclick = async () => { try { await api("/v1/analysis/start", { method: "POST", admin: true, body: { symbol: $("symbol").value } }); toast("Analysis started"); } catch (e) { toast(e.message); } poll(); loadRuns(); };
  $("stop").onclick = async () => { try { await api("/v1/analysis/stop", { method: "POST", admin: true }); toast("Analysis stopped"); } catch (e) { toast(e.message); } poll(); loadRuns(); };
  $("symbol").onchange = () => { loadRows(); poll(); };
  $("tbl").addEventListener("change", async e => {
    const el = e.target; if (!el.dataset.f) return; const [ts, tf] = el.dataset.i.split("|");
    try { await api("/v1/analysis/rows", { method: "PATCH", admin: true, body: { symbol: $("symbol").value, tf, ts_ms: +ts, [el.dataset.f]: el.value } }); const r = rows.find(x => x.ts_ms == ts && x.tf === tf); if (r) r[el.dataset.f] = el.value; toast("Saved", 1200); }
    catch (err) { toast("Not saved: " + err.message); }
  });
  $("n-add").onclick = async () => { const t = $("n-text").value.trim(); if (!t) return; try { await api("/v1/analysis/notes", { method: "POST", admin: true, body: { text: t, symbol: $("symbol").value } }); $("n-text").value = ""; loadNotes(); } catch (e) { toast(e.message); } };
  $("notes").onclick = async e => { const id = e.target.dataset.del; if (!id) return; e.preventDefault(); await api("/v1/analysis/notes/" + id, { method: "DELETE", admin: true }); loadNotes(); };
  $("x-run").onchange = () => { const r = runs.find(x => x.run_id == $("x-run").value); $("x-from").value = r ? msToIstInput(r.started_ms) : ""; $("x-to").value = r ? msToIstInput(r.stopped_ms || Date.now()) : ""; };
  $("x-csv").onclick = () => download("/v1/analysis/export.csv?" + exportQuery(), `analysis_${$("symbol").value}_${fmtIST(Date.now()).replace(/[: ]/g, "-")}_IST.csv`);
  $("x-notes").onclick = () => { const q = new URLSearchParams(), f = istInputToMs($("x-from").value), t = istInputToMs($("x-to").value); if (f) q.set("from", f); if (t) q.set("to", t); download("/v1/analysis/export_notes.csv?" + q, "analysis_notes_IST.csv"); };
  tick(); setInterval(tick, 1000);
  await poll(); await loadRows(); await loadNotes(); await loadRuns();
  setInterval(poll, 1000); setInterval(() => loadRows().catch(() => {}), 8000);
}
start().catch(e => toast("Error: " + e.message));
