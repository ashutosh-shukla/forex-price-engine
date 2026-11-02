"""IngestService: the 24/7 part of the engine, running inside the same process as the API.

OANDA stream -> CandleEngine (candles + metrics) -> hub (live websocket) and Oracle (history).
"""
from __future__ import annotations

import asyncio
import time

from app.core import jsonx, logs
from app.core.alerts import Alerter
from app.ingest.backfill import Backfiller
from app.ingest.engine import CandleEngine
from app.ingest.monitor import Monitor
from app.ingest.stream import StreamSupervisor
from app.ingest.writer import DbWriter
from app.store import repo


def default_decimals(symbol: str) -> int:
    return 3 if symbol.endswith("_JPY") or symbol.startswith(("XAU", "XAG")) else 5


class IngestService:
    def __init__(self, settings, db, hub):
        self.s = settings
        self.db = db
        self.hub = hub
        self.alerter = Alerter(settings.ALERT_WEBHOOK_URL, settings.ALERT_COOLDOWN_SECONDS)
        self._stop = asyncio.Event()
        self._reconnect = asyncio.Event()
        self._pairs_dirty = asyncio.Event()
        self.symbols: list[str] = []
        self.stream: StreamSupervisor | None = None
        self.health: dict = {}
        self._stream_started = False
        self._tasks: list[asyncio.Task] = []
        self._core: list[asyncio.Task] = []
        self._stream_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        s = self.s
        self.writer = DbWriter(self.db, s.DB_FLUSH_INTERVAL_MS, s.STORE_TICKS)
        self.engine = CandleEngine(s.live_timeframes, self.hub, self.writer, s.CANDLE_PUBLISH_INTERVAL_MS, s.CANDLE_CLOSE_GRACE_MS)
        self.backfiller = Backfiller(s, self.db)
        for sym in s.seed_pairs:
            await repo.upsert_pair(self.db, sym, sym.replace("_", "/"), default_decimals(sym), True, overwrite=False)
        await self.load_pairs()
        self.hub.subscribe(self._on_hub)

        monitor = Monitor(self)
        self._core = [
            asyncio.create_task(self.writer.run(self._stop)),
            asyncio.create_task(self._flush_loop()),
            asyncio.create_task(self._pairs_watcher()),
            asyncio.create_task(monitor.run(self._stop)),
            asyncio.create_task(self._retention_loop()),
        ]
        if s.OANDA_API_KEY and s.OANDA_ACCOUNT_ID:
            self.stream = StreamSupervisor(s, lambda: list(self.symbols), self.engine.on_tick, self._on_connected, self._on_disconnected, self._stop, self._reconnect)
            self._stream_task = asyncio.create_task(self.stream.run())
        else:
            logs.warn("OANDA_API_KEY / OANDA_ACCOUNT_ID are not set, so the live price stream is OFF. The API and UI still work with stored data.")
        logs.milestone(f"The price engine is running with {len(self.symbols)} active pairs and timeframes {', '.join(s.live_timeframes)}.")

    async def stop(self) -> None:
        logs.milestone("A shutdown was requested; saving in-progress data before stopping.")
        self._stop.set()
        if self._stream_task:
            self._stream_task.cancel()
            await asyncio.gather(self._stream_task, return_exceptions=True)
        self.engine.shutdown()
        await asyncio.gather(*self._core, return_exceptions=True)  # writer.run flushes on exit
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logs.milestone("The price engine has stopped cleanly.")

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.append(t)
        t.add_done_callback(lambda x: self._tasks.remove(x) if x in self._tasks else None)

    # ------------------------------------------------------------------ pairs and control
    async def load_pairs(self) -> bool:
        new = await repo.active_symbols(self.db)
        if new == self.symbols:
            return False
        added, removed = sorted(set(new) - set(self.symbols)), sorted(set(self.symbols) - set(new))
        first_load = not self.symbols and not self._stream_started and not self._core
        self.symbols = new
        if added and not first_load:
            logs.milestone(f"Pairs added to the live feed: {', '.join(added)}.")
            for sym in added:
                self._spawn(self.backfiller.run_for(sym))
        if removed:
            logs.milestone(f"Pairs removed from the live feed: {', '.join(removed)}.")
            for sym in removed:
                self.engine.drop_symbol(sym)
        return True

    def _on_hub(self, channel: str, payload: str) -> None:
        if channel != "ctl":
            return
        try:
            cmd = jsonx.loads(payload)
        except Exception:
            return
        if cmd.get("op") == "pairs_changed":
            self._pairs_dirty.set()
        elif cmd.get("op") == "backfill" and cmd.get("symbol"):
            logs.activity(f"A history download was requested for {cmd['symbol']}.")
            self._spawn(self.backfiller.run_for(cmd["symbol"], cmd.get("days")))

    async def _pairs_watcher(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._pairs_dirty.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            self._pairs_dirty.clear()
            try:
                if await self.load_pairs():
                    self._reconnect.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logs.error_throttled("pairs-watch", "Reading the pair list from the database failed; will retry.", exc)

    # ------------------------------------------------------------------ stream callbacks
    async def _on_connected(self, symbols: list[str]) -> None:
        logs.milestone(f"Connected to the OANDA price stream for {len(symbols)} pairs: {', '.join(symbols)}.")
        if self.s.BACKFILL_ON_RECONNECT or not self._stream_started:
            for sym in symbols:
                self._spawn(self.backfiller.run_for(sym))
        self._stream_started = True

    async def _on_disconnected(self, reason: str) -> None:
        logs.milestone(f"The OANDA price stream disconnected because {reason}.")

    # ------------------------------------------------------------------ housekeeping
    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            self.engine.flush_due(int(time.time() * 1000))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def _retention_loop(self) -> None:
        s = self.s
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=3600 if self.health else 120)
                break
            except asyncio.TimeoutError:
                pass
            try:
                now_ms = int(time.time() * 1000)
                if s.STORE_TICKS:
                    total = 0
                    while (n := await repo.prune_ticks(self.db, now_ms - s.TICK_RETENTION_DAYS * 86_400_000)) > 0:
                        total += n
                    if total:
                        logs.activity(f"Removed {total} tick(s) older than {s.TICK_RETENTION_DAYS} days.")
                if s.CANDLE_1M_RETENTION_DAYS > 0:
                    total = 0
                    while (n := await repo.prune_candles(self.db, now_ms - s.CANDLE_1M_RETENTION_DAYS * 86_400_000)) > 0:
                        total += n
                    if total:
                        logs.activity(f"Removed {total} one-minute candle(s) older than {s.CANDLE_1M_RETENTION_DAYS} days.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logs.error_throttled("retention", "Cleaning up old data failed; will retry next hour.", exc)
