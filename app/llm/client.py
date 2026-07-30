"""LLM client: OpenAI-compatible, DeepSeek-V3 primary + Qwen2.5-72b fallback.

Both model targets expose the same chat-completions API, so one thin client
covers them. The client enforces JSON-mode responses (the structured
JD/resume/coverage objects are all parsed from JSON) and falls over from the
primary model to the fallback on network/API errors.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from openai import APIError as _OpenAIAPIError

from app.config import settings

log = logging.getLogger("cvfit.llm")


class LLMError(RuntimeError):
    """Raised when both primary and fallback models fail."""


class LLMUnavailableError(LLMError):
    """Raised when no API key is configured for any model target."""


class _ModelTarget:
    """A single OpenAI-compatible model endpoint."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.model = model
        self._client: Optional[OpenAI] = None
        if api_key:
            self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=settings.request_timeout)

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        assert self._client is not None
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
        }
        if json_mode:
            # DeepSeek/Qwen/OpenAI all honour response_format={"type": "json_object"}.
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except (APITimeoutError, APIConnectionError, RateLimitError, _OpenAIAPIError) as exc:
            raise LLMError(f"{self.name} call failed: {exc}") from exc


class LLMClient:
    """Orders model targets by priority and falls over on failure."""

    def __init__(self) -> None:
        self.primary = _ModelTarget(
            "deepseek-v3", settings.primary_base_url, settings.api_key, settings.primary_model
        )
        self.fallback = _ModelTarget(
            "qwen2.5-72b", settings.fallback_base_url, settings.fallback_api_key, settings.fallback_model
        )
        self._targets = [t for t in (self.primary, self.fallback) if t.available]

    @property
    def available(self) -> bool:
        return len(self._targets) > 0

    def require_available(self) -> None:
        if not self.available:
            raise LLMUnavailableError(
                "No LLM API key configured. Set DEEPSEEK_API_KEY (primary) or "
                "DASHSCOPE_API_KEY (fallback) before rewriting."
            )

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Run a chat completion expecting a JSON object; return parsed dict.

        Tries the primary target, then the fallback. JSON is parsed leniently
        (strips markdown fences if a model wraps output despite json_mode).
        """
        self.require_available()
        last_err: Optional[LLMError] = None
        for target in self._targets:
            try:
                raw = target.chat(system, user, json_mode=True)
                return _parse_json_lenient(raw)
            except LLMError as exc:
                log.warning("target %s failed: %s; trying next", target.name, exc)
                last_err = exc
                continue
            except (ValueError, json.JSONDecodeError) as exc:
                log.warning("target %s returned non-JSON: %s", target.name, exc)
                last_err = LLMError(f"{target.name} JSON parse failed: {exc}")
                continue
        raise LLMError(f"All model targets failed; last error: {last_err}")


def _parse_json_lenient(raw: str) -> dict[str, Any]:
    """Parse a model response into a dict, tolerating code fences / prefix prose."""
    text = raw.strip()
    if not text:
        raise ValueError("empty response")
    # Strip ```json ... ``` fences if present.
    if text.startswith("```"):
        text = text.split("```", 2)
        if len(text) >= 2:
            inner = text[1]
            # Drop a leading language tag like 'json\n'.
            if inner.lower().startswith("json"):
                inner = inner[4:]
            text = inner.strip()
    # If there's still leading prose, try to slice from the first { to the last }.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


# Module-level singleton. Constructed lazily-aware: tests monkeypatch this.
_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_client() -> None:
    """Reset the cached client (used by tests to pick up new env vars)."""
    global _client
    _client = None
