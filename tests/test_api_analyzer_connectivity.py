"""POST /api/analyzer/test: the LLM connectivity probe — ok / error paths.

A fake anthropic client is patched in, so tests never touch the network.
"""

from __future__ import annotations

from typing import Any

import anthropic
from httpx import ASGITransport, AsyncClient

from openpoly.api.main import app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _AckBlock:
    type = "tool_use"
    name = "ack"
    input = {"ok": True}


class _AckResponse:
    content = [_AckBlock()]
    stop_reason = "tool_use"


class _FakeMessages:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> _AckResponse:
        self.calls.append(kw)
        if self._raise is not None:
            raise self._raise
        return _AckResponse()


def _patch_anthropic(monkeypatch, messages: _FakeMessages) -> dict[str, Any]:
    """Patch ``anthropic.Anthropic``; return the kwargs it was built with."""
    captured: dict[str, Any] = {}
    fake = type("_FakeClient", (), {"messages": messages})()

    def _factory(**kw: Any) -> Any:
        captured.update(kw)
        return fake

    monkeypatch.setattr(anthropic, "Anthropic", _factory)
    return captured


async def test_ok_returns_latency(monkeypatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_LLM_KEY", "sk-test")
    _patch_anthropic(monkeypatch, _FakeMessages())
    client = await _client()
    try:
        r = await client.post(
            "/api/analyzer/test",
            json={
                "llm_model": "claude-haiku-4-5",
                "api_key_ref": "env:OPENPOLY_TEST_LLM_KEY",
                "base_url": "https://yunwu.ai",
                "temperature": 0.2,
            },
        )
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert isinstance(body["latency_ms"], int)


async def test_base_url_v1_suffix_stripped(monkeypatch) -> None:
    # A yunwu-style /v1 URL must reach anthropic.Anthropic as the root domain —
    # proves the request flows through LLMAnalyzerConfig's normalizer.
    monkeypatch.setenv("OPENPOLY_TEST_LLM_KEY", "sk-test")
    captured = _patch_anthropic(monkeypatch, _FakeMessages())
    client = await _client()
    try:
        r = await client.post(
            "/api/analyzer/test",
            json={
                "llm_model": "claude-haiku-4-5",
                "api_key_ref": "env:OPENPOLY_TEST_LLM_KEY",
                "base_url": "https://yunwu.ai/v1",
            },
        )
    finally:
        await client.aclose()
    assert r.json()["ok"] is True
    assert captured["base_url"] == "https://yunwu.ai"


async def test_missing_key_returns_error() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/analyzer/test",
            json={
                "llm_model": "claude-haiku-4-5",
                "api_key_ref": "env:OPENPOLY_DEFINITELY_NOT_SET",
            },
        )
    finally:
        await client.aclose()
    body = r.json()
    assert body["ok"] is False
    assert "api_key_ref" in body["error"]
    assert body["latency_ms"] is None


async def test_invalid_config_returns_error() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/analyzer/test",
            json={
                "llm_model": "claude-haiku-4-5",
                "api_key_ref": "env:X",
                "temperature": 9.0,  # outside [0, 1]
            },
        )
    finally:
        await client.aclose()
    body = r.json()
    assert body["ok"] is False
    assert "invalid config" in body["error"]


async def test_api_error_surfaces_as_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_LLM_KEY", "sk-test")
    import httpx

    err = anthropic.APIConnectionError(
        message="boom",
        request=httpx.Request("POST", "https://yunwu.ai/v1/messages"),
    )
    _patch_anthropic(monkeypatch, _FakeMessages(raise_exc=err))
    client = await _client()
    try:
        r = await client.post(
            "/api/analyzer/test",
            json={
                "llm_model": "claude-haiku-4-5",
                "api_key_ref": "env:OPENPOLY_TEST_LLM_KEY",
            },
        )
    finally:
        await client.aclose()
    body = r.json()
    assert body["ok"] is False
    assert body["error"]
