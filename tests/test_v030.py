"""v0.3.0 fixes — paywall integrity regression tests.

Two correctness fixes shipped in v0.3.0:

  (a) The export watermark paywall holds: a free request (no paid order_token)
      with ``watermark: false`` is still rendered WITH the watermark. The client
      flag can no longer bypass the unwatermark paywall — only a paid order
      token removes the watermark.
  (b) The payment price config is honored: ``CVFIT_PAYMENT_PRICE_CENTS`` flows
      into the order amount instead of a hardcoded value, and a 0/misconfigured
      price falls back to the default so the paywall can't be made free.

The docx path is used so these run without weasyprint native libs.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app

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


def _rewrite_result(client, sample_jd, sample_resume):
    resp = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- (a) the watermark paywall holds ---------------------------------------


def test_free_export_with_watermark_false_still_watermarked(client, sample_jd, sample_resume):
    """v0.3.0 fix (a): a free request with watermark=false and no paid
    order_token must still carry the watermark — the client flag can no longer
    bypass the unwatermark paywall."""
    result = _rewrite_result(client, sample_jd, sample_resume)
    resp = client.post(
        "/api/export",
        json={**result, "format": "docx", "watermark": False},
    )
    assert resp.status_code == 200, resp.text
    text = _docx_text(resp)
    assert WATERMARK_TEXT in text, (
        "free export with watermark=false must still be watermarked (paywall holds)"
    )


def test_paid_token_still_unwatermarks(client, sample_jd, sample_resume):
    """Control for fix (a): a paid order_token still removes the watermark, so
    the legitimate unwatermark path is intact after closing the bypass."""
    result = _rewrite_result(client, sample_jd, sample_resume)
    order = client.post("/api/payment").json()
    token = order["order_token"]
    resp = client.post(
        "/api/export",
        json={**result, "format": "docx", "watermark": False, "order_token": token},
    )
    assert resp.status_code == 200, resp.text
    text = _docx_text(resp)
    assert WATERMARK_TEXT not in text, "paid order_token must still unwatermark"


# --- (b) the payment price config is honored -------------------------------


def test_payment_price_config_honored(client, monkeypatch):
    """v0.3.0 fix (b): CVFIT_PAYMENT_PRICE_CENTS flows into the order amount
    instead of a hardcoded 999."""
    monkeypatch.setenv("CVFIT_PAYMENT_PRICE_CENTS", "1999")
    monkeypatch.setattr("app.api.payment.settings", Settings())
    order = client.post("/api/payment").json()
    assert order["amount_cents"] == 1999
    assert order["amount_yuan"] == 19.99


def test_payment_price_zero_falls_back_to_default(client, monkeypatch):
    """v0.3.0 fix (b): a 0/misconfigured price falls back to the default (999)
    so the unwatermark can't be silently made free."""
    monkeypatch.setenv("CVFIT_PAYMENT_PRICE_CENTS", "0")
    monkeypatch.setattr("app.api.payment.settings", Settings())
    order = client.post("/api/payment").json()
    assert order["amount_cents"] == 999
    assert order["amount_yuan"] == 9.99
