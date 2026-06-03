"""Tests for LLMClient — the Anthropic API wrapper.

A fake anthropic client is patched in, so tests never touch the network.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from openpoly.llm import LLMClient, LLMError

_TOOL: dict[str, Any] = {
    "name": "submit_analysis",
    "description": "d",
    "input_schema": {"type": "object", "properties": {}},
}


class _Block:
    def __init__(
        self,
        type_: str,
        *,
        name: str | None = None,
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        self.type = type_
        self.name = name
        self.input = tool_input


class _Response:
    def __init__(self, content: list[_Block], stop_reason: str = "tool_use") -> None:
        self.content = content
        self.stop_reason = stop_reason


class _Messages:
    """Fake ``client.messages``: returns queued responses or raises."""

    def __init__(
        self,
        responses: list[_Response] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self._responses.pop(0)


def _patch(monkeypatch, messages: _Messages) -> None:
    fake_client = type("_FakeClient", (), {"messages": messages})()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake_client)
    monkeypatch.setenv("OPENPOLY_TEST_LLM_KEY", "sk-test")


def _client(model: str = "claude-haiku-4-5") -> LLMClient:
    return LLMClient(
        api_key_ref="env:OPENPOLY_TEST_LLM_KEY",
        model=model,
        temperature=0.2,
    )


def _tool_response(payload: dict[str, Any]) -> _Response:
    return _Response([_Block("tool_use", name="submit_analysis", tool_input=payload)])


# ---------- key resolution ----------


def test_key_resolution_failure_raises_llm_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENPOLY_MISSING_KEY", raising=False)
    client = LLMClient(
        api_key_ref="env:OPENPOLY_MISSING_KEY",
        model="claude-haiku-4-5",
        temperature=0.2,
    )
    with pytest.raises(LLMError, match="api_key_ref"):
        client.analyze(system="s", user="u", tool=_TOOL)


# ---------- tool parsing ----------


def test_returns_tool_input(monkeypatch) -> None:
    messages = _Messages([_tool_response({"selected_index": 1, "p_yes": 0.7})])
    _patch(monkeypatch, messages)
    out = _client().analyze(system="s", user="u", tool=_TOOL)
    assert out == {"selected_index": 1, "p_yes": 0.7}
    assert len(messages.calls) == 1


def test_forces_tool_and_marks_system_cacheable(monkeypatch) -> None:
    messages = _Messages([_tool_response({"ok": True})])
    _patch(monkeypatch, messages)
    _client().analyze(system="SYS", user="u", tool=_TOOL)
    kw = messages.calls[0]
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_analysis"}
    assert kw["tools"] == [_TOOL]
    assert kw["system"][0]["text"] == "SYS"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


# ---------- structural retry ----------


def test_retries_when_first_response_has_no_tool_block(monkeypatch) -> None:
    messages = _Messages(
        [
            _Response([_Block("text")]),  # attempt 1: no tool_use
            _tool_response({"selected_index": 0}),  # attempt 2: ok
        ]
    )
    _patch(monkeypatch, messages)
    out = _client().analyze(system="s", user="u", tool=_TOOL)
    assert out == {"selected_index": 0}
    assert len(messages.calls) == 2


def test_raises_when_no_tool_block_after_retries(monkeypatch) -> None:
    messages = _Messages(
        [
            _Response([_Block("text")]),
            _Response([_Block("text")]),
        ]
    )
    _patch(monkeypatch, messages)
    with pytest.raises(LLMError, match="no usable tool call"):
        _client().analyze(system="s", user="u", tool=_TOOL)
    assert len(messages.calls) == 2


# ---------- API error ----------


def test_api_error_raises_llm_error(monkeypatch) -> None:
    err = anthropic.APIConnectionError(
        message="boom",
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    _patch(monkeypatch, _Messages(raise_exc=err))
    with pytest.raises(LLMError, match="Anthropic API call failed"):
        _client().analyze(system="s", user="u", tool=_TOOL)


# ---------- temperature handling ----------


def test_temperature_sent_for_haiku(monkeypatch) -> None:
    messages = _Messages([_tool_response({"ok": True})])
    _patch(monkeypatch, messages)
    _client("claude-haiku-4-5").analyze(system="s", user="u", tool=_TOOL)
    assert messages.calls[0]["temperature"] == 0.2


def test_temperature_omitted_for_opus_4_7(monkeypatch) -> None:
    messages = _Messages([_tool_response({"ok": True})])
    _patch(monkeypatch, messages)
    _client("claude-opus-4-7").analyze(system="s", user="u", tool=_TOOL)
    assert "temperature" not in messages.calls[0]


# ---------- base_url (third-party gateway, e.g. yunwu) ----------


def _patch_capturing(monkeypatch, messages: _Messages) -> dict[str, Any]:
    """Like ``_patch`` but returns the kwargs the Anthropic ctor was built with."""
    captured: dict[str, Any] = {}
    fake_client = type("_FakeClient", (), {"messages": messages})()

    def _factory(**kw: Any) -> Any:
        captured.update(kw)
        return fake_client

    monkeypatch.setattr(anthropic, "Anthropic", _factory)
    monkeypatch.setenv("OPENPOLY_TEST_LLM_KEY", "sk-test")
    return captured


def test_base_url_omitted_when_empty(monkeypatch) -> None:
    # Empty base_url is not passed, so the SDK keeps its own default (and
    # still honors an ANTHROPIC_BASE_URL env var).
    captured = _patch_capturing(monkeypatch, _Messages([_tool_response({"ok": True})]))
    _client().analyze(system="s", user="u", tool=_TOOL)
    assert "base_url" not in captured


def test_base_url_passed_when_set(monkeypatch) -> None:
    captured = _patch_capturing(monkeypatch, _Messages([_tool_response({"ok": True})]))
    client = LLMClient(
        api_key_ref="env:OPENPOLY_TEST_LLM_KEY",
        model="claude-haiku-4-5",
        temperature=0.2,
        base_url="https://yunwu.ai",
    )
    client.analyze(system="s", user="u", tool=_TOOL)
    assert captured["base_url"] == "https://yunwu.ai"


# ---------- ping (connectivity probe) ----------


def test_ping_ok(monkeypatch) -> None:
    ack = _Response([_Block("tool_use", name="ack", tool_input={"ok": True})])
    messages = _Messages([ack])
    _patch(monkeypatch, messages)
    _client().ping()  # returns None, no raise
    assert len(messages.calls) == 1
    assert messages.calls[0]["tool_choice"] == {"type": "tool", "name": "ack"}


def test_ping_raises_on_api_error(monkeypatch) -> None:
    err = anthropic.APIConnectionError(
        message="boom",
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    _patch(monkeypatch, _Messages(raise_exc=err))
    with pytest.raises(LLMError, match="Anthropic API call failed"):
        _client().ping()


def test_ping_raises_on_bad_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENPOLY_MISSING_KEY", raising=False)
    client = LLMClient(
        api_key_ref="env:OPENPOLY_MISSING_KEY",
        model="claude-haiku-4-5",
        temperature=0.2,
    )
    with pytest.raises(LLMError, match="api_key_ref"):
        client.ping()
