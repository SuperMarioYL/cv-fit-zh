"""v0.2 fix tests — LLM empty-choices crash (fix-llm-empty-choices-crash).

The bug: _ModelTarget.chat indexed resp.choices[0] without guarding an empty
choices list (content-moderation refusals return choices=[]). The IndexError
escaped the APIError tuple and complete_json's `except LLMError`, 500ing the
request without trying the fallback. The fix raises LLMError so the fallback
loop catches it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.client import LLMClient, LLMError, _ModelTarget


class _FakeChoicesEmpty:
    """A fake OpenAI completions response with NO choices (content_filter)."""

    choices = []


class _FakeChoicesOk:
    """A fake OpenAI completions response with one JSON content message."""

    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _FakeOpenAI:
    """A stub OpenAI client: ``chat.completions.create`` returns scripted responses."""

    def __init__(self, responses: list) -> None:
        # each entry is either a _FakeChoicesEmpty / _FakeChoicesOk instance,
        # or an Exception to raise.
        self._responses = list(responses)
        self._i = 0
        self.calls = 0

        class _Completions:
            def __init__(slf, parent):
                slf._p = parent

            def create(slf, **kwargs):
                slf._p.calls += 1
                if slf._p._i >= len(slf._p._responses):
                    raise AssertionError("no more scripted responses")
                r = slf._p._responses[slf._p._i]
                slf._p._i += 1
                if isinstance(r, Exception):
                    raise r
                return r

        class _Chat:
            def __init__(slf, parent):
                slf.completions = _Completions(parent)

        self.chat = _Chat(self)


def _make_target(fake: _FakeOpenAI, name: str = "test") -> _ModelTarget:
    t = _ModelTarget(name, "https://example/v1", "key", "model")
    t._client = fake  # inject the fake OpenAI client
    return t


def test_empty_choices_raises_llmerror_not_indexerror():
    """Empty choices (content_filter) must raise LLMError, not IndexError."""
    fake = _FakeOpenAI([_FakeChoicesEmpty(), _FakeChoicesEmpty()])
    target = _make_target(fake)
    with pytest.raises(LLMError) as exc_info:
        target.chat("system", "user", json_mode=True)
    # The error message names the moderation/no-choices cause.
    assert "no choices" in str(exc_info.value).lower() or "content_filter" in str(
        exc_info.value
    ).lower()


def test_empty_choices_then_ok_falls_over_in_complete_json():
    """complete_json must fall over to the next target when one returns no choices.

    primary -> empty choices (LLMError) -> fallback -> valid JSON. complete_json
    returns the fallback's parsed dict; the primary failure is logged, not raised.
    """
    ok_target = _ModelTarget("ok", "https://example/v1", "key", "model")
    ok_target._client = _FakeOpenAI([_FakeChoicesOk('{"k": 1}')])

    bad_target = _ModelTarget("bad", "https://example/v1", "key", "model")
    bad_target._client = _FakeOpenAI([_FakeChoicesEmpty()])

    client = LLMClient()
    # Force the target order: bad first (empty choices), then ok (valid JSON).
    client._targets = [bad_target, ok_target]

    result = client.complete_json("system", "user")
    assert result == {"k": 1}
    # Both targets were actually consulted (bad raised, ok succeeded).
    assert bad_target._client.calls == 1
    assert ok_target._client.calls == 1


def test_all_targets_empty_choices_raises_llmerror():
    """When every target returns empty choices, complete_json raises LLMError."""
    bad1 = _ModelTarget("bad1", "https://example/v1", "key", "model")
    bad1._client = _FakeOpenAI([_FakeChoicesEmpty()])
    bad2 = _ModelTarget("bad2", "https://example/v1", "key", "model")
    bad2._client = _FakeOpenAI([_FakeChoicesEmpty()])
    client = LLMClient()
    client._targets = [bad1, bad2]
    with pytest.raises(LLMError):
        client.complete_json("system", "user")
