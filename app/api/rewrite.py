"""POST /api/rewrite — the core transaction.

Accepts pasted resume text + JD text, returns the full JDResume JSON (parsed JD +
parsed resume + STAR-rewritten bullets + coverage). The web front end then hands
this JSON to /api/export to render a PDF/docx. Splitting rewrite from export
means the expensive LLM work happens once; re-exporting to a different format is
free and instant.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.jd.keywords import Coverage
from app.jd.parser import ParsedJD, parse_jd
from app.llm.client import LLMUnavailableError
from app.resume.parser import ParsedResume, parse_resume
from app.resume.star import JDResume, RewrittenBullet, rewrite_star

log = logging.getLogger("cvfit.api.rewrite")

router = APIRouter(prefix="/api", tags=["rewrite"])


class RewriteRequest(BaseModel):
    resume_text: str = Field(..., description="旧简历全文（粘贴文本）")
    jd_text: str = Field(..., description="目标岗位 JD 全文（粘贴文本）")


class RewriteResponse(BaseModel):
    jd: ParsedJD
    resume: ParsedResume
    rewritten: list[RewrittenBullet]
    coverage: Coverage


@router.post("/rewrite", response_model=RewriteResponse)
def rewrite_endpoint(req: RewriteRequest) -> RewriteResponse:
    """Parse JD + resume, STAR-rewrite, score coverage → JDResume JSON."""
    if not (req.resume_text or "").strip():
        raise HTTPException(status_code=400, detail="resume_text 不能为空")
    if not (req.jd_text or "").strip():
        raise HTTPException(status_code=400, detail="jd_text 不能为空")

    try:
        parsed_jd = parse_jd(req.jd_text)
        parsed_resume = parse_resume(req.resume_text)
    except LLMUnavailableError as exc:
        # No key at all — parse_* falls back to heuristic, so this shouldn't
        # fire for parsing; surface it cleanly if it ever does.
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        jd_resume = rewrite_star(parsed_jd, parsed_resume)
    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure as 502
        log.exception("rewrite pipeline failed")
        raise HTTPException(status_code=502, detail=f"改写失败：{exc}") from exc

    return RewriteResponse(
        jd=jd_resume.jd,
        resume=jd_resume.resume,
        rewritten=jd_resume.rewritten,
        coverage=jd_resume.coverage,
    )
