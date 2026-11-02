"""Shared helper for the command-line scripts: connect to Oracle using .env."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import logs  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.store.db import Database  # noqa: E402


async def open_db():
    s = get_settings()
    logs.setup_logging(s.log_dir, s.LOG_LEVEL)
    need = [n for n in ("ORACLE_ADB_USER", "ORACLE_ADB_PASSWORD", "ORACLE_ADB_DSN") if not getattr(s, n)]
    if need:
        raise SystemExit(f"Fill these in .env first: {', '.join(need)}")
    db = Database(s)
    try:
        await db.start()
    except Exception as exc:
        raise SystemExit(f"Could not connect to Oracle: {exc}\nCheck that the wallet files are in {s.wallet_dir} and the ORACLE_ADB_* values in .env.")
    return s, db
