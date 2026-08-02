"""FastAPI application — the single process serving the cv-fit-zh web UI + API.

Layout:
  GET  /                       → web/templates/index.html (single page: two text boxes + button)
  POST /api/rewrite            → parse JD + resume, STAR rewrite, coverage → JDResume JSON
  POST /api/export             → render the JDResume to PDF/docx → download
  POST /api/payment            → create a ¥9.9/投递 unwatermark order (v0.2)
  GET  /api/payment/notify     → WeChat payment async callback (v0.2)
  GET  /api/payment/{token}    → poll an order's paid status (v0.2)
  GET  /api/health             → liveness probe
  GET  /api/version            → version probe

No DB, no queue, no multi-tenant — one stateless process per the mvp_plan §4
"一个 binary（uvicorn）". Run with: ``uvicorn app.main:app``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.export import router as export_router
from app.api.payment import router as payment_router
from app.api.rewrite import router as rewrite_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cvfit.main")

# Repo root is the parent of this package (app/ lives at repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_TEMPLATES = REPO_ROOT / "web" / "templates"
WEB_STATIC = REPO_ROOT / "web" / "static"

app = FastAPI(
    title="cv-fit-zh",
    description="JD 驱动的中文简历改写器 — 粘贴旧简历 + JD，30 秒生成 ATS 友好的中文简历 PDF。",
    version=__version__,
)

# Web UI templates + static assets.
templates = Jinja2Templates(directory=str(WEB_TEMPLATES))
if WEB_STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")

# API routers.
app.include_router(rewrite_router)
app.include_router(export_router)
app.include_router(payment_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the single-page web UI."""
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/api/health")
async def health() -> JSONResponse:
    from app.config import settings

    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "llm_configured": bool(settings.api_key or settings.fallback_api_key),
            "payment_configured": not settings.payment_disabled,
        }
    )


@app.get("/api/version")
async def version() -> JSONResponse:
    return JSONResponse({"version": __version__})
