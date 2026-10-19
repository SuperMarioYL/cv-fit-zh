"""v0.7.0 fixes — date-first resume headers + non-iterable LLM list fields +
in-process store eviction.

Three changes shipped in v0.7.0:

  (a) cv-fit-zh-fix-1 — the heuristic splitter mis-classified date-first
      experience headers ("2021.07-至今 某公司 某职位") as bullets because
      _BULLET_RE's character class includes \\d, and its sub() then chewed off
      the leading "2021." as a list ordinal — corrupting the line and merging
      separate jobs into the previous entry. A date-first line now takes the
      entry-header branch (discriminated by a 4-digit year + separator, so
      numbered 1-2 digit bullets are unaffected).
  (b) cv-fit-zh-fix-2 — a drifted LLM response can carry a NON-ITERABLE value
      in a list field ({"experience": 3} / {"matched": 7}); iterating it raised
      TypeError, which escapes every (LLMError, ValueError) fallback catch and
      500s /api/rewrite. Non-list values are now treated as empty (same guard
      posture as app/jd/parser.py:_as_str_list). Residual of the v0.5.0/v0.6.0
      non-dict fallback fixes, one level deeper.
  (c) cv-fit-zh-quality-1 — _OrderStore.create never evicted expired orders
      (removal happened only if that exact token was get()-accessed after
      expiry) and _FreeQuotaStore kept one bucket per IP forever; both stores
      now sweep stale entries opportunistically so long-running instances stay
      bounded. TTL / daily-quota semantics unchanged.

All tests are offline (heuristic paths) or use scripted fake LLM clients — no
network, no weasyprint native libs required.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.llm.client as llm_client_mod
from app.api.payment import _ORDER_TTL_SECONDS, _store
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- (a) cv-fit-zh-fix-1: date-first experience headers ----------------------


def test_dates_first_experience_headers_parse():
    """Two date-first jobs (no pipes, single spaces — a common CN layout) parse
    as TWO experiences with dates/company/title populated and clean bullets.
    Before the fix this produced ONE empty-metadata experience whose bullets
    were the two header lines chewed down to '07-至今 …' (v0.7.0 fix a)."""
    from app.resume.parser import parse_resume

    text = (
        "李四\n"
        "13900139000 | lisi@example.com\n"
        "\n"
        "工作经历\n"
        "2021.07-至今 某科技公司 后端开发工程师\n"
        "- 负责订单系统开发\n"
        "2019.07-2021.06 某创业公司 后端实习生\n"
        "- 负责内部工具开发\n"
    )
    out = parse_resume(text)
    assert len(out.experience) == 2
    first, second = out.experience
    assert first.dates.startswith("2021")
    assert "某科技公司" in (first.company + first.title)
    assert "后端" in (first.company + first.title)
    assert first.bullets == ["负责订单系统开发"]
    assert second.dates.startswith("2019")
    assert "某创业公司" in (second.company + second.title)
    assert second.bullets == ["负责内部工具开发"]
    # No chewed bullet fragments anywhere.
    for exp in out.experience:
        for b in exp.bullets:
            assert not b.startswith("07-")


def test_numbered_bullets_stay_bullets():
    """Control: 1-2 digit numbered/bullet lines are still bullets — the 4-digit
    year discriminator must not misclassify them."""
    from app.resume.parser import parse_resume

    text = (
        "工作经历\n"
        "某科技公司 | 后端开发工程师 | 2021.07-至今\n"
        "1. 负责订单系统开发\n"
        "12. 主导网关重构\n"
        "- 负责监控告警\n"
    )
    out = parse_resume(text)
    assert len(out.experience) == 1
    assert out.experience[0].company == "某科技公司"
    assert out.experience[0].bullets == [
        "负责订单系统开发",
        "主导网关重构",
        "负责监控告警",
    ]


def test_pipe_separated_sample_parse_unchanged(sample_resume):
    """Control: the pipe-separated conftest sample parses exactly as in v0.6.0
    (two experiences, company-first headers, no behaviour drift)."""
    from app.resume.parser import parse_resume

    out = parse_resume(sample_resume)
    assert len(out.experience) == 2
    assert out.experience[0].company == "某科技公司"
    assert out.experience[0].title == "后端开发工程师"
    assert out.experience[0].dates == "2021.07-至今"
    assert len(out.experience[0].bullets) == 3


# --- (b) cv-fit-zh-fix-2: non-iterable LLM list fields -----------------------


class _NonIterableListDriftClient:
    """Scripted client whose resume response carries {"experience": 3}."""

    available = True

    def require_available(self) -> None:
        return None

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if system.startswith("你是一名资深中文简历结构化解析器"):
            return {
                "basics": {"name": "张三", "phone": "13800138000"},
                "experience": 3,  # drifted: non-iterable where a list belongs
                "education": [],
            }
        if system.startswith("你是一名资深中文招聘 JD 结构化解析器"):
            return {"title": "后端开发", "must_skills": ["Python"], "nice_skills": [], "responsibilities": []}
        # STAR rewrite / anything else: passthrough shape.
        return {"rewritten": []}


@pytest.fixture
def experience_drift_llm():
    prev = llm_client_mod._client
    llm_client_mod._client = _NonIterableListDriftClient()
    try:
        yield
    finally:
        llm_client_mod._client = prev


def test_parse_resume_noniterable_experience_falls_back_to_heuristic(experience_drift_llm):
    """{"experience": 3} must degrade to the heuristic splitter (no TypeError,
    which escaped parse_resume's (LLMError, ValueError) catch — v0.7.0 fix b)."""
    from app.resume.parser import ParsedResume, parse_resume

    out = parse_resume("张三\n13800138000\n\n工作经历\n某公司 | 后端 | 2020-2023\n- 负责订单系统")
    assert isinstance(out, ParsedResume)
    # Heuristic fallback recovered the experience from the raw text.
    assert len(out.experience) == 1
    assert out.experience[0].company == "某公司"
    assert out.basics.name == "张三"


def test_rewrite_endpoint_experience_drift_returns_200(experience_drift_llm, client):
    """End-to-end: the drifted client must not 500 /api/rewrite."""
    resp = client.post(
        "/api/rewrite",
        json={
            "resume_text": "张三 13800138000\n\n工作经历\n某公司 | 后端 | 2020-2023\n- 负责订单系统",
            "jd_text": "招聘岗位：后端开发\n任职要求：\n1. 熟悉 Python",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "rewritten" in resp.json()


def test_score_coverage_noniterable_matched_degrades():
    """{"matched": 7} in the keyword-match response must not raise TypeError —
    the truthy non-list raises ValueError (the caught, fallback-triggering
    error), so score_coverage degrades to the deterministic scorer instead of
    escaping its (LLMError, ValueError) catch (v0.7.0 fix b)."""
    from app.jd.keywords import Coverage, score_coverage

    class _CovDriftClient:
        available = True

        def require_available(self) -> None:
            return None

        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            assert system.startswith("你是一名 JD 关键词覆盖核对器")
            return {"matched": 7}

    prev = llm_client_mod._client
    llm_client_mod._client = _CovDriftClient()
    try:
        from app.jd.parser import ParsedJD

        cov = score_coverage(["用 Python 重构服务"], ParsedJD(must_skills=["Python"]))
        assert isinstance(cov, Coverage)
        # The deterministic scorer engaged and honestly matched the keyword.
        assert cov.matched == ["Python"]
        assert cov.score == 1.0
    finally:
        llm_client_mod._client = prev


def test_parse_resume_valid_list_experience_still_llm_parsed():
    """Control: a VALID list experience field still parses via the LLM path
    (the isinstance guard only diverts non-lists)."""
    from app.resume.parser import parse_resume

    class _GoodClient(_NonIterableListDriftClient):
        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            if system.startswith("你是一名资深中文简历结构化解析器"):
                return {
                    "basics": {"name": "张三"},
                    "experience": [
                        {"title": "后端", "company": "某公司", "dates": "2020-2023", "bullets": ["负责订单系统"]}
                    ],
                    "education": [],
                }
            return {"rewritten": []}

    prev = llm_client_mod._client
    llm_client_mod._client = _GoodClient()
    try:
        out = parse_resume("张三\n某公司 后端")
        assert len(out.experience) == 1
        assert out.experience[0].company == "某公司"
        assert out.experience[0].bullets == ["负责订单系统"]
    finally:
        llm_client_mod._client = prev


# --- (c) cv-fit-zh-quality-1: opportunistic store eviction -------------------


def test_order_store_evicts_expired_on_create():
    """An order past its TTL is dropped from the store by the NEXT create —
    previously it lingered until that exact token was get()-accessed again."""
    stale = _store.create("stale-trade", 999, dev_mode=False)
    _store._orders[stale]["created_at"] = time.time() - _ORDER_TTL_SECONDS - 10
    fresh = _store.create("fresh-trade", 999, dev_mode=True)
    try:
        assert stale not in _store._orders
        assert fresh in _store._orders
        # The expired token is dead for every reader (unchanged semantics).
        assert _store.get(stale) is None
    finally:
        _store._orders.pop(fresh, None)


def test_quota_store_prunes_stale_buckets_on_consume():
    """Consume drops other clients' buckets whose date has rolled over — the
    per-IP map stays bounded at one entry per IP seen today."""
    import datetime

    from app.api.rewrite import _quota

    old_today = datetime.date.today().isoformat()
    try:
        _quota._counts["10.0.0.1"] = ("2020-01-01", 5)
        _quota._counts["10.0.0.2"] = ("2020-01-02", 7)
        _quota.consume("203.0.113.9")
        assert "10.0.0.1" not in _quota._counts
        assert "10.0.0.2" not in _quota._counts
        # The consuming IP gets a fresh bucket for today (quota semantics
        # unchanged: first consume of the day leaves quota-1 remaining).
        assert _quota._counts["203.0.113.9"] == (old_today, 1)
    finally:
        _quota.reset()
