"""Create an API client (one per project that consumes the engine) and print its key once.

    python -m scripts.create_client --name my-website-backend --ips 127.0.0.1
"""
import argparse
import asyncio

from scripts._boot import open_db
from app.store import accounts


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--symbols", help="comma separated; default all pairs")
    p.add_argument("--timeframes", help="comma separated; default all")
    p.add_argument("--ips", help="comma separated IPs/CIDRs allowed to use the key; default any")
    p.add_argument("--no-metrics", action="store_true")
    p.add_argument("--no-ws", action="store_true")
    p.add_argument("--ticks", action="store_true", help="allow raw tick history")
    p.add_argument("--rate-limit", type=int, default=6000, help="requests per minute (0 = unlimited)")
    a = p.parse_args()
    s, db = await open_db()
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()] if v else None
    try:
        info, key = await accounts.create_client(
            db, s.API_KEY_PEPPER, a.name, allowed_symbols=split(a.symbols), allowed_timeframes=split(a.timeframes),
            allowed_ips=split(a.ips), allow_metrics=not a.no_metrics, allow_ws=not a.no_ws, allow_ticks=a.ticks,
            rate_limit_per_min=a.rate_limit)
        print(f"\nClient created: {info['name']} (id {info['client_id']})\nAPI key (store it now, it cannot be shown again):\n\n  {key}\n")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
