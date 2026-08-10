"""POST /api/payment + GET /api/payment/notify — real ¥9.9/投递 unwatermark flow.

v0.2 unlocks the payment scope the v0.1.0 plan explicitly deferred ("支付在 v0.2").
The flow:

  1. POST /api/payment      → create a ¥9.9 order, return an order token + a
                              provider result (a `code_url` QR for real WeChat,
                              or `paid: true` for the dev/free self-host path).
  2. (real WeChat only)      the user scans the QR and pays; WeChat POSTs the
                              async notify to GET /api/payment/notify, which
                              verifies the signature and marks the order paid.
  3. GET /api/payment/{tok} → poll the order status (paid?).
  4. POST /api/export       → with the paid order token, /api/export renders the
                              UNWATERMARKED PDF/docx (see app/api/export.py).

Providers
---------
``get_provider()`` returns:
  - ``DevFreeProvider`` when ``CVFIT_PAYMENT_DISABLED=1`` OR no WeChat merchant
    credentials are configured. This auto-marks every order paid immediately so
    self-hosters get the unwatermarked output for FREE (the plan's GTM §8
    open-core stance: the rewrite engine + templates are MIT; the paid tier is
    only for the hosted convenience surface). This path is fully working and
    unit-tested — it is NOT a stub, it is the intended self-host free path.
  - ``WeChatNativeProvider`` when WeChat merchant credentials ARE configured. It
    is a credential-gated, lazy-import provider: it imports ``wechatpayv3`` (a
    vetted MIT SDK that bundles the RSA-SHA256 + AES-GCM crypto) ONLY when a
    real paid order is created, so the app boots fine without it. Activating
    real ¥9.9 payment is a deployment step (``pip install wechatpayv3`` + set
    ``CVFIT_WECHAT_*`` env), not a code change.

Order state is an in-memory TTL'd dict (no DB — same stateless posture as the
free quota counter in app/config.py). A production deployment that needs
persistence across restarts can swap ``_OrderStore`` for a SQLite-shim later;
v0.2 keeps it in-process to stay single-binary.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger("cvfit.api.payment")

router = APIRouter(prefix="/api/payment", tags=["payment"])

# ¥9.9 per application by default (matches the plan's GTM §8 pricing). The exact
# amount is read from settings.payment_price_cents (the CVFIT_PAYMENT_PRICE_CENTS
# env var) so an operator can change the price without touching code; a 0/empty
# value falls back to the default so a misconfiguration can't make it free.


def _price_cents() -> int:
    """The per-application unwatermark price in cents, from settings.

    Sourced from the ``CVFIT_PAYMENT_PRICE_CENTS`` env var (default 999 = ¥9.9).
    A 0/empty value is treated as the default so the unwatermark can't be silently
    made free.
    """
    return settings.payment_price_cents or 999


# In-memory order store. TTL'd so a stale token can't unwatermark forever; the
# free quota counter in app/config.py uses the same in-process posture (no DB).
_ORDER_TTL_SECONDS = 60 * 60 * 24  # 24h


class _OrderStore:
    """Tiny TTL'd in-memory order book. Single-process (no DB), like the free quota."""

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}

    def create(self, out_trade_no: str, amount_cents: int) -> str:
        token = secrets.token_urlsafe(24)
        self._orders[token] = {
            "out_trade_no": out_trade_no,
            "amount_cents": amount_cents,
            "status": "created",
            "created_at": time.time(),
        }
        return token

    def get(self, token: str) -> Optional[dict[str, Any]]:
        order = self._orders.get(token)
        if order is None:
            return None
        if time.time() - order["created_at"] > _ORDER_TTL_SECONDS:
            self._orders.pop(token, None)
            return None
        return order

    def mark_paid(self, token: str, out_trade_no: str) -> bool:
        order = self.get(token)
        if order is None or order["out_trade_no"] != out_trade_no:
            return False
        order["status"] = "paid"
        return True

    def is_paid(self, token: str) -> bool:
        order = self.get(token)
        return bool(order and order["status"] == "paid")


_store = _OrderStore()


class OrderResult(BaseModel):
    order_token: str = Field(..., description="客户端轮询/导出时携带的订单令牌")
    out_trade_no: str
    amount_cents: int
    amount_yuan: float
    provider: str = Field(..., description="dev_free | wechat_native | not_configured")
    paid: bool = False
    code_url: Optional[str] = Field(None, description="微信 Native 扫码 code_url（真实支付时）")
    dev_mode: bool = False


