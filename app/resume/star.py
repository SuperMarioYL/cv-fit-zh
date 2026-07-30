"""STAR rewrite orchestrator — assembles the core JDResume primitive.

Owns :class:`RewrittenBullet` and :class:`JDResume` (the core primitive from
mvp_plan §2: a structured resume where each experience bullet is paired with
its STAR rewrite and the JD keywords it covers). :func:`rewrite_star` is the
single entry point: ParsedJD + ParsedResume in, JDResume out.

Pipeline (per mvp_plan §4, ~3 LLM calls per resume when a key is set):
  star_rewrite (1 batched call)  ->  keyword_match (1 call)  ->  assemble.
When no LLM key is configured, bullets pass through unchanged and coverage is
scored deterministically, so the export pipeline stays fully runnable offline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from pydantic import BaseModel, Field

from app.jd.keywords import Coverage, extract_keywords, score_coverage
from app.jd.parser import ParsedJD
from app.llm.client import LLMError, get_client
from app.llm.prompts import STAR_REWRITE_SYSTEM, STAR_REWRITE_USER
from app.resume.parser import ParsedResume

log = logging.getLogger("cvfit.star")


class RewrittenBullet(BaseModel):
    """One experience bullet, its STAR rewrite, and the keywords it covers."""

    orig: str = Field(default="", description="原始经历条目")
    star: str = Field(default="", description="STAR 改写后的条目")
    covers_keywords: list[str] = Field(default_factory=list, description="本条覆盖到的 JD 关键词")


class JDResume(BaseModel):
    """The core primitive: a JD-aligned resume document."""

    jd: ParsedJD
    resume: ParsedResume
    rewritten: list[RewrittenBullet] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)

    @property
    def star_bullets(self) -> list[str]:
        """Convenience: the rewritten STAR text, one string per bullet."""
        return [b.star for b in self.rewritten]


def _coerce_rewritten(
    raw: dict[str, Any], originals: list[str]
) -> list[RewrittenBullet]:
    """Reconcile LLM output to the input bullet list (length + order safe)."""
    items = raw.get("rewritten") or raw.get("bullets") or []
    if not isinstance(items, list):
        items = []
    out: list[RewrittenBullet] = []
    for i, orig in enumerate(originals):
        item = items[i] if i < len(items) else {}
        if not isinstance(item, dict):
            item = {}
        star = str(item.get("star") or item.get("rewrite") or "").strip()
        covers = item.get("covers_keywords") or item.get("covers") or []
        if isinstance(covers, str):
            import re

            covers = [c.strip() for c in re.split(r"[,，、；;]+", covers) if c.strip()]
        out.append(
            RewrittenBullet(
                orig=orig,
                star=star or orig,  # never emit an empty bullet
                covers_keywords=[str(k).strip() for k in covers if str(k).strip()],
            )
        )
    return out


def _rewrite_with_llm(parsed_jd: ParsedJD, parsed_resume: ParsedResume) -> list[RewrittenBullet]:
    originals = parsed_resume.all_bullets
    if not originals:
        return []
    keywords = extract_keywords(parsed_jd)
    bullets_json = json.dumps(
        [{"index": i, "text": b} for i, b in enumerate(originals)],
        ensure_ascii=False,
        indent=2,
    )
    client = get_client()
    raw = client.complete_json(
        STAR_REWRITE_SYSTEM,
        STAR_REWRITE_USER.format(
            jd_title=parsed_jd.title or "（未识别岗位名）",
            jd_keywords=json.dumps(keywords, ensure_ascii=False),
            bullets_json=bullets_json,
        ),
    )
    return _coerce_rewritten(raw, originals)


def _rewrite_passthrough(parsed_resume: ParsedResume) -> list[RewrittenBullet]:
    """Offline fallback: bullets unchanged, covers_keywords left empty."""
    return [RewrittenBullet(orig=b, star=b, covers_keywords=[]) for b in parsed_resume.all_bullets]


def rewrite_star(parsed_jd: ParsedJD, parsed_resume: ParsedResume) -> JDResume:
    """Rewrite every experience bullet to STAR form and score JD coverage.

    Returns a fully assembled :class:`JDResume`. Never raises on LLM failure —
    it degrades to passthrough bullets + deterministic coverage so export keeps
    working.
    """
    client = get_client()
    if client.available:
        try:
            rewritten = _rewrite_with_llm(parsed_jd, parsed_resume)
        except (LLMError, ValueError) as exc:  # noqa: BLE001
            log.warning("STAR rewrite LLM call failed (%s); passthrough fallback", exc)
            rewritten = _rewrite_passthrough(parsed_resume)
    else:
        rewritten = _rewrite_passthrough(parsed_resume)

    star_bullets = [b.star for b in rewritten]
    coverage = score_coverage(star_bullets, parsed_jd)
    # Back-fill each bullet's covers_keywords from the coverage roll-up so the
    # per-bullet keyword annotation is consistent with the global score.
    _annotate_bullet_coverage(rewritten, coverage)

    return JDResume(jd=parsed_jd, resume=parsed_resume, rewritten=rewritten, coverage=coverage)


def _annotate_bullet_coverage(bullets: list[RewrittenBullet], coverage: Coverage) -> None:
    """Re-derive per-bullet covers_keywords from the deterministic matcher.

    The LLM's per-bullet covers_keywords can drift; we re-run the deterministic
    hit check so the on-PDF keyword highlighting is honest and stable.
    """
    from app.jd.keywords import _keyword_hit  # local import to avoid cycle

    for b in bullets:
        b.covers_keywords = [kw for kw in coverage.matched if _keyword_hit(kw, [b.star])]


# --- CLI (m3-style: python -m app.resume.star --resume x.txt --jd y.txt) ---

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.resume.star",
        description="Rewrite a resume's bullets to STAR form aligned to a JD, print JDResume JSON.",
    )
    parser.add_argument("--resume", required=True, help="path to a resume text file (or '-' for stdin)")
    parser.add_argument("--jd", required=True, help="path to a JD text file (or '-' for stdin)")
    args = parser.parse_args(argv)

    from app.jd.parser import parse_jd
    from app.resume.parser import parse_resume

    def _read(path: str) -> str:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    parsed_jd = parse_jd(_read(args.jd))
    parsed_resume = parse_resume(_read(args.resume))
    jd_resume = rewrite_star(parsed_jd, parsed_resume)
    print(json.dumps(jd_resume.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
