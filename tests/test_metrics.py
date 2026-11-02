import random

from app.core.metrics import DB_COLUMNS, CandleState, derive, merge_rows
from app.ingest.engine import CandleEngine


def feed(state, ticks, t0=0):
    for i, mid in enumerate(ticks):
        state.add_tick(t0 + i, mid - 0.00001, mid + 0.00001, mid)


def test_up_up_then_big_drop():
    s = CandleState(0)
    feed(s, [100, 101, 102, 97])
    d = s.to_dict()
    assert d["open"] == 100 and d["close"] == 97 and d["high"] == 102 and d["low"] == 97
    assert s.up_ticks == 2 and s.down_ticks == 1
    assert s.max_jump_down == 5 and s.max_jump_up == 1
    assert s.reversals == 1 and s.max_run_up == 2
    der = derive(d, 60)
    assert der["jump_dir"] == "down" and der["max_jump"] == -5 and der["direction"] == "down"
    assert der["close_location"] == 0.0


def test_oscillation_gives_low_efficiency():
    s = CandleState(0)
    feed(s, [100, 101, 100, 101, 100, 101, 100.5])
    assert derive(s.to_dict(), 60)["efficiency"] < 0.2 and s.reversals >= 4


def test_flat_ticks_not_counted_as_direction():
    s = CandleState(0)
    feed(s, [1.0, 1.0, 1.0])
    assert s.tick_count == 3 and s.up_ticks == 0 and s.down_ticks == 0


def test_additivity_across_candles():
    """Sums of 1m candles (with carry-over) must equal one big candle built from the same ticks."""
    random.seed(7)
    mids, p = [], 1.1
    for _ in range(600):
        p = round(p + random.choice([-2, -1, 0, 1, 2, 5, -7]) * 0.00001, 8)
        mids.append(p)
    big = CandleState(0)
    feed(big, mids)
    parts, cur = [], CandleState(0)
    for i, mid in enumerate(mids):
        if i and i % 100 == 0:
            parts.append(cur)
            cur = CandleState(i, cur._prev, cur._dir, cur._run)
        cur.add_tick(i, mid - 0.00001, mid + 0.00001, mid)
    parts.append(cur)
    merged = merge_rows([p_.to_dict() for p_ in parts])
    for k in ("tick_count", "up_ticks", "down_ticks", "reversals"):
        assert merged[k] == getattr(big, k), k
    for k in ("move_up", "move_down", "path_length", "sum_sq_move", "spread_sum"):
        assert abs(merged[k] - getattr(big, k)) < 1e-9, k
    assert merged["max_jump_up"] == big.max_jump_up and merged["max_jump_down"] == big.max_jump_down
    assert (merged["open"], merged["close"], merged["high"], merged["low"]) == (big.open, big.close, big.high, big.low)


def test_snapshot_roundtrip():
    s = CandleState(60000)
    feed(s, [1.0, 1.1, 1.05])
    r = CandleState.from_snapshot(s.snapshot())
    assert r.to_dict() == s.to_dict() and r._prev == s._prev and r._dir == s._dir


def test_derive_on_backfilled_candle_is_price_only():
    c = {"open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "tick_count": 50, "up_ticks": None, "down_ticks": None}
    der = derive(c, 60)
    assert "imbalance" not in der and der["range"] == 0.3


def test_db_row_matches_columns():
    s = CandleState(60000)
    feed(s, [1.0, 1.1])
    row = s.to_row("EUR_USD")
    assert len(row) == len(DB_COLUMNS) and row[0] == "EUR_USD" and row[1] == 60000 and row[-1] == "live"
    assert isinstance(row[DB_COLUMNS.index("tick_count")], int) and isinstance(row[DB_COLUMNS.index("open_px")], float)


class FakeHub:
    def __init__(self): self.msgs = []
    def publish(self, ch, p): self.msgs.append((ch, p))


class FakeWriter:
    def __init__(self): self.candles, self.ticks = [], []
    def add_tick(self, *a): self.ticks.append(a)
    def queue_candle(self, row): self.candles.append(row)


def test_engine_closes_minutes_and_carries_state():
    hub, wr = FakeHub(), FakeWriter()
    eng = CandleEngine(["1m", "5m"], hub, wr, publish_interval_ms=0)
    base = 1_700_000_040_000
    base -= base % 60_000
    eng.on_tick("EUR_USD", base + 1_000, 1.0, 1.0002)
    eng.on_tick("EUR_USD", base + 30_000, 1.001, 1.0012)
    assert wr.candles == []
    eng.on_tick("EUR_USD", base + 61_000, 1.002, 1.0022)
    assert len(wr.candles) == 1
    assert wr.candles[0][0] == "EUR_USD" and wr.candles[0][1] == base and wr.candles[0][-1] == "live"
    st2 = eng.states["EUR_USD"]["1m"]
    assert st2.up_ticks == 1 and st2.move_up > 0.0009  # carried move counts in the new candle
    eng.flush_due(base + 60_000 * 3)
    assert len(wr.candles) == 2
    chans = {m[0] for m in hub.msgs}
    assert {"px:EUR_USD", "cd:EUR_USD:1m", "cm:EUR_USD:5m"} <= chans
    assert "EUR_USD" in eng.last_payload and eng.forming("EUR_USD", "5m") is not None


def test_engine_late_tick_reopens_closed_candle():
    hub, wr = FakeHub(), FakeWriter()
    eng = CandleEngine(["1m"], hub, wr, publish_interval_ms=0, close_grace_ms=0)
    base = 1_700_000_040_000
    base -= base % 60_000
    eng.on_tick("X_Y", base + 1000, 1.0, 1.0)
    eng.flush_due(base + 61_000)
    assert len(wr.candles) == 1
    eng.on_tick("X_Y", base + 59_900, 1.5, 1.5)  # arrives late, same minute
    eng.flush_due(base + 62_000)
    assert len(wr.candles) == 2 and wr.candles[1][DB_COLUMNS.index("tick_count")] == 2
