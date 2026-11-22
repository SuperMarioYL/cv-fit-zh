"""v0.8.0 fixes — honest dates, nested LLM drift, honest coverage, release assets.

Five shipped defects are covered here:

  (a) m1_fix_date_first_dates — the heuristic splitter reused the date-first
      *discriminator* as the date token, so "2021.07-至今 某科技公司 后端开发工程师"
      parsed with dates="2021." (the rest of the range was discarded and later
      rendered into the export). The full no-space range is now kept.
  (b) m2_fix_nested_llm_bullets — a drifted nested LLM field ({"bullets": 3} or
      {"covers_keywords": 7}) raised TypeError, which escaped every
      (LLMError, ValueError) fallback catch and 500'd /api/rewrite. bullets are
      user content, so the truthy non-list value raises ValueError (heuristic
      fallback); covers_keywords are advisory and re-derived, so they degrade to
      an empty list without losing the rewrite.
  (c) m3_fix_coverage_honesty — Latin keywords matched inside longer words
      (Go in Google, Java in JavaScript) and the LLM matched-list was reconciled
      case-sensitively, so a drifted reply scored a false 0%.
  (d) m4_fix_release_assets — the release workflow built dist/* but never
      attached it to the GitHub release, so every release had 0 assets.
  (e) m5/m6 — docs/site prose and the version surfaces stay in lockstep.

All tests are offline or use scripted fake LLM clients — no network call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.llm.client as llm_client_mod
from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- (a) m1_fix_date_first_dates -------------------------------------------


def test_date_first_header_keeps_full_date_range():
    """Date-first headers keep the whole range, not just "YYYY.".

    Before the fix both entries parsed with dates "2021." / "2019." and the
    "07-至今" / "07-2021.06" remainder was discarded.
    """
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
    assert [e.dates for e in out.experience] == ["2021.07-至今", "2019.07-2021.06"]
    assert [e.company for e in out.experience] == ["某科技公司", "某创业公司"]
    assert [e.bullets for e in out.experience] == [["负责订单系统开发"], ["负责内部工具开发"]]


def test_date_first_controls_unchanged(sample_resume):
    """Control: pipe-separated headers, year-only ranges and numbered bullets
    behave exactly as before."""
    from app.resume.parser import parse_resume

    out = parse_resume(sample_resume)
    assert len(out.experience) == 2
    assert out.experience[0].company == "某科技公司"
    assert out.experience[0].dates == "2021.07-至今"

    text = (
        "工作经历\n"
        "某公司 | 后端 | 2020-2023\n"
        "1. 负责订单系统\n"
        "12. 主导网关重构\n"
    )
    numbered = parse_resume(text)
    assert numbered.experience[0].dates == "2020-2023"
    assert numbered.experience[0].bullets == ["负责订单系统", "主导网关重构"]


# --- (b) m2_fix_nested_llm_bullets -----------------------------------------


class _NestedBulletsDriftClient:
    """Scripted client whose resume response carries {"bullets": 3}."""

    available = True

    def require_available(self) -> None:
        return None

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if system.startswith("你是一名资深中文简历结构化解析器"):
            return {
                "basics": {"name": "张三", "phone": "13800138000"},
                "experience": [{"title": "后端", "company": "某公司", "bullets": 3}],
                "education": [],
            }
        if system.startswith("你是一名资深中文招聘 JD 结构化解析器"):
            return {"title": "后端", "must_skills": ["Python"], "nice_skills": [], "responsibilities": []}
        return {"rewritten": []}


@pytest.fixture
def nested_bullets_drift_llm():
    prev = llm_client_mod._client
    llm_client_mod._client = _NestedBulletsDriftClient()
    try:
        yield
    finally:
        llm_client_mod._client = prev


def test_nested_bullets_drift_falls_back_to_heuristic(nested_bullets_drift_llm):
    """{"bullets": 3} must raise ValueError (caught) and degrade to the
    heuristic splitter, not raise TypeError out of parse_resume."""
    from app.resume.parser import ParsedResume, parse_resume

    out = parse_resume("张三\n13800138000\n\n工作经历\n某公司 | 后端 | 2020-2023\n- 负责订单系统")
    assert isinstance(out, ParsedResume)
    assert len(out.experience) == 1
    assert out.experience[0].company == "某公司"
    assert out.experience[0].bullets == ["负责订单系统"]


def test_rewrite_endpoint_nested_bullets_drift_returns_200(nested_bullets_drift_llm, client):
    """End-to-end: the drifted client must not 500 /api/rewrite."""
    from app.api.rewrite import _quota

    _quota.reset()
    resp = client.post(
        "/api/rewrite",
        json={
            "resume_text": "张三 13800138000\n\n工作经历\n某公司 | 后端 | 2020-2023\n- 负责订单系统",
            "jd_text": "招聘岗位：后端开发\n任职要求：\n1. 熟悉 Python",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "rewritten" in resp.json()


def test_star_covers_drift_keeps_rewrites():
    """{"covers_keywords": 7} must not destroy valid rewritten text.

    covers_keywords is advisory and re-derived deterministically, so the drifted
    value degrades to empty instead of raising TypeError out of rewrite_star.
    """
    from app.jd.parser import ParsedJD
    from app.resume.parser import Basics, Experience, ParsedResume
    from app.resume.star import rewrite_star

    class _CoversDriftClient:
        available = True

        def require_available(self) -> None:
            return None

        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            if system.startswith("你是一名资深中文简历改写专家"):
                return {"rewritten": [{"star": "主导订单系统，日均 50w 单", "covers_keywords": 7}]}
            raise AssertionError(system[:40])

    prev = llm_client_mod._client
    llm_client_mod._client = _CoversDriftClient()
    try:
        resume = ParsedResume(
            basics=Basics(name="张三"),
            experience=[Experience(company="某公司", bullets=["负责订单系统"])],
        )
        result = rewrite_star(ParsedJD(must_skills=["Python"]), resume)
        assert result.rewritten[0].star == "主导订单系统，日均 50w 单"
        # Advisory annotation degraded to empty (it is re-derived anyway).
        assert isinstance(result.rewritten[0].covers_keywords, list)
    finally:
        llm_client_mod._client = prev


# --- (c) m3_fix_coverage_honesty -------------------------------------------


def test_latin_keywords_require_word_boundaries():
    """Go/Java/R must not match inside Google/JavaScript/Rust.

    Before the fix this reported matched=['Go','Java','R'] and score 0.6 with
    zero real coverage.
    """
    from app.jd.keywords import score_coverage_deterministic
    from app.jd.parser import ParsedJD

    jd = ParsedJD(must_skills=["Go", "Java", "R"])
    bullets = [
        "使用 Google Analytics 分析用户增长",
        "用 JavaScript 重写了前端页面",
        "用 Rust 写了一个 CLI 工具",
    ]
    cov = score_coverage_deterministic(bullets, jd)
    assert cov.matched == []
    assert cov.missing == ["Go", "Java", "R"]
    assert cov.score == 0.0


def test_deterministic_coverage_alias_and_demo_unchanged():
    """Control: alias matching, CJK substrings and the README demo are intact."""
    from app.jd.keywords import score_coverage_deterministic
    from app.jd.parser import ParsedJD

    jd = ParsedJD(
        title="后端工程师",
        must_skills=["Python", "SQL", "Kubernetes"],
        nice_skills=["Redis"],
    )
    bullets = ["使用 FastAPI 开发服务，使用 PostgreSQL 保存业务数据。", "使用 Redis 缓存查询结果。"]
    cov = score_coverage_deterministic(bullets, jd)
    assert cov.matched == ["Python", "SQL", "Redis"]
    assert cov.missing == ["Kubernetes"]
    assert cov.score == 0.75

    # True synonym still matches on a token boundary.
    golang = score_coverage_deterministic(["用 Golang 写服务"], ParsedJD(must_skills=["Go"]))
    assert golang.matched == ["Go"]

    # CJK keywords keep substring semantics (no word boundaries in Chinese).
    cjk = score_coverage_deterministic(["负责分布式系统设计"], ParsedJD(must_skills=["分布式"]))
    assert cjk.matched == ["分布式"]


def test_coverage_reconciliation_is_case_insensitive():
    """A case-drifted model reply reconciles to the canonical keywords.

    Before the fix {"matched": ["python", "kubernetes"]} produced matched=[] and
    score=0.0 for the keywords Python/Kubernetes.
    """
    from app.jd.keywords import score_coverage
    from app.jd.parser import ParsedJD

    class _CaseDriftClient:
        available = True

        def require_available(self) -> None:
            return None

        def complete_json(self, system: str, user: str) -> dict[str, Any]:
            assert system.startswith("你是一名 JD 关键词覆盖核对器")
            return {"matched": ["python", "kubernetes"]}

    prev = llm_client_mod._client
    llm_client_mod._client = _CaseDriftClient()
    try:
        cov = score_coverage(["用 Python 和 Kubernetes 部署服务"], ParsedJD(must_skills=["Python", "Kubernetes"]))
        assert cov.matched == ["Python", "Kubernetes"]
        assert cov.missing == []
        assert cov.score == 1.0
    finally:
        llm_client_mod._client = prev


# --- (d) m4_fix_release_assets ---------------------------------------------


def test_release_workflow_attaches_dist_to_release():
    """The release workflow must attach dist/* to the GitHub release.

    Before the fix it only used actions/upload-artifact, so the published
    release carried 0 downloadable assets.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "gh release view" in workflow
    assert "gh release upload" in workflow
    assert "dist/*" in workflow
    assert "contents: write" in workflow


# --- (e) m5 docs/site + m6 version lockstep --------------------------------


def test_site_prose_matches_shipped_matching_rules():
    """Every user-facing locale describes the same matching semantics."""
    readme_zh = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
    site = (REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8")
    template = (REPO_ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")

    assert "整词" in readme_zh and "子串" in readme_zh
    assert "whole-token" in readme_en and "substring" in readme_en
    assert "whole-token" in site and "substring" in site
    # The v0.1 manual-transfer copy is gone: payment shipped in v0.2.
    assert "v0.1 暂未接入在线支付" not in template


def test_version_surfaces_report_0_8_0(client):
    from app import __version__

    assert __version__ == "0.8.0"
    assert client.get("/api/version").json()["version"] == "0.8.0"
