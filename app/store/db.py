"""Oracle Autonomous Database access (python-oracledb, THIN mode, wallet/mTLS).

Connects exactly like your ETL project: dsn = TNS alias, config_dir + wallet_location = wallet folder.
No Oracle Instant Client needed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Sequence

import oracledb

from app.core import logs

_RETRY_HINTS = ("DPY-4011", "DPY-6005", "DPY-1001", "ORA-03113", "ORA-03114", "ORA-03135", "ORA-12170", "ORA-12541", "ORA-12514")


def _norm(name: str) -> str:
    n = name.lower()
    return n[:-3] if n.endswith("_px") else n


class Database:
    def __init__(self, settings):
        self.s = settings
        self.pool = None

    async def start(self) -> None:
        s = self.s
        self.pool = oracledb.create_pool_async(
            user=s.ORACLE_ADB_USER, password=s.ORACLE_ADB_PASSWORD, dsn=s.ORACLE_ADB_DSN,
            config_dir=s.wallet_dir, wallet_location=s.wallet_dir,
            wallet_password=s.ORACLE_ADB_WALLET_PASSWORD or None,
            min=1, max=max(2, s.DB_POOL_MAX), increment=1, getmode=oracledb.POOL_GETMODE_WAIT,
        )
        async with self.pool.acquire() as conn:  # fail fast with a clear message if credentials/wallet are wrong
            with conn.cursor() as cur:
                await cur.execute("SELECT 'connected' FROM dual")
                await cur.fetchone()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close(force=True)

    async def _run(self, fn):
        last = None
        for attempt in range(3):
            try:
                return await fn()
            except oracledb.Error as exc:
                last = exc
                if not any(h in str(exc) for h in _RETRY_HINTS) or attempt == 2:
                    raise
                logs.warn(f"Database connection hiccup ({str(exc)[:80]}); retrying.")
                await asyncio.sleep(1 + attempt)
        raise last  # pragma: no cover

    async def fetchall(self, sql: str, params: dict | Sequence | None = None) -> list[dict]:
        async def go():
            async with self.pool.acquire() as conn:
                with conn.cursor() as cur:
                    await cur.execute(sql, params or {})
                    cols = [_norm(d[0]) for d in cur.description]
                    return [dict(zip(cols, row)) for row in await cur.fetchall()]
        return await self._run(go)

    async def fetchval(self, sql: str, params: dict | Sequence | None = None) -> Any:
        async def go():
            async with self.pool.acquire() as conn:
                with conn.cursor() as cur:
                    await cur.execute(sql, params or {})
                    row = await cur.fetchone()
                    return row[0] if row else None
        return await self._run(go)

    async def execute(self, sql: str, params: dict | Sequence | None = None) -> int:
        async def go():
            async with self.pool.acquire() as conn:
                with conn.cursor() as cur:
                    await cur.execute(sql, params or {})
                    n = cur.rowcount
                await conn.commit()
                return n
        return await self._run(go)

    async def executemany(self, sql: str, rows: list, input_sizes: list | None = None) -> int:
        if not rows:
            return 0

        async def go():
            async with self.pool.acquire() as conn:
                with conn.cursor() as cur:
                    if input_sizes:
                        cur.setinputsizes(*input_sizes)
                    await cur.executemany(sql, rows)
                await conn.commit()
                return len(rows)
        return await self._run(go)
