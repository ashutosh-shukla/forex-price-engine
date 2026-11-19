"""Hands-free daily capture: starts the analysis at AUTO_ANALYSIS_START_IST, stops it at AUTO_ANALYSIS_END_IST,
and a while later (so the 5/15/60-minute outcomes are filled in) writes the day's CSV files to EXPORT_DIR.

Runs as a background task inside the engine. Manual Start/Stop on the Analysis screen still works; the scheduler
only ever stops a run that it started itself.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.analysis import exporter
from app.core import logs
from app.core.market_hours import market_clock
from app.core.timeutil import IST, ist_str
from app.store import analysis_repo as repo

STOP_GRACE_S = 15      # the last candle closes ~2 s after the boundary; let it be captured before stopping
TICK_S = 5


def _parse_hm(text: str) -> tuple[int, int]:
    h, m = text.strip().split(":")
    return int(h), int(m)


class AutoCapture:
    def __init__(self, settings, db, svc):
        self.s, self.db, self.svc = settings, db, svc
        self.start_hm, self.end_hm = _parse_hm(settings.AUTO_ANALYSIS_START_IST), _parse_hm(settings.AUTO_ANALYSIS_END_IST)
        self.symbol = settings.AUTO_ANALYSIS_SYMBOL
        self.export_dir = Path(settings.export_dir)
        self.auto_run_id: int | None = None
        self._attempted: set[str] = set()      # window dates already exported (or found empty)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def begin(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logs.milestone(f"Automatic capture is ON for {self.symbol}: {self.s.AUTO_ANALYSIS_START_IST}-{self.s.AUTO_ANALYSIS_END_IST} IST, "
                       f"files go to {self.export_dir}.")

    async def shutdown(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # ------------------------------------------------------------------ window maths (all IST)
    def window(self, day: datetime) -> tuple[datetime, datetime]:
        d = day.astimezone(IST).date()
        return (datetime(d.year, d.month, d.day, *self.start_hm, tzinfo=IST), datetime(d.year, d.month, d.day, *self.end_hm, tzinfo=IST))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logs.error_throttled("auto-capture", "The automatic capture loop hit a problem; it will try again.", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(IST)
        w_start, w_end = self.window(now)
        in_window = w_start <= now < w_end + timedelta(seconds=STOP_GRACE_S) if self.svc.active and self.auto_run_id else w_start <= now < w_end
        market_open = market_clock(int(now.timestamp() * 1000))["open"]

        if in_window and market_open and not self.svc.active:
            run = await self.svc.start(self.symbol)
            self.auto_run_id = run["run_id"]
            logs.milestone(f"Automatic capture started for {self.symbol} (run {self.auto_run_id}).")
        elif self.svc.active and self.auto_run_id == (self.svc.run or {}).get("run_id") and not in_window:
            await self.svc.stop()
            logs.milestone("Automatic capture stopped at the end of the window.")
            self.auto_run_id = None

        await self._maybe_export(now)

    # ------------------------------------------------------------------ export
    async def _maybe_export(self, now: datetime) -> None:
        delay = timedelta(minutes=self.s.AUTO_EXPORT_DELAY_MINUTES)
        for back in range(0, 4):                      # today plus the last 3 days, so a restart or outage never loses a file
            day = now - timedelta(days=back)
            key = day.astimezone(IST).strftime("%Y-%m-%d")
            w_start, w_end = self.window(day)
            if key in self._attempted or now < w_end + delay:
                continue
            if self.svc.active and back == 0:
                continue
            self._attempted.add(key)
            await self.export_window(w_start, w_end, key)

    async def export_window(self, w_start: datetime, w_end: datetime, key: str) -> list[Path]:
        lo, hi = int(w_start.timestamp() * 1000), int(w_end.timestamp() * 1000)
        tag = f"{self.symbol.replace('_', '').lower()}_{key}_{w_start:%H%M}-{w_end:%H%M}_IST"
        written: list[Path] = []

        rows = await repo.fetch_rows(self.db, self.symbol, None, lo, hi, None, 200_000, newest_first=False)
        if not rows:
            logs.activity(f"No analysis rows for {key}; nothing to export (weekend, holiday or the engine was off).")
            return written
        have = {r["ts_ms"] for r in rows if r["tf"] == "1m"}
        missing = [m for m in range(lo, hi, 60_000) if m not in have]
        if missing:
            logs.warn(f"{key}: {len(missing)} of {(hi - lo) // 60_000} one-minute analysis candles are missing (e.g. {ist_str(missing[0])} IST); "
                      "the engine 1m file below still has them if OANDA history filled the gap.")
        p = self.export_dir / f"{tag}_analysis_candles.csv"
        exporter.write_atomic(p, exporter.candles_csv_text(rows)); written.append(p)

        m1 = await self.db.fetchall("SELECT * FROM pe_candles_1m WHERE symbol = :s AND ts_ms >= :lo AND ts_ms < :hi ORDER BY ts_ms",
                                    {"s": self.symbol, "lo": lo, "hi": hi})
        if m1:
            p = self.export_dir / f"{tag}_engine_1m_candles.csv"
            exporter.write_atomic(p, exporter.generic_csv_text(m1)); written.append(p)

        notes = await repo.list_notes(self.db, lo, hi + 3_600_000, 100_000)
        if notes:
            p = self.export_dir / f"{tag}_notes.csv"
            exporter.write_atomic(p, exporter.generic_csv_text(list(reversed(notes)))); written.append(p)

        logs.milestone(f"Daily export for {key} saved: {len(rows)} analysis rows, {len(m1)} one-minute candles -> {self.export_dir}.")
        return written
