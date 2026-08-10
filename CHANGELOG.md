# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
