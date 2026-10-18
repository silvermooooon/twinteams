"""Twinteams API composition. Product business state and SDK state stay separate."""
import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .api import router
from .provider_config import router as provider_config_router
from .personal_api import router as personal_router
from .credential_api import router as credential_router
from .config import settings
from .service import service

if sys.version_info[:2] != (3, 13):
    raise RuntimeError("twinteams backend requires Python 3.13.x")


@asynccontextmanager
async def lifespan(app):
    # API never starts recovery, a scheduler, or an Agent executor.
    await service.db_call("check_initialized")
    from .runtime_bridge import RuntimeBridge
    from .websocket import hub
    runtime = RuntimeBridge(service)
    try:
        try:
            await runtime.start()
        except Exception as exc:
            runtime.startup_error = str(exc)
            logging.getLogger(__name__).exception("Runtime read store unavailable")
        service.bind_runtime(runtime)
        await service.start(worker=False)
        await hub.start()
        yield
    finally:
        await hub.close()
        await service.close()
        await runtime.close()


app = FastAPI(title="Twinteams API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "X-Owner-Id"])
app.include_router(router)
app.include_router(provider_config_router)
app.include_router(personal_router)
app.include_router(credential_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    detail = "; ".join(f"{'.'.join(str(x) for x in item['loc'])}: {item['msg']}" for item in exc.errors())
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("API operation failed", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Operation failed; see server logs for the diagnostic"})


@app.get("/health")
def health():
    return {"status": "ok", "model_configured": service.model_configured, "development_mode": settings.local_identity_enabled}


# Optional same-origin production bundle. API routes above always win.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    assets = frontend_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith(("api/", "api", "health")):
            return JSONResponse(status_code=404, content={"detail": "Endpoint not found"})
        file = (frontend_dist / path).resolve()
        if file.is_relative_to(frontend_dist.resolve()) and file.is_file():
            return FileResponse(file)
        return FileResponse(frontend_dist / "index.html")
