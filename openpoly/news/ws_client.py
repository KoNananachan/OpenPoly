"""Async WebSocket client with exponential-backoff reconnect.

Connects to a news provider's WS endpoint with the `X-API-Key` header. Each
incoming message is parsed and pushed into the supplied ring buffer. The loop
yields cooperatively (``await asyncio.sleep(0)``) so co-scheduled tasks are
never starved (lesson from a prior project's async-reconnect-starvation incident).

Lifecycle: external code calls ``run_forever()`` as an asyncio task; ``stop()``
sets an event that breaks the loop, then the task should be cancelled / awaited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from typing import Callable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .ring_buffer import NewsItem, NewsRingBuffer

# (kind, detail) — see manager.NewsSourceManager.record_event for the contract.
OnEventHook = Callable[[str, str | None], None]

# Single fresh news item — wired by manager to orchestrator.enqueue in v7
# lifespan. Sync because orchestrator.enqueue is sync.
OnItemHook = Callable[[NewsItem], None]

logger = logging.getLogger(__name__)


def _to_epoch(value: object, fallback: float) -> float:
    """Accept ISO 8601 string or numeric epoch; fall back if neither parses."""
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # tradingnews emits ISO 8601 with offset (e.g. 2026-05-19T10:49:35+00:00).
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return fallback
    return fallback


def default_parse(raw: str) -> NewsItem | None:
    """Parse a news provider WS message. Returns None on malformed input.

    Handles tradingnews shape (ISO 8601 timestamps, free-form urgency string).
    Unknown providers can override by passing a custom parser to NewsWSClient.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    now = time.time()
    try:
        return NewsItem(
            id=str(data["id"]),
            content=str(data.get("content", "")),
            urgency=str(data.get("urgency", "regular")),
            sentiment=data.get("sentiment"),
            published_at=_to_epoch(data.get("published_at"), now),
            received_at=now,
            raw=data,
        )
    except (KeyError, ValueError, TypeError):
        return None


def _redact(url: str) -> str:
    """Strip query string from URL for logging (api_key may live there)."""
    return url.split("?", 1)[0]


class NewsWSClient:
    def __init__(
        self,
        endpoint: str,
        buffer: NewsRingBuffer,
        *,
        headers: dict[str, str] | None = None,
        parse: Callable[[str], NewsItem | None] = default_parse,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        ping_interval: float | None = 20.0,
        on_event: OnEventHook | None = None,
        on_item: OnItemHook | None = None,
        freshness_seconds: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}
        self.buffer = buffer
        self.parse = parse
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.ping_interval = ping_interval
        self._stop = asyncio.Event()
        self._on_event = on_event
        self._on_item = on_item
        # When set, items older than this (by ``received_at - published_at``)
        # are silently dropped: not appended to buffer, no ``message`` emit,
        # not enqueued for the pipeline. Mainly guards against startup
        # backlog (server replaying recent history on reconnect). ``None``
        # disables the filter entirely.
        self._freshness_seconds = freshness_seconds

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, kind: str, detail: str | None = None) -> None:
        """Forward a lifecycle event to the hook; exceptions are swallowed so
        a buggy consumer cannot kill the WS loop."""
        if self._on_event is None:
            return
        try:
            self._on_event(kind, detail)
        except Exception:  # noqa: BLE001 — hook is untrusted code
            logger.exception("on_event hook raised; suppressing")

    def _emit_item(self, item: NewsItem) -> None:
        """Forward a fresh news item to the on_item hook. Same exception
        discipline as ``_emit`` — never kills the WS loop."""
        if self._on_item is None:
            return
        try:
            self._on_item(item)
        except Exception:  # noqa: BLE001
            logger.exception("on_item hook raised; suppressing")

    async def run_forever(self) -> None:
        backoff = self.initial_backoff
        is_first_attempt = True
        while not self._stop.is_set():
            if is_first_attempt:
                self._emit("connecting", _redact(self.endpoint))
                is_first_attempt = False
            else:
                self._emit("reconnect_attempt")
            try:
                connect_kwargs: dict = {"ping_interval": self.ping_interval}
                if self.headers:
                    connect_kwargs["additional_headers"] = self.headers
                async with websockets.connect(self.endpoint, **connect_kwargs) as ws:
                    backoff = self.initial_backoff
                    self._emit("connected", _redact(self.endpoint))
                    logger.info("WS connected to %s", _redact(self.endpoint))
                    await self._consume(ws)
                self._emit("disconnected", "server closed cleanly")
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                # HTTP-status reject during upgrade — almost always auth/config,
                # not transient. Stop retrying; user must fix and restart.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                detail = f"HTTP {status}" if status else str(exc)
                self._emit("auth_fail", detail)
                logger.error("WS rejected by server (%s); stopping", detail)
                self._stop.set()
                break
            except (ConnectionClosed, WebSocketException, OSError) as exc:
                self._emit("disconnected", str(exc))
                logger.warning("WS dropped: %s; reconnect in %.2fs", exc, backoff)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                backoff = min(backoff * 2.0, self.max_backoff)

    async def _consume(self, ws) -> None:
        async for raw in ws:
            if self._stop.is_set():
                break
            text = raw if isinstance(raw, str) else raw.decode()
            item = self.parse(text)
            if item is None:
                snippet = text[:80] if isinstance(text, str) else None
                self._emit("parse_error", snippet)
                await asyncio.sleep(0)
                continue
            if self._freshness_seconds is not None:
                # Negative ages (clock drift / published in future) sail
                # through; only positive ages past the threshold drop.
                age = item.received_at - item.published_at
                if age > self._freshness_seconds:
                    # Silent drop per plan §P2 — no buffer, no emit.
                    await asyncio.sleep(0)
                    continue
            self.buffer.append(item)
            self._emit("message", item.id)
            self._emit_item(item)
            await asyncio.sleep(0)
