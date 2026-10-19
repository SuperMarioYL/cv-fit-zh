"""JD keyword extraction + coverage scoring.

Owns the :class:`Coverage` model (the scoring half of the core ``JDResume``
primitive). Coverage can be computed two ways:

* :func:`score_coverage` — LLM-backed semantic match (the GTM/plan path); falls
  back to a deterministic substring baseline when no model key is configured so
  the pipeline stays runnable for local exploration and tests.
* :func:`score_coverage_deterministic` — pure-Python substring/normalised match,
  used as the baseline and by the unit tests (no network, no key).

Both produce a ``Coverage`` with ``matched`` / ``missing`` / ``score``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.jd.parser import ParsedJD
from app.llm.client import LLMError, get_client
from app.llm.prompts import KEYWORD_MATCH_SYSTEM, KEYWORD_MATCH_USER


class Coverage(BaseModel):
    """How well the rewritten bullets cover the JD's keyword set."""

    matched: list[str] = Field(default_factory=list, description="被至少一条条目覆盖到的关键词")
    missing: list[str] = Field(default_factory=list, description="没有被任何条目覆盖到的关键词")
    score: float = Field(default=0.0, description="matched / 全部关键词数量")

    @field_validator("score")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if v < 0:
            return 0.0
        if v > 1:
            return 1.0
        return round(v, 4)


def extract_keywords(parsed_jd: ParsedJD) -> list[str]:
    """Return the de-duplicated union of a JD's must + nice skills."""
    return parsed_jd.all_skills


# --- deterministic baseline (no LLM, no network) ---------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _normalize(s: str) -> str:
    """Lowercase + strip whitespace + remove common Chinese/English punctuation."""
    s = unicodedata.normalize("NFKC", s).lower().strip()
    # Drop spaces inside keywords that should be contiguous ("python 3" -> "python3")
    # but keep them for readability matching. We keep spaces; substring match is
    # robust enough. Just strip punctuation noise.
    return re.sub(r"[，。、；；：·\s]+", "", s)


def _keyword_hit(keyword: str, bullets: list[str]) -> bool:
    """True if a keyword (or an obvious synonym) appears in any bullet."""
    kw = _normalize(keyword)
    if not kw:
        return False
    haystack = " ".join(_normalize(b) for b in bullets)
    if kw in haystack:
        return True
    # A few common abbreviation/alias pairs so the baseline isn't too brittle.
    aliases = {
        "python": ["py", "django", "flask", "fastapi"],
        "java": ["jvm", "spring", "springboot"],
        "sql": ["mysql", "postgres", "postgresql", "hive"],
        "分布式": ["分布式系统", "微服务", "rpc", "grpc"],
        "高并发": ["高吞吐", "qps", "并发"],
        "机器学习": ["ml", "深度学习", "神经网络", "ai模型"],
    }
    for key, syns in aliases.items():
        if kw == _normalize(key):
            for syn in syns:
                if _normalize(syn) in haystack:
                    return True
        if kw in [_normalize(s) for s in syns]:
            # if the keyword IS a synonym, accept the canonical too
            if _normalize(key) in haystack:
                return True
    return False


def score_coverage_deterministic(
    rewritten_bullets: list[str], parsed_jd: ParsedJD
) -> Coverage:
    """Substring/alias coverage — the no-LLM baseline. Pure & deterministic."""
    keywords = extract_keywords(parsed_jd)
    matched = [kw for kw in keywords if _keyword_hit(kw, rewritten_bullets)]
    matched_set = set(matched)
    missing = [kw for kw in keywords if kw not in matched_set]
    total = len(keywords)
    score = (len(matched) / total) if total else 0.0
    return Coverage(matched=matched, missing=missing, score=score)


# --- LLM-backed coverage (semantic match) ---------------------------------

def _coerce_coverage(raw: dict[str, Any], keywords: list[str]) -> Coverage:
    """Build a Coverage from raw LLM JSON, reconciling against the keyword list."""
    # v0.7.0 (cv-fit-zh-fix-2): a drifted {"matched": 7} (non-iterable) raised
    # TypeError here, escaping score_coverage's ``(LLMError, ValueError)`` catch.
    # A truthy non-list raises ValueError — the caught, fallback-triggering
    # error — so score_coverage degrades to the deterministic scorer (treating
    # it as empty would report a false 0% instead of scoring the bullets).
    # Falsy values keep the old `or []` empty semantics.
    matched_raw = raw.get("matched")
    if matched_raw and not isinstance(matched_raw, list):
        raise ValueError(
            f"LLM coverage JSON 'matched' was not a list (got {type(matched_raw).__name__})"
        )
    matched = [str(k).strip() for k in matched_raw or [] if str(k).strip()]
    # Reconcile: every keyword must land in exactly one bucket. If the model
    # drifted, rebuild deterministically from matched so score is honest. The
    # raw `missing` list is intentionally ignored — it is re-derived below.
    matched_set = {k for k in matched}
    final_matched = [kw for kw in keywords if kw in matched_set]
    final_missing = [kw for kw in keywords if kw not in matched_set]
    total = len(keywords)
    score = (len(final_matched) / total) if total else 0.0
    return Coverage(matched=final_matched, missing=final_missing, score=score)


def score_coverage(
    rewritten_bullets: list[str], parsed_jd: ParsedJD
) -> Coverage:
    """Score keyword coverage, preferring the LLM but falling back to baseline.

    The LLM does semantic matching (synonyms / phrasing). If no key is
    configured or the call fails, the deterministic substring scorer is used so
    the pipeline is never blocked on a model outage.
    """
    keywords = extract_keywords(parsed_jd)
    client = get_client()
    if not client.available:
        return score_coverage_deterministic(rewritten_bullets, parsed_jd)
    bullets_json = json.dumps(
        [{"index": i, "text": b} for i, b in enumerate(rewritten_bullets)],
        ensure_ascii=False,
        indent=2,
    )
    try:
        raw = client.complete_json(
            KEYWORD_MATCH_SYSTEM,
            KEYWORD_MATCH_USER.format(
                keywords_json=json.dumps(keywords, ensure_ascii=False, indent=2),
                bullets_json=bullets_json,
            ),
        )
        return _coerce_coverage(raw, keywords)
    except (LLMError, ValueError) as exc:  # noqa: BLE001
        # Fall back to the deterministic scorer — coverage must never hard-fail.
        import logging

        logging.getLogger("cvfit.jd").warning("LLM coverage failed (%s); using baseline", exc)
        return score_coverage_deterministic(rewritten_bullets, parsed_jd)
