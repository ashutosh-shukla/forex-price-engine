"""Gap filling and history download from OANDA's candle endpoint (1-minute mid candles).

Backfilled rows only carry OHLC + OANDA's tick count; order-flow metrics exist only for
candles the engine built live. Live rows are never overwritten by backfilled rows.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from app.core import logs
from app.store import repo


class Backfiller:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db
        self._locks: dict[str, asyncio.Lock] = {}

    async def run_for(self, symbol: str, days: int | None = None) -> int:
        """days=None means 'since the last stored candle' (or BACKFILL_DAYS_NEW_PAIR when the pair is new)."""
        if not (self.s.OANDA_API_KEY and self.s.OANDA_ACCOUNT_ID):
            return 0  # no OANDA credentials: nothing to download from
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        if lock.locked():
            return 0
        async with lock:
            try:
                return await self._run(symbol, days)
            except Exception as exc:
                logs.error(f"Filling missing history for {symbol} failed: {exc}", exc)
                return 0

    async def _run(self, symbol: str, days: int | None) -> int:
        now = time.time()
        last_ms = await repo.latest_candle_ts(self.db, symbol)
        if days is not None:
            start = now - days * 86400
        elif last_ms is not None:
            start = last_ms / 1000 + 60
        elif self.s.BACKFILL_DAYS_NEW_PAIR > 0:
            start = now - self.s.BACKFILL_DAYS_NEW_PAIR * 86400
        else:
            return 0
        if start >= now - 90:
            return 0

        logs.activity(f"Filling missing one-minute history for {symbol} starting from {datetime.fromtimestamp(start, tz=timezone.utc):%Y-%m-%d %H:%M} UTC.")
        headers = {"Authorization": f"Bearer {self.s.OANDA_API_KEY}", "Accept-Datetime-Format": "UNIX"}
        url = f"{self.s.OANDA_API_URL}/v3/instruments/{symbol}/candles"
        inserted, first_ts, last_ts, cursor = 0, None, None, int(start)
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            for _ in range(400):  # safety cap
                resp = await client.get(url, params={"price": "M", "granularity": "M1", "from": str(cursor), "count": "5000"})
                if resp.status_code != 200:
                    raise RuntimeError(f"OANDA candle request failed (HTTP {resp.status_code}): {resp.text[:200]}")
                candles = resp.json().get("candles", [])
                rows, newest = [], None
                for c in candles:
                    if not c.get("complete"):
                        continue
                    t = int(float(c["time"]))
                    m = c["mid"]
                    rows.append((symbol, t * 1000, float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]), int(c.get("volume", 0))))
                    newest = t
                if not rows:
                    break
                await repo.insert_missing_candles(self.db, rows)
                inserted += len(rows)
                first_ts = first_ts or rows[0][1]
                last_ts = rows[-1][1]
                if newest is None or newest + 60 >= now - 90 or len(candles) < 5000:
                    break
                cursor = newest + 60
                await asyncio.sleep(0.25)  # stay far below OANDA's request limits
        if inserted:
            fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            logs.milestone(f"History for {symbol} was filled: {inserted} one-minute candles from {fmt(first_ts)} to {fmt(last_ts)} UTC.")
        return inserted
