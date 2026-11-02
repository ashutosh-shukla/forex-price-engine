"""Endpoints of the analysis screen. All of them need the admin key (X-Admin-Key): this is your private workbench."""
from __future__ import annotations

import csv
import io
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.analysis.columns import DIRECTIONS, LABELS, NAMES
from app.api.auth import require_admin
from app.core import jsonx
from app.core.timeutil import ist_str
from app.store import analysis_repo as repo

router = APIRouter(prefix="/v1/analysis", dependencies=[Depends(require_admin)])
TFS = ("1m", "5m", "15m", "30m", "1h")


def _svc(request: Request):
    svc = getattr(request.app.state, "analysis", None)
    if svc is None:
        raise HTTPException(503, "The analysis service is not available.")
    return svc


def _json(obj, status: int = 200) -> Response:
    return Response(content=jsonx.dumps_bytes(obj), media_type="application/json", status_code=status)


@router.get("/state")
async def state(request: Request):
    return _json(_svc(request).state())


@router.get("/live")
async def live(request: Request, symbol: str = "XAU_USD"):
    svc = _svc(request)
    return _json({"symbol": symbol, "now_ms": int(time.time() * 1000), "timeframes": svc.live(symbol)})


class StartBody(BaseModel):
    symbol: str = Field("XAU_USD", pattern=r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")
    timeframes: list[str] | None = None


@router.post("/start")
async def start(request: Request, body: StartBody):
    svc = _svc(request)
    if body.symbol not in await request.app.state.pairs.active_symbols():
        raise HTTPException(404, "That pair is not active.")
    if not svc.state()["market"]["open"]:
        raise HTTPException(409, "The market is closed (weekend or holiday). Start the analysis when it reopens.")
    try:
        run = await svc.start(body.symbol, body.timeframes)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _json({"run": run})


@router.post("/stop")
async def stop(request: Request):
    return _json({"run": await _svc(request).stop()})


@router.get("/runs")
async def runs(request: Request):
    _svc(request)
    return _json({"runs": await repo.list_runs(request.app.state.db)})


def _clean(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["time_ist"] = ist_str(r["ts_ms"])
    return rows


@router.get("/rows")
async def rows(request: Request, symbol: str = "XAU_USD", tfs: str = "", from_ms: int | None = Query(None, alias="from"),
               to_ms: int | None = Query(None, alias="to"), run_id: int | None = None, limit: int = Query(300, ge=1, le=5000)):
    _svc(request)
    tf_list = [t for t in tfs.split(",") if t in TFS] or None
    data = await repo.fetch_rows(request.app.state.db, symbol, tf_list, from_ms, to_ms, run_id, limit)
    return _json({"rows": _clean(data), "count": len(data)})


class RowPatch(BaseModel):
    symbol: str
    tf: str
    ts_ms: int
    label_15m: str | None = None
    algo_direction: str | None = None
    note: str | None = Field(None, max_length=1000)


@router.patch("/rows")
async def patch_row(request: Request, body: RowPatch):
    _svc(request)
    fields = {}
    if body.label_15m is not None:
        if body.label_15m and body.label_15m not in LABELS:
            raise HTTPException(400, f"label must be one of: {', '.join(LABELS)}")
        fields["label_15m"] = body.label_15m or None
    if body.algo_direction is not None:
        if body.algo_direction and body.algo_direction not in DIRECTIONS:
            raise HTTPException(400, f"direction must be one of: {', '.join(DIRECTIONS)}")
        fields["algo_direction"] = body.algo_direction or None
    if body.note is not None:
        fields["note"] = body.note or None
    if not fields:
        raise HTTPException(400, "Nothing to change.")
    fields["updated_ms"] = int(time.time() * 1000)
    if not await repo.update_fields(request.app.state.db, body.symbol, body.tf, body.ts_ms, fields):
        raise HTTPException(404, "Row not found.")
    return _json({"ok": True})


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    symbol: str | None = "XAU_USD"


@router.post("/notes")
async def add_note(request: Request, body: NoteBody):
    svc = _svc(request)
    now = int(time.time() * 1000)
    nid = await repo.add_note(request.app.state.db, body.symbol, now, body.text, svc.run["run_id"] if svc.run else None)
    return _json({"note_id": nid, "ts_ms": now, "time_ist": ist_str(now)})


@router.get("/notes")
async def notes(request: Request, from_ms: int | None = Query(None, alias="from"), to_ms: int | None = Query(None, alias="to")):
    _svc(request)
    data = await repo.list_notes(request.app.state.db, from_ms, to_ms)
    for n in data:
        n["time_ist"] = ist_str(n["ts_ms"])
    return _json({"notes": data})


@router.delete("/notes/{note_id}")
async def delete_note(request: Request, note_id: int):
    _svc(request)
    return _json({"deleted": await repo.delete_note(request.app.state.db, note_id)})


def _csv(header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8")   # BOM so Excel opens it correctly


# the readable column names used in the CSV (time first, in IST)
_CSV_NAMES = ["time_ist"] + [n for n in NAMES if n not in ("symbol",)]


@router.get("/export.csv")
async def export_candles(request: Request, symbol: str = "XAU_USD", tfs: str = "", from_ms: int | None = Query(None, alias="from"),
                         to_ms: int | None = Query(None, alias="to"), run_id: int | None = None):
    _svc(request)
    tf_list = [t for t in tfs.split(",") if t in TFS] or None
    data = await repo.fetch_rows(request.app.state.db, symbol, tf_list, from_ms, to_ms, run_id, 100_000, newest_first=False)
    header = ["symbol"] + [("open" if n == "open_px" else "high" if n == "high_px" else "low" if n == "low_px" else "close" if n == "close_px" else n) for n in _CSV_NAMES]
    out = []
    for r in data:
        vals = [r.get("symbol"), ist_str(r["ts_ms"])]
        for n in _CSV_NAMES[1:]:
            key = n[:-3] if n.endswith("_px") else n
            v = r.get(key)
            vals.append(round(v, 6) if isinstance(v, float) else v)
        out.append(vals)
    return _csv(header, out)


@router.get("/export_notes.csv")
async def export_notes(request: Request, from_ms: int | None = Query(None, alias="from"), to_ms: int | None = Query(None, alias="to")):
    _svc(request)
    data = await repo.list_notes(request.app.state.db, from_ms, to_ms, 100_000)
    return _csv(["time_ist", "symbol", "run_id", "note"], [[ist_str(n["ts_ms"]), n["symbol"], n["run_id"], n["note_text"]] for n in reversed(data)])
