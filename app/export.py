"""Export — render a JDResume into an ATS-friendly Chinese PDF / docx.

This is the format-asset layer (mvp_plan §4 "护城河"): a Chinese ATS template
with SimSun/思源宋体, 2cm margins, single-column no-table layout (so domestic
ATS parsers don't choke), plus a JD keyword-coverage block. weasyprint renders
PDF from the Jinja2 HTML template; python-docx builds the .docx directly.

Both exporters accept a :class:`JDResume` and optional ``watermark`` text
(free tier shows the watermark; the ¥9.9/投递 unwatermark path is a stub per
GTM §8 — real payment lands in v0.2).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.resume.star import JDResume

_TEMPLATES_DIR = Path(settings.templates_dir)


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_resume_html(jd_resume: JDResume, watermark: Optional[str] = None) -> str:
    """Render the Chinese ATS resume HTML for the given JDResume."""
    env = _jinja_env()
    template = env.get_template("resume_zh.html.j2")
    return template.render(
        resume=jd_resume,
        basics=jd_resume.resume.basics,
        experience=jd_resume.resume.experience,
        education=jd_resume.resume.education,
        rewritten=jd_resume.rewritten,
        coverage=jd_resume.coverage,
        jd=jd_resume.jd,
        watermark=watermark or "",
    )


def render_pdf_bytes(jd_resume: JDResume, watermark: Optional[str] = None) -> bytes:
    """Render the resume to a PDF as bytes via weasyprint."""
    from weasyprint import CSS, HTML

    html = render_resume_html(jd_resume, watermark=watermark)
    css = CSS(filename=str(_TEMPLATES_DIR / "resume_zh.css"))
    pdf = HTML(string=html).write_pdf(stylesheets=[css])
    return pdf


def render_pdf(jd_resume: JDResume, out_path: str | Path, watermark: Optional[str] = None) -> Path:
    """Render the resume PDF to a file path. Returns the path."""
    data = render_pdf_bytes(jd_resume, watermark=watermark)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def render_docx_bytes(jd_resume: JDResume, watermark: Optional[str] = None) -> bytes:
    """Render the resume to a .docx as bytes via python-docx."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    # A4 with 2cm margins (ATS-friendly).
    for section in doc.sections:
        section.page_height = Cm(29.7)
        section.page_width = Cm(21.0)
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)

    # Default font: SimSun / 思源宋体 fallback chain.
    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.font.size = Pt(10.5)
    # East-Asian font binding (python-docx needs the w:eastAsia hint).
    rpr = style.element.get_or_add_rPr()
    from docx.oxml.ns import qn

    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement

        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "SimSun")

    basics = jd_resume.resume.basics

    # --- Name + contact header ---
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name_p.add_run(basics.name or "简历")
    run.font.size = Pt(18)
    run.bold = True
    run.font.color.rgb = RGBColor(0x1D, 0x1D, 0x1F)

    contact_bits = [b for b in (basics.phone, basics.email) if b]
    if contact_bits:
        contact_p = doc.add_paragraph()
        contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_p.add_run(" | ".join(contact_bits))

    if basics.summary:
        doc.add_paragraph(basics.summary)

    # --- Experience (single column, no tables) ---
    if jd_resume.resume.experience:
        h = doc.add_paragraph()
        hr = h.add_run("工作经历")
        hr.bold = True
        hr.font.size = Pt(12)
        for exp in jd_resume.resume.experience:
            line = "  ".join(p for p in (exp.company, exp.title, exp.dates) if p)
            if line:
                p = doc.add_paragraph()
                pr = p.add_run(line)
                pr.bold = True
                pr.font.size = Pt(11)
            for b in exp.bullets:
                doc.add_paragraph(b, style="List Bullet")

    # STAR-rewritten bullets are rendered as an aligned block so the user can
    # choose the rewritten phrasing. We list them under each experience entry's
    # original bullets via the rewritten list (order matches all_bullets).
    if jd_resume.rewritten:
        star_iter = iter(jd_resume.rewritten)
        if jd_resume.resume.experience:
            h = doc.add_paragraph()
            hr = h.add_run("STAR 改写建议")
            hr.bold = True
            hr.font.size = Pt(12)
            for exp in jd_resume.resume.experience:
                for _ in exp.bullets:
                    try:
                        rb = next(star_iter)
                    except StopIteration:
                        break
                    if rb.star and rb.star != rb.orig:
                        p = doc.add_paragraph(style="List Bullet")
                        p.add_run(rb.star).italic = True

    # --- Education ---
    if jd_resume.resume.education:
        h = doc.add_paragraph()
        hr = h.add_run("教育背景")
        hr.bold = True
        hr.font.size = Pt(12)
        for edu in jd_resume.resume.education:
            line = "  ".join(p for p in (edu.school, edu.degree, edu.dates) if p)
            if line:
                doc.add_paragraph(line)

    # --- JD keyword coverage block ---
    cov = jd_resume.coverage
    total = len(cov.matched) + len(cov.missing)
    h = doc.add_paragraph()
    hr = h.add_run("JD 关键词覆盖")
    hr.bold = True
    hr.font.size = Pt(12)
    score_p = doc.add_paragraph()
    score_p.add_run(f"关键词覆盖 {len(cov.matched)}/{total}（{cov.score * 100:.0f}%）").bold = True
    if cov.matched:
        doc.add_paragraph("已覆盖：" + "、".join(cov.matched))
    if cov.missing:
        doc.add_paragraph("待补强：" + "、".join(cov.missing))

    # --- Watermark (free tier) ---
    if watermark:
        wm = doc.add_paragraph()
        wm.alignment = WD_ALIGN_PARAGRAPH.CENTER
        wmr = wm.add_run(watermark)
        wmr.font.size = Pt(8)
        wmr.font.color.rgb = RGBColor(0x6E, 0x6E, 0x73)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def render_docx(jd_resume: JDResume, out_path: str | Path, watermark: Optional[str] = None) -> Path:
    """Render the resume .docx to a file path. Returns the path."""
    data = render_docx_bytes(jd_resume, watermark=watermark)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
