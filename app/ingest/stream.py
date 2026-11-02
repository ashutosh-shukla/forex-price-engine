"""OANDA streaming price feed with automatic reconnect."""
from __future__ import annotations

import asyncio
import random
import time

import httpx

from app.core import jsonx, logs
from app.core.market_hours import market_clock


class StreamSupervisor:
    def __init__(self, settings, symbols_provider, on_tick, on_connected, on_disconnected, stop: asyncio.Event, reconnect: asyncio.Event):
        self.s = settings
        self.symbols_provider = symbols_provider
        self.on_tick = on_tick
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.stop = stop
        self.reconnect = reconnect
        self.connected = False
        self.last_message_at = 0.0
        self.reconnects = 0
        self.halted = False                 # True while the weekend pause is active
        self.last_tradeable_at = 0.0
        self.non_tradeable_count = 0

    async def run(self) -> None:
        s = self.s
        backoff = 1.0
        url = f"{s.OANDA_STREAM_URL}/v3/accounts/{s.OANDA_ACCOUNT_ID}/pricing/stream"
        headers = {"Authorization": f"Bearer {s.OANDA_API_KEY}", "Accept-Datetime-Format": "UNIX"}
        timeout = httpx.Timeout(connect=10, read=s.STREAM_STALL_SECONDS, write=10, pool=10)
        while not self.stop.is_set():
            clock = market_clock(time.time() * 1000)
            if not clock["open"]:                      # weekend: do not even try to connect
                if not self.halted:
                    self.halted = True
                    logs.milestone(f"Weekend: the forex market is closed, so the price stream is paused. It reopens {clock['next_change_ist']} IST.")
                await self._sleep(30)
                continue
            if self.halted:
                self.halted = False
                logs.milestone("The forex market has reopened; connecting to the price stream again.")
            symbols = self.symbols_provider()
            if not symbols:
                logs.activity("No active pairs are configured, so the price stream is idle. Add a pair to start it.")
                await self._sleep(10)
                continue
            self.reconnect.clear()
            reason = "unknown"
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    params = {"instruments": ",".join(symbols)}
                    async with client.stream("GET", url, params=params, headers=headers) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread())[:300].decode(errors="replace")
                            hint = " Check OANDA_API_KEY and OANDA_ACCOUNT_ID." if resp.status_code in (401, 403) else ""
                            raise RuntimeError(f"OANDA refused the price stream (HTTP {resp.status_code}).{hint} Response: {body}")
                        self.connected = True
                        self.last_message_at = time.monotonic()
                        backoff = 1.0
                        await self.on_connected(symbols)
                        async for line in resp.aiter_lines():
                            if self.stop.is_set():
                                reason = "service is stopping"
                                break
                            if self.reconnect.is_set():
                                reason = "the list of active pairs changed"
                                break
                            if not line:
                                continue
                            self.last_message_at = time.monotonic()
                            self._handle(line)
                        else:
                            reason = "OANDA closed the connection"
            except httpx.ReadTimeout:
                reason = f"no prices or heartbeats were received for {s.STREAM_STALL_SECONDS} seconds"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = f"an error occurred: {exc}"
                logs.error(f"The OANDA price stream failed: {exc}", exc if not isinstance(exc, RuntimeError) else None)
            finally:
                was = self.connected
                self.connected = False
            if was:
                await self.on_disconnected(reason)
            if self.stop.is_set():
                break
            self.reconnects += 1
            delay = min(backoff, s.STREAM_RECONNECT_MAX_BACKOFF) * (0.8 + 0.4 * random.random())
            if "active pairs changed" not in reason:
                logs.activity(f"Reconnecting to the OANDA price stream in {delay:.1f} seconds.")
                await self._sleep(delay)
                backoff = min(backoff * 2, s.STREAM_RECONNECT_MAX_BACKOFF)

    def _handle(self, line: str) -> None:
        try:
            msg = jsonx.loads(line)
            if msg.get("type") != "PRICE":
                return
            if not msg.get("tradeable", True):
                self.non_tradeable_count += 1
                return
            self.last_tradeable_at = time.time()
            bids, asks = msg.get("bids"), msg.get("asks")
            if not bids or not asks:
                return
            self.on_tick(msg["instrument"], int(float(msg["time"]) * 1000), float(bids[0]["price"]), float(asks[0]["price"]))
        except Exception as exc:
            logs.error_throttled("tick-parse", "A price message from OANDA could not be processed and was skipped.", exc)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
