"""WS client live test using a local websockets.serve fixture.

Verifies happy-path message ingest and reconnect-after-drop. Kept fast
(sub-second) so it runs as part of the default pytest suite.
"""

from __future__ import annotations

import asyncio
import json

import websockets
from websockets.exceptions import InvalidStatus

from openpoly.news.ring_buffer import NewsRingBuffer
from openpoly.news.ws_client import NewsWSClient, default_parse


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.05) -> bool:
    iters = int(timeout / interval)
    for _ in range(iters):
        await asyncio.sleep(interval)
        if predicate():
            return True
    return False


async def test_ws_client_receives_messages() -> None:
    async def handler(ws):
        for i in range(3):
            await ws.send(
                json.dumps(
                    {
                        "id": f"n{i}",
                        "content": f"news {i}",
                        "urgency": "high",
                        "published_at": 1700000000.0 + i,
                    }
                )
            )
        await asyncio.sleep(0.1)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        buf = NewsRingBuffer(maxsize=10)
        client = NewsWSClient(
            endpoint=f"ws://127.0.0.1:{port}",
            buffer=buf,
            initial_backoff=0.05,
            max_backoff=0.1,
            ping_interval=None,
        )
        task = asyncio.create_task(client.run_forever())
        try:
            ok = await _wait_for(lambda: len(buf) >= 3, timeout=3.0)
            assert ok, f"expected 3 items, got {len(buf)}"
        finally:
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    ids = [it.id for it in buf.snapshot()]
    assert ids == ["n0", "n1", "n2"]


async def test_ws_client_reconnects_after_drop() -> None:
    counter = {"connect": 0}

    async def handler(ws):
        counter["connect"] += 1
        await ws.send(
            json.dumps(
                {
                    "id": f"hit{counter['connect']}",
                    "content": "x",
                    "urgency": "low",
                    "published_at": 0.0,
                }
            )
        )
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        buf = NewsRingBuffer(maxsize=10)
        client = NewsWSClient(
            endpoint=f"ws://127.0.0.1:{port}",
            buffer=buf,
            initial_backoff=0.05,
            max_backoff=0.1,
            ping_interval=None,
        )
        task = asyncio.create_task(client.run_forever())
        try:
            ok = await _wait_for(lambda: counter["connect"] >= 2, timeout=3.0)
            assert ok, f"expected ≥2 connects, got {counter['connect']}"
        finally:
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert len(buf) >= 2


def test_default_parse_malformed_json() -> None:
    assert default_parse("not json {{}") is None


def test_default_parse_missing_id() -> None:
    assert default_parse(json.dumps({"content": "x"})) is None


async def test_on_event_lifecycle_emits_connecting_connected_message_disconnected() -> None:
    events: list[tuple[str, str | None]] = []

    async def handler(ws):
        await ws.send(
            json.dumps(
                {
                    "id": "x1",
                    "content": "hi",
                    "urgency": "high",
                    "published_at": 0.0,
                }
            )
        )
        await ws.send("not json {{")
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        buf = NewsRingBuffer(maxsize=10)
        client = NewsWSClient(
            endpoint=f"ws://127.0.0.1:{port}",
            buffer=buf,
            initial_backoff=0.05,
            max_backoff=0.1,
            ping_interval=None,
            on_event=lambda k, d: events.append((k, d)),
        )
        task = asyncio.create_task(client.run_forever())
        try:
            ok = await _wait_for(lambda: any(e[0] == "disconnected" for e in events), timeout=3.0)
            assert ok, f"expected disconnect event, got {[e[0] for e in events]}"
        finally:
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    kinds = [e[0] for e in events]
    assert kinds[0] == "connecting"
    assert "connected" in kinds
    assert "message" in kinds
    assert "parse_error" in kinds
    assert "disconnected" in kinds
    # message detail carries the item id
    msg_ev = next(e for e in events if e[0] == "message")
    assert msg_ev[1] == "x1"


