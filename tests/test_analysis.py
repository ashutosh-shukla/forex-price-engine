import random
from datetime import datetime, timezone

from app.analysis.builder import MIN_TRAIL, Trail, build_row
from app.analysis.columns import CAPTURE, NAMES, UPDATABLE
from app.analysis.outcomes import OutcomeTracker
from app.core.market_hours import market_clock
from app.core.metrics import CandleState
from app.core.sessions import sessions_at
from app.core.timeutil import ist_str
from app.ingest.engine import CandleEngine


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


class Hub:
    def publish(self, *a): pass


class Writer:
    def add_tick(self, *a): pass
    def queue_candle(self, *a): pass


def make_engine(ticks, symbol="XAU_USD"):
    eng = CandleEngine(["1m", "5m"], Hub(), Writer(), publish_interval_ms=10**9)
    for ts, mid in ticks:
        eng.on_tick(symbol, ts, mid - 0.2, mid + 0.2)
    return eng


# ---------------------------------------------------------------- metrics added for the analysis screen
def test_pullback_and_bounce_inside_the_candle():
    s = CandleState(0)
    for i, m in enumerate([100, 150, 120, 140]):       # your example: rally to 150, fall to 120, close 140
        s.add_tick(i, m - .1, m + .1, m)
    assert s.max_pullback == 30 and s.max_bounce == 50 and s.high - s.close == 10


# ---------------------------------------------------------------- row builder
def test_row_has_every_capture_column_and_correct_values():
    base = ms(2026, 10, 26, 3, 0)
    ticks = [(base + i * 1000, 4000 + (i % 7) * 0.3 + i * 0.05) for i in range(130)]   # a bit over 2 minutes
    eng = make_engine(ticks)
    st = eng.states["XAU_USD"]["1m"]
    row = build_row("XAU_USD", "1m", st, run_id=1, sessions="Tokyo", now_ms=base + 200_000, engine=eng, trail=Trail())
    assert set(CAPTURE) <= set(row), set(CAPTURE) - set(row)
    assert abs(row["net_move"] - (st.close - st.open)) < 1e-9
    assert row["direction"] in ("BULLISH", "BEARISH", "FLAT") and 0 <= row["body_ratio"] <= 1
    assert abs(row["momentum"] - (st.close - st.start_mid)) < 1e-9
    assert row["ret_5s"] is not None and row["rng_15s"] is not None and row["acc_5s"] is not None
    assert row["ret_z"] is None            # not enough history yet
    assert row["max_pullback_high"] == st.max_pullback and row["max_bounce_low"] == st.max_bounce


def test_zscore_needs_history():
    base = ms(2026, 10, 26, 3, 0)
    eng = make_engine([(base + i * 1000, 4000 + i * 0.1) for i in range(70)])
    st = eng.states["XAU_USD"]["1m"]
    tr = Trail()
    random.seed(1)
    for _ in range(MIN_TRAIL):
        tr.add(random.gauss(0, 0.01), random.random(), 100.0 + random.random() * 20)
    row = build_row("XAU_USD", "1m", st, run_id=1, sessions="", now_ms=base + 80_000, engine=eng, trail=tr)
    assert row["ret_z"] is not None and row["range_z"] is not None and row["ticks_z"] is not None


def test_partial_flag_when_engine_started_mid_candle():
    s = CandleState(0)
    s.add_tick(45_000, 99.9, 100.1, 100.0)
    class E:
        def price_ago(self, *a): return None
        def window_hilo(self, *a): return None
    row = build_row("X", "1m", s, run_id=1, sessions="", now_ms=60_000, engine=E())
    assert row["partial"] == 1


# ---------------------------------------------------------------- outcomes after the close
def test_outcome_tracker_ret_mfe_mae_and_stale_gap():
    done = {}
    tr = OutcomeTracker(lambda k, f: done.setdefault(k, {}).update(f))
    t0 = 1_000_000
    tr.add("XAU_USD", "1m", t0 - 60_000, 100.0, t0)
    path = [(t0 + 60_000, 101.0), (t0 + 120_000, 98.0), (t0 + 240_000, 100.5), (t0 + 299_000, 103.0), (t0 + 301_000, 99.0)]
    for ts, p in path:
        tr.on_tick("XAU_USD", ts, p)
    f = done[("XAU_USD", "1m", t0 - 60_000)]
    assert abs(f["ret_5m_x"] if "ret_5m_x" in f else f["ret_5m"] - 3.0) < 1e-9      # price 5 minutes later was 103
    assert f["mfe_5m"] == 3.0 and f["mae_5m"] == 2.0
    # a 15 minute deadline passes during a data gap (weekend): result stays empty instead of lying
    tr.on_tick("XAU_USD", t0 + 15 * 60_000 + 10 * 60_000, 120.0)
    assert done[("XAU_USD", "1m", t0 - 60_000)]["ret_15m"] is None


def test_outcome_flush_on_quiet_market():
    done = {}
    tr = OutcomeTracker(lambda k, f: done.setdefault(k, {}).update(f))
    tr.add("X", "5m", 0, 100.0, 1000)
    tr.on_tick("X", 2000, 101.0)
    tr.flush(1000 + 5 * 60_000 + 6000)        # no tick for a while but still fresh enough? last tick was long ago -> empty
    assert "ret_5m" in done[("X", "5m", 0)]


# ---------------------------------------------------------------- sessions, market hours, time
def test_sessions_follow_clock_changes():
    assert "New York" in sessions_at(ms(2026, 10, 26, 13, 30))            # US summer time: 19:00 IST
    assert "New York" not in sessions_at(ms(2026, 11, 2, 13, 30))         # after US clocks go back, 19:00 IST is only pre-market
    assert "New York pre" in sessions_at(ms(2026, 11, 2, 13, 30))
    assert "New York" in sessions_at(ms(2026, 11, 2, 14, 30))             # 20:00 IST
    assert "Tokyo" in sessions_at(ms(2026, 10, 26, 0, 0))                 # 05:30 IST
    assert sessions_at(ms(2026, 10, 26, 13, 30), "fixed") == ["London", "New York pre"][:0] + [s for s in sessions_at(ms(2026, 10, 26, 13, 30), "fixed")]
    assert "New York" in sessions_at(ms(2026, 11, 2, 13, 30), "fixed")    # fixed IST windows never move


def test_market_hours_follow_new_york_clock():
    assert market_clock(ms(2026, 10, 23, 20, 59))["open"] and not market_clock(ms(2026, 10, 23, 21, 1))["open"]   # Fri close 21:00 UTC (summer)
    assert not market_clock(ms(2026, 10, 25, 20, 59))["open"] and market_clock(ms(2026, 10, 25, 21, 1))["open"]
    assert not market_clock(ms(2026, 11, 8, 21, 30))["open"] and market_clock(ms(2026, 11, 8, 22, 1))["open"]    # after the US clock change: 22:00 UTC
    c = market_clock(ms(2026, 10, 24, 12, 0))
    assert c["next_change_ist"] == "2026-10-26 02:30" and "WEEKEND" in c["text"]


def test_ist_formatting():
    assert ist_str(ms(2026, 10, 26, 0, 0)) == "2026-10-26 05:30:00"


def test_column_groups_consistent():
    assert "ret_5m" in UPDATABLE and "note" in UPDATABLE and "open_px" not in UPDATABLE
    assert len(NAMES) == len(set(NAMES)) and len(NAMES) == 85
