"""v0.4.0 fixes — cost / revenue / integrity regression tests.

Three correctness fixes shipped in v0.4.0:

  (a) fix-free-quota-unenforced-llm-cost-drain — the advertised "免费 1 次/天"
      free-tier quota (settings.free_daily_quota) is now enforced at
      /api/rewrite: a 2nd free rewrite on the same calendar day returns 429, so
      the LLM-expensive endpoint (≈3 billable completions/req) can no longer be
      spammed to drain the operator's DeepSeek/Qwen budget. The paid ¥9.9
      unwatermark path gates /api/export, so it is unchanged.
  (b) fix-paid-order-token-reusable-unlimited — a real ¥9.9 paid order token is
      now single-use: the first unwatermarked export consumes it, and a replay
      (same or different JDResume) falls back to watermarked — matching the
      plan's ¥9.9/投递 single-use pricing. The dev/free self-host path
      (dev_mode=True) is exempt so self-hosters keep the free unwatermark flow.
  (c) fix-wechat-notify-broaden-exception-scope — /api/payment/notify now catches
      any signature/parse/decrypt error (not just PaymentNotConfigured) and
      returns HTTP 200 + {"code":"FAIL"} so WeChat retries cleanly instead of
      hitting a 500 that exhausts retries and leaves a paid order unmarked.

The docx path is used so these run without weasyprint native libs.
"""

from __future__ import annotations

import copy
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api import payment as payment_mod
from app.api.payment import _store, consume_paid_order, is_paid_order
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


# --- (a) free-quota enforcement --------------------------------------------


def test_rewrite_under_quota_succeeds(client, sample_jd, sample_resume):
    """Within the daily free quota, /api/rewrite succeeds (200)."""
    resp = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert resp.status_code == 200, resp.text
    assert "rewritten" in resp.json()


def test_rewrite_over_quota_returns_429(client, sample_jd, sample_resume):
    """A 2nd free rewrite on the same calendar day exceeds the 1/day quota → 429
    with a clear quota-exceeded detail (v0.4.0 fix a, acceptance criterion 1)."""
    first = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert second.status_code == 429, second.text
    detail = second.json()["detail"]
    assert "配额" in detail or "quota" in detail.lower()


def test_rewrite_empty_input_does_not_consume_quota(client, sample_jd, sample_resume):
    """A 400 validation failure (empty resume) is cheap (no LLM) and must NOT
    consume the daily slot — so the next valid rewrite still succeeds."""
    bad = client.post(
        "/api/rewrite", json={"resume_text": "", "jd_text": sample_jd}
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/rewrite",
        json={"resume_text": sample_resume, "jd_text": sample_jd},
    )
    assert ok.status_code == 200, ok.text


def test_paid_export_path_unaffected_by_quota(client, sample_jd, sample_resume):
    """The ¥9.9 paid unwatermark path gates /api/export, not /api/rewrite, so
    quota enforcement on /api/rewrite does not touch a paid unwatermarked export
    (v0.4.0 fix a, acceptance criterion 4)."""
    # Get the rewrite result first (under quota).
    result = _rewrite_result(client, sample_jd, sample_resume)
    # A 2nd rewrite is now over quota → 429 (proves the quota is enforced).
    blocked = client.post(
        "/api/rewrite", json={"resume_text": sample_resume, "jd_text": sample_jd}
    )
    assert blocked.status_code == 429
    # The paid unwatermark export still works even though the rewrite quota is
    # exhausted — the paid path is independent of the free-rewrite quota.
    order = client.post("/api/payment").json()
    resp = client.post(
        "/api/export",
        json={
            **result,
            "format": "docx",
            "watermark": True,
            "order_token": order["order_token"],
        },
    )
    assert resp.status_code == 200, resp.text
    assert WATERMARK_TEXT not in _docx_text(resp)


# --- (b) paid order token single-use ---------------------------------------


