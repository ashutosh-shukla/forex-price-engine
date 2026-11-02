"""Client authentication, IP allow-lists and rate limiting for the REST API."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request

from app.core.security import constant_time_equals, ip_allowed, split_api_key, verify_api_key
from app.store import repo
from app.store.repo import ClientRecord


class ClientRegistry:
    """Caches client records for 30 s so authentication does not hit the database on every request."""

    def __init__(self, db, ttl: float = 30.0):
        self.db = db
        self.ttl = ttl
        self._cache: dict[str, tuple[float, ClientRecord | None]] = {}
        self._touched: dict[str, float] = {}

    async def get(self, client_id: str) -> ClientRecord | None:
        now = time.monotonic()
        hit = self._cache.get(client_id)
        if hit and hit[0] > now:
            return hit[1]
        rec = await repo.get_client(self.db, client_id)
        self._cache[client_id] = (now + (self.ttl if rec else 10.0), rec)
        if len(self._cache) > 5000:
            self._cache.clear()
        return rec

    def invalidate(self, client_id: str | None = None) -> None:
        if client_id:
            self._cache.pop(client_id, None)
        else:
            self._cache.clear()

    async def touch(self, client_id: str) -> None:
        now = time.monotonic()
        if now - self._touched.get(client_id, -1e9) < 300:
            return
        self._touched[client_id] = now
        try:
            await repo.touch_client(self.db, client_id)
        except Exception:
            pass


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def authenticate_key(app, api_key: str | None, ip: str) -> ClientRecord:
    cid = split_api_key(api_key)
    if cid is None:
        raise HTTPException(401, "Missing or invalid API key")
    rec = await app.state.registry.get(cid)
    if rec is None or not verify_api_key(app.state.settings.API_KEY_PEPPER, api_key, rec.key_hash):
        raise HTTPException(401, "Missing or invalid API key")
    if not rec.active:
        raise HTTPException(403, "This client is disabled")
    if not ip_allowed(ip, rec.allowed_ips):
        raise HTTPException(403, "This client is not allowed from this IP address")
    return rec


async def require_client(request: Request) -> ClientRecord:
    app = request.app
    rec = await authenticate_key(app, request.headers.get("x-api-key"), client_ip(request))
    if rec.rate_limit_per_min > 0:
        count = app.state.hub.incr_window(f"rl:{rec.client_id}", 60)
        if count > rec.rate_limit_per_min:
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "10"})
    await app.state.registry.touch(rec.client_id)
    return rec


async def require_admin(request: Request) -> None:
    key = request.headers.get("x-admin-key", "")
    if not key or not constant_time_equals(key, request.app.state.settings.ADMIN_API_KEY):
        raise HTTPException(401, "Invalid admin key")
