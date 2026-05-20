from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.api.eval import router as eval_router
from app.api.health import router as health_router
from app.api.personas import router as personas_router
from app.config import settings
from app.db import close_pool, init_pool
from app.realtime.retrieve import router as realtime_retrieve_router
from app.realtime.token import router as realtime_token_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool(settings.database_url)
    yield
    await close_pool()


app = FastAPI(title="Stakeholder Interview Simulator", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(personas_router, prefix="/api")
app.include_router(eval_router, prefix="/api")
app.include_router(realtime_token_router, prefix="/api")
app.include_router(realtime_retrieve_router, prefix="/api")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file = static_dir / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(static_dir / "index.html")
