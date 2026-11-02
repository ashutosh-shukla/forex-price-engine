"""Create the engine tables from sql/oracle_schema.sql (same file you can run by hand in Database Actions)."""
from __future__ import annotations

import re
from pathlib import Path

from app.core import logs
from app.core.config import PROJECT_ROOT

SCHEMA_FILE = PROJECT_ROOT / "sql" / "oracle_schema.sql"
REQUIRED_TABLES = ("PE_PAIRS", "PE_CLIENTS", "PE_CANDLES_1M", "PE_TICKS")
ANALYSIS_TABLES = ("PE_ANALYSIS", "PE_ANALYSIS_RUNS", "PE_NOTES")   # optional: only the analysis screen needs them


def split_blocks(sql: str) -> list[str]:
    """Blocks are separated by a line containing only '/'. Comment-only blocks are dropped."""
    blocks = []
    for b in re.split(r"^\s*/\s*$", sql, flags=re.M):
        code = "\n".join(l for l in b.splitlines() if l.strip() and not l.strip().startswith("--")).strip()
        if code:
            blocks.append(code)
    return blocks


async def init_schema(db) -> int:
    blocks = split_blocks(SCHEMA_FILE.read_text(encoding="utf-8"))
    for b in blocks:
        await db.execute(b)
    logs.milestone(f"Database tables are ready ({len(blocks)} schema steps applied).")
    return len(blocks)


async def analysis_tables_ready(db) -> bool:
    rows = await db.fetchall("SELECT table_name FROM user_tables WHERE table_name LIKE 'PE\\_%' ESCAPE '\\'")
    have = {r["table_name"] for r in rows}
    return all(t in have for t in ANALYSIS_TABLES)


async def missing_tables(db) -> list[str]:
    rows = await db.fetchall("SELECT table_name FROM user_tables WHERE table_name LIKE 'PE\\_%' ESCAPE '\\'")
    have = {r["table_name"] for r in rows}
    return [t for t in REQUIRED_TABLES if t not in have]
