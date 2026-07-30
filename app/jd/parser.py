"""JD parser — turns pasted job-description text into a structured ParsedJD.

Owns the :class:`ParsedJD` model (the JD half of the core ``JDResume``
primitive). Exposes :func:`parse_jd` for the web app and a CLI entry point so
the m2 milestone acceptance criterion ("CLI 可跑: python -m app.jd.parser
--jd y.txt 打结构化 JSON") is verifiable from a shell.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.llm.client import LLMError, get_client
from app.llm.prompts import JD_PARSE_SYSTEM, JD_PARSE_USER


class ParsedJD(BaseModel):
    """Structured representation of one job description."""

    title: str = Field(default="", description="岗位名称")
    must_skills: list[str] = Field(default_factory=list, description="必备技能/任职要求关键词")
    nice_skills: list[str] = Field(default_factory=list, description="加分项/优先项关键词")
    responsibilities: list[str] = Field(default_factory=list, description="岗位职责要点")

    @property
    def all_skills(self) -> list[str]:
        """Union of must + nice skills, de-duplicated, order-preserving."""
        seen: set[str] = set()
        out: list[str] = []
        for kw in [*self.must_skills, *self.nice_skills]:
            k = kw.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out


def _coerce(data: dict[str, Any]) -> ParsedJD:
    """Build a ParsedJD from raw LLM JSON, tolerating minor shape drift."""
    # Models occasionally hand back a list where the plan says string; normalise.
    def _as_str_list(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, str):
            # A single comma/slash-separated blob — split on common separators.
            import re

            parts = re.split(r"[,，、；;/\n]+", val)
            return [p.strip() for p in parts if p.strip()]
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        return []

    title = data.get("title") or data.get("job_title") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    return ParsedJD(
        title=str(title).strip(),
        must_skills=_as_str_list(data.get("must_skills") or data.get("must_have") or data.get("required_skills")),
        nice_skills=_as_str_list(data.get("nice_skills") or data.get("preferred_skills") or data.get("plus") or []),
        responsibilities=_as_str_list(data.get("responsibilities") or data.get("duties") or []),
    )


def parse_jd(jd_text: str) -> ParsedJD:
    """Parse a JD's free text into a :class:`ParsedJD` via the LLM.

    Uses the LLM when a key is configured; otherwise (and on LLM failure) falls
    back to a deterministic section-header heuristic so the pipeline never
    blocks on a missing key. Raises :class:`app.llm.client.LLMError` only if
    the LLM is configured but fails AND the heuristic cannot salvage the text.
    """
    jd_text = (jd_text or "").strip()
    if not jd_text:
        return ParsedJD()
    client = get_client()
    if client.available:
        try:
            raw = client.complete_json(JD_PARSE_SYSTEM, JD_PARSE_USER.format(jd_text=jd_text))
            return _coerce(raw)
        except (LLMError, ValueError) as exc:  # noqa: BLE001
            import logging

            logging.getLogger("cvfit.jd").warning("LLM JD parse failed (%s); heuristic fallback", exc)
    return _parse_jd_heuristic(jd_text)


# --- heuristic fallback (no LLM, no network) -------------------------------

# JD section headers a Chinese job description commonly uses.
_MUST_HEADERS = re.compile(
    r"(任职要求|任职资格|岗位要求|任职条件|我们需要|职位要求|招聘要求|硬性要求|必备条件)", re.I
)
_NICE_HEADERS = re.compile(r"(加分项|优先考虑|优先条件|具有以下经验者优先|优先|nice to have|加分)", re.I)
_RESP_HEADERS = re.compile(r"(岗位职责|工作职责|工作内容|你将负责|职位描述|岗位描述|job description|responsibilities)", re.I)
_TITLE_HINT = re.compile(
    r"(?:招聘岗位|岗位名称|职位名称|招聘职位|岗位|职位|招聘)\s*[:：]\s*([^\n]{2,40})", re.I
)

# A skill keyword is usually a short noun phrase; we extract trailing tokens
# after list markers and from skill-like lines.
_BULLET_RE = re.compile(r"^\s*(?:\d+[.)、]|[•·\-\*▸▪‣►])\s*")


def _parse_jd_heuristic(text: str) -> ParsedJD:
    """Deterministic JD splitter — extracts title/skills/responsibilities by
    scanning for Chinese JD section headers. Good enough for the offline path
    and the unit-test baseline; the LLM does semantic atomisation."""
    title = ""
    m = _TITLE_HINT.search(text)
    if m:
        title = m.group(1).strip().rstrip("，。.,")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    section = "desc"
    must: list[str] = []
    nice: list[str] = []
    resp: list[str] = []

    for ln in lines:
        # Detect a section switch (short header line).
        if _MUST_HEADERS.search(ln) and len(ln) < 16:
            section = "must"
            continue
        if _NICE_HEADERS.search(ln) and len(ln) < 16:
            section = "nice"
            continue
        if _RESP_HEADERS.search(ln) and len(ln) < 16:
            section = "resp"
            continue

        item = _BULLET_RE.sub("", ln).strip()
        if not item:
            continue
        # A title-like first line if we still don't have one.
        if not title and section == "desc" and len(item) <= 30 and not item.endswith(("。", ".", "；")):
            title = item
        if section == "must":
            must.append(item)
        elif section == "nice":
            nice.append(item)
        elif section == "resp":
            resp.append(item)

    # Atomize requirement lines into short keywords (the LLM does this
    # semantically; the heuristic approximates by splitting on punctuation /
    # conjunctions and stripping filler verbs so coverage matching works).
    must = _atomize_skills(must)
    nice = _atomize_skills(nice)

    # De-duplicate while preserving order.
    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    return ParsedJD(
        title=title,
        must_skills=_dedup(must),
        nice_skills=_dedup(nice),
        responsibilities=_dedup(resp),
    )


# Split a requirement line into short keyword tokens.
_ATOM_SEP = re.compile(r"[，,、；;与和及/或（）()\s/]+")
_ATOM_PREFIX = re.compile(r"^(?:熟悉|掌握|了解|精通|具备|具有|能够|会|使用|至少|优先|有|的|等|一种|良好|较强|扎实|相关|以下|上述)+")
_ATOM_NUM_YEAR = re.compile(r"\d+\s*年")


def _atomize_skills(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for tok in _ATOM_SEP.split(line):
            tok = tok.strip()
            if not tok:
                continue
            tok = _ATOM_PREFIX.sub("", tok).strip()
            tok = _ATOM_NUM_YEAR.sub("", tok).strip()
            # Drop pure numbers, single chars, over-long phrases, and orphaned
            # time-unit fragments (年/月/岁) left when a quantity was split off.
            if (
                not tok
                or tok.isdigit()
                or len(tok) < 2
                or len(tok) > 14
                or tok[0] in "年月岁天种"
            ):
                continue
            out.append(tok)
    return out


# --- CLI (m2 acceptance: python -m app.jd.parser --jd y.txt) -----------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.jd.parser",
        description="Parse a JD text file into structured JSON (ParsedJD).",
    )
    parser.add_argument("--jd", required=True, help="path to a JD text file (or '-' for stdin)")
    args = parser.parse_args(argv)

    if args.jd == "-":
        text = sys.stdin.read()
    else:
        with open(args.jd, encoding="utf-8") as fh:
            text = fh.read()

    try:
        parsed = parse_jd(text)
    except (LLMError, ValidationError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
