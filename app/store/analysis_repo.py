"""SQL for the analysis screen (PE_ANALYSIS, PE_ANALYSIS_RUNS, PE_NOTES)."""
from __future__ import annotations

import random
import time

from app.analysis.columns import CAPTURE, KEY, NAMES, UPDATABLE

_UPDATE_ON_RECAPTURE = [c for c in CAPTURE if c not in KEY]   # a re-captured candle never wipes outcomes or your labels
# every bind name appears exactly once (in the USING select); UPDATE and INSERT read the values from "s"
_CAPTURE_SQL = (
    f"MERGE INTO pe_analysis t USING (SELECT {', '.join(f':{c} AS {c}' for c in CAPTURE)} FROM dual) s "
    "ON (t.symbol = s.symbol AND t.tf = s.tf AND t.ts_ms = s.ts_ms) "
    f"WHEN MATCHED THEN UPDATE SET {', '.join(f't.{c} = s.{c}' for c in _UPDATE_ON_RECAPTURE)} "
    f"WHEN NOT MATCHED THEN INSERT ({', '.join(CAPTURE)}) VALUES ({', '.join('s.' + c for c in CAPTURE)})"
)


def _norm_row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        out[k] = v
    # price columns come back as open/high/low/close (the *_px suffix is stripped by the db layer)
    return out


async def capture_row(db, row: dict) -> None:
    await db.execute(_CAPTURE_SQL, {c: row.get(c) for c in CAPTURE})


async def update_fields(db, symbol: str, tf: str, ts_ms: int, fields: dict) -> bool:
    cols = {k: v for k, v in fields.items() if k in UPDATABLE}
    if not cols:
        return False
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    params = dict(cols, symbol=symbol, tf=tf, ts_ms=ts_ms)
    return (await db.execute(f"UPDATE pe_analysis SET {sets} WHERE symbol = :symbol AND tf = :tf AND ts_ms = :ts_ms", params)) > 0


async def fetch_rows(db, symbol: str, tfs: list[str] | None, from_ms: int | None, to_ms: int | None,
                     run_id: int | None, limit: int, newest_first: bool = True) -> list[dict]:
    where, params = ["symbol = :symbol"], {"symbol": symbol, "n": limit}
    if tfs:
        names = []
        for i, tf in enumerate(tfs):
            params[f"tf{i}"] = tf
            names.append(f":tf{i}")
        where.append(f"tf IN ({', '.join(names)})")
    if from_ms is not None:
        where.append("ts_ms >= :lo"); params["lo"] = from_ms
    if to_ms is not None:
        where.append("ts_ms < :hi"); params["hi"] = to_ms
    if run_id is not None:
        where.append("run_id = :run"); params["run"] = run_id
    order = "DESC" if newest_first else "ASC"
    return await db.fetchall(f"SELECT * FROM pe_analysis WHERE {' AND '.join(where)} ORDER BY ts_ms {order}, tf FETCH FIRST :n ROWS ONLY", params)


async def create_run(db, run_id: int, symbol: str, tfs: list[str], started_ms: int) -> None:
    await db.execute("INSERT INTO pe_analysis_runs (run_id, symbol, tfs, started_ms) VALUES (:r, :s, :t, :a)",
                     {"r": run_id, "s": symbol, "t": ",".join(tfs), "a": started_ms})


async def stop_run(db, run_id: int, stopped_ms: int) -> None:
    await db.execute("UPDATE pe_analysis_runs SET stopped_ms = :s WHERE run_id = :r", {"s": stopped_ms, "r": run_id})


async def list_runs(db, limit: int = 50) -> list[dict]:
    return await db.fetchall("SELECT r.run_id, r.symbol, r.tfs, r.started_ms, r.stopped_ms, "
                             "(SELECT COUNT(*) FROM pe_analysis a WHERE a.run_id = r.run_id) AS candles "
                             "FROM pe_analysis_runs r ORDER BY r.started_ms DESC FETCH FIRST :n ROWS ONLY", {"n": limit})


async def add_note(db, symbol: str | None, ts_ms: int, text: str, run_id: int | None) -> int:
    nid = int(time.time() * 1000) * 1000 + random.randint(0, 999)
    await db.execute("INSERT INTO pe_notes (note_id, symbol, ts_ms, run_id, note_text) VALUES (:i, :s, :t, :r, :x)",
                     {"i": nid, "s": symbol, "t": ts_ms, "r": run_id, "x": text[:2000]})
    return nid


async def list_notes(db, from_ms: int | None, to_ms: int | None, limit: int = 1000) -> list[dict]:
    where, params = ["1 = 1"], {"n": limit}
    if from_ms is not None:
        where.append("ts_ms >= :lo"); params["lo"] = from_ms
    if to_ms is not None:
        where.append("ts_ms < :hi"); params["hi"] = to_ms
    return await db.fetchall(f"SELECT note_id, symbol, ts_ms, run_id, note_text FROM pe_notes WHERE {' AND '.join(where)} ORDER BY ts_ms DESC FETCH FIRST :n ROWS ONLY", params)


async def delete_note(db, note_id: int) -> bool:
    return (await db.execute("DELETE FROM pe_notes WHERE note_id = :i", {"i": note_id})) > 0
