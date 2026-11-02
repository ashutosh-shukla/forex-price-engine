"""Admin API (X-Admin-Key)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.core import jsonx, logs
from app.store import accounts, repo

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(require_admin)])


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    allowed_symbols: list[str] | None = None
    allowed_timeframes: list[str] | None = None
    allow_metrics: bool = True
    allow_ws: bool = True
    allow_ticks: bool = False
    rate_limit_per_min: int = Field(6000, ge=0)
    max_ws_connections: int = Field(5000, ge=1)
    allowed_ips: list[str] | None = None
    ws_token_ttl_seconds: int = Field(60, ge=5, le=600)


class ClientPatch(BaseModel):
    name: str | None = None
    allowed_symbols: list[str] | None = None
    allowed_timeframes: list[str] | None = None
    allow_metrics: bool | None = None
    allow_ws: bool | None = None
    allow_ticks: bool | None = None
    rate_limit_per_min: int | None = Field(None, ge=0)
    max_ws_connections: int | None = Field(None, ge=1)
    allowed_ips: list[str] | None = None
    ws_token_ttl_seconds: int | None = Field(None, ge=5, le=600)
    active: bool | None = None


class PairCreate(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")
    display_name: str | None = None
    price_decimals: int = Field(5, ge=0, le=8)
    active: bool = True


class PairPatch(BaseModel):
    active: bool | None = None
    display_name: str | None = None
    price_decimals: int | None = Field(None, ge=0, le=8)


class BackfillRequest(BaseModel):
    symbol: str
    days: int = Field(7, ge=1, le=3650)


@router.post("/clients")
async def create_client(request: Request, body: ClientCreate):
    app = request.app
    info, key = await accounts.create_client(app.state.db, app.state.settings.API_KEY_PEPPER, body.name, **body.model_dump(exclude={"name"}))
    logs.milestone(f"A new API client '{body.name}' ({info['client_id']}) was created.")
    return {"client": info, "api_key": key, "note": "Store this key now. It cannot be shown again."}


@router.get("/clients")
async def list_clients(request: Request):
    return {"clients": await repo.list_clients(request.app.state.db)}


@router.patch("/clients/{client_id}")
async def patch_client(request: Request, client_id: str, body: ClientPatch):
    rec = await repo.update_client(request.app.state.db, client_id, body.model_dump(exclude_unset=True))
    if rec is None:
        raise HTTPException(404, "Client not found or nothing to change")
    request.app.state.registry.invalidate(client_id)
    logs.activity(f"API client {client_id} was updated by an administrator.")
    return {"client": rec.public()}


@router.post("/clients/{client_id}/rotate-key")
async def rotate(request: Request, client_id: str):
    key = await accounts.rotate_key(request.app.state.db, request.app.state.settings.API_KEY_PEPPER, client_id)
    if key is None:
        raise HTTPException(404, "Client not found")
    request.app.state.registry.invalidate(client_id)
    logs.milestone(f"The API key for client {client_id} was rotated; the old key no longer works.")
    return {"api_key": key, "note": "Store this key now. The previous key is already invalid."}


@router.get("/pairs")
async def list_pairs(request: Request):
    request.app.state.pairs.invalidate()
    return {"pairs": await request.app.state.pairs.all()}


@router.post("/pairs")
async def add_pair(request: Request, body: PairCreate):
    app = request.app
    await repo.upsert_pair(app.state.db, body.symbol, body.display_name or body.symbol.replace("_", "/"), body.price_decimals, body.active, overwrite=True)
    app.state.pairs.invalidate()
    app.state.hub.publish("ctl", jsonx.dumps({"op": "pairs_changed"}))
    logs.milestone(f"Pair {body.symbol} was added by an administrator.")
    return {"symbol": body.symbol, "active": body.active, "note": "The engine picks it up within seconds and downloads history automatically."}


@router.patch("/pairs/{symbol}")
async def patch_pair(request: Request, symbol: str, body: PairPatch):
    app = request.app
    ch = body.model_dump(exclude_unset=True)
    if not ch or not await repo.update_pair(app.state.db, symbol.upper(), ch):
        raise HTTPException(404, "Pair not found or nothing to change")
    app.state.pairs.invalidate()
    app.state.hub.publish("ctl", jsonx.dumps({"op": "pairs_changed"}))
    logs.milestone(f"Pair {symbol.upper()} was changed by an administrator.")
    return {"symbol": symbol.upper(), **ch}


@router.post("/backfill")
async def backfill(request: Request, body: BackfillRequest):
    symbol = body.symbol.upper()
    if symbol not in await request.app.state.pairs.active_symbols():
        raise HTTPException(404, "Pair is not active")
    request.app.state.hub.publish("ctl", jsonx.dumps({"op": "backfill", "symbol": symbol, "days": body.days}))
    return {"queued": True, "symbol": symbol, "days": body.days}


@router.post("/shutdown")
async def shutdown(request: Request):
    """Graceful stop (used by stop.bat): saves in-progress candles, then exits."""
    server = getattr(request.app.state, "server", None)
    if server is None:
        raise HTTPException(409, "Not started through app.run; stop it with Ctrl+C")
    logs.milestone("A stop was requested through the admin API.")
    server.should_exit = True
    return {"stopping": True}
