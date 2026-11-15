"""
Permit Orchestrator - UI & Cloud Run Probes Route Handler (T10).

Serves:
- Production frontend build from frontend/dist (GET / and static assets at /assets)
- Single Page Application (SPA) routing fallback for client-side navigation
- Cloud Run health probe (GET /health)
- Cloud Run verification probe (GET /hello)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_dist_dir() -> Path:
    """Return the absolute path to the frontend build directory."""
    env_dir = os.environ.get("FRONTEND_DIST_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return (REPO_ROOT / "frontend" / "dist").resolve()


def get_index_path() -> Path:
    """Return path to production index.html."""
    return get_dist_dir() / "index.html"


def get_assets_dir() -> Path:
    """Return path to frontend assets directory."""
    return get_dist_dir() / "assets"


FALLBACK_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Permit Orchestrator — Trace Waterfall UI</title>
  </head>
  <body class="bg-slate-950 text-slate-100 min-h-screen">
    <div id="root">
      <h1>Permit Orchestrator</h1>
      <p>Frontend production distribution ready.</p>
    </div>
  </body>
</html>
"""


async def serve_index() -> Response:
    """Serve production index.html or fallback HTML if dist not built yet."""
    index_file = get_index_path()
    if index_file.is_file():
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse(content=FALLBACK_HTML, status_code=status.HTTP_200_OK)


router = APIRouter(tags=["UI & Health"])


@router.get("/health", summary="Cloud Run health probe")
async def health() -> Dict[str, str]:
    """Cloud Run health probe returning healthy status."""
    return {"status": "healthy"}


@router.get("/hello", summary="Cloud Run verification probe")
async def hello() -> Dict[str, str]:
    """Cloud Run verification probe returning hello confirmation."""
    return {"message": "hello", "status": "ok"}


@router.get("/", response_class=HTMLResponse, summary="Serve frontend UI root")
async def root() -> Response:
    """Serve production index.html at root path."""
    return await serve_index()


@router.get("/assets/{file_path:path}", summary="Serve frontend static assets")
async def serve_asset(file_path: str) -> Response:
    """Serve static asset files (JS, CSS, images) from frontend/dist/assets."""
    assets_dir = get_assets_dir().resolve()
    target_file = (assets_dir / file_path).resolve()

    # Prevent directory traversal attacks
    try:
        target_file.relative_to(assets_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    if not target_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    return FileResponse(target_file)


@router.get("/{full_path:path}", response_class=HTMLResponse, summary="SPA client-side routing fallback")
async def spa_fallback(full_path: str, request: Request) -> Response:
    """
    Fallback for Single Page Application client-side routes.
    Serves production index.html for non-asset and non-API requests.
    """
    # Missing assets under /assets/ should return 404 rather than HTML
    if full_path.startswith("assets/"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Check if a top-level static file in dist matches (e.g. vite.svg, favicon.ico)
    dist_dir = get_dist_dir().resolve()
    candidate_file = (dist_dir / full_path).resolve()
    try:
        candidate_file.relative_to(dist_dir)
        if candidate_file.is_file():
            return FileResponse(candidate_file)
    except ValueError:
        pass

    return await serve_index()
