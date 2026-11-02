"""Websocket load test: open N clients, subscribe, and report latency and throughput.

    python -m scripts.loadtest_ws --url ws://localhost:8000 --api http://localhost:8000 \
        --key pek_xxx.yyy --clients 1000 --symbols EUR_USD,GBP_USD --seconds 60

Latency is measured as (receive time - engine publish time). It is only meaningful when this
script runs on the same machine as the engine (or clocks are synced).
"""
import argparse
import asyncio
import json
import statistics
import time

import httpx
import websockets


async def client(i, a, token, lat, counters):
    try:
        async with websockets.connect(f"{a.url}/v1/stream", max_size=2**20, ping_interval=20) as ws:
            await ws.send(json.dumps({"op": "auth", "token": token}))
            await ws.send(json.dumps({"op": "subscribe", "channel": "ticks", "symbols": a.symbols.split(",")}))
            counters["connected"] += 1
            end = time.time() + a.seconds
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                now = time.time() * 1000
                counters["messages"] += 1
                if '"e":"tick"' in msg:
                    p = json.loads(msg).get("p")
                    if p:
                        lat.append(now - p)
    except Exception as exc:
        counters["errors"] += 1
        counters["last_error"] = repr(exc)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--clients", type=int, default=200)
    ap.add_argument("--symbols", default="EUR_USD")
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    lat, counters = [], {"connected": 0, "messages": 0, "errors": 0, "last_error": ""}
    async with httpx.AsyncClient(base_url=a.api, headers={"X-API-Key": a.key}, timeout=10) as http:
        tokens = []
        for _ in range(a.clients):
            r = await http.post("/v1/ws-token", json={"ttl_seconds": 120})
            r.raise_for_status()
            tokens.append(r.json()["token"])
    t0 = time.time()
    await asyncio.gather(*(client(i, a, tokens[i], lat, counters) for i in range(a.clients)))
    el = time.time() - t0
    print(f"connected={counters['connected']} errors={counters['errors']} messages={counters['messages']} ({counters['messages'] / el:.0f}/s)")
    if counters["last_error"]:
        print("last error:", counters["last_error"])
    if lat:
        lat.sort()
        q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]
        print(f"latency ms  p50={q(.5):.1f}  p95={q(.95):.1f}  p99={q(.99):.1f}  max={lat[-1]:.1f}  mean={statistics.mean(lat):.1f}")


if __name__ == "__main__":
    asyncio.run(main())
