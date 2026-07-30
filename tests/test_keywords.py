"""m1/m2 tests — keyword extraction + coverage scoring."""

from __future__ import annotations

from app.jd.keywords import (
    Coverage,
    extract_keywords,
    score_coverage,
    score_coverage_deterministic,
)
from app.jd.parser import ParsedJD
from tests.conftest import CANNED_STAR


def _jd() -> ParsedJD:
    return ParsedJD(
        title="后端工程师",
        must_skills=["Python", "SQL", "分布式", "Redis", "沟通能力"],
        nice_skills=["大数据", "Kubernetes"],
    )


def test_extract_keywords_returns_union_dedup():
    kws = extract_keywords(_jd())
    assert "Python" in kws
    assert "SQL" in kws
    assert "Kubernetes" in kws
    assert len(kws) == len(set(kws))


def test_coverage_score_clamped_to_unit_interval():
    cov = Coverage(matched=["a", "b", "c"], missing=[], score=1.5)
    assert cov.score == 1.0
    cov2 = Coverage(matched=[], missing=["a"], score=-0.2)
    assert cov2.score == 0.0


def test_deterministic_coverage_matches_present_keywords():
    bullets = [
        "用 Python + Flask 重构服务，将响应从 800ms 降至 200ms",
        "主导 MySQL 表结构与索引优化，慢查询减少 70%",
        "主导分布式订单系统，支撑日均 50w 订单",
    ]
    cov = score_coverage_deterministic(bullets, _jd())
    assert "Python" in cov.matched
    assert "SQL" in cov.matched  # MySQL is a SQL alias
    assert "分布式" in cov.matched
    assert cov.score > 0
    # missing must be the complement
    assert set(cov.missing).isdisjoint(set(cov.matched))


def test_deterministic_coverage_score_is_honest():
    bullets = ["只提到了 Python 一项"]
    cov = score_coverage_deterministic(bullets, _jd())
    assert cov.matched == ["Python"]
    assert len(cov.missing) == 6  # 7 total - 1 matched
    assert cov.score == round(1 / 7, 4)


def test_score_coverage_offline_uses_deterministic():
    """With the offline (no-key) client, coverage falls back to the matcher."""
    bullets = ["用 Python 开发，处理 SQL 和分布式系统，还用了 Redis"]
    cov = score_coverage(bullets, _jd())
    assert "Python" in cov.matched
    assert "Redis" in cov.matched
    assert isinstance(cov, Coverage)


def test_score_coverage_llm_path_uses_canned(mock_llm):
    bullets = [b["star"] for b in CANNED_STAR["rewritten"]]
    cov = score_coverage(bullets, _jd())
    # The canned coverage reconciles against the JD keyword set.
    assert cov.score > 0
    assert set(cov.matched + cov.missing) == set(extract_keywords(_jd()))
