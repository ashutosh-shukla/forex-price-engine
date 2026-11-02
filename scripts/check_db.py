"""Test the Oracle connection and show what is in the engine tables.

    python -m scripts.check_db
"""
import asyncio

from scripts._boot import open_db
from app.store.schema import missing_tables


async def main() -> None:
    s, db = await open_db()
    try:
        print(f"Connected to Oracle as {s.ORACLE_ADB_USER} using alias '{s.ORACLE_ADB_DSN}'.")
        ver = await db.fetchval("SELECT banner FROM v$version FETCH FIRST 1 ROWS ONLY")
        print("Database:", ver)
        miss = await missing_tables(db)
        if miss:
            print("Missing tables:", miss, "-> run: python engine_ctl.py initdb  (or run sql/oracle_schema.sql)")
            return
        for t in ("PE_PAIRS", "PE_CLIENTS", "PE_CANDLES_1M", "PE_TICKS", "PE_ANALYSIS", "PE_ANALYSIS_RUNS", "PE_NOTES"):
            try:
                print(f"  {t:<17} {await db.fetchval(f'SELECT COUNT(*) FROM {t}')} rows")
            except Exception:
                print(f"  {t:<17} MISSING -> run initdb.bat (only the analysis screen needs the last three)")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
