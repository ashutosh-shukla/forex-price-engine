"""Low-latency websocket gateway.

The ingest service publishes each tick ONCE as a ready-made JSON string on the in-process hub; this module
forwards that same string to every subscribed client's own bounded queue. Nothing is re-serialised per client,
and one slow client can never delay the others.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket

from app.api.wsmanager import ConnectionManager, WsClient
from app.core import jsonx, logs
from app.core.metrics import build_candle_messages
from app.core.security import verify_ws_token
from app.core.timeframes import TF_SECONDS, is_valid_tf

router = APIRouter()
CHANNELS = {"ticks": "px", "candles": "cd", "candle_metrics": "cm"}


def _key(channel: str, symbol: str, tf: str | None) -> str:
    p = CHANNELS[channel]
    return f"{p}:{symbol}" if channel == "ticks" else f"{p}:{symbol}:{tf}"


def _err(code: str, message: str) -> str:
    return jsonx.dumps({"e": "error", "code": code, "message": message})


async def stats_logger(app) -> None:
    while True:
        await asyncio.sleep(300)
        m = app.state.ws
        if m.total:
            logs.activity(f"The websocket gateway is serving {m.total} connection(s) and has forwarded {m.messages_forwarded} messages so far.")


def _snapshot(app, c: WsClient, channel: str, symbols: list[str], tfs: list[str]) -> None:
    """Send the latest known state immediately so a screen can draw before the next tick."""
    eng = app.state.ingest.engine
    if channel == "ticks":
        for s in symbols:
            p = eng.last_payload.get(s)
            if p:
                c.push(p)
    else:
        for s in symbols:
            for tf in tfs:
                st = eng.forming(s, tf)
                if st is not None and st.tick_count:
                    basic, full = build_candle_messages(s, tf, TF_SECONDS[tf], st.to_dict(), False)
                    c.push(jsonx.dumps(basic if channel == "candles" else full))


async def _writer(c: WsClient) -> None:
    try:
        while True:
            await c.event.wait()
            c.event.clear()
            q = c.queue
            while q:
                await c.ws.send_text(q.popleft())
    except Exception:
        pass


async def _handle_sub(app, c: WsClient, msg: dict, subscribe: bool) -> None:
    s = app.state.settings
    channel = msg.get("channel", "ticks")
    if channel not in CHANNELS:
        c.push(_err("bad_request", "channel must be ticks, candles or candle_metrics"))
        return
    if channel == "candle_metrics" and not c.claims.get("m"):
        c.push(_err("forbidden", "Metrics are not enabled for this token"))
        return
    active = await app.state.pairs.active_symbols()
    allowed_syms = c.claims.get("sym")
    symbols = [x for x in (msg.get("symbols") or []) if isinstance(x, str)]
    bad = [x for x in symbols if x not in active or (allowed_syms and x not in allowed_syms)]
    symbols = [x for x in symbols if x not in bad]
    tfs: list[str] = []
    if channel != "ticks":
        allowed_tfs = c.claims.get("tfs")
        want = [x for x in (msg.get("tfs") or ["1m"]) if isinstance(x, str)]
        tfs = [x for x in want if x in s.live_timeframes and is_valid_tf(x) and (not allowed_tfs or x in allowed_tfs)]
        bad += [f"tf:{x}" for x in want if x not in tfs]
    if not symbols or (channel != "ticks" and not tfs):
        c.push(_err("bad_request", f"Nothing valid to {'subscribe' if subscribe else 'unsubscribe'}. Rejected: {bad}"))
        return
    mgr: ConnectionManager = app.state.ws
    keys = [_key(channel, sym, tf) for sym in symbols for tf in (tfs or [None])]
    if subscribe:
        if len(c.keys) + len(keys) > s.WS_MAX_SUBSCRIPTIONS:
            c.push(_err("limit", "Subscription limit reached"))
            return
        for k in keys:
            mgr.add_key(c, k)
        c.push(jsonx.dumps({"e": "subscribed", "channel": channel, "symbols": symbols, "tfs": tfs, "rejected": bad}))
        _snapshot(app, c, channel, symbols, tfs)
    else:
        for k in keys:
            mgr.remove_key(c, k)
        c.push(jsonx.dumps({"e": "unsubscribed", "channel": channel, "symbols": symbols, "tfs": tfs}))


@router.websocket("/v1/stream")
async def stream(ws: WebSocket):
    app = ws.app
    s = app.state.settings
    mgr: ConnectionManager = app.state.ws
    origins = s.ws_origins
    if origins and ws.headers.get("origin", "").rstrip("/") not in origins:
        await ws.close(code=4403)
        return
    ip = ws.client.host if ws.client else "unknown"
    if mgr.total >= s.WS_MAX_CONNECTIONS or mgr.by_ip.get(ip, 0) >= s.WS_MAX_CONNECTIONS_PER_IP:
        await ws.close(code=1013)
        return
    await ws.accept()
    mgr.total += 1
    mgr.by_ip[ip] += 1
    c = WsClient(ws, ip, s.WS_SEND_QUEUE_MAX)
    writer: asyncio.Task | None = None
    authed = False
    try:
        # ---- 1. authenticate with the short-lived token
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=s.WS_AUTH_TIMEOUT_SECONDS)
            first = jsonx.loads(raw)
            claims = verify_ws_token(s.WS_TOKEN_SECRET, first.get("token", "")) if first.get("op") == "auth" else None
        except Exception:
            claims = None
        if not claims:
            await ws.send_text(_err("unauthorized", 'Send {"op":"auth","token":"..."} first with a valid token'))
            await ws.close(code=4401)
            return
        c.claims, c.client_id = claims, claims["cid"]
        rec = await app.state.registry.get(c.client_id)
        if rec is None or not rec.active or not rec.allow_ws:
            await ws.send_text(_err("forbidden", "This client may not use the websocket"))
            await ws.close(code=4403)
            return
        if mgr.by_client.get(c.client_id, 0) >= rec.max_ws_connections:
            await ws.send_text(_err("limit", "Too many concurrent websocket connections for this client"))
            await ws.close(code=1013)
            return
        mgr.by_client[c.client_id] += 1
        authed = True
        logs.activity(f"Websocket client {c.client_id} connected from {ip} ({mgr.total} connection(s) open).")
        writer = asyncio.create_task(_writer(c))
        c.push(jsonx.dumps({"e": "authed", "client": c.client_id, "symbols": claims.get("sym"), "timeframes": claims.get("tfs"), "metrics": claims.get("m", False)}))

        # ---- 2. command loop
        deadline = time.time() + s.WS_MAX_SESSION_SECONDS
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                c.push(_err("session_expired", "Session time limit reached; reconnect with a new token"))
                await asyncio.sleep(0.2)
                await ws.close(code=4408)
                return
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=min(remaining, 3600))
            except asyncio.TimeoutError:
                continue
            if len(raw) > 4096:
                await ws.close(code=1009)
                return
            try:
                msg = jsonx.loads(raw)
                op = msg.get("op")
            except Exception:
                c.push(_err("bad_request", "Messages must be JSON"))
                continue
            if op == "ping":
                c.push('{"e":"pong"}')
            elif op in ("subscribe", "unsubscribe"):
                await _handle_sub(app, c, msg, op == "subscribe")
            else:
                c.push(_err("bad_request", "Unknown op. Use subscribe, unsubscribe or ping"))
    except Exception as exc:
        if exc.__class__.__name__ not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError", "RuntimeError"):
            logs.error_throttled("ws-handler", f"A websocket session ended unexpectedly: {exc}", exc)
    finally:
        mgr.remove_client(c)
        mgr.total -= 1
        mgr.by_ip[ip] -= 1
        if mgr.by_ip[ip] <= 0:
            mgr.by_ip.pop(ip, None)
        if writer:
            writer.cancel()
        if authed:
            mgr.by_client[c.client_id] -= 1
            if mgr.by_client[c.client_id] <= 0:
                mgr.by_client.pop(c.client_id, None)
            logs.activity(f"Websocket client {c.client_id} from {ip} disconnected after {int(time.time() - c.connected_at)} seconds ({c.dropped} messages skipped because it was too slow).")