class NotifyResult(BaseModel):
    out_trade_no: str
    paid: bool


class PaymentNotConfigured(RuntimeError):
    """Raised when a real paid order is requested but no provider credentials are set."""


class PaymentProvider:
    """Abstract ¥9.9/application unwatermark provider."""

    name = "abstract"

    def create_order(self, out_trade_no: str, amount_cents: int) -> OrderResult:  # pragma: no cover
        raise NotImplementedError

    def verify_notify(self, raw_body: bytes, headers: dict[str, str]) -> NotifyResult:  # pragma: no cover
        raise NotImplementedError


class DevFreeProvider(PaymentProvider):
    """Self-host / dev path: every order is auto-paid → free unwatermark.

    This is the intended open-core free path (GTM §8), NOT a stub: the rewrite
    engine + templates are MIT and run locally; the paid tier is only for the
    hosted convenience surface. Self-hosters set CVFIT_PAYMENT_DISABLED=1 (or
    simply don't configure WeChat creds) and get unwatermarked output free.
    """

    name = "dev_free"

    def create_order(self, out_trade_no: str, amount_cents: int) -> OrderResult:
        token = _store.create(out_trade_no, amount_cents)
        _store.mark_paid(token, out_trade_no)
        return OrderResult(
            order_token=token,
            out_trade_no=out_trade_no,
            amount_cents=amount_cents,
            amount_yuan=round(amount_cents / 100, 2),
            provider=self.name,
            paid=True,
            code_url=None,
            dev_mode=True,
        )

    def verify_notify(self, raw_body: bytes, headers: dict[str, str]) -> NotifyResult:
        # Dev/free path never receives a real callback.
        return NotifyResult(out_trade_no="", paid=False)


class WeChatNativeProvider(PaymentProvider):
    """Real 微信支付 v3 Native (扫码) provider — credential-gated + lazy-import.

    Activating real ¥9.9 payment is a DEPLOYMENT step, not a code change:
      1. ``pip install wechatpayv3``  (a vetted MIT SDK bundling RSA-SHA256 +
         AES-GCM; avoids shipping hand-rolled crypto).
      2. Set the ``CVFIT_WECHAT_APPID`` / ``CVFIT_WECHAT_MCHID`` /
         ``CVFIT_WECHAT_SERIAL_NO`` / ``CVFIT_WECHAT_PRIVATE_KEY_PATH`` /
         ``CVFIT_WECHAT_APIV3_KEY`` / ``CVFIT_PAYMENT_NOTIFY_URL`` env vars.
    Until then ``get_provider()`` returns the DevFreeProvider so the unwatermark
    flow still works (free for self-hosters). This keeps the app bootable
    without the SDK and avoids shipping untested crypto code.
    """

    name = "wechat_native"

    def __init__(self) -> None:
        # Creds are read from settings (app/config.py) at call time, not import time.
        if not settings.wechat_appid or not settings.wechat_mchid:
            raise PaymentNotConfigured(
                "WeChat merchant credentials not set. Set CVFIT_WECHAT_APPID/"
                "CVFIT_WECHAT_MCHID/CVFIT_WECHAT_SERIAL_NO/"
                "CVFIT_WECHAT_PRIVATE_KEY_PATH/CVFIT_WECHAT_APIV3_KEY to enable "
                "real ¥9.9 payment; otherwise the dev/free path auto-unwatermarks "
                "for self-hosters."
            )

    def _client(self):  # pragma: no cover — needs the SDK + creds (not exercised in CI)
        try:
            from wechatpayv3 import WeChatPay  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PaymentNotConfigured(
                "wechatpayv3 not installed. `pip install wechatpayv3` then set the "
                "CVFIT_WECHAT_* credentials to enable real ¥9.9 WeChat payment."
            ) from exc
        with open(settings.wechat_private_key_path, "r", encoding="utf-8") as fh:
            private_key = fh.read()
        return WeChatPay(
            appid=settings.wechat_appid,
            mchid=settings.wechat_mchid,
            private_key=private_key,
            cert_serial_no=settings.wechat_serial_no,
            apiv3_key=settings.wechat_apiv3_key,
            app_type="native",
            notify_url=settings.payment_notify_url or "",
        )

    def create_order(self, out_trade_no: str, amount_cents: int) -> OrderResult:  # pragma: no cover
        token = _store.create(out_trade_no, amount_cents)
        code_url = self._client().pay.native(
            description="cv-fit-zh 去水印 ¥%.2f" % (amount_cents / 100),
            out_trade_no=out_trade_no,
            amount={"total": amount_cents, "currency": "CNY"},
        )
        return OrderResult(
            order_token=token,
            out_trade_no=out_trade_no,
            amount_cents=amount_cents,
            amount_yuan=round(amount_cents / 100, 2),
            provider=self.name,
            paid=False,
            code_url=code_url,
            dev_mode=False,
        )

    def verify_notify(self, raw_body: bytes, headers: dict[str, str]) -> NotifyResult:  # pragma: no cover
        result = self._client().callback(headers=dict(headers), body=raw_body.decode("utf-8"))
        out_trade_no = result.get("out_trade_no", "")
        paid = result.get("trade_state") == "SUCCESS"
        if paid and out_trade_no:
            # Mark the matching local order paid (reverse-lookup by out_trade_no).
            for tok, order in list(_store._orders.items()):
                if order.get("out_trade_no") == out_trade_no:
                    _store.mark_paid(tok, out_trade_no)
                    break
        return NotifyResult(out_trade_no=out_trade_no, paid=paid)


