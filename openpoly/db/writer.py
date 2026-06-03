"""Write-behind DB writer.

Hot paths (the news WS callback, the market poll / book-sample loops) must
never block on a DB round-trip. They ``enqueue`` rows synchronously into a
bounded queue; a single background task drains it in batches and persists each
batch off the event loop. The actual persist call is an injected ``sink``, so
the buffering logic is fully testable without a database.

Overflow drops the newest row (and counts it) rather than blocking a producer —
the same discipline as the pipeline orchestrator's queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAXSIZE = 5000
DEFAULT_BATCH_SIZE = 200

# Persists one batch of rows. Sync — runs in a worker thread, off the loop.
Sink = Callable[[list[Any]], None]


class WriteBehindWriter:
    """Bounded queue + a single drain task.

    ``enqueue`` is sync and non-blocking. ``start`` launches the drain loop;
    ``stop`` cancels it and flushes whatever is still queued.
    """

    def __init__(
        self,
        sink: Sink,
        *,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._sink = sink
        self._batch_size = batch_size
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_maxsize)
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0
        self._written = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def written(self) -> int:
        return self._written

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def enqueue(self, row: Any) -> bool:
        """Queue one row. Returns False (and counts a drop) when the queue is
        full — never blocks the caller."""
        try:
            self._queue.put_nowait(row)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            return False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Cancel the drain loop, then flush whatever is still queued."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._flush()

    # ---------- internals ----------

    async def _drain_loop(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._write(batch)

    async def _flush(self) -> None:
        """Drain everything still queued in one final pass."""
        batch: list[Any] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._write(batch)

    async def _write(self, batch: list[Any]) -> None:
        """Persist a batch via the sink, off the event loop. Sink errors are
        logged and swallowed — a bad write must not kill the drain loop."""
        try:
            await asyncio.to_thread(self._sink, batch)
            self._written += len(batch)
        except Exception:  # noqa: BLE001 — drain loop must survive sink errors
            logger.exception("write-behind sink failed for %d rows", len(batch))
