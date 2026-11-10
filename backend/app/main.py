from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .profiles import router as profiles_router
from .config import get_settings
from .db import create_all, init_engine, migrate
from .jobs import recover_interrupted, worker_loop
from .seed import ensure_seed, redact_stored_personal_details

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    if os.environ.get("LOOP_WATCHDOG") == "1":
        from .watchdog import start

        start(asyncio.get_running_loop())
    init_engine()
    await create_all()
    await migrate()
    await ensure_seed()
    await redact_stored_personal_details()
    await recover_interrupted()
    stop = asyncio.Event()
    task = asyncio.create_task(worker_loop(stop)) if get_settings().worker_enabled else None
    yield
    stop.set()
    if task:
        task.cancel()


app = FastAPI(title="Outreach Draft Pipeline", lifespan=lifespan)
app.include_router(router)
app.include_router(profiles_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    logging.getLogger("api").exception("unhandled")
    return JSONResponse({"detail": f"Server error: {type(exc).__name__}"}, status_code=500)


static = Path(get_settings().static_dir)
if (static / "assets").exists():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    f = static / full_path
    if full_path and f.is_file():
        return FileResponse(f)
    index = static / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/."}, status_code=404)