def _wechat_creds_configured() -> bool:
    return bool(
        settings.wechat_appid
        and settings.wechat_mchid
        and settings.wechat_serial_no
        and settings.wechat_private_key_path
        and settings.wechat_apiv3_key
    )


def get_provider() -> PaymentProvider:
    """Resolve the active payment provider.

    - CVFIT_PAYMENT_DISABLED=1 → DevFreeProvider (free unwatermark for self-host).
    - No WeChat creds configured → DevFreeProvider (same free path).
    - WeChat creds configured → WeChatNativeProvider (real ¥9.9).
    """
    if settings.payment_disabled or not _wechat_creds_configured():
        return DevFreeProvider()
    try:
        return WeChatNativeProvider()
    except PaymentNotConfigured:
        return DevFreeProvider()


def _new_out_trade_no() -> str:
    """Idempotent-ish merchant order id: cvfit-<timestamp>-<rand>."""
    return "cvfit-{}-{}".format(int(time.time()), secrets.token_hex(4))


def is_paid_order(order_token: str) -> bool:
    """Public hook for /api/export: is this order token a paid unwatermark grant?"""
    return _store.is_paid(order_token)


class CreateOrderResponse(OrderResult):
    """POST /api/payment response (same shape as OrderResult)."""


@router.post("", response_model=CreateOrderResponse)
def create_order() -> CreateOrderResponse:
    """Create a ¥9.9/投递 unwatermark order.

    In dev/free mode (no WeChat creds or CVFIT_PAYMENT_DISABLED=1) the order is
    returned already paid so self-hosters get the unwatermarked output free. In
    real mode the response carries a WeChat Native `code_url` to render as a QR.
    """
    provider = get_provider()
    out_trade_no = _new_out_trade_no()
    try:
        result = provider.create_order(out_trade_no, _price_cents())
    except PaymentNotConfigured as exc:
        # Provider said it could do real payment but creds vanished between
        # resolution and the call — degrade to the free path rather than 500.
        log.warning("paid provider not configured at call time (%s); dev/free fallback", exc)
        result = DevFreeProvider().create_order(out_trade_no, _price_cents())
    return CreateOrderResponse(**result.model_dump())


@router.get("/notify")
def notify_get(request: Request) -> dict[str, str]:
    """WeChat sends a GET verification probe before the POST callback in some flows."""
    return {"code": "SUCCESS", "message": "OK"}


@router.post("/notify")
async def notify_post(request: Request) -> dict[str, str]:
    """WeChat async payment callback. Verifies the signature + marks the order paid."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_provider()
    try:
        result = provider.verify_notify(raw, headers)
    except PaymentNotConfigured:
        return {"code": "FAIL", "message": "payment not configured"}
    return {"code": "SUCCESS" if result.paid else "FAIL", "message": "OK"}


@router.get("/{order_token}")
def order_status(order_token: str) -> dict[str, Any]:
    """Poll an order's paid status (the front-end uses this before re-exporting)."""
    order = _store.get(order_token)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found or expired")
    return {
        "order_token": order_token,
        "out_trade_no": order["out_trade_no"],
        "amount_yuan": round(order["amount_cents"] / 100, 2),
        "paid": order["status"] == "paid",
    }
