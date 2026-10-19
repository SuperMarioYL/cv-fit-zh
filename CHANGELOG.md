# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-10-20

### Fixed

- **Date-first experience headers no longer corrupt the offline resume parse.**
  The heuristic splitter treats any line starting with digits as a list bullet,
  so a date-first entry header — `2021.07-至今 某科技公司 后端开发工程师`, a
  common Chinese resume layout without pipe separators — was mis-classified as
  a bullet, its leading `2021.` chewed off as a list ordinal, and the whole
  entry merged into the previous job with empty company/title/dates. The
  splitter now recognises the 4-digit-year + date-separator prefix as an entry
  header and splits the date, company and title apart; numbered bullets
  (`1.` / `12.`) and pipe-separated layouts parse exactly as before. This path
  is the default when no LLM key is configured (and the documented fallback
  when the LLM fails), and is reachable from both the web UI and
  `python -m app.resume.parser --resume x.txt`.
- **A drifted LLM list field no longer 500s `/api/rewrite`.** A model response
  like `{"experience": 3}` (a number where a list belongs) raised `TypeError`
  while iterating the field — `TypeError` is not in `parse_resume`'s
  `(LLMError, ValueError)` fallback catch nor in `/api/rewrite`'s
  `LLMUnavailableError`-only catch, so a single drifted response 500'd the
  endpoint. The keyword-coverage response had the same gap (`{"matched": 7}`
  escaped `score_coverage`'s fallback). A truthy non-list value now raises
  `ValueError` — the caught, fallback-triggering error — so `parse_resume`
  degrades to the heuristic splitter (recovering the experience from the raw
  text instead of silently dropping it) and `score_coverage` degrades to the
  deterministic scorer. This closes the field-level residual of the v0.5.0 /
  v0.6.0 non-dict guards at the coercion layer that owns the field contract.

### Changed

- **Bounded memory for the in-process stores.** Expired payment orders were
  only removed if that exact token happened to be polled again after expiry,
  and the free-rewrite quota kept one bucket per client IP forever — a
  long-running self-host instance grew monotonically. Both stores now sweep
  stale entries opportunistically on write (order create / quota consume), so
  memory stays bounded with no new dependencies. The 24h order TTL, the
  dev/free self-host exemption, single-use consumption with render-failure
  refund, and the daily quota semantics are unchanged.

## [0.6.0] - 2026-09-14

### Fixed

- **A failed export render no longer burns the paid ¥9.9 unwatermark grant.**
  `/api/export` claimed the single-use paid grant (atomic check-and-consume)
  BEFORE rendering; if the render then failed (e.g. a weasyprint font/native-lib
  error → 502), the real paid order had already been flipped to the terminal
  `consumed` state. The paying user's natural retry silently fell back to the
  watermarked free output — money taken, product never delivered. v0.6.0 adds
  `refund_paid_order` (the counterpart of `consume_paid_order`): the endpoint
  refunds the order it consumed when the render raises, so consumption stays
  tied to a SUCCESSFUL unwatermarked export, exactly as the v0.4.0 single-use
  milestone specified ("一次成功无水印导出后…翻转为终态 consumed"). The atomic
  check-and-consume (no replay race window), the dev/free self-host exemption,
  and the 24h TTL cleanup are unchanged.
- **A non-dict LLM `basics` field no longer 500s `/api/rewrite`.** Under
  json-mode drift a model can collapse the resume `basics` object into a string
  (e.g. `{"basics": "张三 13800138000 …"}`); the top level IS a dict, so the
  v0.5.0 top-level non-dict guard did not fire, and `_coerce`'s
  `basics_raw.get("name")` raised `AttributeError` — not in `parse_resume`'s
  `(LLMError, ValueError)` catch and not in `/api/rewrite`'s
  `LLMUnavailableError`-only catch — so a single drifted response 500'd the
  endpoint instead of degrading to the documented heuristic splitter. v0.6.0
  raises `ValueError` on a non-dict `basics` (the nested experience/education
  items were already isinstance-guarded), so the heuristic fallback engages and
  the pipeline keeps working. This is the nested-field residual of the v0.5.0
  `fix-llm-nondict-response-attributeerror`, closed at the coercion layer that
  owns the "basics is an object" contract.

### Added

- **Version-lockstep test.** The release version lives in three hand-maintained
  surfaces (`VERSION`, `pyproject.toml`, `app.__version__`); a new test pins
  all three equal (and matching the current release) so a future bump that
  misses a surface fails CI instead of shipping a mismatched release.

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
