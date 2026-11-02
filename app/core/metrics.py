"""Candle state + order-flow metrics.

CandleState is updated tick by tick and is the single source of truth for every metric.
Metrics are *additive* where possible so that 5m/15m/1h/4h/1d candles can be rebuilt
exactly from stored 1m candles (see sql/003_aggregates.sql).

Carry-over rule: when a new candle starts, it inherits the previous tick's mid price,
direction and run length. So the move between the last tick of the previous candle and the
first tick of the new candle is counted (once) in the new candle. This keeps sums exact.
"""
from __future__ import annotations

import math

EPS = 1e-9  # price changes smaller than this are treated as "no change"

RAW_FIELDS = (
    "open", "high", "low", "close",
    "tick_count", "up_ticks", "down_ticks",
    "move_up", "move_down", "path_length", "reversals",
    "max_run_up", "max_run_down", "max_jump_up", "max_jump_down",
    "spread_sum", "min_spread", "max_spread", "mid_sum", "sum_sq_move",
    "start_mid", "bid_close", "ask_close",
)
PRICE_FIELDS = ("open", "high", "low", "close")
INT_FIELDS = ("tick_count", "up_ticks", "down_ticks", "reversals", "max_run_up", "max_run_down")


def db_col(field: str) -> str:
    """open/high/low/close are stored as *_px in Oracle (avoids reserved-word trouble)."""
    return field + "_px" if field in PRICE_FIELDS else field


# column order of the pe_candles_1m insert/merge (symbol, ts_ms first, source last)
DB_COLUMNS = ("symbol", "ts_ms") + tuple(db_col(f) for f in RAW_FIELDS) + ("source",)

# Raw metric fields exposed to clients (besides OHLC)
METRIC_RAW_FIELDS = (
    "tick_count", "up_ticks", "down_ticks", "move_up", "move_down", "path_length",
    "reversals", "max_run_up", "max_run_down", "max_jump_up", "max_jump_down",
    "spread_sum", "min_spread", "max_spread", "bid_close", "ask_close",
)


def _r(x, nd: int = 8):
    if x is None:
        return None
    return round(float(x), nd)


