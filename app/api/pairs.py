from __future__ import annotations

import time

from app.store import repo


class PairBook:
    """Cached list of pairs (refreshed every 15 s, or immediately after an admin change)."""

    def __init__(self, db):
        self.db = db
        self._until = 0.0
        self._active: set[str] = set()
        self._all: list[dict] = []

    async def _refresh(self) -> None:
        if time.monotonic() < self._until:
            return
        self._all = await repo.list_pairs(self.db)
        self._active = {p["symbol"] for p in self._all if p["active"]}
        self._until = time.monotonic() + 15

    async def active_symbols(self) -> set[str]:
        await self._refresh()
        return self._active

    async def all(self) -> list[dict]:
        await self._refresh()
        return self._all

    def invalidate(self) -> None:
        self._until = 0.0
