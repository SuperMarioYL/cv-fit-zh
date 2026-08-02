"""Application configuration.

All settings are read from environment variables so the service is stateless
and self-hostable. No config file is required to boot — sensible defaults let
the server start for local exploration; only the LLM key is needed to actually
rewrite a resume (a clear error is raised at rewrite time if missing).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the cv-fit-zh service."""

    # --- LLM ---
    # DeepSeek-V3 is the primary model; Qwen2.5-72b is the fallback. Both speak
    # the OpenAI-compatible chat completions API, so a single client works.
    api_key: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", "")))

    # Primary (DeepSeek-V3). Base URL points at the OpenAI-compatible endpoint.
    primary_base_url: str = field(default_factory=lambda: os.environ.get("CVFIT_PRIMARY_BASE_URL", "https://api.deepseek.com/v1"))
    primary_model: str = field(default_factory=lambda: os.environ.get("CVFIT_PRIMARY_MODEL", "deepseek-chat"))

    # Fallback (Qwen2.5-72b via DashScope OpenAI-compatible endpoint).
    fallback_base_url: str = field(default_factory=lambda: os.environ.get("CVFIT_FALLBACK_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    fallback_model: str = field(default_factory=lambda: os.environ.get("CVFIT_FALLBACK_MODEL", "qwen-plus"))
    fallback_api_key: str = field(default_factory=lambda: os.environ.get("DASHSCOPE_API_KEY", ""))

    # --- Service ---
    host: str = field(default_factory=lambda: os.environ.get("CVFIT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("CVFIT_PORT", "8000")))

    # Free tier: 1 watermarked rewrite per day per local install (no DB — the
    # counter is in-memory and resets on restart; the ¥9.9/投递 unwatermark path
    # is the real payment flow, v0.2 — see app/api/payment.py).
    free_daily_quota: int = field(default_factory=lambda: int(os.environ.get("CVFIT_FREE_QUOTA", "1")))
    watermark_text: str = field(default_factory=lambda: os.environ.get("CVFIT_WATERMARK", "cv-fit-zh 生成"))

    # --- Payment (v0.2 — ¥9.9/投递 unwatermark) ---
    # CVFIT_PAYMENT_DISABLED=1 → DevFreeProvider auto-unwatermarks for self-host
    # (the open-core free path). Configure the WeChat fields to enable real
    # ¥9.9 WeChat Pay v3 Native (扫码) payment (requires `pip install wechatpayv3`).
    payment_disabled: bool = field(default_factory=lambda: _env_bool("CVFIT_PAYMENT_DISABLED", False))
    payment_price_cents: int = field(default_factory=lambda: int(os.environ.get("CVFIT_PAYMENT_PRICE_CENTS", "999")))
    payment_notify_url: str = field(default_factory=lambda: os.environ.get("CVFIT_PAYMENT_NOTIFY_URL", ""))
    wechat_appid: str = field(default_factory=lambda: os.environ.get("CVFIT_WECHAT_APPID", ""))
    wechat_mchid: str = field(default_factory=lambda: os.environ.get("CVFIT_WECHAT_MCHID", ""))
    wechat_serial_no: str = field(default_factory=lambda: os.environ.get("CVFIT_WECHAT_SERIAL_NO", ""))
    wechat_private_key_path: str = field(default_factory=lambda: os.environ.get("CVFIT_WECHAT_PRIVATE_KEY_PATH", ""))
    wechat_apiv3_key: str = field(default_factory=lambda: os.environ.get("CVFIT_WECHAT_APIV3_KEY", ""))

    # --- LLM call tuning ---
    request_timeout: float = field(default_factory=lambda: float(os.environ.get("CVFIT_TIMEOUT", "60")))
    temperature: float = field(default_factory=lambda: float(os.environ.get("CVFIT_TEMPERATURE", "0.3")))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("CVFIT_MAX_TOKENS", "4096")))

    # --- Templates ---
    templates_dir: str = field(default_factory=lambda: os.environ.get("CVFIT_TEMPLATES_DIR", os.path.join(os.path.dirname(__file__), "templates")))

    @property
    def has_primary(self) -> bool:
        return bool(self.api_key)

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback_api_key)


settings = Settings()
