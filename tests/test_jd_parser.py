"""m1 tests — JD parsing (heuristic fallback + LLM-path coercion)."""

from __future__ import annotations

from app.jd.parser import ParsedJD, parse_jd


def test_parse_jd_heuristic_extracts_title(sample_jd):
    """Offline heuristic: title pulled from the 招聘岗位 header."""
    parsed = parse_jd(sample_jd)
    assert parsed.title
    assert "后端" in parsed.title
    assert "Python" in parsed.title


def test_parse_jd_heuristic_extracts_must_skills(sample_jd):
    """任职要求 section yields the hard-skill keywords."""
    parsed = parse_jd(sample_jd)
    skills_text = " ".join(parsed.must_skills)
    assert "Python" in skills_text
    assert "SQL" in skills_text
    assert "分布式" in skills_text
    assert len(parsed.must_skills) >= 4


def test_parse_jd_heuristic_extracts_nice_skills(sample_jd):
    """加分项 section yields the preferred keywords."""
    parsed = parse_jd(sample_jd)
    nice_text = " ".join(parsed.nice_skills)
    assert "Kubernetes" in nice_text or "大数据" in nice_text


def test_parse_jd_heuristic_extracts_responsibilities(sample_jd):
    parsed = parse_jd(sample_jd)
    assert len(parsed.responsibilities) >= 2
    assert any("业务" in r for r in parsed.responsibilities)


def test_parse_jd_empty_returns_empty():
    parsed = parse_jd("")
    assert parsed.title == ""
    assert parsed.must_skills == []
    assert isinstance(parsed, ParsedJD)


def test_parse_jd_all_skills_dedupes(sample_jd):
    parsed = parse_jd(sample_jd)
    all_kw = parsed.all_skills
    # order-preserving, de-duplicated
    assert len(all_kw) == len(set(all_kw))


def test_parse_jd_llm_path_uses_canned(mock_llm, sample_jd):
    """When the LLM is available, its structured JSON is used verbatim."""
    parsed = parse_jd(sample_jd)
    assert parsed.title == "后端开发工程师（Python）"
    assert "Python" in parsed.must_skills
    assert "SQL" in parsed.must_skills
    assert "Kubernetes" in parsed.nice_skills
    assert len(mock_llm.calls) == 1
