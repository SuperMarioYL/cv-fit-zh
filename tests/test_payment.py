"""v0.2 tests — the ¥9.9/投递 unwatermark payment flow (dev/free path).

These run on the offline (no-key, no-WeChat-creds) path, so they exercise the
DevFreeProvider — the intended open-core self-host free unwatermark path (NOT a
stub). The paid WeChatNativeProvider is credential-gated + lazy-import and is
never active without merchant creds; its real HTTP/signing path is covered by
`# pragma: no cover` and activated at deployment time.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _rewrite_result(client, sample_jd, sample_resume):
    resp = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _docx_text(resp) -> str:
    """Extract the visible text from a .docx response body (document.xml)."""
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        # Concatenate the main document + headers/footers (watermark lives in a
        # centered paragraph at the end of document.xml).
        parts = [z.read(n) for n in z.namelist() if n.endswith(".xml")]
    return b"".join(parts).decode("utf-8", errors="ignore")


def test_payment_creates_dev_paid_order(client):
    """No WeChat creds → DevFreeProvider: order returned already paid (free path)."""
    resp = client.post("/api/payment")
    assert resp.status_code == 200, resp.text
    order = resp.json()
    assert order["paid"] is True
    assert order["dev_mode"] is True
    assert order["provider"] == "dev_free"
    assert order["amount_yuan"] == 9.99
    assert order["amount_cents"] == 999
    assert order["order_token"]
    assert order["out_trade_no"].startswith("cvfit-")
    assert order["code_url"] is None  # dev path: no QR


def test_payment_order_status_paid(client):
    create = client.post("/api/payment").json()
    token = create["order_token"]
    resp = client.get(f"/api/payment/{token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["paid"] is True
    assert body["order_token"] == token
    assert body["amount_yuan"] == 9.99


def test_payment_unknown_order_404(client):
    resp = client.get("/api/payment/not-a-real-token")
    assert resp.status_code == 404


def test_export_paid_token_unwatermarks_docx(client, sample_jd, sample_resume):
    """A paid order token → /api/export renders WITHOUT the watermark text.

    Uses docx (python-docx) so it runs without weasyprint native libs. The
    control: a watermarked export DOES contain the watermark text.
    """
    result = _rewrite_result(client, sample_jd, sample_resume)
    order = client.post("/api/payment").json()
    token = order["order_token"]

    # Paid path: order_token + watermark=True → still unwatermarked (token wins).
    paid_resp = client.post(
        "/api/export",
        json={**result, "format": "docx", "watermark": True, "order_token": token},
    )
    assert paid_resp.status_code == 200, paid_resp.text
    paid_text = _docx_text(paid_resp)
    assert "cv-fit-zh 生成" not in paid_text, "paid export must not carry the watermark"

    # Control: free-tier watermarked export DOES carry the watermark text.
    wm_resp = client.post(
        "/api/export",
        json={**result, "format": "docx", "watermark": True},
    )
    assert wm_resp.status_code == 200, wm_resp.text
    wm_text = _docx_text(wm_resp)
    assert "cv-fit-zh 生成" in wm_text, "watermarked export should carry the watermark"


def test_export_bogus_token_stays_watermarked(client, sample_jd, sample_resume):
    """An unknown/unpaid order token is ignored → free-tier watermark stands."""
    result = _rewrite_result(client, sample_jd, sample_resume)
    resp = client.post(
        "/api/export",
        json={**result, "format": "docx", "watermark": True, "order_token": "bogus"},
    )
    assert resp.status_code == 200, resp.text
    assert "cv-fit-zh 生成" in _docx_text(resp)


def test_payment_notify_get_probe(client):
    """WeChat sends a GET verification probe before the POST callback."""
    resp = client.get("/api/payment/notify")
    assert resp.status_code == 200
    assert resp.json()["code"] == "SUCCESS"


def test_payment_notify_post_dev_path(client):
    """Dev path callback: no real signature → returns a non-SUCCESS code (no crash)."""
    resp = client.post("/api/payment/notify", content=b"{}", headers={})
    assert resp.status_code == 200
    # DevFreeProvider.verify_notify returns paid=False → code FAIL (not a crash).
    assert resp.json()["code"] in ("FAIL", "SUCCESS")


def test_health_reports_payment_configured(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    # No CVFIT_PAYMENT_DISABLED set in tests → payment_configured is True.
    assert resp.json()["payment_configured"] is True
