"""Buffers closed 1m candles (and optionally ticks) and writes them to Oracle in batches."""
from __future__ import annotations

import asyncio

from app.core import logs
from app.store import repo


class DbWriter:
    def __init__(self, db, flush_ms: int, store_ticks: bool, max_buffer: int = 200_000):
        self.db = db
        self.flush_s = flush_ms / 1000.0
        self.store_ticks = store_ticks
        self.max_buffer = max_buffer
        self._ticks: list[tuple] = []
        self._candles: list[tuple] = []
        self._wake = asyncio.Event()
        self.ticks_written = 0
        self.candles_written = 0
        self.ticks_dropped = 0

    def add_tick(self, ts_ms: int, symbol: str, bid: float, ask: float) -> None:
        if not self.store_ticks:
            return
        if len(self._ticks) >= self.max_buffer:  # database has been unreachable for a long time
            del self._ticks[:20_000]
            self.ticks_dropped += 20_000
            logs.error_throttled("tick-drop", "The database has been unreachable for too long; the oldest buffered ticks were discarded.")
        self._ticks.append((symbol, ts_ms, bid, ask))

    def queue_candle(self, row: tuple) -> None:
        self._candles.append(row)
        self._wake.set()

    async def flush(self) -> None:
        candles, self._candles = self._candles, []
        ticks, self._ticks = self._ticks, []
        if candles:
            try:
                await repo.upsert_candles(self.db, candles)
                self.candles_written += len(candles)
            except Exception as exc:
                self._candles = candles + self._candles
                logs.error_throttled("candle-write", f"Could not save {len(candles)} closed candle(s) to the database; will retry shortly.", exc)
        if ticks:
            try:
                await repo.insert_ticks(self.db, ticks)
                self.ticks_written += len(ticks)
            except Exception as exc:
                self._ticks = ticks + self._ticks
                logs.error_throttled("tick-write", f"Could not save {len(ticks)} tick(s) to the database; will retry shortly.", exc)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self.flush()
        await self.flush()