class CandleState:
    __slots__ = (
        "bucket_ms", "open", "high", "low", "close", "tick_count", "up_ticks", "down_ticks",
        "move_up", "move_down", "path_length", "reversals", "max_run_up", "max_run_down",
        "max_jump_up", "max_jump_down", "spread_sum", "min_spread", "max_spread", "mid_sum",
        "sum_sq_move", "start_mid", "bid_close", "ask_close",
        "_prev", "_dir", "_run", "emitted", "last_tick_ms", "first_tick_ms", "max_pullback", "max_bounce",
    )

    def __init__(self, bucket_ms: int, prev: float | None = None, direction: int = 0, run: int = 0):
        self.bucket_ms = bucket_ms
        self.open = self.high = self.low = self.close = 0.0
        self.tick_count = self.up_ticks = self.down_ticks = self.reversals = 0
        self.max_run_up = self.max_run_down = 0
        self.move_up = self.move_down = self.path_length = 0.0
        self.max_jump_up = self.max_jump_down = 0.0
        self.spread_sum = self.mid_sum = self.sum_sq_move = 0.0
        self.min_spread = self.max_spread = 0.0
        self.bid_close = self.ask_close = 0.0
        self.start_mid = prev
        self._prev = prev
        self._dir = direction
        self._run = run
        self.emitted = False
        self.last_tick_ms = 0
        self.first_tick_ms = 0
        self.max_pullback = 0.0   # deepest fall from the candle's running high (inside the candle only)
        self.max_bounce = 0.0     # highest rise from the candle's running low (inside the candle only)

    # ------------------------------------------------------------------ updates
    def add_tick(self, ts_ms: int, bid: float, ask: float, mid: float) -> None:
        spread = ask - bid
        if self.tick_count == 0:
            self.open = self.high = self.low = mid
            self.min_spread = self.max_spread = spread
            if self.start_mid is None:
                self.start_mid = mid
        else:
            if mid > self.high:
                self.high = mid
            elif mid < self.low:
                self.low = mid
            if spread < self.min_spread:
                self.min_spread = spread
            elif spread > self.max_spread:
                self.max_spread = spread
        if self.tick_count == 0:
            self.first_tick_ms = ts_ms
        self.tick_count += 1
        self.close = mid
        if self.high - mid > self.max_pullback:
            self.max_pullback = self.high - mid
        if mid - self.low > self.max_bounce:
            self.max_bounce = mid - self.low
        self.bid_close = bid
        self.ask_close = ask
        self.spread_sum += spread
        self.mid_sum += mid

        prev = self._prev
        if prev is not None:
            d = mid - prev
            if d > EPS:
                self.up_ticks += 1
                self.move_up += d
                self.path_length += d
                self.sum_sq_move += d * d
                if self._dir == 1:
                    self._run += 1
                else:
                    if self._dir == -1:
                        self.reversals += 1
                    self._dir = 1
                    self._run = 1
                if self._run > self.max_run_up:
                    self.max_run_up = self._run
                if d > self.max_jump_up:
                    self.max_jump_up = d
            elif d < -EPS:
                a = -d
                self.down_ticks += 1
                self.move_down += a
                self.path_length += a
                self.sum_sq_move += a * a
                if self._dir == -1:
                    self._run += 1
                else:
                    if self._dir == 1:
                        self.reversals += 1
                    self._dir = -1
                    self._run = 1
                if self._run > self.max_run_down:
                    self.max_run_down = self._run
                if a > self.max_jump_down:
                    self.max_jump_down = a
        self._prev = mid
        self.last_tick_ms = ts_ms

    # ------------------------------------------------------------------ export
    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in RAW_FIELDS}
        d["t"] = self.bucket_ms
        return d

    def to_row(self, symbol: str, source: str = "live") -> tuple:
        """Values in DB_COLUMNS order, ready for the Oracle MERGE."""
        vals = []
        for f in RAW_FIELDS:
            v = getattr(self, f)
            vals.append(int(v) if f in INT_FIELDS else (None if v is None else float(v)))
        return (symbol, int(self.bucket_ms)) + tuple(vals) + (source,)

    def snapshot(self) -> dict:
        d = self.to_dict()
        d["_prev"] = self._prev
        d["_dir"] = self._dir
        d["_run"] = self._run
        d["last_tick_ms"] = self.last_tick_ms
        return d

    @classmethod
    def from_snapshot(cls, d: dict) -> "CandleState":
        st = cls(int(d["t"]), d.get("_prev"), int(d.get("_dir", 0)), int(d.get("_run", 0)))
        for f in RAW_FIELDS:
            if f in d and d[f] is not None:
                setattr(st, f, d[f])
        st.start_mid = d.get("start_mid")
        st.last_tick_ms = int(d.get("last_tick_ms", 0))
        return st


# ---------------------------------------------------------------------- derived metrics
def derive(c: dict, tf_seconds: int) -> dict:
    """Compute derived metrics from raw candle fields (works for rows with missing metrics)."""
    o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
    out: dict = {}
    if o is None or cl is None:
        return out
    rng = h - l
    net = cl - o
    out["range"] = _r(rng)
    out["net_move"] = _r(net)
    out["direction"] = "up" if net > EPS else "down" if net < -EPS else "flat"
    out["close_location"] = _r((cl - l) / rng) if rng > EPS else 0.5
    out["body_ratio"] = _r(abs(net) / rng) if rng > EPS else 0.0
    out["wick_upper"] = _r(h - max(o, cl))
    out["wick_lower"] = _r(min(o, cl) - l)

    up, down = c.get("up_ticks"), c.get("down_ticks")
    if up is None or down is None:
        return out  # backfilled candle: price-only metrics

    n = c.get("tick_count") or 0
    ud = up + down
    mu, md = c.get("move_up") or 0.0, c.get("move_down") or 0.0
    mv = mu + md
    path = c.get("path_length") or 0.0
    start = c.get("start_mid")
    if start is None:
        start = o
    out["imbalance"] = _r((up - down) / ud) if ud else 0.0
    out["move_imbalance"] = _r((mu - md) / mv) if mv > 0 else 0.0
    out["up_down_ratio"] = _r(up / down) if down else None
    out["efficiency"] = _r(min(1.0, abs(cl - start) / path)) if path > EPS else None
    out["choppiness"] = _r((c.get("reversals") or 0) / ud) if ud else 0.0
    out["avg_spread"] = _r(c["spread_sum"] / n) if n and c.get("spread_sum") is not None else None
    out["avg_mid"] = _r(c["mid_sum"] / n) if n and c.get("mid_sum") is not None else None
    out["realized_vol"] = _r(math.sqrt(c["sum_sq_move"])) if c.get("sum_sq_move") is not None else None
    out["tick_velocity"] = _r(n / tf_seconds, 4) if tf_seconds else None  # ticks per second
    ju, jd = c.get("max_jump_up") or 0.0, c.get("max_jump_down") or 0.0
    if ju <= EPS and jd <= EPS:
        out["max_jump"], out["jump_dir"] = 0.0, "none"
    elif ju >= jd:
        out["max_jump"], out["jump_dir"] = _r(ju), "up"
    else:
        out["max_jump"], out["jump_dir"] = _r(-jd), "down"
    out["jump_share"] = _r(max(ju, jd) / rng) if rng > EPS else 0.0
    score = 0.5 * out["imbalance"] + 0.5 * out["move_imbalance"]
    out["pressure_score"] = _r(score)
    out["pressure"] = "buying" if score > 0.2 else "selling" if score < -0.2 else "balanced"
    return out


