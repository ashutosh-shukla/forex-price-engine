"""Read APIs for prices, candles, metrics and websocket tokens (authenticated with X-API-Key)."""
from __future__ import annotations

import re
import secrets
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.core import jsonx
from app.core.metrics import format_candle, merge_rows, metrics_block
from app.core.security import make_ws_token
from app.core.timeframes import TF_SECONDS, is_valid_tf
from app.api.auth import require_client
from app.store import repo
from app.store.repo import ClientRecord

router = APIRouter(prefix="/v1")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")


def _json(obj, status: int = 200) -> Response:
    return Response(content=jsonx.dumps_bytes(obj), media_type="application/json", status_code=status)


async def _check(request: Request, client: ClientRecord, symbol: str, tf: str | None = None, metrics: str = "none") -> None:
    if not SYMBOL_RE.match(symbol) or symbol not in await request.app.state.pairs.active_symbols():
        raise HTTPException(404, f"Unknown or inactive pair '{symbol}'")
    if client.allowed_symbols and symbol not in client.allowed_symbols:
        raise HTTPException(403, f"This client may not read {symbol}")
    if tf is not None:
        if not is_valid_tf(tf):
            raise HTTPException(400, f"Unsupported timeframe. Use one of: {', '.join(TF_SECONDS)}")
        if client.allowed_timeframes and tf not in client.allowed_timeframes:
            raise HTTPException(403, f"This client may not read timeframe {tf}")
    if metrics != "none" and not client.allow_metrics:
        raise HTTPException(403, "Metrics are not enabled for this client")


@router.get("/pairs")
async def pairs(request: Request, client: ClientRecord = Depends(require_client)):
    items = [p for p in await request.app.state.pairs.all() if p["active"] and (not client.allowed_symbols or p["symbol"] in client.allowed_symbols)]
    return _json({"pairs": items, "timeframes": list(TF_SECONDS), "live_timeframes": request.app.state.settings.live_timeframes})


@router.get("/price/latest")
async def latest(request: Request, symbols: str = Query(..., description="Comma separated, e.g. EUR_USD,GBP_USD"), client: ClientRecord = Depends(require_client)):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    eng = request.app.state.ingest.engine
    out = {}
    for s in syms:
        await _check(request, client, s)
        p = eng.last_payload.get(s)
        d = jsonx.loads(p) if p else None
        if d:
            d.pop("e", None)
        out[s] = d
    return _json({"prices": out, "server_time": int(time.time() * 1000)})


@router.get("/candles")
async def candles(
    request: Request,
    symbol: str,
    tf: str = "1m",
    from_ms: int | None = Query(None, alias="from", description="Start, epoch milliseconds (inclusive). With `from`, results page forward."),
    to_ms: int | None = Query(None, alias="to", description="End, epoch milliseconds (exclusive)"),
    limit: int = Query(500, ge=1),
    metrics: Literal["none", "basic", "full"] = "none",
    client: ClientRecord = Depends(require_client),
):
    s = request.app.state.settings
    symbol = symbol.upper()
    await _check(request, client, symbol, tf, metrics)
    limit = min(limit, s.MAX_CANDLES_PER_REQUEST)
    tf_s = TF_SECONDS[tf]

    async def load() -> bytes:
        page = await repo.fetch_candles(request.app.state.db, symbol, tf, from_ms, to_ms, limit)
        now_ms = int(time.time() * 1000)
        out = [format_candle(r, tf_s, metrics, now_ms) for r in page.rows]
        body = {"symbol": symbol, "tf": tf, "count": len(out), "candles": out}
        if page.next_from is not None:
            body["next_from"] = page.next_from
        return jsonx.dumps_bytes(body)

    ttl = s.CACHE_TTL_LATEST_MS / 1000 if to_ms is None else s.CACHE_TTL_HISTORY_S
    data = await request.app.state.cache.get_or_load(("c", symbol, tf, from_ms, to_ms, limit, metrics), ttl, load)
    return Response(content=data, media_type="application/json")


@router.get("/candles/forming")
async def forming(request: Request, symbol: str, tf: str = "1m", metrics: Literal["none", "basic", "full"] = "full", client: ClientRecord = Depends(require_client)):
    symbol = symbol.upper()
    await _check(request, client, symbol, tf, metrics)
    if tf not in request.app.state.settings.live_timeframes:
        raise HTTPException(400, f"Forming candles exist only for: {', '.join(request.app.state.settings.live_timeframes)}")
    st = request.app.state.ingest.engine.forming(symbol, tf)
    if st is None or not st.tick_count:
        raise HTTPException(404, "No forming candle yet (no prices received)")
    return _json({"symbol": symbol, "tf": tf, "candle": format_candle(st.to_dict(), TF_SECONDS[tf], metrics, int(time.time() * 1000))})


