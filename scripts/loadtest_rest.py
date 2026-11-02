"""REST load test: many concurrent chart-style requests.

    python -m scripts.loadtest_rest --api http://localhost:8000 --key pek_xxx.yyy --concurrency 200 --requests 20000
"""
import argparse
import asyncio
import time

import httpx


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--symbol", default="EUR_USD")
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--requests", type=int, default=5000)
    a = ap.parse_args()
    paths = [f"/v1/candles?symbol={a.symbol}&tf=5m&limit=300&metrics=basic", f"/v1/price/latest?symbols={a.symbol}", f"/v1/candles?symbol={a.symbol}&tf=1h&limit=200"]
    lat, errors, sem = [], 0, asyncio.Semaphore(a.concurrency)
    limits = httpx.Limits(max_connections=a.concurrency, max_keepalive_connections=a.concurrency)
    async with httpx.AsyncClient(base_url=a.api, headers={"X-API-Key": a.key}, limits=limits, timeout=15) as c:
        async def one(i):
            nonlocal errors
            async with sem:
                t = time.perf_counter()
                try:
                    r = await c.get(paths[i % len(paths)])
                    if r.status_code != 200:
                        errors += 1
                except Exception:
                    errors += 1
                lat.append((time.perf_counter() - t) * 1000)
        t0 = time.time()
        await asyncio.gather(*(one(i) for i in range(a.requests)))
        el = time.time() - t0
    lat.sort()
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]
    print(f"{a.requests} requests in {el:.1f}s = {a.requests / el:.0f} req/s, errors={errors}")
    print(f"latency ms p50={q(.5):.1f} p95={q(.95):.1f} p99={q(.99):.1f} max={lat[-1]:.1f}")


if __name__ == "__main__":
    asyncio.run(main())