def test_real_paid_order_token_is_single_use(client, sample_jd, sample_resume):
    """A real (non-dev) ¥9.9 paid order token is consumed on the first
    unwatermarked export; a replay (with a different JDResume) falls back to
    watermarked — the unwatermark privilege is single-use (v0.4.0 fix b,
    acceptance criteria 1 + 2).

    The test env has no WeChat creds, so /api/payment yields dev orders. To
    exercise the REAL paid single-use path we create a real (dev_mode=False)
    order directly in the in-process store and mark it paid (as the WeChat
    notify callback would).
    """
    token = _store.create("cvfit-real-single-use", 999, dev_mode=False)
    assert _store.mark_paid(token, "cvfit-real-single-use") is True
    assert is_paid_order(token) is True

    result = _rewrite_result(client, sample_jd, sample_resume)

    # First export: paid → unwatermarked (and the grant is consumed).
    first = client.post(
        "/api/export",
        json={
            **result,
            "format": "docx",
            "watermark": True,
            "order_token": token,
        },
    )
    assert first.status_code == 200, first.text
    assert WATERMARK_TEXT not in _docx_text(first), "first paid export unwatermarks"

    # The token is now consumed → no longer a paid grant.
    assert is_paid_order(token) is False
    assert consume_paid_order(token) is False

    # Replay with a *different* JDResume (different candidate name): watermarked,
    # NOT unwatermarked — the consumed token can't unlock a second export.
    different_result = copy.deepcopy(result)
    different_result["resume"]["basics"]["name"] = "李四"
    second = client.post(
        "/api/export",
        json={
            **different_result,
            "format": "docx",
            "watermark": True,
            "order_token": token,
        },
    )
    assert second.status_code == 200, second.text
    assert WATERMARK_TEXT in _docx_text(second), (
        "a replayed (consumed) paid token must not unwatermark again"
    )


def test_dev_paid_order_token_not_consumed(client, sample_jd, sample_resume):
    """The dev/free self-host path (dev_mode=True, auto-paid) is NOT consumed on
    export — the consumed-state guard must not break the free self-host flow, so
    a self-hoster can re-export (e.g. to a different format) with the same token
    (v0.4.0 fix b, acceptance criterion 3)."""
    order = client.post("/api/payment").json()
    assert order["dev_mode"] is True
    token = order["order_token"]

    result = _rewrite_result(client, sample_jd, sample_resume)

    # First export: unwatermarked.
    first = client.post(
        "/api/export",
        json={
            **result,
            "format": "docx",
            "watermark": True,
            "order_token": token,
        },
    )
    assert first.status_code == 200, first.text
    assert WATERMARK_TEXT not in _docx_text(first)

    # Dev order NOT consumed → still paid → a re-export still unwatermarks.
    assert is_paid_order(token) is True
    second = client.post(
        "/api/export",
        json={
            **result,
            "format": "docx",
            "watermark": True,
            "order_token": token,
        },
    )
    assert second.status_code == 200, second.text
    assert WATERMARK_TEXT not in _docx_text(second), (
        "dev/free self-host path must still unwatermark on re-export"
    )


# --- (c) WeChat notify broad exception scope -------------------------------


class _RaisingProvider:
    """Fake provider whose verify_notify raises — simulates a WeChat signature /
    parse / decrypt error, the failure modes NOT covered by the old
    PaymentNotConfigured-only catch in v0.1–v0.3."""

    name = "raising"

    def verify_notify(self, raw_body, headers):
        raise ValueError("invalid signature")


def test_payment_notify_bad_signature_does_not_500(client, monkeypatch):
    """A WeChat callback whose signature verification raises must NOT 500; it
    returns HTTP 200 + {"code":"FAIL"} so WeChat retries cleanly (v0.4.0 fix c,
    acceptance criterion 1)."""
    monkeypatch.setattr(payment_mod, "get_provider", lambda: _RaisingProvider())
    resp = client.post(
        "/api/payment/notify",
        content=b'{"fake":"callback"}',
        headers={"Wechatpay-Signature": "bogus", "Wechatpay-Serial": "x"},
    )
    assert resp.status_code == 200, "notify must not 500 on a signature error"
    body = resp.json()
    assert body["code"] == "FAIL"
    assert "invalid signature" in body["message"]


def test_payment_notify_malformed_body_does_not_500(client, monkeypatch):
    """A malformed callback body (raising) must return 200 + FAIL, not 500
    (v0.4.0 fix c, acceptance criterion 2)."""

    class _MalformedProvider:
        name = "malformed"

        def verify_notify(self, raw_body, headers):
            raise ValueError("malformed body")

    monkeypatch.setattr(payment_mod, "get_provider", lambda: _MalformedProvider())
    resp = client.post("/api/payment/notify", content=b"not-json", headers={})
    assert resp.status_code == 200
    assert resp.json()["code"] == "FAIL"


def test_payment_notify_bad_signature_does_not_mark_order_paid(client, monkeypatch):
    """A failing notify must NOT mark an unpaid real order paid — the order stays
    in its pre-callback state until a valid callback arrives (v0.4.0 fix c)."""
    monkeypatch.setattr(payment_mod, "get_provider", lambda: _RaisingProvider())
    # A real (unpaid) order awaiting the WeChat callback.
    token = _store.create("cvfit-notify-guard", 999, dev_mode=False)
    order = _store.get(token)
    assert order["status"] == "created"

    resp = client.post("/api/payment/notify", content=b'{"x":1}', headers={})
    assert resp.status_code == 200
    assert resp.json()["code"] == "FAIL"

    # The bad notify must not have flipped the order to paid.
    after = _store.get(token)
    assert after["status"] != "paid"