@router.get("/metrics/summary")
async def summary(request: Request, symbol: str, tf: str = "5m", window: int = Query(20, ge=2, le=500), client: ClientRecord = Depends(require_client)):
    """Order-flow summary over the last `window` candles that have live metrics."""
    symbol = symbol.upper()
    await _check(request, client, symbol, tf, "full")

    async def load() -> bytes:
        page = await repo.fetch_candles(request.app.state.db, symbol, tf, None, None, window)
        rows = [r for r in page.rows if r.get("up_ticks") is not None]
        if len(rows) < 2:
            return jsonx.dumps_bytes({"symbol": symbol, "tf": tf, "window": window, "available": len(rows), "summary": None})
        merged = merge_rows(rows)
        tf_s = TF_SECONDS[tf]
        ups = sum(1 for r in rows if r["close"] > r["open"])
        downs = sum(1 for r in rows if r["close"] < r["open"])
        block = metrics_block(merged, tf_s * len(rows))
        block.update({"open": merged["open"], "close": merged["close"], "high": merged["high"], "low": merged["low"],
                      "candles": len(rows), "up_candles": ups, "down_candles": downs, "from": rows[0]["t"], "to": rows[-1]["t"] + tf_s * 1000})
        return jsonx.dumps_bytes({"symbol": symbol, "tf": tf, "window": window, "summary": block})

    data = await request.app.state.cache.get_or_load(("s", symbol, tf, window), request.app.state.settings.CACHE_TTL_LATEST_MS / 1000, load)
    return Response(content=data, media_type="application/json")


@router.get("/ticks")
async def ticks(request: Request, symbol: str, from_ms: int | None = Query(None, alias="from"), to_ms: int | None = Query(None, alias="to"),
                limit: int = Query(5000, ge=1), client: ClientRecord = Depends(require_client)):
    """Raw bid/ask ticks as [time_ms, bid, ask]. Only available when STORE_TICKS=true."""
    if not client.allow_ticks:
        raise HTTPException(403, "Tick access is not enabled for this client")
    symbol = symbol.upper()
    await _check(request, client, symbol)
    if not request.app.state.settings.STORE_TICKS:
        raise HTTPException(409, "Tick storage is off. Set STORE_TICKS=true in .env and restart.")
    limit = min(limit, request.app.state.settings.MAX_TICKS_PER_REQUEST)
    rows = await repo.fetch_ticks(request.app.state.db, symbol, from_ms, to_ms, limit)
    return _json({"symbol": symbol, "columns": ["t", "bid", "ask"], "count": len(rows), "ticks": rows})


@router.get("/status")
async def status(request: Request, client: ClientRecord = Depends(require_client)):
    doc = dict(request.app.state.ingest.health)
    if not doc:
        return _json({"engine": "starting"}, 503)
    if client.allowed_symbols:
        doc["pairs"] = {k: v for k, v in doc["pairs"].items() if k in client.allowed_symbols}
    doc["websocket_connections"] = request.app.state.ws.total
    doc["cache"] = {"hits": request.app.state.cache.hits, "misses": request.app.state.cache.misses}
    return _json(doc)


class WsTokenRequest(BaseModel):
    symbols: list[str] | None = None
    timeframes: list[str] | None = None
    metrics: bool = False
    ttl_seconds: int | None = Field(None, ge=5, le=600)


@router.post("/ws-token")
async def ws_token(request: Request, body: WsTokenRequest | None = None, client: ClientRecord = Depends(require_client)):
    """Mint a short-lived token. Your backend calls this, then hands the token to the browser."""
    body = body or WsTokenRequest()
    if not client.allow_ws:
        raise HTTPException(403, "Websocket access is not enabled for this client")
    s = request.app.state.settings
    syms = [x.upper() for x in body.symbols] if body.symbols else (sorted(client.allowed_symbols) if client.allowed_symbols else None)
    if body.symbols and client.allowed_symbols and not set(syms) <= client.allowed_symbols:
        raise HTTPException(403, "Requested symbols exceed this client's permissions")
    tfs = body.timeframes or (sorted(client.allowed_timeframes) if client.allowed_timeframes else None)
    if body.timeframes and client.allowed_timeframes and not set(tfs) <= client.allowed_timeframes:
        raise HTTPException(403, "Requested timeframes exceed this client's permissions")
    if body.metrics and not client.allow_metrics:
        raise HTTPException(403, "Metrics are not enabled for this client")
    ttl = min(body.ttl_seconds or client.ws_token_ttl_seconds, max(client.ws_token_ttl_seconds, 5))
    now = time.time()
    claims = {"cid": client.client_id, "sym": syms, "tfs": tfs, "m": body.metrics, "iat": int(now), "exp": int(now) + ttl, "jti": secrets.token_hex(6)}
    return _json({"token": make_ws_token(s.WS_TOKEN_SECRET, claims), "expires_at": claims["exp"] * 1000, "ws_path": "/v1/stream"})
