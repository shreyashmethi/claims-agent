"""Serves the console and the API together: API under /api, built frontend at /.

Run:  uvicorn app.api.server:server --port 8000      (build the frontend first: cd frontend && npm run build)
"""
from __future__ import annotations

from pathlib import Path

from app.deps import check as _check_deps

_check_deps()

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.api.main import app as api  # noqa: E402

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

server = FastAPI(title="Claims console", docs_url=None, redoc_url=None)
server.mount("/api", api)

if DIST.exists():
    server.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @server.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
else:
    @server.get("/", include_in_schema=False)
    def no_build():
        return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build. "
                                       "API docs are at /api/docs."})
