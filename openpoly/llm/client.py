"""Anthropic API client wrapper for section impls.

A thin **sync** wrapper over ``anthropic.Anthropic``: it resolves the API key
from an ``api_key_ref``, forces a single structured tool call, and returns that
tool's input dict. An optional ``base_url`` routes the call through an
Anthropic-compatible third-party gateway (e.g. yunwu) instead of the
official endpoint. The Anthropic SDK retries transient HTTP errors (429 / 5xx /
connection) itself; this wrapper additionally retries a *structurally* bad
response — one with no ``tool_use`` block, e.g. a refusal — up to
``_STRUCTURAL_RETRIES`` times before raising ``LLMError``.

The call blocks (network I/O); the orchestrator runs the section on a worker
thread so it does not stall the event loop.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from openpoly.news.secrets import SecretsError, resolve

logger = logging.getLogger(__name__)

# The structured tool output is small (an index, a probability, a one-line
# rationale); this caps the response generously.
_MAX_TOKENS = 1024
# Retries when the model returns no tool_use block (the SDK handles HTTP retries).
_STRUCTURAL_RETRIES = 2
# Per-request timeout — a small completion should never take this long.
_TIMEOUT_SECONDS = 60.0

# Minimal tool for the connectivity probe (``ping``): its only job is to make
# the model emit one forced tool_use block.
_PING_TOOL: dict[str, Any] = {
    "name": "ack",
    "description": "Acknowledge the connectivity check.",
    "input_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


class LLMError(Exception):
    """An LLM call failed — key resolution, the API, or a malformed response."""


class LLMClient:
    """Sync Anthropic wrapper that runs one forced-tool-call analysis.

    Construction touches nothing; the API key is resolved and the underlying
    ``anthropic.Anthropic`` client is built lazily on the first ``analyze`` —
    so a client can be constructed before the secret store is reachable.
    """

    def __init__(
        self,
        *,
        api_key_ref: str,
        model: str,
        temperature: float,
        base_url: str = "",
    ) -> None:
        self._api_key_ref = api_key_ref
        self._model = model
        self._temperature = temperature
        self._base_url = base_url
        self._client: anthropic.Anthropic | None = None

    def _ensure_client(self) -> anthropic.Anthropic:
        if self._client is None:
            try:
                api_key = resolve(self._api_key_ref)
            except SecretsError as exc:
                raise LLMError(
                    f"could not resolve api_key_ref {self._api_key_ref!r}: {exc}"
                ) from exc
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "max_retries": 2,
                "timeout": _TIMEOUT_SECONDS,
            }
            # Empty base_url → omit it, so the SDK keeps its own default (and
            # still honors an ANTHROPIC_BASE_URL env var); non-empty → route
            # through the given Anthropic-compatible gateway (e.g. yunwu).
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def analyze(self, *, system: str, user: str, tool: dict[str, Any]) -> dict[str, Any]:
        """Run one completion forced to call ``tool``; return its input dict.

        ``tool`` is a full tool definition (``name`` / ``description`` /
        ``input_schema``). Raises ``LLMError`` on key / API failure or when the
        model returns no usable tool call.
        """
        client = self._ensure_client()
        tool_name = str(tool["name"])
        # temperature is rejected by claude-opus-4-7; omit it for that model.
        extra: dict[str, Any] = (
            {} if self._model == "claude-opus-4-7" else {"temperature": self._temperature}
        )

        last_error: str | None = None
        for attempt in range(1, _STRUCTURAL_RETRIES + 1):
            try:
                response = client.messages.create(
                    model=self._model,
                    max_tokens=_MAX_TOKENS,
                    # cache_control marks the (static) system prompt cacheable;
                    # it is a no-op below the model's minimum cacheable prefix.
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                    **extra,
                )
            except anthropic.APIError as exc:
                # The SDK already retried transient errors; this is terminal.
                raise LLMError(f"Anthropic API call failed: {exc!r}") from exc

            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return dict(block.input)

            last_error = f"no '{tool_name}' tool call (stop_reason={response.stop_reason})"
            logger.warning("LLM attempt %d/%d: %s", attempt, _STRUCTURAL_RETRIES, last_error)

        raise LLMError(f"LLM returned no usable tool call: {last_error}")

    def ping(self) -> None:
        """Probe connectivity with one minimal forced tool call.

        Reuses ``analyze`` so the probe walks exactly the path a real call
        takes — key resolution, ``base_url`` routing, model-id validity, and
        the forced-tool-call support the analyzer depends on. Raises
        ``LLMError`` on any failure; returns ``None`` on success.
        """
        self.analyze(
            system="Connectivity check.",
            user="Call the ack tool with ok set to true.",
            tool=_PING_TOOL,
        )
