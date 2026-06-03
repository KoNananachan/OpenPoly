"""Tests for openpoly.db.writer — the write-behind writer (no DB; fake sink)."""

from __future__ import annotations

import asyncio

from openpoly.db.writer import WriteBehindWriter


async def _wait_until(predicate, *, iterations: int = 300) -> None:
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


async def test_enqueue_and_drain():
    received: list = []
    writer = WriteBehindWriter(received.extend)
    await writer.start()
    for i in range(5):
        assert writer.enqueue(i) is True
    await _wait_until(lambda: writer.written == 5)
    assert sorted(received) == [0, 1, 2, 3, 4]
    await writer.stop()


async def test_overflow_drops_newest():
    writer = WriteBehindWriter(lambda batch: None, queue_maxsize=3)
    assert writer.enqueue("a") is True
    assert writer.enqueue("b") is True
    assert writer.enqueue("c") is True
    assert writer.enqueue("d") is False  # queue full
    assert writer.dropped == 1
    assert writer.pending == 3


async def test_stop_flushes_remaining():
    received: list = []
    writer = WriteBehindWriter(received.extend, batch_size=2)
    for i in range(6):
        writer.enqueue(i)
    await writer.start()
    await writer.stop()  # stop must flush whatever is still queued
    assert sorted(received) == [0, 1, 2, 3, 4, 5]


async def test_sink_error_does_not_kill_loop():
    received: list = []
    calls = {"n": 0}

    def flaky(batch: list) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        received.extend(batch)

    writer = WriteBehindWriter(flaky, batch_size=1)
    await writer.start()
    writer.enqueue("x")  # batch 1 -> sink raises, loop must survive
    await _wait_until(lambda: calls["n"] >= 1)
    writer.enqueue("y")  # batch 2 -> sink ok
    await _wait_until(lambda: received == ["y"])
    await writer.stop()


async def test_batching():
    batches: list[list] = []
    writer = WriteBehindWriter(lambda batch: batches.append(list(batch)), batch_size=10)
    for i in range(25):
        writer.enqueue(i)
    await writer.start()
    await _wait_until(lambda: writer.written == 25)
    await writer.stop()
    assert sum(len(b) for b in batches) == 25
    assert max(len(b) for b in batches) <= 10


async def test_stop_when_not_started_is_safe():
    writer = WriteBehindWriter(lambda batch: None)
    await writer.stop()  # never started -> no-op, must not raise