async def test_on_event_reconnect_attempt_after_drop() -> None:
    """The first attempt emits ``connecting``; subsequent attempts emit
    ``reconnect_attempt`` so the manager can keep them out of the event ring."""
    counter = {"connect": 0}

    async def handler(ws):
        counter["connect"] += 1
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        events: list[tuple[str, str | None]] = []
        buf = NewsRingBuffer(maxsize=10)
        client = NewsWSClient(
            endpoint=f"ws://127.0.0.1:{port}",
            buffer=buf,
            initial_backoff=0.05,
            max_backoff=0.1,
            ping_interval=None,
            on_event=lambda k, d: events.append((k, d)),
        )
        task = asyncio.create_task(client.run_forever())
        try:
            ok = await _wait_for(lambda: counter["connect"] >= 2, timeout=3.0)
            assert ok, f"expected ≥2 connects, got {counter['connect']}"
        finally:
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    kinds = [e[0] for e in events]
    assert kinds.count("connecting") == 1
    assert "reconnect_attempt" in kinds


async def test_on_event_auth_fail_stops_loop(monkeypatch) -> None:
    """InvalidStatus during upgrade should emit auth_fail and halt the loop."""
    import types as _types

    from openpoly.news import ws_client as wsc_mod

    class FakeConnectCM:
        async def __aenter__(self):
            raise InvalidStatus(_types.SimpleNamespace(status_code=403))

        async def __aexit__(self, *a):
            return False

    def fake_connect(*_args, **_kwargs):
        return FakeConnectCM()

    monkeypatch.setattr(wsc_mod.websockets, "connect", fake_connect)

    events: list[tuple[str, str | None]] = []
    buf = NewsRingBuffer(maxsize=10)
    client = NewsWSClient(
        endpoint="ws://127.0.0.1:0",
        buffer=buf,
        initial_backoff=0.05,
        max_backoff=0.1,
        ping_interval=None,
        on_event=lambda k, d: events.append((k, d)),
    )
    # auth_fail should break the loop; bound by timeout in case of regression.
    await asyncio.wait_for(client.run_forever(), timeout=2.0)
    kinds = [e[0] for e in events]
    assert "auth_fail" in kinds
    auth_ev = next(e for e in events if e[0] == "auth_fail")
    assert "403" in (auth_ev[1] or "")
    # connecting fires exactly once; no reconnect_attempt because we stopped
    assert kinds.count("connecting") == 1
    assert "reconnect_attempt" not in kinds


async def test_freshness_drops_stale_news() -> None:
    """News older than freshness window is silently dropped: not in buffer,
    no 'message' event."""

    async def handler(ws):
        # One fresh (published_at = now), one stale (far in past).
        import time as _time

        now = _time.time()
        await ws.send(
            json.dumps(
                {
                    "id": "fresh",
                    "content": "live",
                    "urgency": "high",
                    "published_at": now,
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "id": "stale",
                    "content": "old",
                    "urgency": "high",
                    "published_at": now - 7200,  # 2h old
                }
            )
        )
        # Hold the connection open so the client doesn't reconnect and
        # re-receive the same fresh message multiple times.
        await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        events: list[tuple[str, str | None]] = []
        buf = NewsRingBuffer(maxsize=10)
        client = NewsWSClient(
            endpoint=f"ws://127.0.0.1:{port}",
            buffer=buf,
            initial_backoff=0.05,
            max_backoff=0.1,
            ping_interval=None,
            on_event=lambda k, d: events.append((k, d)),
            freshness_seconds=1800,  # 30 min
        )
        task = asyncio.create_task(client.run_forever())
        try:
            ok = await _wait_for(lambda: len(buf) >= 1, timeout=3.0)
            assert ok
            await asyncio.sleep(0.2)
        finally:
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    ids_in_buffer = [it.id for it in buf.snapshot()]
    assert ids_in_buffer == ["fresh"]
    msg_events = [e for e in events if e[0] == "message"]
    assert len(msg_events) == 1
    assert msg_events[0][1] == "fresh"


def test_on_event_hook_exception_does_not_crash_emit() -> None:
    buf = NewsRingBuffer(maxsize=10)

    def bad_hook(_k: str, _d: str | None) -> None:
        raise ValueError("hook explode")

    client = NewsWSClient(endpoint="ws://x", buffer=buf, on_event=bad_hook)
    # Should not raise.
    client._emit("connected", "x")


def test_default_parse_happy() -> None:
    item = default_parse(
        json.dumps(
            {
                "id": "abc",
                "content": "hello",
                "urgency": "medium",
                "sentiment": 0.2,
                "published_at": 100.0,
            }
        )
    )
    assert item is not None
    assert item.id == "abc"
    assert item.content == "hello"
    assert item.urgency == "medium"
    assert item.sentiment == 0.2
    assert item.published_at == 100.0
