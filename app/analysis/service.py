"""Manual analysis: when you press Start, every candle that closes (1m, 5m, 15m, 30m, 1h) for the chosen pair is
saved as one row with all metrics. OANDA's official candle, what happened afterwards, and your labels are added later.
The normal engine (live candles, 1-minute history) is not touched by any of this."""
from __future__ import annotations

import asyncio
import time

from app.analysis import official
from app.analysis.builder import Trail, build_row
from app.analysis.columns import DIRECTIONS, LABELS
from app.analysis.outcomes import OutcomeTracker
from app.core import logs
from app.core.market_hours import market_clock
from app.core.sessions import session_text
from app.core.timeframes import TF_SECONDS
from app.store import analysis_repo as repo

ANALYSIS_TFS = ["1m", "5m", "15m", "30m", "1h"]


class AnalysisService:
    def __init__(self, settings, db, ingest, ready: bool):
        self.s, self.db, self.ingest = settings, db, ingest
        self.engine = ingest.engine
        self.ready = ready
        self.active = False
        self.run: dict | None = None
        self.symbol = "XAU_USD"
        self.tfs: list[str] = list(ANALYSIS_TFS)
        self.trails: dict[tuple, Trail] = {}
        self.last: dict[tuple, dict] = {}
        self.tracker = OutcomeTracker(self._outcome_done)
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self.captured = 0
        self.engine.close_listeners.append(self._on_close)
        self.engine.tick_listeners.append(self.tracker.on_tick)

    # ------------------------------------------------------------------ lifecycle
    def begin_background(self) -> None:
        self._loop_task = asyncio.create_task(self._housekeeping())

    async def shutdown(self) -> None:
        self._stop.set()
        if self.active:
            await self.stop()
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _housekeeping(self) -> None:
        while not self._stop.is_set():
            try:
                self.tracker.flush(int(time.time() * 1000))
            except Exception as exc:
                logs.error_throttled("analysis-flush", "Analysis housekeeping failed.", exc)
            await asyncio.sleep(1)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ start / stop
    async def start(self, symbol: str, tfs: list[str] | None = None) -> dict:
        if not self.ready:
            raise RuntimeError("The analysis tables do not exist yet. Run initdb.bat (or sql/oracle_schema.sql) once, then restart.")
        if self.active:
            raise RuntimeError("Analysis is already running. Stop it first.")
        tfs = [t for t in (tfs or ANALYSIS_TFS) if t in ANALYSIS_TFS and t in self.engine_tfs()]
        if not tfs:
            raise RuntimeError("No valid timeframe selected.")
        now = int(time.time() * 1000)
        self.symbol, self.tfs = symbol, tfs
        self.trails.clear()
        self.last.clear()
        self.captured = 0
        await repo.create_run(self.db, now, symbol, tfs, now)
        self.run = {"run_id": now, "symbol": symbol, "tfs": tfs, "started_ms": now}
        self.active = True
        logs.milestone(f"Analysis started for {symbol} on {', '.join(tfs)} (run {now}).")
        self._spawn(self._seed_trails(symbol, tfs))
        return self.run

    async def stop(self) -> dict | None:
        if not self.active:
            return None
        self.active = False
        run, now = self.run, int(time.time() * 1000)
        await repo.stop_run(self.db, run["run_id"], now)
        run["stopped_ms"] = now
        logs.milestone(f"Analysis stopped (run {run['run_id']}): {self.captured} candles were captured.")
        return run

    def engine_tfs(self) -> list[str]:
        return [name for name, _ in self.engine.tfs]

    async def _seed_trails(self, symbol: str, tfs: list[str]) -> None:
        """Start the z-scores from OANDA's recent candles instead of waiting for 20 live ones."""
        for tf in tfs:
            try:
                hist = await official.fetch_history(self.s, symbol, tf, 100)
                tr = self.trails.setdefault((symbol, tf), Trail())
                for h in hist:
                    tr.add(h["ret_pct"], h["rng"], None)
            except Exception as exc:
                logs.error_throttled("analysis-seed", f"Could not load recent {tf} candles to start the z-scores; they will start empty.", exc)

    # ------------------------------------------------------------------ capture
    def _on_close(self, symbol: str, tf: str, st) -> None:
        if not self.active or symbol != self.symbol or tf not in self.tfs:
            return
        now_ms = int(time.time() * 1000)
        key = (symbol, tf)
        tf_ms = TF_SECONDS[tf] * 1000
        prev = self.last.get(key)
        consecutive = prev is not None and prev["ts_ms"] + tf_ms == st.bucket_ms
        trail = self.trails.setdefault(key, Trail())
        row = build_row(symbol, tf, st, run_id=self.run["run_id"], sessions=session_text(st.bucket_ms, self.s.SESSION_MODE), now_ms=now_ms,
                        engine=self.engine, prev_momentum=prev["momentum"] if consecutive else None, trail=trail)
        if consecutive:   # the candle before this one now knows what came next
            nxt = {"next_dir": row["direction"], "next_ret_pct": row["ret_pct"], "next_range": row["rng"], "next_body_ratio": row["body_ratio"],
                   "actual_direction": {"BULLISH": "UP", "BEARISH": "DOWN"}.get(row["direction"], "FLAT"), "updated_ms": now_ms}
            self._spawn(self._safe(repo.update_fields(self.db, symbol, tf, prev["ts_ms"], nxt), "next-candle result"))
        self.last[key] = {"ts_ms": row["ts_ms"], "momentum": row["momentum"]}
        trail.add(row["ret_pct"], row["rng"], float(row["tick_count"]))
        self.tracker.add(symbol, tf, row["ts_ms"], st.close, st.last_tick_ms or now_ms)
        self.captured += 1
        self._spawn(self._store(row, st))

    async def _safe(self, coro, what: str) -> None:
        try:
            await coro
        except Exception as exc:
            logs.error_throttled(f"analysis-{what}", f"Could not save the analysis {what}.", exc)

    async def _store(self, row: dict, st) -> None:
        try:
            await repo.capture_row(self.db, row)
        except Exception as exc:
            logs.error_throttled("analysis-capture", "Could not save an analysis row to the database.", exc)
            return
        for delay in (4, 8, 12, 20):          # OANDA publishes its own candle a few seconds after the close
            await asyncio.sleep(delay)
            try:
                off = await official.fetch_official(self.s, row["symbol"], row["tf"], row["ts_ms"])
            except Exception:
                off = None
            if off:
                off["coverage"] = (row["tick_count"] / off["off_volume"]) if off["off_volume"] else None
                await self._safe(repo.update_fields(self.db, row["symbol"], row["tf"], row["ts_ms"], off), "official candle")
                return

    def _outcome_done(self, key, fields: dict) -> None:
        symbol, tf, ts_ms = key
        self._spawn(self._safe(repo.update_fields(self.db, symbol, tf, ts_ms, fields), "outcome"))

    # ------------------------------------------------------------------ live view and state
    def live(self, symbol: str) -> dict:
        now_ms = int(time.time() * 1000)
        out = {}
        for tf in ANALYSIS_TFS:
            st = self.engine.forming(symbol, tf)
            if st is None or not st.tick_count:
                continue
            trail = self.trails.get((symbol, tf))
            row = build_row(symbol, tf, st, run_id=None, sessions=session_text(st.bucket_ms, self.s.SESSION_MODE), now_ms=now_ms,
                            engine=self.engine, prev_momentum=None, trail=trail, forming=True)
            row["elapsed_s"] = round((now_ms - st.bucket_ms) / 1000.0, 1)
            row["closes_in_s"] = max(0, round((st.bucket_ms + TF_SECONDS[tf] * 1000 - now_ms) / 1000.0, 1))
            out[tf] = row
        return out

    def state(self) -> dict:
        now_ms = int(time.time() * 1000)
        clock = market_clock(now_ms)
        stream = self.ingest.stream
        market = {"open": clock["open"], "text": clock["text"], "next_change_ms": clock["next_change_ms"],
                  "stream_enabled": stream is not None, "stream_connected": bool(stream and stream.connected), "halted": bool(stream and stream.halted)}
        if clock["open"] and stream and stream.connected and stream.non_tradeable_count and time.time() - stream.last_tradeable_at > 120:
            market["open"], market["text"] = False, "OANDA reports the instrument as not tradeable (market closed or a holiday)."
        return {"ready": self.ready, "active": self.active, "run": self.run, "symbol": self.symbol, "tfs": self.tfs, "captured": self.captured,
                "pending_outcomes": self.tracker.count(), "now_ms": now_ms, "market": market,
                "sessions_now": session_text(now_ms, self.s.SESSION_MODE), "labels": list(LABELS), "directions": list(DIRECTIONS),
                "timeframes": ANALYSIS_TFS}
