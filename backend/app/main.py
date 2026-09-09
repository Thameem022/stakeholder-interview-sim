from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure ES module scripts (.mjs) are served as text/javascript regardless of the
# host OS / interpreter mime map. Browsers reject AudioWorklet modules and dynamic
# import() of any script served with a non-JS MIME type, which silently breaks the
# HeadAudio lip-sync pipeline (public/headaudio/dist/*.mjs) in production.
mimetypes.add_type("text/javascript", ".mjs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import Depends

from app.api.auth import router as auth_router
from app.api.eval import router as eval_router
from app.api.health import router as health_router
from app.api.personas import router as personas_router
from app.auth.dependencies import require_user
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

# Public. /health is the deploy probe, and the auth router owns its own 401s —
# gating it would lock everyone out of the login endpoints themselves.
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

# Everything else needs a session. Declared here rather than per-route so that
# "what is public?" has exactly one answer, and so a route added to any of
# these modules is protected the moment it is written rather than the moment
# someone remembers to decorate it. Routes that need the caller's identity
# still declare Depends(require_user) themselves; FastAPI caches on the
# callable, so that is one session lookup per request, not two.
_authenticated = [Depends(require_user)]
app.include_router(personas_router, prefix="/api", dependencies=_authenticated)
app.include_router(eval_router, prefix="/api", dependencies=_authenticated)
app.include_router(realtime_token_router, prefix="/api", dependencies=_authenticated)
app.include_router(realtime_retrieve_router, prefix="/api", dependencies=_authenticated)

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
        # SPA fallback must never be cached: if an asset is temporarily missing
        # (e.g. mid-deploy), a cached index.html under that asset's URL would
        # keep breaking module/worklet loads long after the asset is restored.
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-store"},
        )
