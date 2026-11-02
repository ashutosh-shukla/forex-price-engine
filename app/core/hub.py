"""In-process message hub (replaces Redis for the single-process local engine).

publish() calls every listener immediately, so a tick travels from the OANDA socket
to the websocket clients' send queues with no network hop. Also holds fixed-window
counters used for API rate limiting.
"""
from __future__ import annotations

import time
from typing import Callable

from app.core import logs


class Hub:
    def __init__(self):
        self._listeners: list[Callable[[str, str], None]] = []
        self._windows: dict[str, tuple[float, int]] = {}
        self.published = 0

    def subscribe(self, fn: Callable[[str, str], None]) -> None:
        self._listeners.append(fn)

    def publish(self, channel: str, payload: str) -> None:
        self.published += 1
        for fn in self._listeners:
            try:
                fn(channel, payload)
            except Exception as exc:  # one bad listener must never stop the price feed
                logs.error_throttled("hub-listener", "A message listener failed while handling a live update.", exc)

    def incr_window(self, key: str, ttl: float) -> int:
        now = time.monotonic()
        exp, count = self._windows.get(key, (0.0, 0))
        if exp < now:
            exp, count = now + ttl, 0
        count += 1
        self._windows[key] = (exp, count)
        if len(self._windows) > 20_000:
            self._windows = {k: v for k, v in self._windows.items() if v[0] >= now}
        return count