def metrics_block(c: dict, tf_seconds: int) -> dict:
    block = {f: _r(c.get(f)) if isinstance(c.get(f), float) else c.get(f) for f in METRIC_RAW_FIELDS}
    block.update(derive(c, tf_seconds))
    return block


BASIC_DERIVED = ("imbalance", "move_imbalance", "efficiency", "close_location", "avg_spread", "pressure")


def format_candle(row: dict, tf_seconds: int, level: str, now_ms: int) -> dict:
    """Shape a candle for API output. level: none | basic | full."""
    out = {
        "t": row["t"],
        "open": _r(row["open"]), "high": _r(row["high"]), "low": _r(row["low"]), "close": _r(row["close"]),
        "volume": row.get("tick_count"),
        "complete": row["t"] + tf_seconds * 1000 <= now_ms,
    }
    if level == "none":
        return out
    if level == "basic":
        d = derive(row, tf_seconds)
        for k in BASIC_DERIVED:
            if k in d:
                out[k] = d[k]
        return out
    out.update(metrics_block(row, tf_seconds))
    if row.get("minutes_present") is not None:
        out["minutes_present"] = row["minutes_present"]
        out["metric_minutes"] = row.get("metric_minutes")
    return out


def build_candle_messages(symbol: str, tf: str, tf_seconds: int, d: dict, final: bool) -> tuple[dict, dict]:
    """Return (basic_message, metrics_message) for websocket / pub-sub."""
    basic = {
        "e": "candle", "symbol": symbol, "tf": tf, "t": d["t"],
        "open": _r(d["open"]), "high": _r(d["high"]), "low": _r(d["low"]), "close": _r(d["close"]),
        "volume": d["tick_count"], "final": final,
    }
    full = dict(basic)
    full["e"] = "candle_metrics"
    full.update(metrics_block(d, tf_seconds))
    return basic, full


def merge_rows(rows: list[dict]) -> dict:
    """Merge consecutive candle rows (oldest first) into one candle. Used for summaries."""
    first, last = rows[0], rows[-1]

    def s(k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return sum(vals) if vals else None

    def mx(k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return max(vals) if vals else None

    def mn(k):
        vals = [r[k] for r in rows if r.get(k) is not None]
        return min(vals) if vals else None

    return {
        "t": first["t"], "open": first["open"], "close": last["close"],
        "high": max(r["high"] for r in rows), "low": min(r["low"] for r in rows),
        "tick_count": s("tick_count"), "up_ticks": s("up_ticks"), "down_ticks": s("down_ticks"),
        "move_up": s("move_up"), "move_down": s("move_down"), "path_length": s("path_length"),
        "reversals": s("reversals"), "max_run_up": mx("max_run_up"), "max_run_down": mx("max_run_down"),
        "max_jump_up": mx("max_jump_up"), "max_jump_down": mx("max_jump_down"),
        "spread_sum": s("spread_sum"), "min_spread": mn("min_spread"), "max_spread": mx("max_spread"),
        "mid_sum": s("mid_sum"), "sum_sq_move": s("sum_sq_move"),
        "start_mid": first.get("start_mid"), "bid_close": last.get("bid_close"), "ask_close": last.get("ask_close"),
    }
