from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from contextlib import asynccontextmanager

from .routers import ai_router, settings_router
from .config import init_db, load_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup"""
    print("[STARTUP] Initializing database...")
    try:
        init_db()
        config = load_config()
        print(f"[STARTUP] Loaded {len(config.providers)} providers")
    except Exception as e:
        print(f"[STARTUP ERROR] Database initialization failed: {e}")
        print("[STARTUP] Continuing without database - will use environment defaults")
    yield
    print("[SHUTDOWN] AI Gateway shutting down")


app = FastAPI(
    title="AI Gateway",
    description="Centralized AI service management for multiple applications",
    version="1.0.0",
    lifespan=lifespan
)

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
    return {"status": "healthy", "service": "ai-gateway"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
