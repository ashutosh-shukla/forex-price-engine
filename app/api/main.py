"""FastAPI application: REST + websocket gateway + the 24/7 ingest service, all in one process."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import ws
from app.api.auth import ClientRegistry
from app.api.cache import TTLCache
from app.api.pairs import PairBook
from app.api.routes import admin, analysis, market, public
from app.api.wsmanager import ConnectionManager
from app.core import logs
from app.core.config import get_settings
from app.core.hub import Hub
from app.analysis.service import AnalysisService
from app.ingest.service import IngestService
from app.store import accounts
from app.store.db import Database
from app.store.schema import analysis_tables_ready, missing_tables

settings = get_settings()
logs.setup_logging(settings.log_dir, settings.LOG_LEVEL)
UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings
    missing = s.missing_for_start()
    if missing:
        raise RuntimeError(f"Missing settings in .env: {', '.join(missing)}. Run 'python engine_ctl.py init' and fill in the Oracle values.")
    s.validate_secrets()
    logs.milestone("The price engine is starting.")
    app.state.settings = s
    db = app.state.db = Database(s)
    try:
        await db.start()
    except Exception as exc:
        raise RuntimeError(f"Could not connect to the Oracle database: {exc}. Check wallet/, ORACLE_ADB_* in .env, then run 'python engine_ctl.py check-db'.") from exc
    absent = await missing_tables(db)
    if absent:
        raise RuntimeError(f"Database tables are missing ({', '.join(absent)}). Run sql/oracle_schema.sql in Database Actions, or 'python engine_ctl.py initdb'.")

    app.state.hub = hub = Hub()
    app.state.registry = ClientRegistry(db)
    app.state.pairs = PairBook(db)
    app.state.cache = TTLCache(s.CACHE_MAX_ENTRIES)
    app.state.ws = ws_mgr = ConnectionManager()
    hub.subscribe(ws_mgr.dispatch)
    app.state.ingest = ingest = IngestService(s, db, hub)
    await ingest.start()
    analysis_ready = await analysis_tables_ready(db)
    if not analysis_ready:
        logs.warn("The analysis tables are not in the database yet, so the analysis screen is read-only. Run initdb.bat once to create them.")
    app.state.analysis = AnalysisService(s, db, ingest, analysis_ready)
    app.state.analysis.begin_background()
    if s.is_dev and s.UI_ENABLED:
        await accounts.ensure_local_ui_client(db, s.API_KEY_PEPPER, Path(s.data_dir) / "local_ui.key")
    stats = asyncio.create_task(ws.stats_logger(app))
    logs.milestone(f"Ready. API on http://{s.API_HOST}:{s.API_PORT}" + (f"  UI: http://{s.API_HOST}:{s.API_PORT}/ui/" if s.UI_ENABLED else ""))
    yield
    stats.cancel()
    await app.state.analysis.shutdown()
    await ingest.stop()
    await db.close()
    logs.activity("The API has stopped.")


app = FastAPI(
    title="Price Engine", version="2.0.0", lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_DOCS else None, redoc_url=None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)
app.include_router(public.router)
app.include_router(market.router)
app.include_router(admin.router)
app.include_router(analysis.router)
app.include_router(ws.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logs.error(f"An unexpected error occurred while handling a request to {request.url.path}: {exc}", exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


if settings.UI_ENABLED:
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/ui/")

    @app.get("/ui/config.json", include_in_schema=False)
    async def ui_config(request: Request):
        """Local testing convenience: hands the UI its keys. Only in development and only from this computer."""
        s = request.app.state.settings
        host = request.client.host if request.client else ""
        if not s.is_dev or host not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"detail": "Not available"}, status_code=404)
        key_file = Path(s.data_dir) / "local_ui.key"
        return {"apiKey": key_file.read_text().strip() if key_file.exists() else "", "adminKey": s.ADMIN_API_KEY,
                "liveTimeframes": s.live_timeframes, "storeTicks": s.STORE_TICKS}

    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
