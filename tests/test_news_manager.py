"""Unit tests for NewsSourceManager (N1).

Uses a FakeSource via source_factory injection — no real WS opened.
Covers: state transitions, event ring policy, lifecycle locking, snapshot shape.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openpoly.news.manager import (
    EVENT_RING_MAXLEN,
    LogEvent,
    NewsSourceManager,
)
from openpoly.news.ring_buffer import NewsItem, NewsRingBuffer


class FakeSource:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.buffer = NewsRingBuffer(maxsize=10)
        self.start_count = 0
        self.stop_count = 0
        self.last_on_event: Any = None
        self.last_on_item: Any = None

    async def start_async(self, *, on_event: Any = None, on_item: Any = None) -> None:
        self.start_count += 1
        self.last_on_event = on_event
        self.last_on_item = on_item

    async def stop_async(self) -> None:
        self.stop_count += 1


def make_manager() -> tuple[NewsSourceManager, list[FakeSource]]:
    sources: list[FakeSource] = []

    def factory(config: dict[str, Any]) -> FakeSource:
        s = FakeSource(config)
        sources.append(s)
        return s

    return NewsSourceManager(source_factory=factory), sources


# ---------- record_event policy ----------


def test_initial_status_is_stopped() -> None:
    m, _ = make_manager()
    s = m.status()
    assert s.state == "stopped"
    assert s.total_recv == 0
    assert s.buffer_size == 0
    assert s.running_config is None
    assert s.started_at is None
    assert s.last_msg_at is None
    assert s.reconnect_attempts == 0
    assert m.events() == []


def test_connected_transitions_to_connected() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    assert m.status().state == "connected"
    evs = m.events()
    assert [e.kind for e in evs] == ["connected"]


def test_first_message_synthesized_after_connect() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    m.record_event("message", detail="abc")
    s = m.status()
    assert s.total_recv == 1
    assert s.last_msg_at is not None
    assert [e.kind for e in m.events()] == ["connected", "first_message"]


def test_subsequent_messages_counter_only() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    for _ in range(5):
        m.record_event("message")
    assert m.status().total_recv == 5
    kinds = [e.kind for e in m.events()]
    assert kinds.count("first_message") == 1
    assert "message" not in kinds


def test_first_message_resets_after_reconnect() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    m.record_event("message")
    m.record_event("disconnected", "closed")
    m.record_event("connected")
    m.record_event("message")
    kinds = [e.kind for e in m.events()]
    assert kinds.count("first_message") == 2


def test_reconnect_attempt_counter_only_no_ring() -> None:
    m, _ = make_manager()
    m.record_event("disconnected", "closed")
    for _ in range(3):
        m.record_event("reconnect_attempt")
    assert m.status().reconnect_attempts == 3
    assert "reconnect_attempt" not in [e.kind for e in m.events()]


def test_disconnected_while_running_goes_to_connecting() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    m.record_event("disconnected", "closed")
    assert m.status().state == "connecting"


def test_disconnected_while_stopped_stays_stopped() -> None:
    m, _ = make_manager()
    # state starts as 'stopped'; an unsolicited disconnected event should not flip it
    m.record_event("disconnected", "spurious")
    assert m.status().state == "stopped"


def test_auth_fail_to_error_with_detail() -> None:
    m, _ = make_manager()
    m.record_event("auth_fail", "401 Unauthorized")
    s = m.status()
    assert s.state == "error"
    assert s.last_error == "401 Unauthorized"


def test_parse_error_does_not_change_state() -> None:
    m, _ = make_manager()
    m.record_event("connected")
    m.record_event("parse_error", "bad json")
    assert m.status().state == "connected"
    assert "parse_error" in [e.kind for e in m.events()]


def test_connected_clears_last_error_and_resets_reconnects() -> None:
    m, _ = make_manager()
    m.record_event("auth_fail", "transient")
    m.record_event("reconnect_attempt")
    m.record_event("reconnect_attempt")
    m.record_event("connected")
    s = m.status()
    assert s.last_error is None
    assert s.reconnect_attempts == 0


# ---------- event ring capacity ----------


def test_event_ring_truncates_to_maxlen() -> None:
    m, _ = make_manager()
    overflow = 50
    for i in range(EVENT_RING_MAXLEN + overflow):
        m.record_event("parse_error", f"i={i}")
    evs = m.events()
    assert len(evs) == EVENT_RING_MAXLEN
    # Newest preserved, oldest evicted
    assert evs[-1].detail == f"i={EVENT_RING_MAXLEN + overflow - 1}"
    assert evs[0].detail == f"i={overflow}"


def test_events_limit_returns_tail() -> None:
    m, _ = make_manager()
    for i in range(10):
        m.record_event("parse_error", str(i))
    tail = m.events(limit=3)
    assert [e.detail for e in tail] == ["7", "8", "9"]


def test_log_event_to_dict_shape() -> None:
    ev = LogEvent(ts=1.5, kind="connected", detail="ok")
    assert ev.to_dict() == {"ts": 1.5, "kind": "connected", "detail": "ok"}


# ---------- lifecycle (async) ----------


async def test_start_sets_connecting_and_calls_source() -> None:
    m, sources = make_manager()
    cfg = {"endpoint": "wss://test", "api_key_ref": "env:X"}
    snap = await m.start(cfg)
    assert snap.state == "connecting"
    assert snap.running_config == cfg
    assert snap.started_at is not None
    assert len(sources) == 1
    assert sources[0].start_count == 1
    # Manager wires its record_event as the source's on_event hook
    assert sources[0].last_on_event == m.record_event


async def test_start_is_idempotent_while_running() -> None:
    m, sources = make_manager()
    cfg = {"endpoint": "wss://test"}
    await m.start(cfg)
    # While 'connecting' (or 'connected'), second start is a no-op
    await m.start(cfg)
    assert sources[0].start_count == 1


async def test_stop_then_start_same_config_reuses_source() -> None:
    m, sources = make_manager()
    cfg = {"endpoint": "wss://test"}
    await m.start(cfg)
    await m.stop()
    await m.start(cfg)
    # Same FakeSource reused — buffer preserved (per plan decision)
    assert len(sources) == 1
    assert sources[0].start_count == 2


async def test_stop_then_start_different_config_builds_new_source() -> None:
    m, sources = make_manager()
    await m.start({"endpoint": "wss://a"})
    await m.stop()
    await m.start({"endpoint": "wss://b"})
    assert len(sources) == 2


async def test_stop_when_already_stopped_is_noop() -> None:
    m, sources = make_manager()
    snap = await m.stop()
    assert snap.state == "stopped"
    assert len(sources) == 0


async def test_stop_clears_running_config_but_preserves_buffer() -> None:
    m, sources = make_manager()
    await m.start({"endpoint": "wss://x"})
    sources[0].buffer.append(
        NewsItem(
            id="x",
            content="",
            urgency="medium",
            sentiment=None,
            published_at=0.0,
            received_at=0.0,
        )
    )
    snap = await m.stop()
    assert snap.state == "stopped"
    assert snap.running_config is None
    assert snap.buffer_size == 1  # buffer preserved


async def test_concurrent_starts_serialize_into_single_source() -> None:
    m, sources = make_manager()
    cfg = {"endpoint": "wss://x"}
    await asyncio.gather(m.start(cfg), m.start(cfg), m.start(cfg))
    assert len(sources) == 1
    assert sources[0].start_count == 1


async def test_start_failure_sets_error_state_and_records_event() -> None:
    class FailingSource:
        def __init__(self, config: dict[str, Any]) -> None:
            self.buffer = NewsRingBuffer(maxsize=10)

        async def start_async(self, *, on_event: Any = None, on_item: Any = None) -> None:
            raise RuntimeError("boom")

        async def stop_async(self) -> None:
            pass

    m = NewsSourceManager(source_factory=lambda c: FailingSource(c))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom"):
        await m.start({"endpoint": "wss://x"})
    s = m.status()
    assert s.state == "error"
    assert s.last_error is not None and "boom" in s.last_error
    assert "start_failed" in [e.kind for e in m.events()]


async def test_shutdown_stops_running_source() -> None:
    m, sources = make_manager()
    await m.start({"endpoint": "wss://x"})
    await m.shutdown()
    assert m.status().state == "stopped"
    assert sources[0].stop_count == 1


async def test_buffer_size_reflects_source_buffer() -> None:
    m, sources = make_manager()
    await m.start({"endpoint": "wss://x"})
    for i in range(3):
        sources[0].buffer.append(
            NewsItem(
                id=f"x{i}",
                content="",
                urgency="medium",
                sentiment=None,
                published_at=0.0,
                received_at=0.0,
            )
        )
    assert m.status().buffer_size == 3


async def test_status_snapshot_to_dict_keys() -> None:
    m, _ = make_manager()
    d = m.status().to_dict()
    assert set(d.keys()) == {
        "state",
        "started_at",
        "last_msg_at",
        "total_recv",
        "buffer_size",
        "running_config",
        "last_error",
        "reconnect_attempts",
    }
