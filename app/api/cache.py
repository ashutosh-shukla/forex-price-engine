"""Tiny in-process response cache with single-flight loading.

If 1000 users open the same chart at once, the database is queried once, not 1000 times.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Hashable


class TTLCache:
    def __init__(self, max_entries: int = 5000):
        self.max = max_entries
        self._data: OrderedDict[Hashable, tuple[float, bytes]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task] = {}
        self.hits = self.misses = 0

    async def get_or_load(self, key: Hashable, ttl_s: float, loader: Callable[[], Awaitable[bytes]]) -> bytes:
        now = time.monotonic()
        hit = self._data.get(key)
        if hit is not None and hit[0] > now:
            self._data.move_to_end(key)
            self.hits += 1
            return hit[1]
        self.misses += 1
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, ttl_s, loader))
            self._inflight[key] = task
            task.add_done_callback(lambda t, k=key: (self._inflight.pop(k, None), t.cancelled() or t.exception()))
        return await asyncio.shield(task)

    async def _load(self, key, ttl_s, loader) -> bytes:
        value = await loader()
        self._data[key] = (time.monotonic() + ttl_s, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max:
            self._data.popitem(last=False)
        return value
