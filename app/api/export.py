"""POST /api/export — render a JDResume to PDF or docx.

Takes the JDResume JSON produced by /api/rewrite and a format selector.
Returns the rendered file as a download. ``watermark`` defaults to True (free
tier); v0.2 wires the real ¥9.9/投递 unwatermark flow: a request carrying a
*paid* ``order_token`` (from POST /api/payment) is rendered WITHOUT the
watermark, regardless of the ``watermark`` flag. This is the unlock the v0.1.0
plan explicitly deferred to v0.2 ("支付在 v0.2").
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.payment import is_paid_order
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
    # Free tier = watermarked; a paid ¥9.9 order_token is the only unwatermark
    # trigger. v0.3: the client `watermark` flag is no longer honored for the
    # unwatermark decision, so `watermark: false` cannot bypass the paywall.
    watermark: bool = True
    # v0.2: a paid order token (from POST /api/payment) grants the unwatermark.
    # When present AND paid, the output is rendered without the watermark even
    # if the client left `watermark: True`. Missing/expired/unpaid tokens are
    # ignored (the free-tier watermark stands).
    order_token: Optional[str] = None


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
    # v0.3: only a paid order token removes the watermark. The client `watermark`
    # flag is dropped from this decision so `watermark: false` cannot bypass the
    # ¥9.9 unwatermark paywall. Missing/expired/unpaid tokens are ignored (the
    # free-tier watermark stands).
    paid = bool(req.order_token) and is_paid_order(req.order_token)
    wants_watermark = not paid
    watermark_text = settings.watermark_text if wants_watermark else None
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
