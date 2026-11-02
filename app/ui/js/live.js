mountNav("index.html");
const cards = {}; let symbols = [], sock = null, msgCount = 0, latSum = 0, latN = 0, lastStat = Date.now();

function cardHtml(s) {
  return `<div class="card" id="c-${s}"><div class="sym"><b>${s.replace("_", "/")}</b><span class="badge" data-k="pressure">–</span></div>
  <div class="mid" data-k="mid">–</div>
  <div class="quote"><span>bid <b data-k="bid">–</b></span><span>ask <b data-k="ask">–</b></span><span>spread <b data-k="spread">–</b></span></div>
  <div class="gauge" title="imbalance: -1 selling ... +1 buying"><div class="mid"></div><div class="fill" data-k="gauge"></div></div>
  <div class="kv">
    <div><span>imbalance</span><b data-k="imbalance">–</b></div><div><span>move imbalance</span><b data-k="move_imbalance">–</b></div><div><span>efficiency</span><b data-k="efficiency">–</b></div>
    <div><span>ticks / sec</span><b data-k="tick_velocity">–</b></div><div><span>reversals</span><b data-k="reversals">–</b></div><div><span>max jump</span><b data-k="max_jump">–</b></div>
    <div><span>candle O → C</span><b data-k="oc">–</b></div><div><span>close location</span><b data-k="close_location">–</b></div><div><span>latency</span><b data-k="lat">–</b></div>
  </div></div>`;
}
const set = (s, k, v, c) => { const el = document.querySelector(`#c-${s} [data-k="${k}"]`); if (el) { el.textContent = v; if (c !== undefined) el.className = c; } };

function onMessage(m, now) {
  if (m.e === "tick") {
    msgCount++; const lat = now - m.p; if (lat >= 0 && lat < 5000) { latSum += lat; latN++; }
    const d = decimalsFor(m.symbol), c = cards[m.symbol] || (cards[m.symbol] = {}), prev = c.mid;
    set(m.symbol, "mid", fmt(m.mid, d), prev === undefined ? "mid" : "mid " + (m.mid > prev ? "up" : m.mid < prev ? "down" : ""));
    set(m.symbol, "bid", fmt(m.bid, d)); set(m.symbol, "ask", fmt(m.ask, d)); set(m.symbol, "spread", fmt(m.ask - m.bid, d)); set(m.symbol, "lat", Math.round(now - m.p) + " ms");
    c.mid = m.mid;
  } else if (m.e === "candle_metrics" && m.tf === document.getElementById("tf").value) {
    msgCount++; const s = m.symbol, d = decimalsFor(s);
    set(s, "pressure", m.pressure || "–"); document.querySelector(`#c-${s} [data-k="pressure"]`).className = "badge " + (m.pressure || "");
    set(s, "imbalance", fmt(m.imbalance, 2), cls(m.imbalance)); set(s, "move_imbalance", fmt(m.move_imbalance, 2), cls(m.move_imbalance));
    set(s, "efficiency", fmt(m.efficiency, 2)); set(s, "tick_velocity", fmt(m.tick_velocity, 2)); set(s, "reversals", fmtInt(m.reversals));
    set(s, "max_jump", fmt(m.max_jump, d), cls(m.max_jump)); set(s, "oc", fmt(m.open, d) + " → " + fmt(m.close, d), cls(m.close - m.open)); set(s, "close_location", fmt(m.close_location, 2));
    const g = document.querySelector(`#c-${s} [data-k="gauge"]`), v = Math.max(-1, Math.min(1, m.imbalance || 0));
    g.style.left = (v >= 0 ? 50 : 50 + v * 50) + "%"; g.style.width = Math.abs(v) * 50 + "%"; g.style.background = v >= 0 ? "var(--up)" : "var(--down)";
  }
}

async function start() {
  await loadConfig(); await checkApi(); setInterval(checkApi, 10000);
  if (needKey()) return;
  symbols = await loadPairs();
  document.getElementById("cards").innerHTML = symbols.map(cardHtml).join("");
  const tfSel = document.getElementById("tf"); fillSelect(tfSel, App.liveTfs, App.liveTfs.includes("5m") ? "5m" : App.liveTfs[0]);
  sock = new LiveSocket({ onMessage, onStatus: wsStatusPill });
  const subscribeAll = () => { sock.clear(); sock.subscribe("ticks", symbols); sock.subscribe("candle_metrics", symbols, [tfSel.value]); };
  subscribeAll(); tfSel.onchange = subscribeAll; sock.connect();
  setInterval(() => { const dt = (Date.now() - lastStat) / 1000; document.getElementById("stats").textContent =
    `${(msgCount / dt).toFixed(0)} messages/s · average latency ${latN ? (latSum / latN).toFixed(1) : "–"} ms`; msgCount = 0; latSum = 0; latN = 0; lastStat = Date.now(); }, 2000);
}
start().catch(e => toast("Error: " + e.message));
