"""m1 tests — resume parsing (heuristic fallback + LLM-path coercion)."""

from __future__ import annotations

from app.resume.parser import ParsedResume, parse_resume


def test_parse_resume_heuristic_extracts_basics(sample_resume):
    parsed = parse_resume(sample_resume)
    assert isinstance(parsed, ParsedResume)
    assert parsed.basics.name == "张三"
    assert parsed.basics.phone == "13800138000"
    assert parsed.basics.email == "zhangsan@example.com"


def test_parse_resume_heuristic_extracts_summary(sample_resume):
    parsed = parse_resume(sample_resume)
    assert "Python" in parsed.basics.summary or "后端" in parsed.basics.summary


def test_parse_resume_heuristic_extracts_experience(sample_resume):
    parsed = parse_resume(sample_resume)
    assert len(parsed.experience) == 2
    first = parsed.experience[0]
    assert first.company == "某科技公司"
    assert first.title == "后端开发工程师"
    assert "2021" in first.dates
    assert len(first.bullets) == 3
    assert any("订单" in b for b in first.bullets)


def test_parse_resume_heuristic_extracts_education(sample_resume):
    parsed = parse_resume(sample_resume)
    assert len(parsed.education) == 1
    assert parsed.education[0].school == "某某大学"


def test_parse_resume_all_bullets_flattens(sample_resume):
    parsed = parse_resume(sample_resume)
    flat = parsed.all_bullets
    assert len(flat) == 5  # 3 + 2
    assert any("Django" in b for b in flat)


def test_parse_resume_empty_returns_empty():
    parsed = parse_resume("")
    assert isinstance(parsed, ParsedResume)
    assert parsed.experience == []


def test_parse_resume_llm_path_uses_canned(mock_llm, sample_resume):
    parsed = parse_resume(sample_resume)
    assert parsed.basics.name == "张三"
    assert len(parsed.experience) == 2
    assert parsed.experience[0].company == "某科技公司"
    assert len(parsed.experience[0].bullets) == 3
    assert len(mock_llm.calls) == 1
