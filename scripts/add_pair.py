"""Add (or re-activate) a trading pair. The running engine notices within ~15 seconds.

    python -m scripts.add_pair NAS100_USD --decimals 1
"""
import argparse
import asyncio

from scripts._boot import open_db
from app.store import repo


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("symbol")
    p.add_argument("--decimals", type=int, default=5)
    a = p.parse_args()
    s, db = await open_db()
    try:
        sym = a.symbol.upper()
        await repo.upsert_pair(db, sym, sym.replace("_", "/"), a.decimals, True, overwrite=True)
        print(f"{sym} added. The running engine starts streaming it and downloads history automatically.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
