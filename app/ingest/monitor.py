"""Health snapshot, stale-pair detection, alerts and the hourly summary."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app.core import logs
from app.core.market_hours import market_clock


def market_probably_open(now: datetime) -> bool:
    """Clock-based: spot forex is closed from Friday 17:00 to Sunday 17:00 New York time."""
    return market_clock(now.timestamp() * 1000)["open"]


class Monitor:
    def __init__(self, service):
        self.svc = service
        self.started = time.time()
        self._was_open = None
        self._last_summary = time.monotonic()
        self._summary_base = (0, 0, 0)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logs.error_throttled("monitor", "The health monitor hit an error.", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        svc, s = self.svc, self.svc.s
        engine, writer, stream = svc.engine, svc.writer, svc.stream
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        symbols = list(svc.symbols)
        feed = {}
        for sym in symbols:
            last = engine.last_tick_ms.get(sym)
            feed[sym] = {"last_tick_ms": last, "age_s": round((now_ms - last) / 1000, 1) if last else None}
        connected = bool(stream and stream.connected)
        svc.health = {
            "ts": now_ms, "stream_enabled": stream is not None, "stream_connected": connected,
            "market_open": market_probably_open(now), "pairs": feed, "ticks_total": engine.ticks_total,
            "late_ticks": engine.late_ticks, "candles_closed": engine.candles_closed,
            "candles_written": writer.candles_written, "ticks_written": writer.ticks_written,
            "uptime_s": int(time.time() - self.started),
        }
        is_open = svc.health["market_open"]
        if self._was_open is not None and is_open != self._was_open:
            logs.milestone("The forex market has opened for the week; prices should start flowing." if is_open else "The forex market has closed for the weekend; the stream is paused until it reopens.")
        self._was_open = is_open

        if is_open and connected:
            for sym, info in feed.items():
                age = info["age_s"]
                if age is not None and age > s.STALE_PAIR_SECONDS:
                    msg = f"No new price for {sym} in {int(age)} seconds while the market is open."
                    logs.warn(msg)
                    await svc.alerter.send(f"stale:{sym}", msg)
        if is_open and stream is not None and not connected and time.time() - self.started > 60:
            await svc.alerter.send("stream-down", "The OANDA price stream is disconnected while the market is open.")

        if time.monotonic() - self._last_summary >= 3600:
            t0, c0, w0 = self._summary_base
            ticks, candles, written = engine.ticks_total, engine.candles_closed, writer.candles_written
            logs.milestone(f"Hourly summary: processed {ticks - t0} prices, closed {candles - c0} one-minute candles and saved {written - w0} candles across {len(symbols)} pairs.")
            self._summary_base = (ticks, candles, written)
            self._last_summary = time.monotonic()
