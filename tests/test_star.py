"""m2 tests — STAR rewrite orchestration (passthrough + LLM path)."""

from __future__ import annotations

from app.jd.parser import ParsedJD, parse_jd
from app.resume.parser import ParsedResume, parse_resume
from app.resume.star import JDResume, RewrittenBullet, rewrite_star


def test_rewrite_star_offline_passthrough(sample_jd, sample_resume):
    """No LLM key → bullets pass through unchanged, coverage is deterministic."""
    parsed_jd = parse_jd(sample_jd)
    parsed_resume = parse_resume(sample_resume)
    result = rewrite_star(parsed_jd, parsed_resume)

    assert isinstance(result, JDResume)
    assert result.jd is parsed_jd
    assert result.resume is parsed_resume
    # passthrough: every star equals its orig
    assert all(b.star == b.orig for b in result.rewritten)
    assert len(result.rewritten) == len(parsed_resume.all_bullets)
    # coverage is still computed (deterministic, offline)
    assert result.coverage.score >= 0
    # at least some keyword coverage given the sample mentions Python/SQL/分布式
    assert any(k in result.coverage.matched for k in ("Python", "SQL", "分布式"))


def test_rewrite_star_offline_annotates_per_bullet_coverage(sample_jd, sample_resume):
    parsed_jd = parse_jd(sample_jd)
    parsed_resume = parse_resume(sample_resume)
    result = rewrite_star(parsed_jd, parsed_resume)
    # Each bullet's covers_keywords is a subset of the global matched set.
    for b in result.rewritten:
        for kw in b.covers_keywords:
            assert kw in result.coverage.matched


def test_rewrite_star_llm_path_uses_canned(mock_llm, sample_jd, sample_resume):
    parsed_jd = parse_jd(sample_jd)
    parsed_resume = parse_resume(sample_resume)
    result = rewrite_star(parsed_jd, parsed_resume)

    assert len(result.rewritten) == 5
    # The first canned bullet is STAR-rewritten (differs from orig).
    first = result.rewritten[0]
    assert first.orig != first.star
    assert "订单" in first.star or "50w" in first.star
    # STAR bullets always carry text (never empty).
    assert all(b.star for b in result.rewritten)
    assert isinstance(result, JDResume)


def test_rewrite_star_empty_resume_returns_empty():
    parsed_jd = ParsedJD(must_skills=["Python"])
    parsed_resume = ParsedResume()
    result = rewrite_star(parsed_jd, parsed_resume)
    assert result.rewritten == []
    assert result.coverage.score == 0.0


def test_rewritten_bullet_model_defaults():
    b = RewrittenBullet()
    assert b.orig == ""
    assert b.star == ""
    assert b.covers_keywords == []
