# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-01

### Fixed

- **Non-dict LLM response no longer 500s `/api/rewrite`.** `complete_json` /
  `_parse_json_lenient` now rejects a non-dict LLM JSON response (a JSON
  array / string / number a model can emit under content-filter or json-mode
  drift) with `ValueError`. Previously every downstream coerce called
  `raw.get(...)`, which on a non-dict raised `AttributeError` — not in any
  caller's `(LLMError, ValueError)` fallback catch — so the documented
  heuristic / passthrough / deterministic fallback never engaged and a single
  non-dict JD or resume response 500'd `/api/rewrite` (and `rewrite_star`
  violated its "Never raises on LLM failure" contract). `ValueError` is caught
  by all four callers, so the fallback now engages. This is the same
  defect-class as the v0.2 `fix-llm-empty-choices-crash` (an uncaught exception
  defeating the model fallback), closed at the parse layer that fix left
  unguarded.
- **`POST /api/payment` no longer 500s on a misconfigured real-WeChat setup.**
  The `create_order` endpoint caught only `PaymentNotConfigured`, so a wrong /
  unreadable private-key path (`FileNotFoundError` / `PermissionError`) or any
  WeChatPay SDK error escaped uncaught → HTTP 500, bricking the ¥9.9
  unwatermark flow before a QR was ever shown. v0.4.0 broadened `notify_post`'s
  except to `Exception` but left `create_order`'s narrow; v0.5.0 broadens it
  too and degrades to the `DevFreeProvider` free path (mirroring the existing
  `PaymentNotConfigured` fallback), so a misconfigured real-payment setup
  returns a clean 200 dev/free order instead of a 500. The real paid path is
  unaffected when credentials are correct.

## [0.4.0] - 2026-08-17

### Fixed

- **Free-tier daily quota now enforced on `/api/rewrite`.** The advertised
  "免费 1 次/天" free-tier quota (`settings.free_daily_quota`, default 1) is
  now enforced via an in-process per-IP daily counter at the top of
  `rewrite_endpoint`. Previously `/api/rewrite` jumped straight from input
  validation into the billable LLM fan-out (~3 LLM completions / request) with
  no counter, so an unauthenticated client could spam the endpoint and drain
  the operator's DeepSeek/Qwen budget with no per-IP bound. A 2nd free rewrite
  on the same calendar day now returns 429; the counter resets on date change;
  the ¥9.9 paid unwatermark path (which gates `/api/export`) is unaffected.
- **¥9.9 unwatermark order token is now single-use.** A real paid order
  (`dev_mode=False`) is flipped to a terminal `consumed` state on the first
  unwatermarked export, so a replay of the same `order_token` falls back to
  watermarked — matching the plan's "¥9.9/投递 ... 按投递计费 ... 单次"
  single-use pricing. The dev/free self-host path (`dev_mode=True`) is exempt,
  so self-hosters keep the free unwatermark flow on re-export. The 24h TTL
  cleanup is unchanged.
- **`/api/payment/notify` catches all exceptions, not just
  `PaymentNotConfigured`.** A WeChat callback whose signature verification,
  AES-GCM decryption, or JSON parsing raises (the expected failure modes —
  none of which are `PaymentNotConfigured`) now returns HTTP 200 +
  `{"code":"FAIL"}` so WeChat retries cleanly instead of hitting a 500 that
  exhausts retries and leaves a paid order unmarked. The callback's paid
  amount is also confirmed against the order's `amount_cents` before
  `mark_paid` so a mismatched-amount callback can't unlock a different-priced
  order.

## [0.3.0] - 2026-08-11

### Fixed

- **Watermark paywall bypass closed.** `POST /api/export` no longer honors the
  client-controlled `watermark` field when deciding whether to stamp the
  watermark. Previously a free request with `watermark: false` and no paid
  `order_token` returned an unwatermarked PDF/docx, bypassing the ¥9.9
  unwatermark paywall. The free tier is now always watermarked unless a paid
  `order_token` is presented; the self-host dev/free path still auto-unwatermarks
  because `DevFreeProvider` marks orders paid.
- **Payment price config honored.** `POST /api/payment` now derives the order
  amount from `settings.payment_price_cents` (the `CVFIT_PAYMENT_PRICE_CENTS`
  env var) instead of a hardcoded `999`. Operators can change the price without
  code changes; a `0`/empty value falls back to the default (999 = ¥9.9) so a
  misconfiguration can't silently make the unwatermark free.
