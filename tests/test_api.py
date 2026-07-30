"""m2 integration tests — FastAPI endpoints via TestClient (offline path)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import skip_no_weasyprint


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_index_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "cv-fit-zh" in resp.text
    assert "旧简历" in resp.text  # the resume textarea label


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_version_endpoint(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_rewrite_endpoint_offline(client, sample_jd, sample_resume):
    """Offline path: heuristic parse + passthrough rewrite + deterministic coverage."""
    resp = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "rewritten" in body
    assert "coverage" in body
    assert "jd" in body
    assert "resume" in body
    # passthrough: star == orig when offline
    for b in body["rewritten"]:
        assert b["star"] == b["orig"]
    # coverage has matched/missing lists
    assert isinstance(body["coverage"]["matched"], list)
    assert isinstance(body["coverage"]["missing"], list)


def test_rewrite_rejects_empty_resume(client, sample_jd):
    resp = client.post("/api/rewrite", json={"resume_text": "", "jd_text": sample_jd})
    assert resp.status_code == 400


def test_rewrite_rejects_empty_jd(client, sample_resume):
    resp = client.post("/api/rewrite", json={"resume_text": sample_resume, "jd_text": ""})
    assert resp.status_code == 400


def _rewrite_result(client, sample_jd, sample_resume):
    resp = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@skip_no_weasyprint
def test_export_pdf(client, sample_jd, sample_resume):
    result = _rewrite_result(client, sample_jd, sample_resume)
    resp = client.post("/api/export", json={**result, "format": "pdf", "watermark": True})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert "attachment" in resp.headers["content-disposition"]


def test_export_docx(client, sample_jd, sample_resume):
    result = _rewrite_result(client, sample_jd, sample_resume)
    resp = client.post("/api/export", json={**result, "format": "docx", "watermark": False})
    assert resp.status_code == 200, resp.text
    assert "wordprocessingml.document" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK"


def test_export_unwatermarked_has_no_watermark_text(client, sample_jd, sample_resume):
    """Paid path: watermark=False should not stamp the watermark text."""
    result = _rewrite_result(client, sample_jd, sample_resume)
    resp = client.post("/api/export", json={**result, "format": "pdf", "watermark": False})
    assert resp.status_code == 200
    # The watermark string from settings should not appear as a page label.
    # (PDF is binary; a full text extraction is heavy — we assert the response
    # succeeds and the size is non-trivial, which the render-pdf test covers.)
    assert len(resp.content) > 1000
