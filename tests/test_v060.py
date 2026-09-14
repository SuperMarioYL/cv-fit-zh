"""v0.6.0 fixes — paid-order refund + basics-drift fallback + version lockstep.

Three changes shipped in v0.6.0:

  (a) fix-export-paid-token-burned-on-render-failure — /api/export consumed the
      single-use paid grant BEFORE rendering; a failed render (502) burned the
      ¥9.9 grant and the user's retry fell back to watermarked. The endpoint now
      refunds the order it consumed when the render raises, so consumption is
      tied to a SUCCESSFUL unwatermarked export (the v0.4.0 single-use
      contract). Atomic check-and-consume and the dev/free exemption unchanged.
  (b) fix-resume-basics-nondict-attributeerror — under json-mode drift a model
      can collapse the resume `basics` object into a string; `_coerce`'s
      `.get(...)` raised AttributeError, which escaped parse_resume's
      ``(LLMError, ValueError)`` catch and /api/rewrite's
      LLMUnavailableError-only catch as a 500 instead of degrading to the
      documented heuristic splitter. `_coerce` now raises ValueError on a
      non-dict basics so the fallback engages (nested-field residual of the
      v0.5.0 top-level non-dict fix).
  (c) add-version-lockstep-test — VERSION == pyproject version ==
      app.__version__ is now CI-enforced.

The docx / offline paths are used so these run without weasyprint native libs
or a real LLM key.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.llm.client as llm_client_mod
from app.api import export as export_api
from app.api.payment import _store, is_paid_order, refund_paid_order
from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
WATERMARK_TEXT = "cv-fit-zh 生成"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _docx_text(resp) -> str:
    """Extract the visible text from a .docx response body (all xml parts)."""
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        parts = [z.read(n) for n in z.namelist() if n.endswith(".xml")]
    return b"".join(parts).decode("utf-8", errors="ignore")


def _real_paid_order() -> str:
    """Create a REAL (dev_mode=False) paid order, as the WeChat notify would."""
    token = _store.create("cvfit-v060-real-paid", 999, dev_mode=False)
    assert _store.mark_paid(token, "cvfit-v060-real-paid") is True
    return token


def _export_body(order_token: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "format": "docx",
        "watermark": True,
        "jd": {"title": "后端", "must_skills": ["Python"], "nice_skills": [], "responsibilities": []},
        "resume": {
            "basics": {"name": "张三", "phone": "", "email": "", "summary": ""},
            "experience": [
                {"title": "后端", "company": "某公司", "dates": "2020-2023", "bullets": ["负责订单系统"]}
            ],
            "education": [],
        },
        "rewritten": [{"orig": "负责订单系统", "star": "负责订单系统", "covers_keywords": []}],
        "coverage": {"matched": ["Python"], "missing": [], "score": 1.0},
    }
    if order_token:
        body["order_token"] = order_token
    return body


# --- (a) paid grant survives a failed render --------------------------------


def test_paid_token_survives_failed_render(client, monkeypatch):
    """A real paid order consumed by a request whose render FAILS must be
    refunded: the 502 leaves is_paid_order True so the user's retry still
    unwatermarks (v0.6.0 fix a, acceptance criterion 1)."""
    token = _real_paid_order()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom: render failure")

    monkeypatch.setattr(export_api, "render_docx_bytes", _boom)
    resp = client.post("/api/export", json=_export_body(token))
    assert resp.status_code == 502, resp.text
    # The grant is NOT burned by the failed render.
    assert is_paid_order(token) is True, "a 502 must not consume the paid grant"


def test_paid_token_retry_after_failed_render_unwatermarks(client, monkeypatch):
    """After the refunded failure, a retry with a working render returns the
    UNWATERMARKED docx, and only that successful export consumes the grant —
    a further replay is watermarked (v0.6.0 fix a, acceptance criteria 2+3;
    the v0.4.0 single-use contract is intact)."""
    token = _real_paid_order()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom: render failure")

    monkeypatch.setattr(export_api, "render_docx_bytes", _boom)
    failed = client.post("/api/export", json=_export_body(token))
    assert failed.status_code == 502
    monkeypatch.undo()

    retry = client.post("/api/export", json=_export_body(token))
    assert retry.status_code == 200, retry.text
    assert WATERMARK_TEXT not in _docx_text(retry), "the retry must unwatermark"

    # Single-use is preserved: the successful export consumed the grant.
    assert is_paid_order(token) is False
    replay = client.post("/api/export", json=_export_body(token))
    assert replay.status_code == 200, replay.text
    assert WATERMARK_TEXT in _docx_text(replay), "a replay after success must be watermarked"


def test_failed_pdf_render_also_refunds(client, monkeypatch):
    """The pdf render path refunds too (both export formats share the refund
    hook). The fake render avoids needing weasyprint native libs."""
    token = _real_paid_order()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom: render failure")

    monkeypatch.setattr(export_api, "render_pdf_bytes", _boom)
    body = _export_body(token)
    body["format"] = "pdf"
    resp = client.post("/api/export", json=body)
    assert resp.status_code == 502
    assert is_paid_order(token) is True


def test_refund_is_noop_for_unconsumed_and_dev_orders(client):
    """refund_paid_order only restores a `consumed` real order — it can never
    mint a grant: unpaid/expired/never-consumed tokens and dev orders (never
    consumed) all return False."""
    # An unpaid real order (status=created, never consumed).
    unpaid = _store.create("cvfit-v060-unpaid", 999, dev_mode=False)
    assert refund_paid_order(unpaid) is False
    assert _store.get(unpaid)["status"] == "created"
    # A dev order (auto-paid, never consumed by design).
    order = client.post("/api/payment").json()
    assert order["dev_mode"] is True
    assert refund_paid_order(order["order_token"]) is False
    assert is_paid_order(order["order_token"]) is True
    # An unknown token.
    assert refund_paid_order("no-such-token") is False


# --- (b) non-dict LLM `basics` -> heuristic fallback, not AttributeError -----


class _BadBasicsClient:
    """A fake LLM client whose resume response carries a non-dict `basics`.

    Models the json-mode drift the v0.5.0 top-level guard could not see: the
    top level IS a dict, but `basics` collapsed into a string. Before v0.6.0,
    `_coerce` called ``basics_raw.get("name")`` on it → AttributeError, which
    escaped parse_resume's ``(LLMError, ValueError)`` catch and 500'd
    /api/rewrite instead of degrading to the heuristic splitter.
    """

    available = True

    def require_available(self) -> None:
        return None

    def complete_json(self, system: str, user: str):
        if system.startswith("你是一名资深中文招聘 JD"):
            return {
                "title": "后端开发工程师",
                "must_skills": ["Python", "SQL"],
                "nice_skills": [],
                "responsibilities": [],
            }
        if system.startswith("你是一名资深中文简历结构化解析器"):
            # The drifted shape: basics is a STRING, not an object.
            return {
                "basics": "张三 13800138000 zhangsan@example.com",
                "experience": [],
                "education": [],
            }
        if system.startswith("你是一名资深中文简历改写专家"):
            return {"rewritten": []}
        raise AssertionError(f"unexpected prompt: {system[:40]!r}")


@pytest.fixture
def bad_basics_llm():
    prev = llm_client_mod._client
    llm_client_mod._client = _BadBasicsClient()
    try:
        yield
    finally:
        llm_client_mod._client = prev


def test_parse_resume_nondict_basics_falls_back_to_heuristic(bad_basics_llm):
    """A drifted basics response degrades to the heuristic splitter (no
    AttributeError, no 500) — the fallback recovers the name from the raw
    text (v0.6.0 fix b, acceptance criterion 1)."""
    from app.resume.parser import ParsedResume, parse_resume

    out = parse_resume("张三\n13800138000 | zhangsan@example.com")
    assert isinstance(out, ParsedResume)
    assert out.basics.name == "张三"


def test_rewrite_endpoint_nondict_basics_returns_200(bad_basics_llm, client):
    """End-to-end: the same drifted response must not 500 /api/rewrite
    (v0.6.0 fix b, acceptance criterion 2)."""
    resp = client.post(
        "/api/rewrite",
        json={
            "resume_text": "张三 13800138000\n\n工作经历\n某公司 | 后端 | 2020-2023\n- 负责订单系统",
            "jd_text": "招聘岗位：后端开发\n任职要求：\n1. 熟悉 Python\n2. 熟悉 SQL",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "rewritten" in resp.json()


def test_coerce_nondict_basics_raises_valueerror():
    """The coercion-layer guard itself: a non-dict basics raises ValueError
    (the caught, fallback-triggering error), not AttributeError."""
    from app.resume.parser import _coerce

    for bad in ["张三 13800138000", ["张三"], 42]:
        with pytest.raises(ValueError):
            _coerce({"basics": bad, "experience": [], "education": []})


def test_coerce_missing_basics_still_defaults():
    """Control: a missing/None/empty basics still yields the empty default
    (the `or {}` path is unchanged; only truthy non-dicts raise)."""
    from app.resume.parser import Basics, ParsedResume, _coerce

    for raw in [{}, {"basics": None}, {"basics": ""}, {"basics": {}}]:
        out = _coerce(raw)
        assert isinstance(out, ParsedResume)
        assert out.basics == Basics()


# --- (c) version lockstep ----------------------------------------------------


def test_version_lockstep():
    """VERSION file == pyproject [project] version == app.__version__ (and all
    equal the current release) so a bump that misses a surface fails CI."""
    import tomllib

    from app import __version__

    version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = pyproject["project"]["version"]

    assert version_file == pyproject_version == __version__ == "0.6.0"
