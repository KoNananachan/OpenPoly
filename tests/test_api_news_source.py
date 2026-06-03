"""POST/GET /api/news/source/* — manager lifecycle endpoints (N3).

Uses an in-process FakeSource via source_factory swap so tests never open a
real WS. The manager module singleton is reset between tests so state from
one test (running / events / counters) does not leak to the next.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from openpoly.api.main import app, lifespan
from openpoly.news import manager as manager_module
from openpoly.news.manager import NewsSourceManager, manager
from openpoly.news.ring_buffer import NewsRingBuffer


class FakeSource:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.buffer = NewsRingBuffer(maxsize=10)
        self.last_on_event: Any = None
        self.last_on_item: Any = None

    async def start_async(self, *, on_event: Any = None, on_item: Any = None) -> None:
        self.last_on_event = on_event
        self.last_on_item = on_item

    async def stop_async(self) -> None:
        pass


def _fake_factory(config: dict[str, Any]) -> FakeSource:
    return FakeSource(config)


@pytest.fixture(autouse=True)
async def _reset_manager() -> Any:
    """Reset the singleton between tests with a no-WS factory."""
    await manager.shutdown()
    NewsSourceManager.__init__(manager, source_factory=_fake_factory)
    yield
    await manager.shutdown()
    NewsSourceManager.__init__(manager)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------- /api/news/source/status ----------


async def test_status_initial_is_stopped() -> None:
    client = await _client()
    try:
        r = await client.get("/api/news/source/status")
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    snap = body["snapshot"]
    assert snap["state"] == "stopped"
    assert snap["total_recv"] == 0
    assert snap["buffer_size"] == 0
    assert snap["running_config"] is None
    assert snap["events"] == []
    assert snap["recent_messages"] == []


# ---------- /api/news/source/start ----------


async def test_start_happy_path_preserves_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_SOURCE_KEY", "tk")
    client = await _client()
    try:
        r = await client.post(
            "/api/news/source/start",
            json={
                "endpoint": "wss://fake/x",
                "api_key_ref": "env:OPENPOLY_TEST_SOURCE_KEY",
                "freshness_seconds": 600,
            },
        )
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    snap = body["snapshot"]
    # FakeSource doesn't emit 'connected'; manager remains in 'connecting'.
    assert snap["state"] == "connecting"
    assert snap["running_config"]["endpoint"] == "wss://fake/x"
    # ref preserved, never the resolved secret
    assert snap["running_config"]["api_key_ref"] == "env:OPENPOLY_TEST_SOURCE_KEY"
    assert "tk" not in str(snap["running_config"])


async def test_start_missing_env_returns_error() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/news/source/start",
            json={
                "endpoint": "wss://x",
                "api_key_ref": "env:OPENPOLY_DEFINITELY_NOT_SET",
            },
        )
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "secret resolve failed" in body["error"]
    assert body["snapshot"]["state"] == "stopped"
    assert body["snapshot"]["running_config"] is None


async def test_start_unsupported_scheme_returns_error() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/news/source/start",
            json={"endpoint": "wss://x", "api_key_ref": "vault:secret/x"},
        )
    finally:
        await client.aclose()
    body = r.json()
    assert body["ok"] is False
    assert "not supported" in body["error"] or "scheme" in body["error"]
    assert body["snapshot"]["state"] == "stopped"


async def test_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_SOURCE_KEY", "tk")
    client = await _client()
    try:
        payload = {
            "endpoint": "wss://x",
            "api_key_ref": "env:OPENPOLY_TEST_SOURCE_KEY",
        }
        r1 = await client.post("/api/news/source/start", json=payload)
        r2 = await client.post("/api/news/source/start", json=payload)
    finally:
        await client.aclose()
    assert r1.json()["snapshot"]["state"] == "connecting"
    assert r2.json()["ok"] is True
    assert r2.json()["snapshot"]["state"] == "connecting"


# ---------- /api/news/source/stop ----------


async def test_stop_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_SOURCE_KEY", "tk")
    client = await _client()
    try:
        await client.post(
            "/api/news/source/start",
            json={
                "endpoint": "wss://x",
                "api_key_ref": "env:OPENPOLY_TEST_SOURCE_KEY",
            },
        )
        r = await client.post("/api/news/source/stop")
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    snap = body["snapshot"]
    assert snap["state"] == "stopped"
    assert snap["running_config"] is None


async def test_stop_when_already_stopped_is_noop() -> None:
    client = await _client()
    try:
        r = await client.post("/api/news/source/stop")
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["state"] == "stopped"


# ---------- events surfaced via /status ----------


async def test_status_surfaces_events_after_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate ws_client emitting events via the wired hook to confirm the
    snapshot embeds the event ring."""
    monkeypatch.setenv("OPENPOLY_TEST_SOURCE_KEY", "tk")
    client = await _client()
    try:
        await client.post(
            "/api/news/source/start",
            json={
                "endpoint": "wss://x",
                "api_key_ref": "env:OPENPOLY_TEST_SOURCE_KEY",
            },
        )
        manager.record_event("connected", "wss://x")
        manager.record_event("message", "id-1")

        r = await client.get("/api/news/source/status")
    finally:
        await client.aclose()
    snap = r.json()["snapshot"]
    assert snap["state"] == "connected"
    assert snap["total_recv"] == 1
    kinds = [e["kind"] for e in snap["events"]]
    assert "connected" in kinds
    assert "first_message" in kinds


# ---------- lifespan ----------


async def test_status_surfaces_recent_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushing items into the source buffer should surface them via /status."""
    from openpoly.news.ring_buffer import NewsItem

    monkeypatch.setenv("OPENPOLY_TEST_SOURCE_KEY", "tk")
    client = await _client()
    try:
        await client.post(
            "/api/news/source/start",
            json={
                "endpoint": "wss://x",
                "api_key_ref": "env:OPENPOLY_TEST_SOURCE_KEY",
            },
        )
        # FakeSource has a real NewsRingBuffer; push items directly.
        assert manager._source is not None
        for i in range(7):
            manager._source.buffer.append(
                NewsItem(
                    id=f"m{i}",
                    content=f"content {i}",
                    urgency="high",
                    sentiment=None,
                    published_at=float(i),
                    received_at=float(i),
                )
            )

        r = await client.get("/api/news/source/status")
    finally:
        await client.aclose()
    msgs = r.json()["snapshot"]["recent_messages"]
    # Default tail=5 — newest 5 returned
    assert [m["id"] for m in msgs] == ["m2", "m3", "m4", "m5", "m6"]
    assert msgs[0]["content"] == "content 2"


async def test_lifespan_calls_manager_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    original = manager_module.manager.shutdown

    async def fake_shutdown() -> None:
        called.append(True)
        await original()

    monkeypatch.setattr(manager_module.manager, "shutdown", fake_shutdown)

    async with lifespan(app):
        pass

    assert called == [True]
