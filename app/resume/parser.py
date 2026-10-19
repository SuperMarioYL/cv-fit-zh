"""Resume parser — turns pasted old-resume text into a structured ParsedResume.

Owns the :class:`ParsedResume` model (the resume half of the core ``JDResume``
primitive). Uses the LLM when a key is configured and falls back to a
heuristic section-header splitter so the pipeline is runnable offline and the
tests are deterministic. CLI entry point for the m2 acceptance criterion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.llm.client import LLMError, get_client
from app.llm.prompts import RESUME_PARSE_SYSTEM, RESUME_PARSE_USER


class Basics(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    summary: str = ""


class Experience(BaseModel):
    title: str = ""
    company: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    dates: str = ""


class ParsedResume(BaseModel):
    basics: Basics = Field(default_factory=Basics)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)

    @property
    def all_bullets(self) -> list[str]:
        """Flat list of every experience bullet, in document order."""
        out: list[str] = []
        for exp in self.experience:
            out.extend(exp.bullets)
        return out


# --- LLM path --------------------------------------------------------------

def _coerce(raw: dict[str, Any]) -> ParsedResume:
    basics_raw = raw.get("basics") or {}
    # v0.6.0 (fix-resume-basics-nondict-attributeerror): under json-mode drift a
    # model can collapse the `basics` object into a string (e.g. "张三
    # 13800138000 zhangsan@example.com"). The `or {}` fallback only catches
    # falsy values, so a truthy non-dict sailed through and `.get("name")`
    # raised AttributeError — NOT in parse_resume's ``(LLMError, ValueError)``
    # catch — which escaped /api/rewrite's LLMUnavailableError-only catch as a
    # 500 instead of degrading to the heuristic splitter. Raise ValueError so
    # the documented fallback engages (same posture as the v0.5.0 top-level
    # dict contract in app/llm/client.py:_parse_json_lenient; the nested
    # experience/education items below are already isinstance-guarded).
    if not isinstance(basics_raw, dict):
        raise ValueError(f"LLM resume JSON 'basics' was not an object (got {type(basics_raw).__name__})")
    basics = Basics(
        name=str(basics_raw.get("name") or "").strip(),
        phone=str(basics_raw.get("phone") or "").strip(),
        email=str(basics_raw.get("email") or "").strip(),
        summary=str(basics_raw.get("summary") or basics_raw.get("objective") or "").strip(),
    )
    # v0.7.0 (cv-fit-zh-fix-2): a drifted model response can carry a truthy
    # NON-ITERABLE value in a list field (e.g. {"experience": 3}). Iterating it
    # raised TypeError, which is NOT in parse_resume's ``(LLMError, ValueError)``
    # catch and escaped /api/rewrite as a 500 instead of degrading to the
    # heuristic splitter. A truthy non-list raises ValueError — the caught,
    # fallback-triggering error, exactly the v0.6.0 `basics` posture — so the
    # heuristic splitter recovers the whole resume from the raw text (treating
    # it as empty instead would silently drop the user's experience while
    # keeping the LLM basics). Falsy values (None/""/0) keep the old `or []`
    # empty-list semantics; app/jd/parser.py:_as_str_list shows the same
    # field-level guard posture.
    experience_raw = raw.get("experience")
    if experience_raw and not isinstance(experience_raw, list):
        raise ValueError(
            f"LLM resume JSON 'experience' was not a list (got {type(experience_raw).__name__})"
        )
    exp_list: list[Experience] = []
    for item in experience_raw or []:
        if not isinstance(item, dict):
            continue
        bullets = item.get("bullets") or []
        if isinstance(bullets, str):
            bullets = [b.strip() for b in re.split(r"[\n•·\-\*]+", bullets) if b.strip()]
        exp_list.append(
            Experience(
                title=str(item.get("title") or "").strip(),
                company=str(item.get("company") or item.get("organization") or "").strip(),
                dates=str(item.get("dates") or item.get("period") or "").strip(),
                bullets=[str(b).strip() for b in bullets if str(b).strip()],
            )
        )
    education_raw = raw.get("education")
    if education_raw and not isinstance(education_raw, list):
        raise ValueError(
            f"LLM resume JSON 'education' was not a list (got {type(education_raw).__name__})"
        )
    edu_list: list[Education] = []
    for item in education_raw or []:
        if not isinstance(item, dict):
            continue
        edu_list.append(
            Education(
                school=str(item.get("school") or item.get("university") or "").strip(),
                degree=str(item.get("degree") or item.get("major") or "").strip(),
                dates=str(item.get("dates") or item.get("period") or "").strip(),
            )
        )
    return ParsedResume(basics=basics, experience=exp_list, education=edu_list)


def parse_resume(resume_text: str) -> ParsedResume:
    """Parse free resume text into :class:`ParsedResume`.

    Uses the LLM if a key is configured; otherwise (and on LLM failure) falls
    back to the deterministic heuristic splitter so the pipeline never blocks.
    """
    text = (resume_text or "").strip()
    if not text:
        return ParsedResume()
    client = get_client()
    if client.available:
        try:
            raw = client.complete_json(RESUME_PARSE_SYSTEM, RESUME_PARSE_USER.format(resume_text=text))
            return _coerce(raw)
        except (LLMError, ValueError) as exc:  # noqa: BLE001
            import logging

            logging.getLogger("cvfit.resume").warning("LLM resume parse failed (%s); heuristic fallback", exc)
    return _parse_heuristic(text)


# --- heuristic fallback (no LLM, no network) -------------------------------

# Section headers a Chinese resume commonly uses. Order matters: we scan the
# document for these and split accordingly.
_SECTION_PATTERNS = [
    (re.compile(r"(?:^|\n)\s*个人简介|自我评价|个人总结|about me", re.I), "summary"),
    (re.compile(r"(?:^|\n)\s*教育背景|教育经历|学历|education", re.I), "education"),
    (re.compile(r"(?:^|\n)\s*工作经历|工作 经验|项目经历|项目经验|工作经验|experience|work", re.I), "experience"),
]

_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A name line: 2-4 CJK chars optionally followed by contact info, at the very top.
_NAME_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4})\b")

# An experience entry header: "公司 职位 时间" or "职位 | 公司 | 时间" lines.
# We treat a non-empty line that is NOT a bullet as a potential entry header.
_BULLET_RE = re.compile(r"^\s*[•·\-\*▸▪‣►\d]+[.)、]?\s*")

# v0.7.0 (cv-fit-zh-fix-1): a DATE-FIRST entry header ("2021.07-至今 某公司 某职位",
# a common CN resume layout without pipe separators) also matches _BULLET_RE,
# because its character class includes \d — so the line was mis-classified as a
# bullet, and the sub() below then chewed off the leading "2021." as a list
# ordinal, corrupting the text and merging separate jobs into the previous
# entry. A 4-digit year + date separator is the discriminator: numbered list
# bullets are 1-2 digit ordinals, so requiring \d{4} + separator cannot
# misclassify them.
_DATE_FIRST_RE = re.compile(r"^\d{4}\s*[.．年/-]")

# Company-ish suffixes and title-ish suffixes for content-based classification.
_COMPANY_RE = re.compile(r"公司|集团|有限|股份|院$|局$|中心|实验室|大学|学校|厂")
_TITLE_RE = re.compile(r"师|员|工程师|开发|实习|总监|经理|顾问|设计|架构|负责|主管")
_DATE_RE = re.compile(r"\d{4}")


def _classify_entry_parts(parts: list[str]) -> tuple[str, str, str]:
    """Classify a split entry-header into (title, company, dates) by content.

    Falls back to positional assignment (company-first, the common Chinese
    resume convention) when no content signal fires.
    """
    title = company = dates = ""
    leftover: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not dates and _DATE_RE.search(p):
            dates = p
        elif not company and _COMPANY_RE.search(p):
            company = p
        elif not title and _TITLE_RE.search(p):
            title = p
        else:
            leftover.append(p)
    # Positional fallback for unclassified parts.
    if not company and leftover:
        company = leftover.pop(0)
    if not title and leftover:
        title = leftover.pop(0)
    if not dates and leftover:
        dates = leftover.pop(0)
    return title, company, dates


def _split_sections(text: str) -> dict[str, list[str]]:
    """Return {section_name: [lines]} by scanning for known headers."""
    sections: dict[str, list[str]] = {"_top": [], "summary": [], "experience": [], "education": []}
    current = "_top"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        matched_header = False
        for pat, name in _SECTION_PATTERNS:
            if pat.search(stripped) and len(stripped) < 12:
                current = name
                matched_header = True
                break
        if matched_header:
            continue
        sections.setdefault(current, []).append(stripped)
    return sections


def _split_entry_header(line: str) -> list[str]:
    """Split an entry-header line into (title/company/dates) candidate parts.

    Pipe / 2+-space separated lines (the common layouts) keep the existing
    behaviour. A date-first line ("2021.07-至今 某公司 某职位", single spaces,
    no pipes) has no separator to split on, so the leading date token is peeled
    off first and the rest is split on whitespace — without this the whole line
    would land in one part and the date would swallow the company/title.
    """
    parts = re.split(r"\s*[|｜]\s*|\s{2,}", line, maxsplit=2)
    if len(parts) > 1:
        return parts
    m = _DATE_FIRST_RE.match(line)
    if m:
        dates = m.group(0).strip()
        rest = line[m.end():].strip(" |｜，,-—")
        if rest:
            return [dates, *[p for p in re.split(r"\s+", rest) if p]]
        return [dates]
    return parts


def _parse_heuristic(text: str) -> ParsedResume:
    sections = _split_sections(text)
    top = sections.get("_top", [])

    name = ""
    for line in top:
        m = _NAME_RE.match(line)
        if m:
            name = m.group(1)
            break

    phone = ""
    email = ""
    for line in top:
        if not phone:
            pm = _PHONE_RE.search(line)
            if pm:
                phone = pm.group(0)
        if not email:
            em = _EMAIL_RE.search(line)
            if em:
                email = em.group(0)
        if phone and email:
            break

    summary = ""
    for line in sections.get("summary", []):
        summary = line
        break

    # Experience: group consecutive bullet lines under the nearest preceding header line.
    exp_lines = sections.get("experience", [])
    experiences: list[Experience] = []
    current_exp: Experience | None = None
    for line in exp_lines:
        # v0.7.0 (cv-fit-zh-fix-1): a date-first entry header matches _BULLET_RE
        # (its class includes \d); it must take the header branch, not be
        # chewed into a bullet with its leading "2021." stripped.
        if _BULLET_RE.match(line) and not _DATE_FIRST_RE.match(line):
            bullet = _BULLET_RE.sub("", line).strip()
            if bullet:
                if current_exp is None:
                    current_exp = Experience()
                    experiences.append(current_exp)
                current_exp.bullets.append(bullet)
        else:
            # Treat as a new entry header. Heuristically split title/company/dates
            # by content (company often carries a suffix like 公司/集团/院; the date
            # field carries a 4-digit year). Positional fallback covers the rest.
            parts = _split_entry_header(line)
            title, company, dates = _classify_entry_parts(parts)
            current_exp = Experience(title=title.strip(), company=company.strip(), dates=dates.strip())
            experiences.append(current_exp)
    # Drop trailing empty entries (headers with no bullets).
    experiences = [e for e in experiences if e.bullets or e.title or e.company]

    # Education: one entry per non-empty line.
    edu: list[Education] = []
    for line in sections.get("education", []):
        parts = re.split(r"\s*[|｜]\s*|\s{2,}", line, maxsplit=2)
        school = parts[0] if parts else ""
        degree = parts[1] if len(parts) > 1 else ""
        dates = parts[2] if len(parts) > 2 else ""
        edu.append(Education(school=school.strip(), degree=degree.strip(), dates=dates.strip()))

    return ParsedResume(
        basics=Basics(name=name, phone=phone, email=email, summary=summary),
        experience=experiences,
        education=edu,
    )


# --- CLI (m2 acceptance: python -m app.resume.parser --resume x.txt) -------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.resume.parser",
        description="Parse an old-resume text file into structured JSON (ParsedResume).",
    )
    parser.add_argument("--resume", required=True, help="path to a resume text file (or '-' for stdin)")
    args = parser.parse_args(argv)

    if args.resume == "-":
        text = sys.stdin.read()
    else:
        with open(args.resume, encoding="utf-8") as fh:
            text = fh.read()

    try:
        parsed = parse_resume(text)
    except ValidationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
