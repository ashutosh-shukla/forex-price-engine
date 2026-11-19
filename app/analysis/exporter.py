"""CSV building shared by the manual download buttons and the automatic daily export."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from app.analysis.columns import NAMES
from app.core.timeutil import ist_str

_CSV_NAMES = ["time_ist"] + [n for n in NAMES if n not in ("symbol", "partial")]   # no "partial" column in the files
_RENAME = {"open_px": "open", "high_px": "high", "low_px": "low", "close_px": "close"}


def candles_csv_text(data: list[dict]) -> str:
    """Every analysis column (candle, order-flow, rolling, z-scores, OANDA official, outcomes, your labels)."""
    header = ["symbol"] + [_RENAME.get(n, n) for n in _CSV_NAMES]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in data:
        vals = [r.get("symbol"), ist_str(r["ts_ms"])]
        for n in _CSV_NAMES[1:]:
            v = r.get(n[:-3] if n.endswith("_px") else n)
            vals.append(round(v, 6) if isinstance(v, float) else v)
        w.writerow(vals)
    return buf.getvalue()


def generic_csv_text(data: list[dict], time_key: str = "ts_ms") -> str:
    """Any table: IST time first, then every column as stored."""
    if not data:
        return ""
    cols = list(data[0].keys())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_ist"] + cols)
    for r in data:
        w.writerow([ist_str(r[time_key]) if r.get(time_key) else ""] + [round(r[c], 6) if isinstance(r[c], float) else r[c] for c in cols])
    return buf.getvalue()


def write_atomic(path: Path, text: str) -> None:
    """Write to a temp file then rename, so a download never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\ufeff" + text, encoding="utf-8", newline="")
    tmp.replace(path)
