"""v0.5.0 fixes — LLM fallback + payment error-surface regression tests.

Two correctness fixes shipped in v0.5.0:

  (a) fix-llm-nondict-response-attributeerror — `complete_json` /
      `_parse_json_lenient` now rejects a non-dict LLM JSON response (a JSON
      array / string / number a model can emit under content-filter or
      json-mode drift) with ValueError. Every downstream coerce calls
      `raw.get(...)`, which on a non-dict raises AttributeError — NOT in any
      caller's ``(LLMError, ValueError)`` fallback catch, so the documented
      heuristic/passthrough/deterministic fallback never engaged and a single
      non-dict JD/resume response 500'd /api/rewrite (and rewrite_star
      violated its "Never raises on LLM failure" contract). ValueError IS
      caught by all four callers, so the fallback now engages. This is the
      same defect-class as the v0.2 fix-llm-empty-choices-crash (an uncaught
      exception defeating the model fallback), closed at the parse layer the
      v0.2 fix left unguarded.
  (b) fix-payment-create-order-narrow-exception-scope — `POST /api/payment`'s
      `create_order` endpoint caught ONLY PaymentNotConfigured, so a
      misconfigured real-WeChat setup (e.g. a wrong/unreadable private-key
      path raising FileNotFoundError, or any WeChatPay SDK error) escaped
      uncaught -> HTTP 500, bricking the ¥9.9 unwatermark flow before a QR was
      ever shown. v0.4.0 broadened `notify_post`'s except to `Exception` but
      left `create_order`'s narrow; v0.5.0 broadens it too and degrades to the
      DevFreeProvider free path (mirroring the existing PaymentNotConfigured
      fallback), so a misconfigured real-payment setup returns a clean 200
      dev/free order instead of a 500.

The docx / offline paths are used so these run without weasyprint native libs
or a real LLM key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.payment as payment_mod
import app.llm.client as llm_client_mod
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- (a) non-dict LLM response -> fallback engages (not AttributeError) -----


class _NonDictClient:
    """A fake LLM client whose model emits a non-dict JSON value.

    Faithfully models the real ``complete_json`` flow: the model's raw text is
    parsed by ``_parse_json_lenient``. A model that emits ``"[1,2,3]"`` (a JSON
    array) is parsed by the lenient parser, which (after the v0.5.0 fix) raises
    ``ValueError`` rather than returning a list. Before the fix the parser
    returned a list, and every coerce's ``raw.get(...)`` then raised
    ``AttributeError`` (not in the ``(LLMError, ValueError)`` fallback catch),
    so the fallback never engaged. Routing the fake through
    ``_parse_json_lenient`` exercises the real parse→guard→fallback path.
    """

    available = True

    def require_available(self) -> None:
        return None

    def complete_json(self, system: str, user: str):
        from app.llm.client import _parse_json_lenient

        # A non-dict JSON value a model could emit as raw text.
        return _parse_json_lenient("[1,2,3]")


@pytest.fixture
def nondict_llm():
    """Install a client whose complete_json returns a non-dict (JSON array)."""
    prev = llm_client_mod._client
    llm_client_mod._client = _NonDictClient()
    try:
        yield
    finally:
        llm_client_mod._client = prev


def test_parse_json_lenient_rejects_non_dict():
    """A non-dict JSON value (array / string / number / bool / null) raises
    ValueError, not a silent list/str return that would AttributeError
    downstream (v0.5.0 fix a, acceptance criterion 3)."""
    from app.llm.client import _parse_json_lenient

    for bad in ["[1,2,3]", '"hello"', "42", "true", "null"]:
        with pytest.raises(ValueError):
            _parse_json_lenient(bad)


def test_parse_json_lenient_accepts_dict():
    """Control: a valid JSON object still parses to a dict (v0.5.0 fix a does
    not break the happy path)."""
    from app.llm.client import _parse_json_lenient

    obj = _parse_json_lenient('{"a": 1}')
    assert obj == {"a": 1}
    # Tolerating fences + prose still yields a dict.
    obj = _parse_json_lenient('```json\n{"b": 2}\n```')
    assert obj == {"b": 2}


def test_rewrite_star_degrades_on_nondict_llm_response(nondict_llm):
    """A model returning a non-dict JSON value still makes rewrite_star degrade
    to passthrough (its documented 'Never raises on LLM failure' contract), not
    raise AttributeError / 502 / 500 (v0.5.0 fix a, acceptance criterion 1)."""
    from app.jd.parser import ParsedJD
    from app.resume.parser import Experience, ParsedResume
    from app.resume.star import rewrite_star

    pj = ParsedJD(title="后端", must_skills=["Python"], nice_skills=[], responsibilities=[])
    pr = ParsedResume(
        experience=[Experience(title="x", company="y", dates="z", bullets=["b1", "b2"])]
    )
    out = rewrite_star(pj, pr)  # must NOT raise; degrades to passthrough
    assert len(out.rewritten) == 2
    # passthrough: star == orig
    assert [b.star for b in out.rewritten] == ["b1", "b2"]
    assert [b.orig for b in out.rewritten] == ["b1", "b2"]


def test_parse_jd_degrades_on_nondict_llm_response(nondict_llm):
    """A non-dict JD LLM response makes parse_jd fall back to the heuristic
    (no AttributeError, no 500) — the ValueError from _parse_json_lenient is
    caught by parse_jd's ``(LLMError, ValueError)`` fallback (v0.5.0 fix a,
    acceptance criterion 2)."""
    from app.jd.parser import ParsedJD, parse_jd

    out = parse_jd("招聘岗位：后端开发\n任职要求：熟悉 Python")
    assert isinstance(out, ParsedJD)
    # Heuristic fallback extracted *something* (no 500 / no AttributeError).
    assert out.title or out.must_skills


def test_parse_resume_degrades_on_nondict_llm_response(nondict_llm):
    """A non-dict resume LLM response makes parse_resume fall back to the
    heuristic (no AttributeError, no 500) — v0.5.0 fix a, acceptance criterion 2."""
    from app.resume.parser import ParsedResume, parse_resume

    out = parse_resume("张三\n13800138000 | zhangsan@example.com")
    assert isinstance(out, ParsedResume)
    # Heuristic fallback found the name.
    assert out.basics.name == "张三"


# --- (b) POST /api/payment broad exception scope ----------------------------


class _FileNotFoundProvider:
    """Simulates WeChatNativeProvider when the private-key path is wrong:
    open() raises FileNotFoundError inside create_order, which is NOT
    PaymentNotConfigured — the failure mode v0.1–v0.4 let escape to a 500."""

    name = "wechat_native"

    def create_order(self, out_trade_no, amount_cents):
        # Mirrors WeChatNativeProvider._client() at app/api/payment.py:238.
        open("/nonexistent/wechat_key.pem", "r", encoding="utf-8").read()

    def verify_notify(self, raw_body, headers):  # pragma: no cover
        raise RuntimeError("not used")


class _SDKErrorProvider:
    """Simulates a WeChatPay SDK error during native pay (a non-
    PaymentNotConfigured, non-FileNotFoundError failure mode)."""

    name = "wechat_native"

    def create_order(self, out_trade_no, amount_cents):
        raise RuntimeError("wechatpay: invalid mchid")

    def verify_notify(self, raw_body, headers):  # pragma: no cover
        raise RuntimeError("not used")


class _PaymentNotConfiguredProvider:
    """Control: the existing PaymentNotConfigured path must still degrade to
    dev/free after broadening the except (v0.5.0 fix b preserves v0.2)."""

    name = "wechat_native"

    def create_order(self, out_trade_no, amount_cents):
        from app.api.payment import PaymentNotConfigured

        raise PaymentNotConfigured("creds vanished at call time")

    def verify_notify(self, raw_body, headers):  # pragma: no cover
        raise RuntimeError("not used")


def test_payment_create_bad_key_path_does_not_500(client, monkeypatch):
    """A real-WeChat create_order that raises FileNotFoundError (bad/unreadable
    private-key path) must NOT 500; it returns 200 + a dev/free order so the
    ¥9.9 unwatermark flow is not bricked by a misconfigured key path
    (v0.5.0 fix b, acceptance criterion 1)."""
    monkeypatch.setattr(payment_mod, "get_provider", lambda: _FileNotFoundProvider())
    resp = client.post("/api/payment")
    assert resp.status_code == 200, "create_order must not 500 on a bad key path"
    order = resp.json()
    assert order["provider"] == "dev_free"
    assert order["dev_mode"] is True
    assert order["paid"] is True


def test_payment_create_sdk_error_does_not_500(client, monkeypatch):
    """A real-WeChat create_order that raises a generic SDK error must also
    degrade to dev/free, not 500 (v0.5.0 fix b — the broadened except covers
    the whole non-PaymentNotConfigured error family)."""
    monkeypatch.setattr(payment_mod, "get_provider", lambda: _SDKErrorProvider())
    resp = client.post("/api/payment")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "dev_free"


def test_payment_create_payment_not_configured_still_degrades(client, monkeypatch):
    """Control: the existing PaymentNotConfigured fallback (v0.2) still engages
    after broadening the except to Exception (v0.5.0 fix b preserves it)."""
    monkeypatch.setattr(payment_mod, "get_provider", lambda: _PaymentNotConfiguredProvider())
    resp = client.post("/api/payment")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "dev_free"


def test_payment_create_dev_free_path_unchanged(client):
    """Control: the unmonkeypatched dev/free path (no WeChat creds) still
    creates a paid dev order normally (v0.5.0 fix b preserves existing
    behavior — only the error path changed)."""
    resp = client.post("/api/payment")
    assert resp.status_code == 200
    order = resp.json()
    assert order["provider"] == "dev_free"
    assert order["paid"] is True
    assert order["dev_mode"] is True
    assert order["amount_cents"] == 999
