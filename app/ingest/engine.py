"""CandleEngine: turns ticks into live candles for every timeframe, computes metrics,
publishes live updates (in-process hub) and hands closed 1m candles to the database writer.

Pure in-memory + callbacks, so it is fast and easy to test.
"""
from __future__ import annotations

import time
from collections import deque

from app.core import jsonx, logs
from app.core.metrics import CandleState, build_candle_messages
from app.core.timeframes import TF_SECONDS


class CandleEngine:
    def __init__(self, timeframes: list[str], hub, writer, publish_interval_ms: int = 250, close_grace_ms: int = 2000):
        self.tfs = [(tf, TF_SECONDS[tf] * 1000) for tf in timeframes]
        if "1m" not in timeframes:
            raise ValueError("LIVE_TIMEFRAMES must include 1m (it is the base of all history)")
        self.hub = hub
        self.writer = writer
        self.interval = publish_interval_ms / 1000.0
        self.grace = close_grace_ms
        self.states: dict[str, dict[str, CandleState]] = {}
        self._last_pub: dict[tuple[str, str], float] = {}
        self.last_tick_ms: dict[str, int] = {}
        self.last_payload: dict[str, str] = {}
        self.recent: dict[str, deque] = {}            # last few minutes of (ts_ms, mid) per pair, for rolling windows
        self.close_listeners: list = []               # fn(symbol, tf, CandleState) called when a candle closes
        self.tick_listeners: list = []                # fn(symbol, ts_ms, mid) called for every price
        self.ticks_total = 0
        self.ticks_window = 0
        self.late_ticks = 0
        self.candles_closed = 0

    # ------------------------------------------------------------------ tick path
    def on_tick(self, symbol: str, ts_ms: int, bid: float, ask: float) -> None:
        mid = round((bid + ask) * 0.5, 8)
        self.ticks_total += 1
        self.ticks_window += 1
        self.last_tick_ms[symbol] = ts_ms

        payload = jsonx.dumps({
            "e": "tick", "symbol": symbol, "t": ts_ms, "bid": bid, "ask": ask, "mid": mid,
            "p": int(time.time() * 1000),
        })
        self.last_payload[symbol] = payload
        buf = self.recent.get(symbol)
        if buf is None:
            buf = self.recent[symbol] = deque(maxlen=4000)
        buf.append((ts_ms, mid))
        self.hub.publish(f"px:{symbol}", payload)
        self.writer.add_tick(ts_ms, symbol, bid, ask)

        sym_states = self.states.get(symbol)
        if sym_states is None:
            sym_states = self.states[symbol] = {}
        now = time.monotonic()
        for name, tf_ms in self.tfs:
            bucket = ts_ms - ts_ms % tf_ms
            st = sym_states.get(name)
            if st is None:
                st = sym_states[name] = CandleState(bucket)
            elif bucket != st.bucket_ms:
                if bucket < st.bucket_ms:
                    self.late_ticks += 1
                    continue
                self._close(symbol, name, st)
                st = sym_states[name] = CandleState(bucket, st._prev, st._dir, st._run)
                self._last_pub[(symbol, name)] = 0.0  # publish the new candle immediately
            st.add_tick(ts_ms, bid, ask, mid)
            st.emitted = False  # a late tick re-opens a candle that was closed by the timer
            key = (symbol, name)
            if now - self._last_pub.get(key, 0.0) >= self.interval:
                self._last_pub[key] = now
                self._publish_forming(symbol, name, st, final=False)
        for fn in self.tick_listeners:
            try:
                fn(symbol, ts_ms, mid)
            except Exception as exc:
                logs.error_throttled("tick-listener", "A price listener failed.", exc)

    # ------------------------------------------------------------------ closing
    def _publish_forming(self, symbol: str, name: str, st: CandleState, final: bool) -> None:
        tf_s = TF_SECONDS[name]
        basic, full = build_candle_messages(symbol, name, tf_s, st.to_dict(), final)
        self.hub.publish(f"cd:{symbol}:{name}", jsonx.dumps(basic))
        self.hub.publish(f"cm:{symbol}:{name}", jsonx.dumps(full))

    def _close(self, symbol: str, name: str, st: CandleState) -> None:
        if st.emitted or st.tick_count == 0:
            return
        st.emitted = True
        self._publish_forming(symbol, name, st, final=True)
        if name == "1m":
            self.writer.queue_candle(st.to_row(symbol))
            self.candles_closed += 1
        for fn in self.close_listeners:
            try:
                fn(symbol, name, st)
            except Exception as exc:
                logs.error_throttled("close-listener", "A candle-close listener failed.", exc)

    def flush_due(self, now_ms: int) -> None:
        """Close candles whose time is over even if no new tick arrived (quiet markets)."""
        for symbol, per_tf in self.states.items():
            for name, tf_ms in self.tfs:
                st = per_tf.get(name)
                if st is not None and not st.emitted and st.tick_count and st.bucket_ms + tf_ms + self.grace <= now_ms:
                    self._close(symbol, name, st)

    def drop_symbol(self, symbol: str) -> None:
        per_tf = self.states.pop(symbol, None)
        if per_tf and "1m" in per_tf and not per_tf["1m"].emitted and per_tf["1m"].tick_count:
            self.writer.queue_candle(per_tf["1m"].to_row(symbol))

    # ------------------------------------------------------------------ rolling windows
    def price_ago(self, symbol: str, at_ms: int) -> float | None:
        """Mid price of the last tick at or before `at_ms` (None if we have no data that old)."""
        buf = self.recent.get(symbol)
        if not buf:
            return None
        for ts, mid in reversed(buf):
            if ts <= at_ms:
                return mid
        return None

    def window_hilo(self, symbol: str, from_ms: int, to_ms: int):
        """(highest mid, lowest mid) between two times, or None if there were no ticks."""
        buf = self.recent.get(symbol)
        if not buf:
            return None
        hi = lo = None
        for ts, mid in reversed(buf):
            if ts > to_ms:
                continue
            if ts < from_ms:
                break
            hi = mid if hi is None or mid > hi else hi
            lo = mid if lo is None or mid < lo else lo
        return (hi, lo) if hi is not None else None

    # ------------------------------------------------------------------ lifecycle
    def forming(self, symbol: str, tf: str):
        return self.states.get(symbol, {}).get(tf)

    def shutdown(self) -> None:
        """Save the partially built 1m candles so nothing is lost on a graceful stop."""
        saved = 0
        for symbol, per_tf in self.states.items():
            st = per_tf.get("1m")
            if st is not None and st.tick_count and not st.emitted:
                self.writer.queue_candle(st.to_row(symbol))
                saved += 1
        if saved:
            logs.activity(f"Saved {saved} partially built one-minute candle(s) before shutting down.")
