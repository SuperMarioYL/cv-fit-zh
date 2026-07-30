"""m2 tests — PDF/docx export (the format-asset layer)."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.export import render_docx_bytes, render_pdf_bytes, render_resume_html
from app.jd.keywords import Coverage
from app.jd.parser import ParsedJD
from app.resume.parser import Basics, Education, Experience, ParsedResume
from app.resume.star import JDResume, RewrittenBullet
from tests.conftest import skip_no_weasyprint


def _sample_jd_resume() -> JDResume:
    """A fully-populated JDResume for export tests (no LLM needed)."""
    parsed_jd = ParsedJD(
        title="后端开发工程师（Python）",
        must_skills=["Python", "SQL", "分布式", "Redis", "沟通能力"],
        nice_skills=["大数据", "Kubernetes"],
        responsibilities=["业务系统设计开发", "高并发架构优化"],
    )
    parsed_resume = ParsedResume(
        basics=Basics(name="张三", phone="13800138000", email="zhangsan@example.com", summary="3 年后端开发经验。"),
        experience=[
            Experience(
                title="后端开发工程师",
                company="某科技公司",
                dates="2021.07-至今",
                bullets=[
                    "负责订单系统的开发与维护，日均处理 50w 订单",
                    "使用 Python + Flask 搭建服务，将接口响应从 800ms 优化到 200ms",
                ],
            ),
        ],
        education=[Education(school="某某大学", degree="计算机科学·本科", dates="2015-2019")],
    )
    rewritten = [
        RewrittenBullet(
            orig="负责订单系统的开发与维护，日均处理 50w 订单",
            star="主导订单系统设计与开发，支撑日均 50w 订单稳定运行，可用性 99.95%。",
            covers_keywords=["分布式", "Redis"],
        ),
        RewrittenBullet(
            orig="使用 Python + Flask 搭建服务，将接口响应从 800ms 优化到 200ms",
            star="用 Python + Flask 重构核心服务，将 P99 接口响应从 800ms 降至 200ms，吞吐提升 3 倍。",
            covers_keywords=["Python"],
        ),
    ]
    coverage = Coverage(
        matched=["Python", "SQL", "分布式"],
        missing=["Redis", "沟通能力", "大数据", "Kubernetes"],
        score=round(3 / 7, 4),
    )
    return JDResume(jd=parsed_jd, resume=parsed_resume, rewritten=rewritten, coverage=coverage)


def test_render_resume_html_contains_name_and_coverage():
    html = render_resume_html(_sample_jd_resume())
    assert "张三" in html
    assert "关键词覆盖" in html
    assert "3/7" in html  # matched 3 / total 7
    # No <table> in the resume body (ATS-friendly invariant).
    assert "<table" not in html


def test_render_resume_html_includes_star_bullets():
    html = render_resume_html(_sample_jd_resume())
    assert "50w" in html  # from the STAR rewrite
    assert "改写" in html  # the star-label


def test_render_resume_html_with_watermark():
    html = render_resume_html(_sample_jd_resume(), watermark="cv-fit-zh 生成")
    assert "watermark" in html
    assert "cv-fit-zh 生成" in html


@skip_no_weasyprint
def test_render_pdf_bytes_is_valid_pdf():
    data = render_pdf_bytes(_sample_jd_resume())
    assert isinstance(data, bytes)
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000


@skip_no_weasyprint
def test_render_pdf_bytes_with_watermark():
    data = render_pdf_bytes(_sample_jd_resume(), watermark="cv-fit-zh 生成")
    assert data[:5] == b"%PDF-"


def test_render_docx_bytes_is_valid_zip():
    data = render_docx_bytes(_sample_jd_resume())
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # docx is a zip
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "word/document.xml" in names
    # Name + coverage text must be present in the document body.
    body = zf.read("word/document.xml").decode("utf-8")
    assert "张三" in body
    assert "关键词覆盖" in body


def test_render_docx_bytes_with_watermark():
    data = render_docx_bytes(_sample_jd_resume(), watermark="cv-fit-zh 生成")
    zf = zipfile.ZipFile(io.BytesIO(data))
    body = zf.read("word/document.xml").decode("utf-8")
    assert "cv-fit-zh 生成" in body
