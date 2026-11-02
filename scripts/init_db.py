"""Create the engine tables in your Autonomous Database (same as running sql/oracle_schema.sql by hand).

    python -m scripts.init_db
"""
import asyncio

from scripts._boot import open_db
from app.store.schema import init_schema, missing_tables


async def main() -> None:
    s, db = await open_db()
    try:
        await init_schema(db)
        miss = await missing_tables(db)
        print("Tables ready." if not miss else f"Still missing: {miss}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
