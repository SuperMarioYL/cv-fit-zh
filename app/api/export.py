"""POST /api/export — render a JDResume to PDF or docx.

Takes the JDResume JSON produced by /api/rewrite and a format selector.
Returns the rendered file as a download. ``watermark`` defaults to True (free
tier); the ¥9.9/投递 unwatermark path is a front-end stub per GTM §8 — the
button explains the manual-WeChat-payment flow, and only flips watermark off
once the user has paid and the dev confirms (v0.2 adds real payment).
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.export import render_docx_bytes, render_pdf_bytes
from app.jd.keywords import Coverage
from app.jd.parser import ParsedJD
from app.resume.parser import ParsedResume
from app.resume.star import JDResume, RewrittenBullet

log = logging.getLogger("cvfit.api.export")

router = APIRouter(prefix="/api", tags=["export"])

ExportFormat = Literal["pdf", "docx"]


class ExportRequest(BaseModel):
    jd: ParsedJD = Field(default_factory=ParsedJD)
    resume: ParsedResume = Field(default_factory=ParsedResume)
    rewritten: list[RewrittenBullet] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    format: ExportFormat = "pdf"
    # Free tier = watermarked; paid ¥9.9 removes the watermark. Default watermarked.
    watermark: bool = True


def _to_jd_resume(req: ExportRequest) -> JDResume:
    return JDResume(
        jd=req.jd,
        resume=req.resume,
        rewritten=req.rewritten,
        coverage=req.coverage,
    )


@router.post("/export")
def export_endpoint(req: ExportRequest) -> Response:
    """Render the given JDResume to PDF or docx and return it for download."""
    jd_resume = _to_jd_resume(req)
    watermark_text = settings.watermark_text if req.watermark else None
    if req.format == "pdf":
        try:
            data = render_pdf_bytes(jd_resume, watermark=watermark_text)
        except Exception as exc:  # noqa: BLE001
            log.exception("PDF render failed")
            raise HTTPException(status_code=502, detail=f"PDF 导出失败：{exc}") from exc
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="resume.pdf"',
            },
        )
    if req.format == "docx":
        try:
            data = render_docx_bytes(jd_resume, watermark=watermark_text)
        except Exception as exc:  # noqa: BLE001
            log.exception("docx render failed")
            raise HTTPException(status_code=502, detail=f"docx 导出失败：{exc}") from exc
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": 'attachment; filename="resume.docx"',
            },
        )
    raise HTTPException(status_code=400, detail=f"不支持的格式：{req.format}")
