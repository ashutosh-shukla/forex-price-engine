"""Fills in what happened AFTER a candle closed (5, 15, 60 minutes later), tick by tick.

Entry price = the candle's closing mid. For a hypothetical long entered there:
  ret_Xm = price change after X minutes (in %), mfe_Xm = best rise since entry, mae_Xm = worst fall since entry.
"""
from __future__ import annotations

HORIZONS = (5, 15, 60)
STALE_MS = 120_000       # if the price data is older than this at the deadline (weekend, gap) the outcome is left empty


class Pending:
    __slots__ = ("key", "entry", "t0", "hi", "lo", "last", "last_ts", "todo")

    def __init__(self, key, entry, t0):
        self.key, self.entry, self.t0 = key, entry, t0
        self.hi = self.lo = self.last = entry
        self.last_ts = t0
        self.todo = {h: t0 + h * 60_000 for h in HORIZONS}


class OutcomeTracker:
    def __init__(self, on_done):
        self.on_done = on_done           # fn(key, fields_dict)
        self.items: dict[str, list[Pending]] = {}

    def add(self, symbol: str, tf: str, ts_ms: int, entry: float, t0_ms: int) -> None:
        self.items.setdefault(symbol, []).append(Pending((symbol, tf, ts_ms), entry, t0_ms))

    def _finish(self, p: Pending, h: int, ok: bool) -> None:
        f = {}
        if ok:
            f = {f"ret_{h}m": (p.last / p.entry - 1) * 100, f"mfe_{h}m": p.hi - p.entry, f"mae_{h}m": p.entry - p.lo}
        else:
            f = {f"ret_{h}m": None, f"mfe_{h}m": None, f"mae_{h}m": None}
        del p.todo[h]
        self.on_done(p.key, f)

    def on_tick(self, symbol: str, ts_ms: int, mid: float) -> None:
        lst = self.items.get(symbol)
        if not lst:
            return
        for p in lst:
            for h in [h for h, dl in p.todo.items() if ts_ms >= dl]:
                self._finish(p, h, ok=(ts_ms - p.last_ts) <= STALE_MS and ts_ms - p.todo[h] <= STALE_MS)
            if ts_ms > p.t0:
                p.last, p.last_ts = mid, ts_ms
                if mid > p.hi:
                    p.hi = mid
                if mid < p.lo:
                    p.lo = mid
        self.items[symbol] = [p for p in lst if p.todo]

    def flush(self, now_ms: int) -> None:
        """Quiet markets: close out deadlines even when no new tick arrives."""
        for symbol, lst in self.items.items():
            for p in lst:
                for h in [h for h, dl in p.todo.items() if now_ms >= dl + 5_000]:
                    self._finish(p, h, ok=(now_ms - p.last_ts) <= STALE_MS)
            self.items[symbol] = [p for p in lst if p.todo]

    def count(self) -> int:
        return sum(len(v) for v in self.items.values())
