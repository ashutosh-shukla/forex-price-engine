from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    checks, ok = {}, True
    try:
        await request.app.state.db.fetchval("SELECT 1 FROM dual")
        checks["database"] = "ok"
    except Exception:
        checks["database"], ok = "down", False
    health = request.app.state.ingest.health
    checks["engine"] = "ok" if health else "starting"
    checks["stream"] = ("connected" if health.get("stream_connected") else "not connected") if health.get("stream_enabled") else "disabled (no OANDA keys)"
    return JSONResponse({"status": "ready" if ok else "degraded", **checks}, status_code=200 if ok else 503)
