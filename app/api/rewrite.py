"""POST /api/rewrite — the core transaction.

Accepts pasted resume text + JD text, returns the full JDResume JSON (parsed JD +
parsed resume + STAR-rewritten bullets + coverage). The web front end then hands
this JSON to /api/export to render a PDF/docx. Splitting rewrite from export
means the expensive LLM work happens once; re-exporting to a different format is
free and instant.

v0.4.0: this endpoint is the LLM-expensive surface (a single accepted request
fans out into ~3 billable LLM completions: jd_parse + star_rewrite +
keyword_match). The advertised free-tier "1 次/天" quota
(``settings.free_daily_quota``, app/config.py) was configured but never enforced
in v0.1–v0.3, so an unauthenticated client could spam /api/rewrite and drain the
operator's DeepSeek/Qwen budget. v0.4.0 enforces it at this entry via a per-IP
daily counter (in-process, no DB — same posture as ``_OrderStore`` in
app/api/payment.py and the free-quota counter referenced in app/config.py:45-47).
The paid ¥9.9 unwatermark path is untouched (it gates /api/export, not here).
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.jd.keywords import Coverage
from app.jd.parser import ParsedJD, parse_jd
from app.llm.client import LLMUnavailableError
from app.resume.parser import ParsedResume, parse_resume
from app.resume.star import JDResume, RewrittenBullet, rewrite_star

log = logging.getLogger("cvfit.api.rewrite")

router = APIRouter(prefix="/api", tags=["rewrite"])


# --- free-tier daily quota (v0.4.0) -----------------------------------------
# In-process per-IP daily counter, mirroring the _OrderStore pattern in
# app/api/payment.py (no DB — single-instance self-host deploy). Resets when the
# calendar date changes. Capped at settings.free_daily_quota (default 1 = the
# advertised "免费 1 次/天"). The counter is keyed by client IP + date so each
# local install / distinct client gets its own daily bucket.

class _FreeQuotaStore:
    """Tiny in-process per-IP daily free-rewrite counter (no DB).

    Same stateless posture as the free-quota counter referenced in
    app/config.py:45-47 and the ``_OrderStore`` in app/api/payment.py:75. A
    production deployment needing cross-restart persistence can swap this for a
    SQLite shim later; v0.4.0 keeps it in-process to stay single-binary.
    """

    def __init__(self) -> None:
        # ip -> (date_iso, count_of_accepted_rewrites_today)
        self._counts: dict[str, tuple[str, int]] = {}

    def _entry(self, client_ip: str) -> tuple[str, int]:
        today = datetime.date.today().isoformat()
        entry = self._counts.get(client_ip)
        if entry is None or entry[0] != today:
            # Date rollover (or first sight of this IP today) resets the bucket.
            self._counts[client_ip] = (today, 0)
            return (today, 0)
        return entry

    def remaining(self, client_ip: str) -> int:
        """Free rewrites left for this client today (>= 0)."""
        _today, count = self._entry(client_ip)
        return max(0, settings.free_daily_quota - count)

    def consume(self, client_ip: str) -> None:
        """Claim one free-rewrite slot for this client today.

        Called when a request is ACCEPTED (passes the quota gate, before the
        billable LLM fan-out) so a slot is consumed per attempt — not only per
        successful rewrite. Counting attempts (not just successes) closes the
        budget-drain vector where a client spams inputs that trigger LLM calls
        but fail downstream without ever consuming a "success" slot. The cost of
        this stricter stance is that an honest user whose rewrite fails (502/503)
        also spends their daily slot; the ¥9.9/投递 unwatermark path is the
        escape hatch for repeated attempts.
        """
        # v0.7.0 (cv-fit-zh-quality-1): prune other clients' stale buckets from
        # previous days so the per-IP map stays bounded (one entry per IP seen
        # today, not per IP ever). consume() runs at most free_daily_quota times
        # per IP per day, so the O(n) scan is amortised to nothing. remaining()
        # still self-heals the current IP's stale bucket via _entry.
        today_iso = datetime.date.today().isoformat()
        for ip in [k for k, (d, _) in self._counts.items() if d != today_iso]:
            self._counts.pop(ip, None)
        today, count = self._entry(client_ip)
        self._counts[client_ip] = (today, count + 1)

    def reset(self) -> None:
        """Clear all buckets (test hook + date-rollover is handled per-IP above)."""
        self._counts.clear()


_quota = _FreeQuotaStore()


class RewriteRequest(BaseModel):
    resume_text: str = Field(..., description="旧简历全文（粘贴文本）")
    jd_text: str = Field(..., description="目标岗位 JD 全文（粘贴文本）")


class RewriteResponse(BaseModel):
    jd: ParsedJD
    resume: ParsedResume
    rewritten: list[RewrittenBullet]
    coverage: Coverage


@router.post("/rewrite", response_model=RewriteResponse)
def rewrite_endpoint(req: RewriteRequest, request: Request) -> RewriteResponse:
    """Parse JD + resume, STAR-rewrite, score coverage → JDResume JSON."""
    if not (req.resume_text or "").strip():
        raise HTTPException(status_code=400, detail="resume_text 不能为空")
    if not (req.jd_text or "").strip():
        raise HTTPException(status_code=400, detail="jd_text 不能为空")

    # v0.4.0: enforce the advertised free-tier daily quota before the billable
    # LLM fan-out. Keyed by client IP (same in-process posture as the payment
    # order store). The paid ¥9.9 unwatermark path gates /api/export, not here,
    # so it is unaffected.
    client_ip = request.client.host if request.client else "anonymous"
    if _quota.remaining(client_ip) <= 0:
        raise HTTPException(
            status_code=429,
            detail=(
                "今日免费改写配额已用完（免费 1 次/天）。"
                "去水印/多次导出请通过 ¥9.9/投递 支付后导出。"
            ),
        )
    # Consume the slot on acceptance (before the LLM calls) so each attempt
    # counts toward the daily cap — see _FreeQuotaStore.consume rationale.
    _quota.consume(client_ip)

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
