"""Turns a candle (CandleState) into one analysis row. Pure functions: easy to test, no database."""
from __future__ import annotations

import math
from collections import deque
from statistics import mean, pstdev

from app.analysis.columns import ROLL_H
from app.core.metrics import EPS, CandleState, derive
from app.core.timeframes import TF_SECONDS

TRAIL = 100        # candles kept for the z-scores
MIN_TRAIL = 20     # no z-score before this many candles


class Trail:
    """Recent returns / ranges / tick counts of one pair and timeframe (for z-scores)."""

    def __init__(self):
        self.ret, self.rng, self.ticks = deque(maxlen=TRAIL), deque(maxlen=TRAIL), deque(maxlen=TRAIL)

    def add(self, ret_pct, rng, ticks):
        if ret_pct is not None:
            self.ret.append(ret_pct)
        if rng is not None:
            self.rng.append(rng)
        if ticks is not None:
            self.ticks.append(ticks)


def zscore(x, values) -> float | None:
    if x is None or len(values) < MIN_TRAIL:
        return None
    sd = pstdev(values)
    return (x - mean(values)) / sd if sd > EPS else None


def build_row(symbol: str, tf: str, st: CandleState, *, run_id, sessions: str, now_ms: int, engine, prev_momentum=None,
              trail: Trail | None = None, forming: bool = False) -> dict:
    """`engine` must offer price_ago(symbol, at_ms) and window_hilo(symbol, from_ms, to_ms)."""
    tf_s = TF_SECONDS[tf]
    d = st.to_dict()
    der = derive(d, tf_s)
    start = st.start_mid if st.start_mid is not None else st.open
    net = st.close - st.open
    momentum = st.close - start
    n = st.tick_count
    elapsed = max(1.0, (now_ms - st.bucket_ms) / 1000.0) if forming else float(tf_s)
    avg_mid = st.mid_sum / n if n else None
    rv = math.sqrt(st.sum_sq_move)
    row = {
        "symbol": symbol, "tf": tf, "ts_ms": int(st.bucket_ms), "run_id": run_id, "sessions": sessions,
        "partial": 1 if (st.first_tick_ms and st.first_tick_ms - st.bucket_ms > 20_000) else 0, "captured_ms": now_ms,
        "open_px": st.open, "high_px": st.high, "low_px": st.low, "close_px": st.close, "rng": st.high - st.low, "tick_count": n,
        "direction": "BULLISH" if net > EPS else "BEARISH" if net < -EPS else "FLAT",
        "body_ratio": der.get("body_ratio"), "net_move": net, "start_mid": start, "momentum": momentum,
        "ret_pct": (st.close / start - 1) * 100 if start else None,
        "logret_pct": math.log(st.close / start) * 100 if start and st.close > 0 and start > 0 else None,
        "momentum_accel": (momentum - prev_momentum) if prev_momentum is not None else None,
        "max_jump": der.get("max_jump"), "jump_share": der.get("jump_share"), "max_run_up": st.max_run_up, "max_run_down": st.max_run_down,
        "max_pullback_high": st.max_pullback, "max_bounce_low": st.max_bounce, "up_ticks": st.up_ticks, "down_ticks": st.down_ticks,
        "imbalance": der.get("imbalance"), "move_imbalance": der.get("move_imbalance"), "move_up": st.move_up, "move_down": st.move_down,
        "path_length": st.path_length, "reversals": st.reversals, "rv": rv, "rv_pct": rv / avg_mid * 100 if avg_mid else None,
        "tick_rate": n / elapsed, "avg_spread": st.spread_sum / n if n else None, "min_spread": st.min_spread, "max_spread": st.max_spread,
    }
    t_end = st.last_tick_ms or now_ms
    for h in ROLL_H:                       # rolling windows ending at the candle's last tick
        p1 = engine.price_ago(symbol, t_end - h * 1000)
        p2 = engine.price_ago(symbol, t_end - 2 * h * 1000)
        hl = engine.window_hilo(symbol, t_end - h * 1000, t_end)
        row[f"ret_{h}s"] = (st.close / p1 - 1) * 100 if p1 else None
        row[f"mom_{h}s"] = st.close - p1 if p1 else None
        row[f"rng_{h}s"] = (hl[0] - hl[1]) if hl else None
        row[f"acc_{h}s"] = st.close - 2 * p1 + p2 if p1 and p2 else None
    if trail is not None:
        row["ret_z"] = zscore(row["ret_pct"], trail.ret)
        row["range_z"] = zscore(row["rng"], trail.rng)
        row["ticks_z"] = zscore(float(n), trail.ticks)
    else:
        row["ret_z"] = row["range_z"] = row["ticks_z"] = None
    return row
