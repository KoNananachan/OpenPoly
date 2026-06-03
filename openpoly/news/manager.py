"""News-source runtime manager.

Owns the lifecycle of a TradingNewsWSSource instance, tracks a status snapshot
and a bounded event ring, and serializes start/stop with an asyncio.Lock.
Module-level singleton ``manager`` is wired by FastAPI routes (N3); tests build
their own instances with a fake source_factory to avoid opening real WS.

``record_event`` is the sync hook ws_client will call (N2) for connection
lifecycle events. Counter-only kinds (``message`` / ``reconnect_attempt``) do
not enter the event ring — they update counters; ``message`` synthesizes a
single ``first_message`` ring entry on the first message after each connect.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from openpoly.news.ring_buffer import NewsItem, NewsRingBuffer
from openpoly.sections.news_source.tradingnews_ws import (
    TradingNewsWSConfig,
    TradingNewsWSSource,
)

logger = logging.getLogger(__name__)

State = Literal["stopped", "connecting", "connected", "error"]

EVENT_RING_MAXLEN = 200


@dataclass(frozen=True)
class LogEvent:
    ts: float
    kind: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class StatusSnapshot:
    state: State
    started_at: float | None
    last_msg_at: float | None
    total_recv: int
    buffer_size: int
    running_config: dict[str, Any] | None
    last_error: str | None
    reconnect_attempts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "last_msg_at": self.last_msg_at,
            "total_recv": self.total_recv,
            "buffer_size": self.buffer_size,
            "running_config": self.running_config,
            "last_error": self.last_error,
            "reconnect_attempts": self.reconnect_attempts,
        }


class _SourceLike(Protocol):
    buffer: NewsRingBuffer

    async def start_async(
        self,
        *,
        on_event: Callable[[str, str | None], None] | None = None,
        on_item: Callable[[NewsItem], None] | None = None,
    ) -> None: ...

    async def stop_async(self) -> None: ...


SourceFactory = Callable[[dict[str, Any]], _SourceLike]


def _real_source_factory(config: dict[str, Any]) -> TradingNewsWSSource:
    return TradingNewsWSSource(TradingNewsWSConfig(**config))


class NewsSourceManager:
    """Singleton-style manager for the news-source WS lifecycle.

    Not thread-safe; FastAPI runs a single asyncio loop. Concurrent start/stop
    are serialized via ``_lock``. ``record_event`` is sync + non-blocking so
    the WS task can call it without yielding.
    """

    def __init__(self, source_factory: SourceFactory | None = None) -> None:
        self._source_factory = source_factory or _real_source_factory
        self._lock = asyncio.Lock()
        self._source: _SourceLike | None = None
        self._events: deque[LogEvent] = deque(maxlen=EVENT_RING_MAXLEN)
        self._state: State = "stopped"
        self._started_at: float | None = None
        self._last_msg_at: float | None = None
        self._total_recv: int = 0
        self._last_error: str | None = None
        self._reconnect_attempts: int = 0
        self._running_config: dict[str, Any] | None = None
        # Config that built ``_source``; survives stop() so a same-config
        # restart can reuse the source (preserving its buffer).
        self._source_config: dict[str, Any] | None = None
        # Cleared once a synthetic 'first_message' is appended after a connect.
        self._first_msg_pending: bool = False
        # Pipeline hook is wired in main.py's lifespan (v7) — forwards each
        # fresh NewsItem to the orchestrator queue. Sync, must be non-blocking.
        self._pipeline_hook: Callable[[NewsItem], None] | None = None
        # Persistence hook (B2) — the write-behind writer's enqueue. Sync,
        # non-blocking; independent of the pipeline hook.
        self._news_persist: Callable[[NewsItem], None] | None = None

    # ---------- Event recording (sync; called from ws_client) ----------

    def set_pipeline_hook(self, hook: Callable[[NewsItem], None] | None) -> None:
        """Install / clear the pipeline forwarding hook. Called by lifespan."""
        self._pipeline_hook = hook

    def set_news_persist(self, hook: Callable[[NewsItem], None] | None) -> None:
        """Install / clear the persistence hook — the write-behind writer's
        ``enqueue``. Wired by lifespan; ``None`` in tests."""
        self._news_persist = hook

    def _on_item(self, item: NewsItem) -> None:
        """Sync hook chained from ws_client. Forwards each item to the pipeline
        and the persistence hook; exceptions in either are swallowed so the WS
        loop survives. The two hooks are independent — persistence fires even
        with no pipeline wired, and vice versa."""
        if self._pipeline_hook is not None:
            try:
                self._pipeline_hook(item)
            except Exception:  # noqa: BLE001
                logger.exception("pipeline_hook raised; suppressing")
        if self._news_persist is not None:
            try:
                self._news_persist(item)
            except Exception:  # noqa: BLE001
                logger.exception("news_persist raised; suppressing")

    def record_event(self, kind: str, detail: str | None = None) -> None:
        now = time.time()

        if kind == "message":
            if self._first_msg_pending:
                self._events.append(LogEvent(ts=now, kind="first_message", detail=detail))
                self._first_msg_pending = False
            self._total_recv += 1
            self._last_msg_at = now
            return

        if kind == "reconnect_attempt":
            self._reconnect_attempts += 1
            return

        self._events.append(LogEvent(ts=now, kind=kind, detail=detail))

        if kind == "connected":
            self._state = "connected"
            self._first_msg_pending = True
            self._reconnect_attempts = 0
            self._last_error = None
        elif kind == "disconnected":
            if self._state != "stopped":
                self._state = "connecting"
        elif kind == "auth_fail":
            self._state = "error"
            self._last_error = detail
        elif kind == "connecting":
            if self._state != "stopped":
                self._state = "connecting"

    # ---------- Lifecycle (async; called from HTTP routes) ----------

    async def start(self, config: dict[str, Any]) -> StatusSnapshot:
        async with self._lock:
            if self._state in ("connecting", "connected"):
                return self._snapshot()

            if self._source is None or self._source_config != config:
                self._source = self._source_factory(config)
                self._source_config = dict(config)
                self._total_recv = 0
                self._last_msg_at = None
                self._reconnect_attempts = 0

            self._running_config = dict(config)
            self._started_at = time.time()
            self._state = "connecting"
            self._last_error = None
            self._first_msg_pending = False

            try:
                await self._source.start_async(
                    on_event=self.record_event,
                    on_item=self._on_item,
                )
            except Exception as exc:  # noqa: BLE001 — surface to caller, retain state
                self._state = "error"
                self._last_error = repr(exc)
                self._events.append(LogEvent(ts=time.time(), kind="start_failed", detail=repr(exc)))
                raise

            return self._snapshot()

    async def stop(self) -> StatusSnapshot:
        async with self._lock:
            if self._state == "stopped":
                return self._snapshot()
            if self._source is not None:
                await self._source.stop_async()
            self._state = "stopped"
            self._started_at = None
            self._running_config = None
            self._first_msg_pending = False
            self._events.append(LogEvent(ts=time.time(), kind="stopped"))
            return self._snapshot()

    async def shutdown(self) -> None:
        try:
            await self.stop()
        except Exception:  # noqa: BLE001 — best-effort during process exit
            pass

    # ---------- Read-only queries ----------

    def status(self) -> StatusSnapshot:
        return self._snapshot()

    def events(self, limit: int | None = None) -> list[LogEvent]:
        all_events = list(self._events)
        if limit is None or limit >= len(all_events):
            return all_events
        return all_events[-limit:]

    def recent_messages(self, limit: int = 5) -> list[dict[str, Any]]:
        """Snapshot the most recent ``limit`` items from the source buffer.

        Returned as plain dicts (id / content / urgency / published_at /
        received_at) so the HTTP layer can serialize directly. Order: oldest
        first within the returned slice (consumers reverse for display).
        """
        if self._source is None:
            return []
        items = self._source.buffer.snapshot()
        tail = items[-limit:] if limit > 0 else items
        return [
            {
                "id": it.id,
                "content": it.content,
                "urgency": it.urgency,
                "published_at": it.published_at,
                "received_at": it.received_at,
            }
            for it in tail
        ]

    # ---------- Internal ----------

    def _snapshot(self) -> StatusSnapshot:
        buffer_size = len(self._source.buffer) if self._source is not None else 0
        return StatusSnapshot(
            state=self._state,
            started_at=self._started_at,
            last_msg_at=self._last_msg_at,
            total_recv=self._total_recv,
            buffer_size=buffer_size,
            running_config=dict(self._running_config) if self._running_config else None,
            last_error=self._last_error,
            reconnect_attempts=self._reconnect_attempts,
        )


# Module-level singleton; FastAPI routes (N3) wire to this.
manager = NewsSourceManager()
