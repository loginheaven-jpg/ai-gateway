from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from contextlib import asynccontextmanager

from .routers import ai_router, settings_router, stt_router, image_router
from .config import bootstrap_config, config_source, log_db_timing, ConfigUnavailable
from .usage import init_usage_table, usage_writer


_RISKY_MODEL_PATTERNS = ("-preview", "-exp", "-experimental")


def _audit_provider_models(config) -> None:
    """Flag model IDs that historically caused outages: preview/experimental
    aliases retire without notice, and empty API keys."""
    for pid, p in config.providers.items():
        if not p.enabled:
            continue
        if not p.api_key:
            print(f"[STARTUP WARN] {pid}: missing API key (enabled but won't work)")
            continue
        if any(tag in (p.model or "") for tag in _RISKY_MODEL_PATTERNS):
            print(
                f"[STARTUP WARN] {pid}: using risky model '{p.model}' "
                f"(preview/experimental aliases retire without notice - prefer '-latest')"
            )
        else:
            print(f"[STARTUP OK]   {pid}: {p.model}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config into memory once; request paths never touch the DB."""
    print("[STARTUP] Initializing database...")
    log_db_timing()
    config = bootstrap_config()
    print(f"[STARTUP] Loaded {len(config.providers)} providers (source={config_source()})")
    try:
        init_usage_table()
    except Exception as e:
        print(f"[STARTUP ERROR] usage_logs init failed: {e} (writer will retry on connect)")
    usage_writer.start()
    try:
        _audit_provider_models(config)
    except Exception as e:  # a log line must never block startup
        print(f"[STARTUP WARN] model audit failed: {type(e).__name__}")
    yield
    print("[SHUTDOWN] AI Gateway shutting down")
    usage_writer.stop(timeout=5.0)


app = FastAPI(
    title="AI Gateway",
    description="Centralized AI service management for multiple applications",
    version="1.0.0",
    lifespan=lifespan
)


@app.exception_handler(ConfigUnavailable)
async def _config_unavailable_handler(request: Request, exc: ConfigUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(ai_router)
app.include_router(settings_router)
app.include_router(stt_router)
app.include_router(image_router)

# Serve static files (frontend)
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.get("/")
async def root():
    """Serve the admin dashboard"""
    admin_file = frontend_path / "admin.html"
    if admin_file.exists():
        return FileResponse(str(admin_file))
    return {"message": "AI Gateway is running", "docs": "/docs"}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "ai-gateway",
        "config_source": config_source(),
        "usage_log": usage_writer.stats(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
