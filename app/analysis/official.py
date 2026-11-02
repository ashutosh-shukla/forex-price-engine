"""OANDA's own candles: reconcile each captured candle, and seed the z-score history."""
from __future__ import annotations

import httpx

from app.core.timeframes import TF_SECONDS

GRAN = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1"}


def _headers(s) -> dict:
    return {"Authorization": f"Bearer {s.OANDA_API_KEY}", "Accept-Datetime-Format": "UNIX"}


async def fetch_official(s, symbol: str, tf: str, ts_ms: int) -> dict | None:
    gran = GRAN.get(tf)
    if not gran or not (s.OANDA_API_KEY and s.OANDA_ACCOUNT_ID):
        return None
    sec = ts_ms // 1000
    async with httpx.AsyncClient(timeout=15, headers=_headers(s)) as c:
        r = await c.get(f"{s.OANDA_API_URL}/v3/instruments/{symbol}/candles",
                        params={"price": "BAM", "granularity": gran, "from": str(sec), "to": str(sec + TF_SECONDS[tf])})
    if r.status_code != 200:
        return None
    for c in r.json().get("candles", []):
        if c.get("complete") and int(float(c["time"])) == sec and "mid" in c:
            bid, ask = c.get("bid"), c.get("ask")
            return {"off_open": float(c["mid"]["o"]), "off_high": float(c["mid"]["h"]), "off_low": float(c["mid"]["l"]), "off_close": float(c["mid"]["c"]),
                    "off_volume": int(c.get("volume", 0)),
                    "off_spread_close": (float(ask["c"]) - float(bid["c"])) if bid and ask else None}
    return None


async def fetch_history(s, symbol: str, tf: str, count: int = 120) -> list[dict]:
    """Recent complete candles (return %, range, volume) used to start the z-scores."""
    gran = GRAN.get(tf)
    if not gran or not s.OANDA_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=20, headers=_headers(s)) as c:
        r = await c.get(f"{s.OANDA_API_URL}/v3/instruments/{symbol}/candles", params={"price": "M", "granularity": gran, "count": str(count + 1)})
    if r.status_code != 200:
        return []
    out, prev = [], None
    for c in r.json().get("candles", []):
        if not c.get("complete"):
            continue
        m = c["mid"]
        o, h, l, cl = float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"])
        base = prev if prev is not None else o
        out.append({"ret_pct": (cl / base - 1) * 100, "rng": h - l, "volume": int(c.get("volume", 0))})
        prev = cl
    return out
